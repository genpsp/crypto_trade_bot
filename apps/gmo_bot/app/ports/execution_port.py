from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

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
class OrderSubmission:
    order_id: int
    order: TradeOrderSnapshot | None = None
    result: TradeResultSnapshot | None = None


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

    def confirm_order(self, order_id: int, timeout_ms: int) -> OrderConfirmation: ...

    def get_mark_price(self, pair: str) -> float: ...

    def get_available_margin_jpy(self) -> float: ...

    def get_symbol_rule(self, pair: str) -> SymbolRule: ...
