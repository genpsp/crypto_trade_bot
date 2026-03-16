from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
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
        order_id = self.client.create_close_order(
            symbol=symbol,
            side=request.side,
            execution_type="MARKET",
            settle_positions=settle_positions,
        )
        return OrderSubmission(order_id=order_id, order={"order_id": order_id})

    def submit_protective_exit_orders(self, request: SubmitProtectiveExitOrdersRequest) -> ProtectiveExitOrdersSubmission:
        symbol = PAIR_SYMBOL_MAP["SOL/JPY"]
        settle_positions = []
        for lot in request.lots:
            normalized_size = self._normalize_size(symbol, lot["size_sol"])
            if normalized_size <= 0:
                continue
            settle_positions.append({"positionId": lot["position_id"], "size": _decimal_str(normalized_size)})
        if not settle_positions:
            raise RuntimeError("no closeable position lots")

        take_profit_order_id = self.client.create_close_order(
            symbol=symbol,
            side=request.side,
            execution_type="LIMIT",
            settle_positions=settle_positions,
            price=request.take_profit_price,
            time_in_force="FAS",
        )
        stop_loss_order_id = self.client.create_close_order(
            symbol=symbol,
            side=request.side,
            execution_type="STOP",
            settle_positions=settle_positions,
            price=request.stop_price,
            time_in_force="FAK",
        )
        return ProtectiveExitOrdersSubmission(
            take_profit_order=OrderSubmission(
                order_id=take_profit_order_id,
                order={"order_id": take_profit_order_id},
            ),
            stop_loss_order=OrderSubmission(
                order_id=stop_loss_order_id,
                order={"order_id": stop_loss_order_id},
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


def _decimal_str(value: float) -> str:
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text if text else "0"
