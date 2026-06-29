from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from apps.gmo_bot.adapters.execution.gmo_margin_execution import GmoMarginExecutionAdapter
from apps.gmo_bot.adapters.execution.private_ws_client import GmoPrivateWebSocketClient
from apps.gmo_bot.domain.utils.coercion import to_float as _to_float
from apps.gmo_bot.app.usecases.close_position import (
    ClosePositionDependencies,
    ClosePositionInput,
    aggregate_execution_records,
    apply_confirmed_exit_result,
    close_position,
    collect_unprocessed_exit_executions,
    filter_tracked_exit_executions,
)
from apps.gmo_bot.app.usecases.protective_exit_orders import (
    ArmProtectiveExitOrdersDependencies,
    ArmProtectiveExitOrdersInput,
    arm_protective_exit_orders,
    get_stop_loss_order_snapshots,
    has_active_stop_loss_order,
    set_stop_loss_order_status,
)

EMERGENCY_CLOSE_RUN_LOCK_TTL_SECONDS = 600
PROTECTIVE_EXIT_ARMED_STATUSES = {"ARMED", "ARMED_STOP_ONLY"}


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
        on_trade_closed: Callable[[ExitMonitorContext, dict[str, Any]], None] | None = None,
    ):
        self.logger = logger
        self.context_provider = context_provider
        self.on_trade_closed = on_trade_closed
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
        executions = filter_tracked_exit_executions(
            logger=self.logger,
            trade=trade,
            executions=executions,
            order_id=order_id,
        )
        new_executions = collect_unprocessed_exit_executions(trade, executions)
        if not new_executions:
            return
        execution_result = aggregate_execution_records(new_executions)
        close_reason = "TAKE_PROFIT" if exit_kind == "take_profit" else "STOP_LOSS"
        close_price = _resolve_exit_event_close_price(trade, exit_kind=exit_kind, order_id=order_id)
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
            if self.on_trade_closed is not None:
                try:
                    self.on_trade_closed(context, trade)
                except Exception as error:
                    self.logger.error(
                        "gmo exit order close notification failed",
                        {
                            "trade_id": trade.get("trade_id"),
                            "order_id": order_id,
                            "close_reason": close_reason,
                            "error": str(error),
                        },
                    )
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
        if exit_kind == "stop_loss":
            set_stop_loss_order_status(trade, order_id=order_id, status=status)
        else:
            execution_snapshot[status_key] = status
        context.persistence.update_trade(
            trade["trade_id"],
            {"execution": execution_snapshot},
        )

        if exit_kind == "stop_loss" and status == "EXPIRED":
            refreshed_trade = context.persistence.find_open_trade(context.pair)
            if not isinstance(refreshed_trade, dict):
                # Normal race condition (trade closed between EXPIRED event and
                # the persistence read), but worth noticing if it becomes frequent.
                self.logger.warn(
                    "stop order EXPIRED but no open trade was found; treating as already closed",
                    {"order_id": order_id, "pair": context.pair, "model_id": context.model_id},
                )
                return
            if has_active_stop_loss_order(refreshed_trade):
                return
            lock_acquired = context.lock.acquire_runner_lock(EMERGENCY_CLOSE_RUN_LOCK_TTL_SECONDS)
            if not lock_acquired:
                self.logger.info(
                    "gmo stop order expired emergency handling deferred because runner lock is busy",
                    {"trade_id": refreshed_trade.get("trade_id"), "order_id": order_id},
                )
                return
            try:
                current_config = context.persistence.get_current_config()
                rearm_result = arm_protective_exit_orders(
                    ArmProtectiveExitOrdersDependencies(
                        execution=context.execution,
                        logger=self.logger,
                        persistence=context.persistence,
                    ),
                    ArmProtectiveExitOrdersInput(config=current_config, trade=refreshed_trade),
                )
                if rearm_result.status in PROTECTIVE_EXIT_ARMED_STATUSES:
                    self.logger.warn(
                        "gmo stop order expired and protective stop was re-armed",
                        {
                            "trade_id": refreshed_trade.get("trade_id"),
                            "expired_order_id": order_id,
                            "rearm_status": rearm_result.status,
                        },
                    )
                    return

                self.logger.error(
                    "CRITICAL: gmo stop order expired and protective stop re-arm failed",
                    {
                        "trade_id": refreshed_trade.get("trade_id"),
                        "expired_order_id": order_id,
                        "rearm_status": rearm_result.status,
                        "rearm_summary": rearm_result.summary,
                    },
                )
                mark_price = context.execution.get_mark_price(context.pair)
                stop_price = float(refreshed_trade["position"]["stop_price"])
                direction = str(refreshed_trade.get("direction") or "LONG")
                should_force_close = (direction == "LONG" and mark_price <= stop_price) or (
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
                        config=current_config,
                        trade=refreshed_trade,
                        close_reason="STOP_LOSS",
                        close_price=mark_price,
                    ),
                )
                self.logger.warn(
                    "gmo stop order expired, re-arm failed, and emergency close executed",
                    {
                        "trade_id": refreshed_trade.get("trade_id"),
                        "order_id": order_id,
                        "mark_price": mark_price,
                        "stop_price": stop_price,
                    },
                )
            finally:
                context.lock.release_runner_lock()

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
            for item in get_stop_loss_order_snapshots(trade):
                if item.get("order_id") == order_id:
                    return context, trade, "stop_loss"
        return None


def _resolve_exit_event_close_price(trade: dict[str, Any], *, exit_kind: str, order_id: int) -> float:
    execution_snapshot = trade.get("execution", {})
    if exit_kind == "stop_loss":
        for order_snapshot in get_stop_loss_order_snapshots(trade):
            if order_snapshot.get("order_id") != order_id:
                continue
            close_price = _to_float(order_snapshot.get("price"))
            if close_price is not None:
                return close_price
    elif isinstance(execution_snapshot, dict):
        order_snapshot = execution_snapshot.get("take_profit_order")
        close_price = _to_float(order_snapshot.get("price")) if isinstance(order_snapshot, dict) else None
        if close_price is not None:
            return close_price

    position = trade.get("position", {})
    if not isinstance(position, dict):
        raise RuntimeError("trade position snapshot is invalid")
    fallback_key = "take_profit_price" if exit_kind == "take_profit" else "stop_price"
    fallback_price = _to_float(position.get(fallback_key))
    if fallback_price is None:
        raise RuntimeError("protective exit trigger price is invalid")
    return fallback_price


def _to_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


# §9.2: ``_to_float`` is now imported from ``apps.gmo_bot.domain.utils.coercion``.
