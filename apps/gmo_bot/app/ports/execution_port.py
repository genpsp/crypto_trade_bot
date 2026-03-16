from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from apps.gmo_bot.domain.model.types import PositionLotSnapshot, TradeOrderSnapshot, TradeResultSnapshot

OrderSide = Literal["BUY", "SELL"]


@dataclass
class SubmitEntryOrderRequest:
    side: OrderSide
    size_sol: float
    slippage_bps: int
    reference_price: float


@dataclass
class SubmitCloseOrderRequest:
    side: OrderSide
    lots: list[PositionLotSnapshot]
    slippage_bps: int
    reference_price: float


@dataclass
class SubmitProtectiveExitOrdersRequest:
    side: OrderSide
    lots: list[PositionLotSnapshot]
    take_profit_price: float
    stop_price: float


@dataclass
class OrderSubmission:
    order_id: int
    order: TradeOrderSnapshot | None = None
    result: TradeResultSnapshot | None = None


@dataclass
class ProtectiveExitOrdersSubmission:
    take_profit_order: OrderSubmission
    stop_loss_order: OrderSubmission


@dataclass
class OrderConfirmation:
    confirmed: bool
    error: str | None = None
    result: TradeResultSnapshot | None = None


@dataclass
class SymbolRule:
    symbol: str
    tick_size: float
    size_step: float
    min_order_size: float


class ExecutionPort(Protocol):
    def submit_entry_order(self, request: SubmitEntryOrderRequest) -> OrderSubmission: ...

    def submit_close_order(self, request: SubmitCloseOrderRequest) -> OrderSubmission: ...

    def submit_protective_exit_orders(
        self,
        request: SubmitProtectiveExitOrdersRequest,
    ) -> ProtectiveExitOrdersSubmission: ...

    def confirm_order(self, order_id: int, timeout_ms: int) -> OrderConfirmation: ...

    def cancel_order(self, order_id: int) -> None: ...

    def get_order(self, order_id: int) -> dict[str, Any] | None: ...

    def get_executions(self, order_id: int) -> list[dict[str, Any]]: ...

    def get_mark_price(self, pair: str) -> float: ...

    def get_available_margin_jpy(self) -> float: ...

    def get_symbol_rule(self, pair: str) -> SymbolRule: ...
