from __future__ import annotations

from dataclasses import dataclass

from apps.gmo_bot.app.ports.execution_port import (
    ExecutionPort,
    SubmitProtectiveExitOrdersRequest,
)
from apps.gmo_bot.app.ports.logger_port import LoggerPort
from apps.gmo_bot.app.ports.persistence_port import PersistencePort
from apps.gmo_bot.app.usecases.usecase_utils import now_iso, strip_none, to_error_message
from apps.gmo_bot.domain.model.types import BotConfig, TradeRecord
from shared.utils.math import round_to

ACTIVE_EXIT_ORDER_STATUSES = {"SUBMITTED", "ORDERED", "WAITING"}
INACTIVE_EXIT_ORDER_STATUSES = {"CANCELED", "EXECUTED", "EXPIRED", "FAILED", "INACTIVE"}
POSITION_SIZE_EPSILON = 1e-9


def _to_float(value: object) -> float | None:
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


def _tracked_lot_sizes(trade: TradeRecord) -> dict[int, float]:
    position = trade.get("position", {})
    if not isinstance(position, dict):
        return {}
    raw_lots = position.get("lots")
    if not isinstance(raw_lots, list):
        return {}
    tracked_lots: dict[int, float] = {}
    for item in raw_lots:
        if not isinstance(item, dict):
            continue
        position_id = item.get("position_id")
        size_sol = _to_float(item.get("size_sol"))
        if not isinstance(position_id, int) or size_sol is None or size_sol <= 0:
            continue
        tracked_lots[position_id] = size_sol
    return tracked_lots


def _has_full_active_stop_loss_coverage(trade: TradeRecord) -> bool:
    tracked_lots = _tracked_lot_sizes(trade)
    if not tracked_lots:
        return False
    active_orders = [item for item in get_stop_loss_order_snapshots(trade) if item.get("status") in ACTIVE_EXIT_ORDER_STATUSES]
    if len(active_orders) != len(tracked_lots):
        return False
    covered_lots: dict[int, float] = {}
    for order in active_orders:
        position_id = order.get("position_id")
        size_sol = _to_float(order.get("size_sol"))
        if not isinstance(position_id, int) or size_sol is None or size_sol <= 0:
            return False
        covered_lots[position_id] = size_sol
    if covered_lots.keys() != tracked_lots.keys():
        return False
    for position_id, tracked_size in tracked_lots.items():
        if abs(covered_lots[position_id] - tracked_size) > POSITION_SIZE_EPSILON:
            return False
    return True


def get_stop_loss_order_snapshots(trade: TradeRecord) -> list[dict]:
    execution = trade.get("execution", {})
    if not isinstance(execution, dict):
        return []
    raw_orders = execution.get("stop_loss_orders")
    orders: list[dict] = []
    if isinstance(raw_orders, list):
        for item in raw_orders:
            if isinstance(item, dict) and isinstance(item.get("order_id"), int):
                orders.append(item)
        if orders:
            return orders
    order_id = execution.get("stop_loss_order_id")
    order_status = execution.get("stop_loss_order_status")
    order_snapshot = execution.get("stop_loss_order")
    if not isinstance(order_id, int):
        return []
    order_record: dict = {"order_id": order_id}
    if isinstance(order_snapshot, dict):
        order_record.update(order_snapshot)
    if isinstance(order_status, str):
        order_record["status"] = order_status
    return [order_record]


def sync_stop_loss_order_snapshot_fields(trade: TradeRecord) -> None:
    execution = trade.setdefault("execution", {})
    if not isinstance(execution, dict):
        return
    orders = get_stop_loss_order_snapshots(trade)
    if not orders:
        execution.pop("stop_loss_orders", None)
        return
    active_order = next((item for item in orders if item.get("status") in ACTIVE_EXIT_ORDER_STATUSES), None)
    primary_order = active_order or orders[0]
    execution["stop_loss_orders"] = orders
    execution["stop_loss_order_id"] = primary_order["order_id"]
    execution["stop_loss_order"] = {
        key: value
        for key, value in primary_order.items()
        if key in {"order_id", "price", "position_id", "size_sol"}
    }
    status = next((str(item.get("status")) for item in orders if item.get("status") in ACTIVE_EXIT_ORDER_STATUSES), None)
    if status is None:
        status = str(primary_order.get("status") or "INACTIVE")
    execution["stop_loss_order_status"] = status


def set_stop_loss_order_status(trade: TradeRecord, *, order_id: int, status: str) -> bool:
    execution = trade.setdefault("execution", {})
    if not isinstance(execution, dict):
        return False
    updated = False
    raw_orders = execution.get("stop_loss_orders")
    if isinstance(raw_orders, list):
        for item in raw_orders:
            if isinstance(item, dict) and item.get("order_id") == order_id:
                item["status"] = status
                updated = True
    if execution.get("stop_loss_order_id") == order_id:
        execution["stop_loss_order_status"] = status
        updated = True
    if updated:
        sync_stop_loss_order_snapshot_fields(trade)
    return updated


def has_active_protective_exit_orders(trade: TradeRecord) -> bool:
    execution = trade.get("execution", {})
    if not isinstance(execution, dict):
        return False
    take_profit_order_id = execution.get("take_profit_order_id")
    stop_loss_order_id = execution.get("stop_loss_order_id")
    take_profit_status = execution.get("take_profit_order_status")
    stop_loss_status = execution.get("stop_loss_order_status")
    return (
        isinstance(take_profit_order_id, int)
        and isinstance(stop_loss_order_id, int)
        and take_profit_status in ACTIVE_EXIT_ORDER_STATUSES
        and stop_loss_status in ACTIVE_EXIT_ORDER_STATUSES
    )


def has_active_stop_loss_order(trade: TradeRecord) -> bool:
    return _has_full_active_stop_loss_coverage(trade)


def mark_protective_exit_orders_inactive(
    trade: TradeRecord,
    *,
    take_profit_status: str = "INACTIVE",
    stop_loss_status: str = "INACTIVE",
    error_message: str | None = None,
) -> None:
    execution = trade.setdefault("execution", {})
    if not isinstance(execution, dict):
        return
    if "take_profit_order_id" in execution or "take_profit_order_status" in execution:
        execution["take_profit_order_status"] = take_profit_status
    if "stop_loss_order_id" in execution or "stop_loss_order_status" in execution:
        execution["stop_loss_order_status"] = stop_loss_status
    raw_orders = execution.get("stop_loss_orders")
    if isinstance(raw_orders, list):
        for item in raw_orders:
            if isinstance(item, dict):
                item["status"] = stop_loss_status
    if error_message is not None:
        execution["protective_exit_error"] = error_message
    sync_stop_loss_order_snapshot_fields(trade)


@dataclass
class ArmProtectiveExitOrdersInput:
    config: BotConfig
    trade: TradeRecord


@dataclass
class ArmProtectiveExitOrdersResult:
    status: str
    summary: str


@dataclass
class ArmProtectiveExitOrdersDependencies:
    execution: ExecutionPort
    logger: LoggerPort
    persistence: PersistencePort


def arm_protective_exit_orders(
    dependencies: ArmProtectiveExitOrdersDependencies,
    input_data: ArmProtectiveExitOrdersInput,
) -> ArmProtectiveExitOrdersResult:
    trade = input_data.trade
    execution_snapshot = trade.setdefault("execution", {})
    position = trade.get("position", {})
    if not isinstance(execution_snapshot, dict) or not isinstance(position, dict):
        return ArmProtectiveExitOrdersResult(status="FAILED", summary="FAILED: trade snapshot is invalid")
    if trade.get("state") != "CONFIRMED" or position.get("status") != "OPEN":
        return ArmProtectiveExitOrdersResult(status="FAILED", summary="FAILED: trade is not open")
    if has_active_protective_exit_orders(trade):
        return ArmProtectiveExitOrdersResult(status="SKIPPED", summary="SKIPPED: protective exit orders already armed")
    if has_active_stop_loss_order(trade):
        return ArmProtectiveExitOrdersResult(
            status="SKIPPED",
            summary="SKIPPED: protective stop order already armed; take profit remains client-managed",
        )
    if getattr(dependencies.execution, "protective_exit_enabled", True) is False:
        return ArmProtectiveExitOrdersResult(status="FAILED", summary="FAILED: protective exits are disabled")

    lots = list(position.get("lots") or [])
    if not lots:
        return ArmProtectiveExitOrdersResult(status="FAILED", summary="FAILED: no position lots to protect")

    trade_direction = str(trade.get("direction") or input_data.config["direction"])
    close_side = "SELL" if trade_direction == "LONG" else "BUY"
    take_profit_price = float(position["take_profit_price"])
    stop_price = float(position["stop_price"])

    try:
        submission = dependencies.execution.submit_protective_exit_orders(
            SubmitProtectiveExitOrdersRequest(
                side=close_side,
                lots=lots,
                take_profit_price=take_profit_price,
                stop_price=stop_price,
            )
        )
    except Exception as error:
        message = to_error_message(error)
        execution_snapshot["protective_exit_error"] = message
        mark_protective_exit_orders_inactive(
            trade,
            take_profit_status="FAILED",
            stop_loss_status="FAILED",
            error_message=message,
        )
        dependencies.persistence.update_trade(
            trade["trade_id"],
            strip_none({"execution": execution_snapshot, "updated_at": now_iso()}),
        )
        dependencies.logger.error(
            "failed to arm protective exit orders",
            {"trade_id": trade.get("trade_id"), "error": message},
        )
        return ArmProtectiveExitOrdersResult(
            status="FAILED",
            summary=f"FAILED: protective exits not armed: {message}",
        )

    stop_loss_orders = [
        item
        for item in ((submission.stop_loss_orders or []) or ([submission.stop_loss_order] if submission.stop_loss_order else []))
        if item is not None
    ]
    if not stop_loss_orders:
        return ArmProtectiveExitOrdersResult(status="FAILED", summary="FAILED: protective stop order was not created")

    execution_snapshot.pop("take_profit_order_id", None)
    execution_snapshot.pop("take_profit_order", None)
    execution_snapshot["take_profit_order_status"] = "CLIENT_MANAGED"
    execution_snapshot["stop_loss_orders"] = []
    execution_snapshot.pop("protective_exit_error", None)
    stop_order_price = None
    for order_submission in stop_loss_orders:
        order_record = {"order_id": order_submission.order_id, "status": "WAITING"}
        if order_submission.order:
            order_record.update(order_submission.order)
        execution_snapshot["stop_loss_orders"].append(order_record)
        if stop_order_price is None:
            raw_stop_order_price = order_record.get("price")
            if isinstance(raw_stop_order_price, (int, float)):
                stop_order_price = float(raw_stop_order_price)
    sync_stop_loss_order_snapshot_fields(trade)
    if not has_active_stop_loss_order(trade):
        for item in stop_loss_orders:
            try:
                dependencies.execution.cancel_order(item.order_id)
            except Exception as rollback_error:
                dependencies.logger.warn(
                    "failed to cancel partially armed protective stop order",
                    {
                        "trade_id": trade.get("trade_id"),
                        "order_id": item.order_id,
                        "error": to_error_message(rollback_error),
                    },
                )
        mark_protective_exit_orders_inactive(
            trade,
            take_profit_status="FAILED",
            stop_loss_status="FAILED",
            error_message="protective stop coverage does not match tracked lots",
        )
        dependencies.persistence.update_trade(
            trade["trade_id"],
            strip_none({"execution": execution_snapshot, "updated_at": now_iso()}),
        )
        return ArmProtectiveExitOrdersResult(
            status="FAILED",
            summary="FAILED: protective stop coverage does not match tracked lots",
        )

    dependencies.persistence.update_trade(
        trade["trade_id"],
        strip_none({"execution": execution_snapshot, "updated_at": now_iso()}),
    )
    dependencies.logger.info(
        "protective exit orders armed",
        {
            "trade_id": trade.get("trade_id"),
            "take_profit_order_id": None,
            "stop_loss_order_ids": [item.order_id for item in stop_loss_orders],
            "take_profit_price": round_to(take_profit_price, 6),
            "stop_price": round_to(stop_price, 6),
            "stop_order_price": round_to(stop_order_price, 6) if stop_order_price is not None else None,
        },
    )
    return ArmProtectiveExitOrdersResult(
        status="ARMED_STOP_ONLY",
        summary=(
            "ARMED_STOP_ONLY: protective stop order placed "
            f"(sl_order_ids={[item.order_id for item in stop_loss_orders]}); take profit remains client-managed"
        ),
    )
