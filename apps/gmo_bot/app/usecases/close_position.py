from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from apps.gmo_bot.app.ports.execution_port import ExecutionPort, SubmitCloseOrderRequest
from apps.gmo_bot.app.ports.lock_port import LockPort
from apps.gmo_bot.app.ports.logger_port import LoggerPort
from apps.gmo_bot.app.ports.persistence_port import PersistencePort
from apps.gmo_bot.app.usecases.protective_exit_orders import mark_protective_exit_orders_inactive
from apps.gmo_bot.app.usecases.usecase_utils import now_iso, strip_none, summarize_error_for_log, to_error_message
from apps.gmo_bot.domain.model.trade_state import assert_trade_state_transition
from apps.gmo_bot.domain.model.types import BotConfig, CloseReason, TradeRecord, TradeState
from shared.utils.math import round_to

ORDER_CONFIRM_TIMEOUT_MS = 20_000
PROTECTIVE_EXIT_RECONCILE_TIMEOUT_MS = 250
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


@dataclass
class ProtectiveExitReconciliationResult:
    status: str
    close_result: ClosePositionResult | None = None
    order_id: int | None = None
    order_status: str | None = None


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _to_str(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    if value is None:
        return None
    return str(value)


def _processed_exit_execution_ids(trade: TradeRecord) -> set[str]:
    execution_snapshot = trade.get("execution", {})
    if not isinstance(execution_snapshot, dict):
        return set()
    processed = execution_snapshot.get("processed_exit_execution_ids")
    if not isinstance(processed, list):
        return set()
    return {execution_id for execution_id in (_to_str(item) for item in processed) if execution_id is not None}


def collect_unprocessed_exit_executions(trade: TradeRecord, executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    processed_ids = _processed_exit_execution_ids(trade)
    if not processed_ids:
        return executions
    new_executions: list[dict[str, Any]] = []
    for execution in executions:
        execution_id = _to_str(execution.get("executionId"))
        if execution_id is None or execution_id not in processed_ids:
            new_executions.append(execution)
    return new_executions


def aggregate_execution_records(executions: list[dict[str, Any]]) -> dict[str, Any]:
    total_size = 0.0
    total_quote = 0.0
    total_fee = 0.0
    total_realized_pnl = 0.0
    has_realized_pnl = False
    lots_by_position_id: dict[int, float] = defaultdict(float)
    execution_ids: list[str] = []

    for execution in executions:
        size = _to_float(execution.get("size")) or 0.0
        price = _to_float(execution.get("price")) or 0.0
        fee = _to_float(execution.get("fee")) or 0.0
        loss_gain = _to_float(execution.get("lossGain"))
        total_size += size
        total_quote += price * size
        total_fee += fee
        if loss_gain is not None:
            total_realized_pnl += loss_gain
            has_realized_pnl = True
        position_id = execution.get("positionId")
        if isinstance(position_id, int):
            lots_by_position_id[position_id] += size
        execution_id = _to_str(execution.get("executionId"))
        if execution_id is not None:
            execution_ids.append(execution_id)

    if total_size <= 0:
        raise RuntimeError("GMO executions resolved but filled size is 0")

    result = {
        "status": "CONFIRMED",
        "avg_fill_price": total_quote / total_size,
        "filled_base_sol": total_size,
        "filled_quote_jpy": total_quote,
        "fee_jpy": total_fee,
        "execution_ids": execution_ids,
        "lots": [
            {"position_id": position_id, "size_sol": size}
            for position_id, size in sorted(lots_by_position_id.items())
            if size > 0
        ],
    }
    if has_realized_pnl:
        result["realized_pnl_jpy"] = total_realized_pnl
    return result


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


def _cancel_protective_exit_orders_best_effort(
    *,
    execution: ExecutionPort,
    logger: LoggerPort,
    persistence: PersistencePort,
    trade: TradeRecord,
) -> None:
    execution_snapshot = trade.get("execution", {})
    if not isinstance(execution_snapshot, dict):
        return

    changed = False
    for order_key, status_key in (
        ("take_profit_order_id", "take_profit_order_status"),
        ("stop_loss_order_id", "stop_loss_order_status"),
    ):
        order_id = execution_snapshot.get(order_key)
        status = execution_snapshot.get(status_key)
        if not isinstance(order_id, int) or status not in {"SUBMITTED", "ORDERED", "WAITING"}:
            continue
        try:
            execution.cancel_order(order_id)
            execution_snapshot[status_key] = "CANCELED"
            changed = True
        except Exception as error:
            logger.warn(
                "failed to cancel protective exit order before manual close",
                {"trade_id": trade.get("trade_id"), "order_id": order_id, "error": to_error_message(error)},
            )

    if changed:
        persistence.update_trade(
            trade["trade_id"],
            strip_none({"execution": execution_snapshot, "updated_at": now_iso()}),
        )


def _resolve_protective_exit_order_snapshot(
    trade: TradeRecord,
    close_reason: CloseReason,
) -> tuple[int | None, dict[str, Any] | None, str | None]:
    execution_snapshot = trade.get("execution", {})
    if not isinstance(execution_snapshot, dict):
        return None, None, None

    if close_reason == "TAKE_PROFIT":
        order_id = execution_snapshot.get("take_profit_order_id")
        order_snapshot = execution_snapshot.get("take_profit_order")
        order_status = execution_snapshot.get("take_profit_order_status")
    else:
        order_id = execution_snapshot.get("stop_loss_order_id")
        order_snapshot = execution_snapshot.get("stop_loss_order")
        order_status = execution_snapshot.get("stop_loss_order_status")

    resolved_order_id = order_id if isinstance(order_id, int) else None
    resolved_snapshot = order_snapshot if isinstance(order_snapshot, dict) else None
    resolved_status = order_status if isinstance(order_status, str) else None
    return resolved_order_id, resolved_snapshot, resolved_status


def _resolve_protective_exit_close_price(trade: TradeRecord, close_reason: CloseReason) -> float:
    _, order_snapshot, _ = _resolve_protective_exit_order_snapshot(trade, close_reason)
    if isinstance(order_snapshot, dict):
        order_price = _to_float(order_snapshot.get("price"))
        if order_price is not None:
            return order_price
    position = trade.get("position", {})
    if not isinstance(position, dict):
        raise RuntimeError("trade position snapshot is invalid")
    raw_price = position.get("take_profit_price") if close_reason == "TAKE_PROFIT" else position.get("stop_price")
    if not isinstance(raw_price, (int, float)):
        raise RuntimeError("protective exit trigger price is invalid")
    return float(raw_price)


def reconcile_protective_exit_execution(
    *,
    execution: ExecutionPort,
    logger: LoggerPort,
    persistence: PersistencePort,
    trade: TradeRecord,
    close_reason: CloseReason,
) -> ProtectiveExitReconciliationResult:
    order_id, _, cached_order_status = _resolve_protective_exit_order_snapshot(trade, close_reason)
    if order_id is None:
        return ProtectiveExitReconciliationResult(status="UNAVAILABLE")

    executions = execution.get_executions(order_id)
    new_executions = collect_unprocessed_exit_executions(trade, executions)
    if new_executions:
        execution_result = aggregate_execution_records(new_executions)
        close_result = apply_confirmed_exit_result(
            logger=logger,
            persistence=persistence,
            trade=trade,
            execution_result=execution_result,
            order_id=order_id,
            close_reason=close_reason,
            close_price=_resolve_protective_exit_close_price(trade, close_reason),
        )
        return ProtectiveExitReconciliationResult(
            status=close_result.status,
            close_result=close_result,
            order_id=order_id,
            order_status="EXECUTED",
        )

    order = execution.get_order(order_id)
    order_status = cached_order_status
    if isinstance(order, dict):
        raw_status = order.get("status") or order.get("orderStatus")
        if isinstance(raw_status, str) and raw_status.strip():
            order_status = raw_status.upper()
    if order_status in {"SUBMITTED", "ORDERED", "WAITING", "INACTIVE", "EXECUTED"}:
        return ProtectiveExitReconciliationResult(
            status="PENDING",
            order_id=order_id,
            order_status=order_status,
        )
    return ProtectiveExitReconciliationResult(
        status="UNAVAILABLE",
        order_id=order_id,
        order_status=order_status,
    )


def apply_confirmed_exit_result(
    *,
    logger: LoggerPort,
    persistence: PersistencePort,
    trade: TradeRecord,
    execution_result: dict[str, Any],
    order_id: int,
    close_reason: CloseReason,
    close_price: float,
) -> ClosePositionResult:
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
    trade["execution"]["exit_reference_price"] = round_to(close_price, 6)
    trade["execution"]["exit_order_id"] = order_id
    trade["execution"]["exit_result"] = execution_result
    trade["execution"]["exit_submission_state"] = "CONFIRMED"
    processed_execution_ids = _processed_exit_execution_ids(trade)
    processed_execution_ids.update(
        execution_id
        for execution_id in (_to_str(item) for item in execution_result.get("execution_ids", []))
        if execution_id is not None
    )
    trade["execution"]["processed_exit_execution_ids"] = sorted(processed_execution_ids)
    existing_exit_fee_jpy = _to_float(trade["execution"].get("exit_fee_jpy")) or 0.0
    exit_fee_jpy = _to_float(execution_result.get("fee_jpy")) or 0.0
    trade["execution"]["exit_fee_jpy"] = round_to(existing_exit_fee_jpy + exit_fee_jpy, 6)

    open_quantity_sol = _to_float(trade["position"].get("quantity_sol"))
    open_quote_amount_jpy = _to_float(trade["position"].get("quote_amount_jpy"))
    filled_size_sol = _to_float(execution_result.get("filled_base_sol"))
    filled_quote_jpy = _to_float(execution_result.get("filled_quote_jpy"))
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
    realized_increment_jpy = _to_float(execution_result.get("realized_pnl_jpy"))
    if realized_increment_jpy is None:
        realized_increment_jpy = (
            closed_entry_quote_jpy - filled_quote_jpy if direction == "SHORT" else filled_quote_jpy - closed_entry_quote_jpy
        )
    existing_realized_pnl_jpy = _to_float(trade["execution"].get("realized_pnl_jpy")) or 0.0
    trade["execution"]["realized_pnl_jpy"] = round_to(existing_realized_pnl_jpy + realized_increment_jpy, 6)

    remaining_quantity_sol = max(open_quantity_sol - filled_size_sol, 0.0)
    remaining_quote_amount_jpy = max(open_quote_amount_jpy - closed_entry_quote_jpy, 0.0)
    filled_lots = list(execution_result.get("lots") or [])
    remaining_lots = _subtract_filled_lots(lots, filled_lots, filled_size=filled_size_sol)

    if remaining_quantity_sol > POSITION_SIZE_EPSILON:
        if not remaining_lots:
            raise RuntimeError("partial close resolved without remaining position lots")
        trade["position"]["quantity_sol"] = round_to(remaining_quantity_sol, 9)
        trade["position"]["quote_amount_jpy"] = round_to(remaining_quote_amount_jpy, 6)
        trade["position"]["lots"] = remaining_lots
        trade["execution"]["exit_error"] = (
            f"partial close detected: expected {round_to(open_quantity_sol, 9)} SOL, "
            f"got {round_to(filled_size_sol, 9)} SOL"
        )
        is_take_profit_order = trade["execution"].get("take_profit_order_id") == order_id
        is_stop_loss_order = trade["execution"].get("stop_loss_order_id") == order_id
        if not is_take_profit_order and not is_stop_loss_order:
            mark_protective_exit_orders_inactive(
                trade,
                take_profit_status="INACTIVE",
                stop_loss_status="INACTIVE",
                error_message="partial close requires protective exit re-arm",
            )
        persistence.update_trade(
            trade["trade_id"],
            strip_none(
                {
                    "execution": trade["execution"],
                    "position": trade["position"],
                    "updated_at": now_iso(),
                }
            ),
        )
        logger.warn(
            "gmo trade partially closed",
            {
                "trade_id": trade["trade_id"],
                "order_id": order_id,
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
    if close_reason == "TAKE_PROFIT" and trade["execution"].get("take_profit_order_id") == order_id:
        mark_protective_exit_orders_inactive(trade, take_profit_status="EXECUTED", stop_loss_status="INACTIVE")
    elif close_reason == "STOP_LOSS" and trade["execution"].get("stop_loss_order_id") == order_id:
        mark_protective_exit_orders_inactive(trade, take_profit_status="INACTIVE", stop_loss_status="EXECUTED")
    else:
        mark_protective_exit_orders_inactive(trade)
    trade["position"]["status"] = "CLOSED"
    trade["position"]["exit_price"] = round_to(float(execution_result["avg_fill_price"]), 6)
    trade["position"]["exit_trigger_price"] = round_to(close_price, 6)
    trade["position"]["exit_time_iso"] = now_iso()
    trade["position"]["lots"] = []
    trade["close_reason"] = close_reason
    move_state("CLOSED")
    logger.info(
        "gmo trade closed",
        {
            "trade_id": trade["trade_id"],
            "order_id": order_id,
            "close_reason": close_reason,
            "direction": direction,
            "fill": trade["position"]["exit_price"],
            "trigger": close_price,
        },
    )
    return ClosePositionResult(
        status="CLOSED",
        trade_id=trade["trade_id"],
        summary=(
            f"CLOSED: reason={close_reason}, order_id={order_id}, direction={direction}, "
            f"fill={round_to(trade['position']['exit_price'], 4)}, trigger={round_to(close_price, 4)}"
        ),
    )


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

    direction = trade.get("direction", "LONG")
    close_side = "SELL" if direction == "LONG" else "BUY"

    try:
        _cancel_protective_exit_orders_best_effort(
            execution=execution,
            logger=logger,
            persistence=persistence,
            trade=trade,
        )
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

        return apply_confirmed_exit_result(
            logger=logger,
            persistence=persistence,
            trade=trade,
            execution_result=confirmation.result,
            order_id=submission.order_id,
            close_reason=input_data.close_reason,
            close_price=input_data.close_price,
        )
    except Exception as error:
        message = to_error_message(error)
        if input_data.close_reason in ("TAKE_PROFIT", "STOP_LOSS"):
            try:
                reconciled = reconcile_protective_exit_execution(
                    execution=execution,
                    logger=logger,
                    persistence=persistence,
                    trade=trade,
                    close_reason=input_data.close_reason,
                )
            except Exception as reconcile_error:
                logger.warn(
                    "gmo protective exit reconciliation failed after manual close failure",
                    {
                        "trade_id": trade["trade_id"],
                        "close_reason": input_data.close_reason,
                        "error": message,
                        "reconcile_error": to_error_message(reconcile_error),
                    },
                )
            else:
                if reconciled.close_result is not None:
                    logger.warn(
                        "gmo close_position reconciled protective exit after manual close failure",
                        {
                            "trade_id": trade["trade_id"],
                            "close_reason": input_data.close_reason,
                            "error": message,
                            "protective_order_id": reconciled.order_id,
                            "protective_order_status": reconciled.order_status,
                        },
                    )
                    return reconciled.close_result
                if reconciled.status == "PENDING":
                    return ClosePositionResult(
                        status="PENDING",
                        trade_id=trade["trade_id"],
                        summary="PENDING: protective exit execution pending settlement details",
                    )
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
