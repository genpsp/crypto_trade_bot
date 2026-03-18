from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math
import time
from typing import Any

from apps.gmo_bot.adapters.execution.gmo_api_client import GmoApiClient
from apps.gmo_bot.app.ports.execution_port import (
    ExecutionPort,
    OrderConfirmation,
    OrderSubmission,
    ProtectiveExitOrdersSubmission,
    SubmitCloseOrderRequest,
    SubmitEntryOrderRequest,
    SubmitProtectiveExitOrdersRequest,
    SymbolRule,
)
from apps.gmo_bot.app.ports.logger_port import LoggerPort

PAIR_SYMBOL_MAP = {"SOL/JPY": "SOL_JPY"}
POLL_INTERVAL_SECONDS = 0.4
SYMBOL_RULE_CACHE_TTL_SECONDS = 300
CLOSE_ORDER_ACTIVE_STATUSES = {"ORDERED", "WAITING", "MODIFYING", "CANCELLING"}


class GmoMarginExecutionAdapter(ExecutionPort):
    def __init__(self, client: GmoApiClient, logger: LoggerPort):
        self.client = client
        self.logger = logger
        self._symbol_rule_cache: dict[str, tuple[float, SymbolRule]] = {}
        self.protective_exit_enabled = True

    def set_protective_exit_enabled(self, enabled: bool) -> None:
        self.protective_exit_enabled = enabled

    def submit_entry_order(self, request: SubmitEntryOrderRequest) -> OrderSubmission:
        symbol = PAIR_SYMBOL_MAP["SOL/JPY"]
        size = self._normalize_size(symbol, request.size_sol)
        if size <= 0:
            raise RuntimeError("entry size rounded to 0")
        order_id = self.client.create_order(
            symbol=symbol,
            side=request.side,
            execution_type="MARKET",
            size=size,
        )
        return OrderSubmission(order_id=order_id, order={"order_id": order_id})

    def submit_close_order(self, request: SubmitCloseOrderRequest) -> OrderSubmission:
        symbol = PAIR_SYMBOL_MAP["SOL/JPY"]
        settle_positions = []
        for lot in request.lots:
            normalized_size = self._normalize_size(symbol, lot["size_sol"])
            if normalized_size <= 0:
                continue
            settle_positions.append({"positionId": lot["position_id"], "size": _decimal_str(normalized_size)})
        if not settle_positions:
            raise RuntimeError("no closeable position lots")
        if len(settle_positions) == 1:
            order_id = self.client.create_close_order(
                symbol=symbol,
                side=request.side,
                execution_type="MARKET",
                settle_position=settle_positions[0],
            )
        else:
            order_id = self.client.create_close_bulk_order(
                symbol=symbol,
                side=request.side,
                execution_type="MARKET",
                size=sum(_to_float(position["size"]) or 0.0 for position in settle_positions),
            )
        return OrderSubmission(order_id=order_id, order={"order_id": order_id})

    def submit_protective_exit_orders(self, request: SubmitProtectiveExitOrdersRequest) -> ProtectiveExitOrdersSubmission:
        symbol = PAIR_SYMBOL_MAP["SOL/JPY"]
        rule = self.get_symbol_rule("SOL/JPY")
        settle_positions = self._build_settle_positions(symbol, request.lots)
        if not settle_positions:
            canceled_orders = self._cancel_conflicting_close_orders(symbol=symbol, side=request.side)
            if canceled_orders > 0:
                settle_positions = self._build_settle_positions(symbol, request.lots)
        if not settle_positions:
            raise RuntimeError("no settable position lots for protective stop order")
        normalized_stop_price = _round_stop_price(
            request.stop_price,
            rule.tick_size,
            request.side,
        )
        if normalized_stop_price <= 0:
            raise RuntimeError("protective exit price rounded to 0")
        if len(settle_positions) == 1:
            stop_loss_order_id = self.client.create_close_order(
                symbol=symbol,
                side=request.side,
                execution_type="STOP",
                settle_position=settle_positions[0],
                price=normalized_stop_price,
                time_in_force="FAK",
            )
        else:
            stop_loss_order_id = self.client.create_close_bulk_order(
                symbol=symbol,
                side=request.side,
                execution_type="STOP",
                size=sum(_to_float(position["size"]) or 0.0 for position in settle_positions),
                price=normalized_stop_price,
                time_in_force="FAK",
            )
        return ProtectiveExitOrdersSubmission(
            stop_loss_order=OrderSubmission(
                order_id=stop_loss_order_id,
                order={"order_id": stop_loss_order_id, "price": normalized_stop_price},
            ),
        )

    def confirm_order(self, order_id: int, timeout_ms: int) -> OrderConfirmation:
        deadline = time.time() + (timeout_ms / 1000)
        last_error = "order confirmation timed out"
        while time.time() < deadline:
            executions = self.client.get_executions(order_id)
            if executions:
                result = self._aggregate_executions(executions)
                return OrderConfirmation(confirmed=True, result=result)
            order = self.client.get_order(order_id)
            if order is not None:
                status = str(order.get("status") or "")
                executed_size = _to_float(order.get("executedSize")) or 0.0
                if executed_size > 0:
                    executions = self.client.get_executions(order_id)
                    if executions:
                        result = self._aggregate_executions(executions)
                        return OrderConfirmation(confirmed=True, result=result)
                if status in {"CANCELED", "EXPIRED", "REJECTED"}:
                    cancel_type = order.get("cancelType")
                    last_error = f"order {status.lower()} ({cancel_type})"
                    return OrderConfirmation(confirmed=False, error=last_error)
                last_error = f"order status={status}"
            time.sleep(POLL_INTERVAL_SECONDS)
        return OrderConfirmation(confirmed=False, error=last_error)

    def get_mark_price(self, pair: str) -> float:
        symbol = PAIR_SYMBOL_MAP[pair]
        ticker = self.client.get_ticker(symbol)
        last = ticker.get("last")
        if not isinstance(last, str):
            raise RuntimeError(f"GMO ticker last is invalid for {symbol}")
        return float(last)

    def get_available_margin_jpy(self) -> float:
        payload = self.client.get_margin()
        available_amount = payload.get("availableAmount")
        if not isinstance(available_amount, str):
            raise RuntimeError("GMO availableAmount is invalid")
        return float(available_amount)

    def cancel_order(self, order_id: int) -> None:
        self.client.cancel_order(order_id)

    def get_order(self, order_id: int) -> dict[str, Any] | None:
        return self.client.get_order(order_id)

    def get_executions(self, order_id: int) -> list[dict[str, Any]]:
        return self.client.get_executions(order_id)

    def get_symbol_rule(self, pair: str) -> SymbolRule:
        symbol = PAIR_SYMBOL_MAP[pair]
        cached = self._symbol_rule_cache.get(symbol)
        now = time.time()
        if cached is not None and now - cached[0] < SYMBOL_RULE_CACHE_TTL_SECONDS:
            return cached[1]
        symbols = self.client.get_symbols()
        for item in symbols:
            if item.get("symbol") != symbol:
                continue
            rule = SymbolRule(
                symbol=symbol,
                tick_size=float(item["tickSize"]),
                size_step=float(item["sizeStep"]),
                min_order_size=float(item["minOrderSize"]),
            )
            self._symbol_rule_cache[symbol] = (now, rule)
            return rule
        raise RuntimeError(f"GMO symbol rule not found for {symbol}")

    def _normalize_size(self, symbol: str, size_sol: float) -> float:
        rule = self.get_symbol_rule("SOL/JPY")
        normalized = _round_down_to_step(size_sol, rule.size_step)
        if normalized < rule.min_order_size:
            return 0.0
        return normalized

    def _build_settle_positions(self, symbol: str, lots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rule = self.get_symbol_rule("SOL/JPY")
        open_positions = self.client.get_open_positions(symbol)
        open_position_map: dict[int, dict[str, Any]] = {}
        for position in open_positions:
            position_id = position.get("positionId")
            if isinstance(position_id, int):
                open_position_map[position_id] = position

        settle_positions: list[dict[str, Any]] = []
        for lot in lots:
            position_id = lot.get("position_id")
            requested_size = _to_float(lot.get("size_sol"))
            if not isinstance(position_id, int) or requested_size is None:
                continue
            normalized_requested_size = self._normalize_size(symbol, requested_size)
            if normalized_requested_size <= 0:
                continue
            open_position = open_position_map.get(position_id)
            if not isinstance(open_position, dict):
                continue
            actual_size = _to_float(open_position.get("size")) or 0.0
            ordered_size = _to_float(open_position.get("orderdSize")) or 0.0
            settable_size = _round_down_to_step(max(actual_size - ordered_size, 0.0), rule.size_step)
            final_size = _round_down_to_step(min(normalized_requested_size, settable_size), rule.size_step)
            if final_size <= 0:
                continue
            settle_positions.append({"positionId": position_id, "size": _decimal_str(final_size)})
        return settle_positions

    def _cancel_conflicting_close_orders(self, *, symbol: str, side: str) -> int:
        canceled = 0
        for order in self.client.get_active_orders(symbol):
            order_side = str(order.get("side") or "").upper()
            settle_type = str(order.get("settleType") or "").upper()
            status = str(order.get("status") or order.get("orderStatus") or "").upper()
            order_id = order.get("orderId")
            if order_side != side or settle_type != "CLOSE" or status not in CLOSE_ORDER_ACTIVE_STATUSES:
                continue
            if not isinstance(order_id, int):
                continue
            self.client.cancel_order(order_id)
            canceled += 1
            self.logger.warn(
                "canceled conflicting GMO close order before arming protective stop",
                {"symbol": symbol, "side": side, "order_id": order_id, "status": status},
            )
        return canceled

    def _aggregate_executions(self, executions: list[dict[str, Any]]) -> dict[str, Any]:
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
            execution_id = execution.get("executionId")
            if execution_id is not None:
                execution_ids.append(str(execution_id))
        if total_size <= 0:
            raise RuntimeError("GMO executions resolved but filled size is 0")
        avg_fill_price = total_quote / total_size
        lots = [
            {"position_id": position_id, "size_sol": size}
            for position_id, size in sorted(lots_by_position_id.items())
            if size > 0
        ]
        result = {
            "status": "CONFIRMED",
            "avg_fill_price": avg_fill_price,
            "filled_base_sol": total_size,
            "filled_quote_jpy": total_quote,
            "fee_jpy": total_fee,
            "execution_ids": execution_ids,
            "lots": lots,
        }
        if has_realized_pnl:
            result["realized_pnl_jpy"] = total_realized_pnl
        return result


def _to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _round_down_to_step(value: float, step: float) -> float:
    if value <= 0 or step <= 0:
        return 0.0
    scaled = math.floor(value / step)
    return round(scaled * step, 10)


def _round_stop_price(value: float, step: float, side: str) -> float:
    if side == "SELL":
        return _round_price_to_step(value, step, rounding=ROUND_CEILING)
    return _round_price_to_step(value, step, rounding=ROUND_FLOOR)


def _round_price_to_step(value: float, step: float, *, rounding: str) -> float:
    if value <= 0 or step <= 0:
        return 0.0
    value_decimal = Decimal(str(value))
    step_decimal = Decimal(str(step))
    scaled = (value_decimal / step_decimal).to_integral_value(rounding=rounding)
    return float(scaled * step_decimal)


def _decimal_str(value: float) -> str:
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text if text else "0"
