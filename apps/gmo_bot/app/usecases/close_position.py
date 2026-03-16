from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.gmo_bot.app.ports.execution_port import ExecutionPort, SubmitCloseOrderRequest
from apps.gmo_bot.app.ports.lock_port import LockPort
from apps.gmo_bot.app.ports.logger_port import LoggerPort
from apps.gmo_bot.app.ports.persistence_port import PersistencePort
from apps.gmo_bot.app.usecases.usecase_utils import now_iso, strip_none, summarize_error_for_log, to_error_message
from apps.gmo_bot.domain.model.trade_state import assert_trade_state_transition
from apps.gmo_bot.domain.model.types import BotConfig, CloseReason, TradeRecord, TradeState
from shared.utils.math import round_to

ORDER_CONFIRM_TIMEOUT_MS = 20_000
POSITION_SIZE_EPSILON = 1e-9


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


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _subtract_filled_lots(
    open_lots: list[dict[str, Any]],
    filled_lots: list[dict[str, Any]],
    *,
    filled_size: float,
) -> list[dict[str, Any]]:
    remaining_lots: list[dict[str, Any]] = []
    if filled_lots:
        filled_by_position_id: dict[int, float] = {}
        for lot in filled_lots:
            position_id = lot.get("position_id")
            lot_size = _to_float(lot.get("size_sol"))
            if not isinstance(position_id, int) or lot_size is None or lot_size <= 0:
                continue
            filled_by_position_id[position_id] = filled_by_position_id.get(position_id, 0.0) + lot_size

        for lot in open_lots:
            position_id = lot.get("position_id")
            lot_size = _to_float(lot.get("size_sol"))
            if not isinstance(position_id, int) or lot_size is None or lot_size <= 0:
                continue
            remaining_size = lot_size - filled_by_position_id.get(position_id, 0.0)
            if remaining_size < -POSITION_SIZE_EPSILON:
                raise RuntimeError("close fill size exceeds tracked lot size")
            if remaining_size > POSITION_SIZE_EPSILON:
                remaining_lots.append({"position_id": position_id, "size_sol": round_to(remaining_size, 9)})
            filled_by_position_id[position_id] = 0.0

        unresolved = [size for size in filled_by_position_id.values() if size > POSITION_SIZE_EPSILON]
        if unresolved:
            raise RuntimeError("close fill references unknown tracked lot")
        return remaining_lots

    remaining_to_subtract = filled_size
    for lot in open_lots:
        position_id = lot.get("position_id")
        lot_size = _to_float(lot.get("size_sol"))
        if not isinstance(position_id, int) or lot_size is None or lot_size <= 0:
            continue
        consumed_size = min(lot_size, remaining_to_subtract)
        remaining_size = lot_size - consumed_size
        remaining_to_subtract -= consumed_size
        if remaining_size > POSITION_SIZE_EPSILON:
            remaining_lots.append({"position_id": position_id, "size_sol": round_to(remaining_size, 9)})

    if remaining_to_subtract > POSITION_SIZE_EPSILON:
        raise RuntimeError("close fill size exceeds tracked lot size")
    return remaining_lots


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
        trade["execution"]["exit_reference_price"] = round_to(input_data.close_price, 6)
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
        existing_exit_fee_jpy = _to_float(trade["execution"].get("exit_fee_jpy")) or 0.0
        exit_fee_jpy = _to_float(exit_result.get("fee_jpy")) or 0.0
        trade["execution"]["exit_fee_jpy"] = round_to(existing_exit_fee_jpy + exit_fee_jpy, 6)

        open_quantity_sol = _to_float(trade["position"].get("quantity_sol"))
        open_quote_amount_jpy = _to_float(trade["position"].get("quote_amount_jpy"))
        filled_size_sol = _to_float(exit_result.get("filled_base_sol"))
        filled_quote_jpy = _to_float(exit_result.get("filled_quote_jpy"))
        if (
            open_quantity_sol is None
            or open_quote_amount_jpy is None
            or filled_size_sol is None
            or filled_quote_jpy is None
            or open_quantity_sol <= 0
            or filled_size_sol <= 0
        ):
            raise RuntimeError("exit execution result is invalid")
        if filled_size_sol > open_quantity_sol + POSITION_SIZE_EPSILON:
            raise RuntimeError("close filled size exceeds open position size")

        closed_entry_quote_jpy = open_quote_amount_jpy * (filled_size_sol / open_quantity_sol)
        realized_increment_jpy = _to_float(exit_result.get("realized_pnl_jpy"))
        if realized_increment_jpy is None:
            realized_increment_jpy = (
                closed_entry_quote_jpy - filled_quote_jpy if direction == "SHORT" else filled_quote_jpy - closed_entry_quote_jpy
            )
        existing_realized_pnl_jpy = _to_float(trade["execution"].get("realized_pnl_jpy")) or 0.0
        trade["execution"]["realized_pnl_jpy"] = round_to(existing_realized_pnl_jpy + realized_increment_jpy, 6)

        remaining_quantity_sol = max(open_quantity_sol - filled_size_sol, 0.0)
        remaining_quote_amount_jpy = max(open_quote_amount_jpy - closed_entry_quote_jpy, 0.0)
        filled_lots = list(exit_result.get("lots") or [])
        remaining_lots = _subtract_filled_lots(lots, filled_lots, filled_size=filled_size_sol)

        if remaining_quantity_sol > POSITION_SIZE_EPSILON:
            if not remaining_lots:
                raise RuntimeError("partial close resolved without remaining position lots")
            next_updated_at = now_iso()
            trade["position"]["quantity_sol"] = round_to(remaining_quantity_sol, 9)
            trade["position"]["quote_amount_jpy"] = round_to(remaining_quote_amount_jpy, 6)
            trade["position"]["lots"] = remaining_lots
            trade["execution"]["exit_error"] = (
                f"partial close detected: expected {round_to(open_quantity_sol, 9)} SOL, "
                f"got {round_to(filled_size_sol, 9)} SOL"
            )
            persistence.update_trade(
                trade["trade_id"],
                strip_none(
                    {
                        "execution": trade["execution"],
                        "position": trade["position"],
                        "updated_at": next_updated_at,
                    }
                ),
            )
            trade["updated_at"] = next_updated_at
            logger.warn(
                "gmo trade partially closed",
                {
                    "trade_id": trade["trade_id"],
                    "order_id": submission.order_id,
                    "direction": direction,
                    "filled_size_sol": round_to(filled_size_sol, 9),
                    "remaining_size_sol": round_to(remaining_quantity_sol, 9),
                },
            )
            return ClosePositionResult(
                status="PARTIALLY_CLOSED",
                trade_id=trade["trade_id"],
                summary=(
                    "PARTIALLY_CLOSED: partial close detected: "
                    f"expected {round_to(open_quantity_sol, 9)} SOL, got {round_to(filled_size_sol, 9)} SOL"
                ),
            )

        trade["execution"].pop("exit_error", None)
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
