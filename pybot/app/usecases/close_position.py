from __future__ import annotations

from dataclasses import dataclass
import time

from pybot.app.ports.execution_port import ExecutionPort, SubmitSwapRequest, SwapSide
from pybot.app.ports.lock_port import LockPort
from pybot.app.ports.logger_port import LoggerPort
from pybot.app.ports.persistence_port import PersistencePort
from pybot.app.usecases.usecase_utils import (
    is_market_condition_error_message,
    is_slippage_error_message,
    now_iso,
    resolve_tx_fee_lamports,
    summarize_error_for_log,
    should_retry_error,
    strip_none,
    to_error_message,
)
from pybot.domain.model.trade_state import assert_trade_state_transition
from pybot.domain.model.types import BotConfig, CloseReason, TradeRecord, TradeState
from pybot.domain.utils.math import round_to, to_atomic_amount_down

SOL_ATOMIC_MULTIPLIER = 1_000_000_000
USDC_ATOMIC_MULTIPLIER = 1_000_000
TX_CONFIRM_TIMEOUT_MS = 75_000
TX_INFLIGHT_TTL_SECONDS = 180
MIN_ATOMIC_AMOUNT = 1
DEFAULT_EXIT_RETRY_ATTEMPTS = 2
STOP_LOSS_EXIT_RETRY_ATTEMPTS = 5
DEFAULT_EXIT_RETRY_DELAY_SECONDS = 0.8
STOP_LOSS_EXIT_RETRY_DELAY_SECONDS = 0.15
TAKE_PROFIT_EXIT_MAX_SLIPPAGE_BPS = 30
STOP_LOSS_EXIT_MAX_SLIPPAGE_BPS = 120


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


def _next_slippage_bps(current_slippage_bps: int, max_slippage_bps: int) -> int:
    if current_slippage_bps >= max_slippage_bps:
        return current_slippage_bps
    widened = max(current_slippage_bps + 1, current_slippage_bps * 2)
    return min(widened, max_slippage_bps)


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

    def failed_summary(message: str) -> str:
        return f"FAILED: {summarize_error_for_log(message)}"

    def can_skip_take_profit_error(message: str) -> bool:
        return is_slippage_error_message(message) or is_market_condition_error_message(message)

    def take_profit_skip_summary(message: str) -> str:
        summarized = summarize_error_for_log(message)
        if is_slippage_error_message(message):
            return f"SKIPPED: exit slippage exceeded ({summarized})"
        return f"SKIPPED: exit route/liquidity unavailable ({summarized})"

    def snapshot_balances() -> tuple[float, float] | None:
        try:
            quote = float(execution.get_available_quote_usdc(config["pair"]))
            base = float(execution.get_available_base_sol(config["pair"]))
            return quote, base
        except Exception as error:
            logger.warn(
                "close_position balance snapshot failed",
                {"trade_id": trade["trade_id"], "error": to_error_message(error)},
            )
            return None

    direction = trade.get("direction", "LONG")
    side: SwapSide = "SELL_SOL_FOR_USDC"
    amount_atomic = to_atomic_amount_down(float(trade["position"]["quantity_sol"]), SOL_ATOMIC_MULTIPLIER)

    if direction == "SHORT":
        side = "BUY_SOL_WITH_USDC"
        quote_amount_usdc = trade.get("position", {}).get("quote_amount_usdc")
        if not isinstance(quote_amount_usdc, (int, float)) or quote_amount_usdc <= 0:
            quantity_sol = float(trade.get("position", {}).get("quantity_sol") or 0)
            quote_amount_usdc = quantity_sol * close_price
        amount_atomic = to_atomic_amount_down(float(quote_amount_usdc), USDC_ATOMIC_MULTIPLIER)

    before_balances = snapshot_balances()
    if before_balances is not None:
        before_quote, before_base = before_balances
        if side == "BUY_SOL_WITH_USDC":
            available_atomic = to_atomic_amount_down(max(before_quote, 0.0), USDC_ATOMIC_MULTIPLIER)
            if 0 < available_atomic < amount_atomic:
                logger.warn(
                    "close_position short exit amount reduced to available quote balance",
                    {
                        "trade_id": trade["trade_id"],
                        "requested_amount_atomic": amount_atomic,
                        "available_amount_atomic": available_atomic,
                    },
                )
                amount_atomic = available_atomic
        else:
            available_atomic = to_atomic_amount_down(max(before_base, 0.0), SOL_ATOMIC_MULTIPLIER)
            if 0 < available_atomic < amount_atomic:
                logger.warn(
                    "close_position long exit amount reduced to available base balance",
                    {
                        "trade_id": trade["trade_id"],
                        "requested_amount_atomic": amount_atomic,
                        "available_amount_atomic": available_atomic,
                    },
                )
                amount_atomic = available_atomic

    if amount_atomic < MIN_ATOMIC_AMOUNT:
        trade["execution"]["exit_submission_state"] = "FAILED"
        trade["execution"]["exit_error"] = "position close amount is 0"
        move_state("FAILED")
        return ClosePositionResult(
            status="FAILED", trade_id=trade["trade_id"], summary="FAILED: position close amount is 0"
        )

    max_exit_attempts = (
        STOP_LOSS_EXIT_RETRY_ATTEMPTS
        if close_reason == "STOP_LOSS"
        else DEFAULT_EXIT_RETRY_ATTEMPTS
    )
    retry_delay_seconds = (
        STOP_LOSS_EXIT_RETRY_DELAY_SECONDS
        if close_reason == "STOP_LOSS"
        else DEFAULT_EXIT_RETRY_DELAY_SECONDS
    )
    current_slippage_bps = int(config["execution"]["slippage_bps"])
    max_exit_slippage_bps = (
        STOP_LOSS_EXIT_MAX_SLIPPAGE_BPS
        if close_reason == "STOP_LOSS"
        else TAKE_PROFIT_EXIT_MAX_SLIPPAGE_BPS
    )
    if max_exit_slippage_bps < current_slippage_bps:
        max_exit_slippage_bps = current_slippage_bps
    last_error_message = "unknown error"
    inflight_submission = None
    inflight_exit_result = None

    for attempt in range(1, max_exit_attempts + 1):
        submission = inflight_submission
        exit_result = inflight_exit_result
        try:
            trade["execution"]["exit_slippage_bps"] = current_slippage_bps
            if submission is None:
                submission = execution.submit_swap(
                    SubmitSwapRequest(
                        side=side,
                        amount_atomic=amount_atomic,
                        slippage_bps=current_slippage_bps,
                        only_direct_routes=config["execution"]["only_direct_routes"],
                    )
                )

                trade["execution"]["exit_tx_signature"] = submission.tx_signature
                if submission.order:
                    trade["execution"]["exit_order"] = submission.order
                exit_result = submission.result
                if exit_result is None:
                    if side == "SELL_SOL_FOR_USDC":
                        estimated_input_sol = submission.in_amount_atomic / SOL_ATOMIC_MULTIPLIER
                        estimated_output_usdc = submission.out_amount_atomic / USDC_ATOMIC_MULTIPLIER
                        estimated_avg_fill_price = (
                            estimated_output_usdc / estimated_input_sol if estimated_input_sol > 0 else close_price
                        )
                    else:
                        estimated_input_usdc = submission.in_amount_atomic / USDC_ATOMIC_MULTIPLIER
                        estimated_output_sol = submission.out_amount_atomic / SOL_ATOMIC_MULTIPLIER
                        estimated_avg_fill_price = (
                            estimated_input_usdc / estimated_output_sol if estimated_output_sol > 0 else close_price
                        )
                        estimated_output_usdc = estimated_input_usdc
                        estimated_input_sol = estimated_output_sol
                    exit_result = {
                        "status": "ESTIMATED",
                        "avg_fill_price": estimated_avg_fill_price,
                        "spent_quote_usdc": estimated_output_usdc,
                        "filled_base_sol": estimated_input_sol,
                    }
                trade["execution"]["exit_result"] = exit_result
                trade["execution"]["exit_submission_state"] = "SUBMITTED"
                if "exit_error" in trade["execution"]:
                    del trade["execution"]["exit_error"]
                trade["updated_at"] = now_iso()
                persistence.update_trade(
                    trade["trade_id"],
                    strip_none({"execution": trade["execution"], "updated_at": trade["updated_at"]}),
                )

                lock.set_inflight_tx(submission.tx_signature, TX_INFLIGHT_TTL_SECONDS)
                inflight_submission = submission
                inflight_exit_result = exit_result

            confirmation = execution.confirm_swap(submission.tx_signature, TX_CONFIRM_TIMEOUT_MS)

            if not confirmation.confirmed:
                last_error_message = confirmation.error or "unknown confirmation error"
                trade["execution"]["exit_submission_state"] = "FAILED"
                trade["execution"]["exit_error"] = (
                    f"attempt {attempt}/{max_exit_attempts}: {last_error_message}"
                )
                persist_execution_only()
                if is_slippage_error_message(last_error_message):
                    next_slippage_bps = _next_slippage_bps(current_slippage_bps, max_exit_slippage_bps)
                    if attempt < max_exit_attempts:
                        if next_slippage_bps > current_slippage_bps:
                            logger.warn(
                                "close_position widening slippage for retry",
                                {
                                    "trade_id": trade["trade_id"],
                                    "reason": close_reason,
                                    "attempt": attempt,
                                    "max_attempts": max_exit_attempts,
                                    "error": summarize_error_for_log(last_error_message),
                                    "slippage_bps_from": current_slippage_bps,
                                    "slippage_bps_to": next_slippage_bps,
                                },
                            )
                            current_slippage_bps = next_slippage_bps
                        lock.clear_inflight_tx(submission.tx_signature)
                        inflight_submission = None
                        inflight_exit_result = None
                        if retry_delay_seconds > 0:
                            time.sleep(retry_delay_seconds)
                        continue
                    if close_reason == "TAKE_PROFIT":
                        lock.clear_inflight_tx(submission.tx_signature)
                        inflight_submission = None
                        inflight_exit_result = None
                        return ClosePositionResult(
                            status="SKIPPED",
                            trade_id=trade["trade_id"],
                            summary=take_profit_skip_summary(last_error_message),
                        )
                if can_skip_take_profit_error(last_error_message) and close_reason == "TAKE_PROFIT":
                    lock.clear_inflight_tx(submission.tx_signature)
                    inflight_submission = None
                    inflight_exit_result = None
                    return ClosePositionResult(
                        status="SKIPPED",
                        trade_id=trade["trade_id"],
                        summary=take_profit_skip_summary(last_error_message),
                    )
                if should_retry_error(
                    attempt=attempt,
                    max_attempts=max_exit_attempts,
                    error_message=last_error_message,
                ):
                    logger.warn(
                        "close_position retrying confirmation for inflight exit tx",
                        {
                            "trade_id": trade["trade_id"],
                            "reason": close_reason,
                            "attempt": attempt,
                            "max_attempts": max_exit_attempts,
                            "error": last_error_message,
                            "tx_signature": submission.tx_signature,
                        },
                    )
                    if retry_delay_seconds > 0:
                        time.sleep(retry_delay_seconds)
                    continue

                lock.clear_inflight_tx(submission.tx_signature)
                inflight_submission = None
                inflight_exit_result = None
                return ClosePositionResult(
                    status="FAILED",
                    trade_id=trade["trade_id"],
                    summary=failed_summary(f"exit tx not confirmed ({last_error_message})"),
                )

            lock.clear_inflight_tx(submission.tx_signature)
            inflight_submission = None
            inflight_exit_result = None
            if side == "SELL_SOL_FOR_USDC":
                input_sol = submission.in_amount_atomic / SOL_ATOMIC_MULTIPLIER
                output_usdc = submission.out_amount_atomic / USDC_ATOMIC_MULTIPLIER
                fallback_exit_price = output_usdc / input_sol if input_sol > 0 else close_price
            else:
                input_usdc = submission.in_amount_atomic / USDC_ATOMIC_MULTIPLIER
                output_sol = submission.out_amount_atomic / SOL_ATOMIC_MULTIPLIER
                fallback_exit_price = input_usdc / output_sol if output_sol > 0 else close_price

            resolved_exit_price = (
                float(exit_result["avg_fill_price"])
                if "avg_fill_price" in exit_result
                else fallback_exit_price
            )

            exit_fee_lamports = resolve_tx_fee_lamports(
                execution,
                submission.tx_signature,
                logger=logger,
                log_context={"trade_id": trade["trade_id"], "phase": "EXIT"},
            )
            if exit_fee_lamports is not None:
                trade["execution"]["exit_fee_lamports"] = exit_fee_lamports

            after_balances = snapshot_balances()
            if before_balances is not None and after_balances is not None:
                before_quote, before_base = before_balances
                after_quote, after_base = after_balances
                if side == "SELL_SOL_FOR_USDC":
                    actual_quote_usdc = max(round_to(after_quote - before_quote, 6), 0.0)
                    actual_base_sol = max(
                        round_to(submission.in_amount_atomic / SOL_ATOMIC_MULTIPLIER, 9), 0.0
                    )
                else:
                    actual_quote_usdc = max(round_to(before_quote - after_quote, 6), 0.0)
                    actual_base_sol = max(round_to(after_base - before_base, 9), 0.0)

                if actual_quote_usdc > 0 and actual_base_sol > 0:
                    trade["execution"]["exit_result"] = {
                        "status": "CONFIRMED",
                        "spent_quote_usdc": actual_quote_usdc,
                        "filled_base_sol": actual_base_sol,
                        "avg_fill_price": actual_quote_usdc / actual_base_sol,
                    }
                    resolved_exit_price = actual_quote_usdc / actual_base_sol

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

            attempt_note = f" after {attempt} attempts" if attempt > 1 else ""
            return ClosePositionResult(
                status="CLOSED",
                trade_id=trade["trade_id"],
                summary=(
                    f"CLOSED: reason={close_reason}, tx={submission.tx_signature}, direction={direction}, "
                    f"fill={round_to(trade['position']['exit_price'], 4)}, trigger={round_to(close_price, 4)}"
                    f"{attempt_note}"
                ),
            )
        except Exception as error:
            last_error_message = to_error_message(error)
            trade["execution"]["exit_submission_state"] = "FAILED"
            trade["execution"]["exit_error"] = f"attempt {attempt}/{max_exit_attempts}: {last_error_message}"
            try:
                persist_execution_only()
            except Exception as persist_error:
                logger.error(
                    "close_position execution persistence failed",
                    {"trade_id": trade["trade_id"], "error": to_error_message(persist_error)},
                )
            if is_slippage_error_message(last_error_message):
                next_slippage_bps = _next_slippage_bps(current_slippage_bps, max_exit_slippage_bps)
                if attempt < max_exit_attempts:
                    if next_slippage_bps > current_slippage_bps:
                        logger.warn(
                            "close_position widening slippage for retry",
                            {
                                "trade_id": trade["trade_id"],
                                "reason": close_reason,
                                "attempt": attempt,
                                "max_attempts": max_exit_attempts,
                                "error": summarize_error_for_log(last_error_message),
                                "slippage_bps_from": current_slippage_bps,
                                "slippage_bps_to": next_slippage_bps,
                            },
                        )
                        current_slippage_bps = next_slippage_bps
                    if submission is not None and lock.has_inflight_tx(submission.tx_signature):
                        lock.clear_inflight_tx(submission.tx_signature)
                        inflight_submission = None
                        inflight_exit_result = None
                    if retry_delay_seconds > 0:
                        time.sleep(retry_delay_seconds)
                    continue
                if close_reason == "TAKE_PROFIT":
                    if submission is not None and lock.has_inflight_tx(submission.tx_signature):
                        lock.clear_inflight_tx(submission.tx_signature)
                        inflight_submission = None
                        inflight_exit_result = None
                    return ClosePositionResult(
                        status="SKIPPED",
                        trade_id=trade["trade_id"],
                        summary=take_profit_skip_summary(last_error_message),
                    )
            if can_skip_take_profit_error(last_error_message) and close_reason == "TAKE_PROFIT":
                if submission is not None and lock.has_inflight_tx(submission.tx_signature):
                    lock.clear_inflight_tx(submission.tx_signature)
                    inflight_submission = None
                    inflight_exit_result = None
                return ClosePositionResult(
                    status="SKIPPED",
                    trade_id=trade["trade_id"],
                    summary=take_profit_skip_summary(last_error_message),
                )
            if should_retry_error(
                attempt=attempt,
                max_attempts=max_exit_attempts,
                error_message=last_error_message,
            ):
                if submission is not None and lock.has_inflight_tx(submission.tx_signature):
                    logger.warn(
                        "close_position retrying confirmation after exit exception",
                        {
                            "trade_id": trade["trade_id"],
                            "reason": close_reason,
                            "attempt": attempt,
                            "max_attempts": max_exit_attempts,
                            "error": last_error_message,
                            "tx_signature": submission.tx_signature,
                        },
                    )
                else:
                    logger.warn(
                        "close_position retrying after submit exception",
                        {
                            "trade_id": trade["trade_id"],
                            "reason": close_reason,
                            "attempt": attempt,
                            "max_attempts": max_exit_attempts,
                            "error": last_error_message,
                        },
                    )
                if retry_delay_seconds > 0:
                    time.sleep(retry_delay_seconds)
                continue

            if submission is not None and lock.has_inflight_tx(submission.tx_signature):
                lock.clear_inflight_tx(submission.tx_signature)
                inflight_submission = None
                inflight_exit_result = None
            logger.error("close_position failed", {"trade_id": trade["trade_id"], "error": last_error_message})
            return ClosePositionResult(
                status="FAILED",
                trade_id=trade["trade_id"],
                summary=failed_summary(last_error_message),
            )

    return ClosePositionResult(
        status="FAILED",
        trade_id=trade["trade_id"],
        summary=failed_summary(last_error_message),
    )
