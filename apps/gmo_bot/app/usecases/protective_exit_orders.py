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
    if "take_profit_order_id" in execution:
        execution["take_profit_order_status"] = take_profit_status
    if "stop_loss_order_id" in execution:
        execution["stop_loss_order_status"] = stop_loss_status
    if error_message is not None:
        execution["protective_exit_error"] = error_message


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

    execution_snapshot["take_profit_order_id"] = submission.take_profit_order.order_id
    execution_snapshot["stop_loss_order_id"] = submission.stop_loss_order.order_id
    execution_snapshot["take_profit_order_status"] = "ORDERED"
    execution_snapshot["stop_loss_order_status"] = "WAITING"
    execution_snapshot.pop("protective_exit_error", None)
    if submission.take_profit_order.order:
        execution_snapshot["take_profit_order"] = submission.take_profit_order.order
    if submission.stop_loss_order.order:
        execution_snapshot["stop_loss_order"] = submission.stop_loss_order.order

    dependencies.persistence.update_trade(
        trade["trade_id"],
        strip_none({"execution": execution_snapshot, "updated_at": now_iso()}),
    )
    dependencies.logger.info(
        "protective exit orders armed",
        {
            "trade_id": trade.get("trade_id"),
            "take_profit_order_id": submission.take_profit_order.order_id,
            "stop_loss_order_id": submission.stop_loss_order.order_id,
            "take_profit_price": round_to(take_profit_price, 6),
            "stop_price": round_to(stop_price, 6),
        },
    )
    return ArmProtectiveExitOrdersResult(
        status="ARMED",
        summary=(
            "ARMED: protective exit orders placed "
            f"(tp_order_id={submission.take_profit_order.order_id}, sl_order_id={submission.stop_loss_order.order_id})"
        ),
    )
