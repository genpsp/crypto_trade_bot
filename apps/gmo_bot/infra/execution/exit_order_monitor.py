from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from apps.gmo_bot.adapters.execution.gmo_margin_execution import GmoMarginExecutionAdapter
from apps.gmo_bot.adapters.execution.private_ws_client import GmoPrivateWebSocketClient
from apps.gmo_bot.app.usecases.close_position import (
    ClosePositionDependencies,
    ClosePositionInput,
    aggregate_execution_records,
    apply_confirmed_exit_result,
    close_position,
    collect_unprocessed_exit_executions,
)
from apps.gmo_bot.app.usecases.protective_exit_orders import has_active_protective_exit_orders


@dataclass
class ExitMonitorContext:
    model_id: str
    pair: str
    execution: GmoMarginExecutionAdapter
    persistence: Any
    lock: Any


class GmoExitOrderMonitor:
    def __init__(
        self,
        *,
        api_client: Any,
        logger: Any,
        context_provider: Callable[[], list[ExitMonitorContext]],
    ):
        self.logger = logger
        self.context_provider = context_provider
        self.ws_client = GmoPrivateWebSocketClient(client=api_client, logger=logger, on_event=self._handle_event)

    def start(self) -> None:
        self.ws_client.start()

    def stop(self) -> None:
        self.ws_client.stop()

    def _handle_event(self, payload: dict[str, Any]) -> None:
        channel = str(payload.get("channel") or "")
        if channel == "executionEvents":
            self._handle_execution_event(payload)
            return
        if channel == "orderEvents":
            self._handle_order_event(payload)

    def _handle_execution_event(self, payload: dict[str, Any]) -> None:
        order_id = _to_int(payload.get("orderId"))
        if order_id is None:
            return
        resolved = self._find_trade_by_exit_order_id(order_id)
        if resolved is None:
            return
        context, trade, exit_kind = resolved
        sibling_order_id, sibling_status_key = self._sibling_order_state(trade, filled_kind=exit_kind)
        executions = context.execution.get_executions(order_id)
        new_executions = collect_unprocessed_exit_executions(trade, executions)
        if not new_executions:
            return
        execution_result = aggregate_execution_records(new_executions)
        close_reason = "TAKE_PROFIT" if exit_kind == "take_profit" else "STOP_LOSS"
        execution_snapshot = trade.get("execution", {})
        order_snapshot = (
            execution_snapshot.get("take_profit_order") if exit_kind == "take_profit" else execution_snapshot.get("stop_loss_order")
        )
        close_price = _to_float(order_snapshot.get("price")) if isinstance(order_snapshot, dict) else None
        if close_price is None:
            close_price = float(trade["position"]["take_profit_price"] if close_reason == "TAKE_PROFIT" else trade["position"]["stop_price"])
        result = apply_confirmed_exit_result(
            logger=self.logger,
            persistence=context.persistence,
            trade=trade,
            execution_result=execution_result,
            order_id=order_id,
            close_reason=close_reason,
            close_price=close_price,
        )
        if result.status == "CLOSED":
            self._cancel_sibling_order(context, trade, sibling_order_id=sibling_order_id, sibling_status_key=sibling_status_key)
        self.logger.info(
            "gmo exit order execution processed",
            {
                "trade_id": trade.get("trade_id"),
                "order_id": order_id,
                "close_reason": close_reason,
                "status": result.status,
            },
        )

    def _handle_order_event(self, payload: dict[str, Any]) -> None:
        order_id = _to_int(payload.get("orderId"))
        if order_id is None:
            return
        resolved = self._find_trade_by_exit_order_id(order_id)
        if resolved is None:
            return
        context, trade, exit_kind = resolved
        status = str(payload.get("orderStatus") or payload.get("status") or "").upper()
        if not status:
            return

        execution_snapshot = trade.get("execution", {})
        if not isinstance(execution_snapshot, dict):
            return
        status_key = "take_profit_order_status" if exit_kind == "take_profit" else "stop_loss_order_status"
        execution_snapshot[status_key] = status
        context.persistence.update_trade(
            trade["trade_id"],
            {"execution": execution_snapshot},
        )

        if exit_kind == "stop_loss" and status == "EXPIRED":
            refreshed_trade = context.persistence.find_open_trade(context.pair)
            if not isinstance(refreshed_trade, dict):
                return
            if has_active_protective_exit_orders(refreshed_trade):
                return
            mark_price = context.execution.get_mark_price(context.pair)
            stop_price = float(refreshed_trade["position"]["stop_price"])
            direction = str(refreshed_trade.get("direction") or "LONG")
            should_force_close = (
                direction == "LONG" and mark_price <= stop_price
            ) or (
                direction == "SHORT" and mark_price >= stop_price
            )
            if not should_force_close:
                return
            close_position(
                ClosePositionDependencies(
                    execution=context.execution,
                    lock=context.lock,
                    logger=self.logger,
                    persistence=context.persistence,
                ),
                ClosePositionInput(
                    config=context.persistence.get_current_config(),
                    trade=refreshed_trade,
                    close_reason="STOP_LOSS",
                    close_price=mark_price,
                ),
            )
            self.logger.warn(
                "gmo stop order expired and emergency close executed",
                {
                    "trade_id": refreshed_trade.get("trade_id"),
                    "order_id": order_id,
                    "mark_price": mark_price,
                    "stop_price": stop_price,
                },
            )

    def _sibling_order_state(self, trade: dict[str, Any], *, filled_kind: str) -> tuple[int | None, str]:
        execution_snapshot = trade.get("execution", {})
        if not isinstance(execution_snapshot, dict):
            return None, ""
        sibling_id_key = "stop_loss_order_id" if filled_kind == "take_profit" else "take_profit_order_id"
        sibling_status_key = "stop_loss_order_status" if filled_kind == "take_profit" else "take_profit_order_status"
        sibling_id = execution_snapshot.get(sibling_id_key)
        if not isinstance(sibling_id, int):
            return None, sibling_status_key
        return sibling_id, sibling_status_key

    def _cancel_sibling_order(
        self,
        context: ExitMonitorContext,
        trade: dict[str, Any],
        *,
        sibling_order_id: int | None,
        sibling_status_key: str,
    ) -> None:
        execution_snapshot = trade.get("execution", {})
        if not isinstance(execution_snapshot, dict):
            return
        sibling_status = execution_snapshot.get(sibling_status_key)
        if sibling_order_id is None or sibling_status not in {"SUBMITTED", "ORDERED", "WAITING", "INACTIVE"}:
            return
        try:
            context.execution.cancel_order(sibling_order_id)
            execution_snapshot[sibling_status_key] = "CANCELED"
            context.persistence.update_trade(trade["trade_id"], {"execution": execution_snapshot})
        except Exception as error:
            self.logger.warn(
                "failed to cancel sibling protective exit order",
                {"trade_id": trade.get("trade_id"), "order_id": sibling_order_id, "error": str(error)},
            )

    def _find_trade_by_exit_order_id(self, order_id: int) -> tuple[ExitMonitorContext, dict[str, Any], str] | None:
        for context in self.context_provider():
            trade = context.persistence.find_open_trade(context.pair)
            if not isinstance(trade, dict):
                continue
            execution_snapshot = trade.get("execution", {})
            if not isinstance(execution_snapshot, dict):
                continue
            if execution_snapshot.get("take_profit_order_id") == order_id:
                return context, trade, "take_profit"
            if execution_snapshot.get("stop_loss_order_id") == order_id:
                return context, trade, "stop_loss"
        return None


def _to_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


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
