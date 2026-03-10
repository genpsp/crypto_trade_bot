from __future__ import annotations

from dataclasses import dataclass

from apps.gmo_bot.app.ports.execution_port import ExecutionPort, SubmitCloseOrderRequest
from apps.gmo_bot.app.ports.lock_port import LockPort
from apps.gmo_bot.app.ports.logger_port import LoggerPort
from apps.gmo_bot.app.ports.persistence_port import PersistencePort
from apps.gmo_bot.app.usecases.usecase_utils import now_iso, strip_none, summarize_error_for_log, to_error_message
from apps.gmo_bot.domain.model.trade_state import assert_trade_state_transition
from apps.gmo_bot.domain.model.types import BotConfig, CloseReason, TradeRecord, TradeState
from shared.utils.math import round_to

ORDER_CONFIRM_TIMEOUT_MS = 20_000


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


def close_position(dependencies: ClosePositionDependencies, input_data: ClosePositionInput) -> ClosePositionResult:
    execution = dependencies.execution
    logger = dependencies.logger
    persistence = dependencies.persistence
    config = input_data.config
    trade = input_data.trade

    if trade["state"] != "CONFIRMED":
        return ClosePositionResult(
            status="FAILED",
            trade_id=trade["trade_id"],
            summary=f"FAILED: trade state is {trade['state']}, expected CONFIRMED",
        )

    lots = list(trade.get("position", {}).get("lots") or [])
    if not lots:
        return ClosePositionResult(status="FAILED", trade_id=trade["trade_id"], summary="FAILED: no position lots")

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

    direction = trade.get("direction", "LONG")
    close_side = "SELL" if direction == "LONG" else "BUY"

    try:
        submission = execution.submit_close_order(
            SubmitCloseOrderRequest(
                side=close_side,
                lots=lots,
                slippage_bps=int(config["execution"]["slippage_bps"]),
                reference_price=input_data.close_price,
            )
        )
        trade["execution"]["exit_order_id"] = submission.order_id
        trade["execution"]["exit_submission_state"] = "SUBMITTED"
        if submission.order:
            trade["execution"]["exit_order"] = submission.order
        persistence.update_trade(
            trade["trade_id"],
            strip_none({"execution": trade["execution"], "updated_at": now_iso()}),
        )

        confirmation = execution.confirm_order(submission.order_id, ORDER_CONFIRM_TIMEOUT_MS)
        if not confirmation.confirmed or confirmation.result is None:
            trade["execution"]["exit_submission_state"] = "FAILED"
            trade["execution"]["exit_error"] = confirmation.error or "exit order not confirmed"
            persistence.update_trade(
                trade["trade_id"],
                strip_none({"execution": trade["execution"], "updated_at": now_iso()}),
            )
            return ClosePositionResult(
                status="FAILED",
                trade_id=trade["trade_id"],
                summary=f"FAILED: {summarize_error_for_log(str(trade['execution']['exit_error']))}",
            )

        exit_result = confirmation.result
        trade["execution"]["exit_result"] = exit_result
        trade["execution"]["exit_submission_state"] = "CONFIRMED"
        trade["execution"]["exit_fee_jpy"] = round_to(float(exit_result.get("fee_jpy") or 0.0), 6)
        trade["position"]["status"] = "CLOSED"
        trade["position"]["exit_price"] = round_to(float(exit_result["avg_fill_price"]), 6)
        trade["position"]["exit_trigger_price"] = round_to(input_data.close_price, 6)
        trade["position"]["exit_time_iso"] = now_iso()
        trade["position"]["lots"] = []
        trade["close_reason"] = input_data.close_reason
        move_state("CLOSED")
        logger.info(
            "gmo trade closed",
            {
                "trade_id": trade["trade_id"],
                "order_id": submission.order_id,
                "close_reason": input_data.close_reason,
                "direction": direction,
                "fill": trade["position"]["exit_price"],
                "trigger": input_data.close_price,
            },
        )
        return ClosePositionResult(
            status="CLOSED",
            trade_id=trade["trade_id"],
            summary=(
                f"CLOSED: reason={input_data.close_reason}, order_id={submission.order_id}, direction={direction}, "
                f"fill={round_to(trade['position']['exit_price'], 4)}, trigger={round_to(input_data.close_price, 4)}"
            ),
        )
    except Exception as error:
        message = to_error_message(error)
        logger.error("gmo close_position failed", {"trade_id": trade["trade_id"], "error": message})
        trade["execution"]["exit_submission_state"] = "FAILED"
        trade["execution"]["exit_error"] = message
        persistence.update_trade(
            trade["trade_id"],
            strip_none({"execution": trade["execution"], "updated_at": now_iso()}),
        )
        return ClosePositionResult(
            status="FAILED",
            trade_id=trade["trade_id"],
            summary=f"FAILED: {summarize_error_for_log(message)}",
        )
