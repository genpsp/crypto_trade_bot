from __future__ import annotations

from dataclasses import dataclass

from pybot.app.ports.execution_port import ExecutionPort, SubmitSwapRequest
from pybot.app.ports.lock_port import LockPort
from pybot.app.ports.logger_port import LoggerPort
from pybot.app.ports.persistence_port import PersistencePort
from pybot.app.usecases.usecase_utils import now_iso, strip_none, to_error_message
from pybot.domain.model.trade_state import assert_trade_state_transition
from pybot.domain.model.types import BotConfig, CloseReason, TradeRecord, TradeState
from pybot.domain.utils.math import round_to

SOL_ATOMIC_MULTIPLIER = 1_000_000_000
USDC_ATOMIC_MULTIPLIER = 1_000_000
TX_CONFIRM_TIMEOUT_MS = 75_000
TX_INFLIGHT_TTL_SECONDS = 180


@dataclass
class ClosePositionInput:
    config: BotConfig
    trade: TradeRecord
    close_reason: CloseReason
    close_price: float


@dataclass
class ClosePositionResult:
    status: str
    trade_id: str
    summary: str


@dataclass
class ClosePositionDependencies:
    execution: ExecutionPort
    lock: LockPort
    logger: LoggerPort
    persistence: PersistencePort


def close_position(
    dependencies: ClosePositionDependencies, input_data: ClosePositionInput
) -> ClosePositionResult:
    execution = dependencies.execution
    lock = dependencies.lock
    logger = dependencies.logger
    persistence = dependencies.persistence
    config = input_data.config
    trade = input_data.trade
    close_reason = input_data.close_reason
    close_price = input_data.close_price

    if trade["state"] != "CONFIRMED":
        return ClosePositionResult(
            status="FAILED",
            trade_id=trade["trade_id"],
            summary=f"FAILED: trade state is {trade['state']}, expected CONFIRMED",
        )

    current_state: TradeState = trade["state"]  # type: ignore[assignment]

    def move_state(next_state: TradeState) -> None:
        nonlocal current_state
        assert_trade_state_transition(current_state, next_state)
        next_updated_at = now_iso()
        persistence.update_trade(
            trade["trade_id"],
            strip_none(
                {
                    "state": next_state,
                    "execution": trade["execution"],
                    "position": trade["position"],
                    "close_reason": trade.get("close_reason"),
                    "updated_at": next_updated_at,
                }
            ),
        )
        current_state = next_state
        trade["state"] = next_state
        trade["updated_at"] = next_updated_at

    def persist_execution_only() -> None:
        trade["updated_at"] = now_iso()
        persistence.update_trade(
            trade["trade_id"],
            strip_none({"execution": trade["execution"], "updated_at": trade["updated_at"]}),
        )

    amount_atomic = int(trade["position"]["quantity_sol"] * SOL_ATOMIC_MULTIPLIER)
    if amount_atomic <= 0:
        trade["execution"]["exit_submission_state"] = "FAILED"
        trade["execution"]["exit_error"] = "position quantity is 0"
        move_state("FAILED")
        return ClosePositionResult(
            status="FAILED", trade_id=trade["trade_id"], summary="FAILED: position quantity is 0"
        )

    try:
        submission = execution.submit_swap(
            SubmitSwapRequest(
                side="SELL_SOL_FOR_USDC",
                amount_atomic=amount_atomic,
                slippage_bps=config["execution"]["slippage_bps"],
                only_direct_routes=config["execution"]["only_direct_routes"],
            )
        )

        trade["execution"]["exit_tx_signature"] = submission.tx_signature
        if submission.order:
            trade["execution"]["exit_order"] = submission.order
        exit_result = submission.result
        if exit_result is None:
            estimated_input_sol = submission.in_amount_atomic / SOL_ATOMIC_MULTIPLIER
            estimated_output_usdc = submission.out_amount_atomic / USDC_ATOMIC_MULTIPLIER
            estimated_avg_fill_price = (
                estimated_output_usdc / estimated_input_sol if estimated_input_sol > 0 else close_price
            )
            exit_result = {
                "status": "ESTIMATED",
                "avg_fill_price": estimated_avg_fill_price,
                "spent_quote_usdc": estimated_output_usdc,
                "filled_base_sol": estimated_input_sol,
            }
        trade["execution"]["exit_result"] = exit_result
        trade["execution"]["exit_submission_state"] = "SUBMITTED"
        trade["updated_at"] = now_iso()
        persistence.update_trade(
            trade["trade_id"],
            strip_none({"execution": trade["execution"], "updated_at": trade["updated_at"]}),
        )

        lock.set_inflight_tx(submission.tx_signature, TX_INFLIGHT_TTL_SECONDS)
        confirmation = execution.confirm_swap(submission.tx_signature, TX_CONFIRM_TIMEOUT_MS)
        lock.clear_inflight_tx(submission.tx_signature)

        if not confirmation.confirmed:
            trade["execution"]["exit_submission_state"] = "FAILED"
            trade["execution"]["exit_error"] = confirmation.error or "unknown confirmation error"
            persist_execution_only()
            return ClosePositionResult(
                status="FAILED",
                trade_id=trade["trade_id"],
                summary=f"FAILED: exit tx not confirmed ({trade['execution']['exit_error']})",
            )

        input_sol = submission.in_amount_atomic / SOL_ATOMIC_MULTIPLIER
        output_usdc = submission.out_amount_atomic / USDC_ATOMIC_MULTIPLIER
        fallback_exit_price = output_usdc / input_sol if input_sol > 0 else close_price
        resolved_exit_price = (
            float(exit_result["avg_fill_price"])
            if "avg_fill_price" in exit_result
            else fallback_exit_price
        )

        previous_position_snapshot = dict(trade["position"])
        previous_close_reason = trade.get("close_reason")
        trade["execution"]["exit_submission_state"] = "CONFIRMED"
        trade["position"]["status"] = "CLOSED"
        trade["position"]["exit_price"] = round_to(resolved_exit_price, 6)
        trade["position"]["exit_trigger_price"] = round_to(close_price, 6)
        trade["position"]["exit_time_iso"] = now_iso()
        trade["close_reason"] = close_reason

        try:
            move_state("CLOSED")
        except Exception:
            trade["position"] = previous_position_snapshot
            if previous_close_reason is None and "close_reason" in trade:
                del trade["close_reason"]
            elif previous_close_reason is not None:
                trade["close_reason"] = previous_close_reason
            raise

        return ClosePositionResult(
            status="CLOSED",
            trade_id=trade["trade_id"],
            summary=(
                f"CLOSED: reason={close_reason}, tx={submission.tx_signature}, "
                f"fill={round_to(trade['position']['exit_price'], 4)}, trigger={round_to(close_price, 4)}"
            ),
        )
    except Exception as error:
        error_message = to_error_message(error)
        logger.error("close_position failed", {"trade_id": trade["trade_id"], "error": error_message})
        trade["execution"]["exit_submission_state"] = "FAILED"
        trade["execution"]["exit_error"] = error_message
        try:
            persist_execution_only()
        except Exception as persist_error:
            logger.error(
                "close_position execution persistence failed",
                {"trade_id": trade["trade_id"], "error": to_error_message(persist_error)},
            )
        return ClosePositionResult(
            status="FAILED",
            trade_id=trade["trade_id"],
            summary=f"FAILED: {error_message}",
        )
