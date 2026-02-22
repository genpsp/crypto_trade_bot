from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from pybot.domain.model.types import TradeOrderSnapshot, TradeResultSnapshot

SwapSide = Literal["BUY_SOL_WITH_USDC", "SELL_SOL_FOR_USDC"]


@dataclass
class SubmitSwapRequest:
    side: SwapSide
    amount_atomic: int
    slippage_bps: int
    only_direct_routes: bool


@dataclass
class SwapSubmission:
    tx_signature: str
    in_amount_atomic: int
    out_amount_atomic: int
    order: TradeOrderSnapshot | None = None
    result: TradeResultSnapshot | None = None


@dataclass
class SwapConfirmation:
    confirmed: bool
    error: str | None = None


class ExecutionPort(Protocol):
    def submit_swap(self, request: SubmitSwapRequest) -> SwapSubmission: ...

    def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation: ...

    def get_mark_price(self, pair: str) -> float: ...

    def get_available_quote_usdc(self, pair: str) -> float: ...

    def get_available_base_sol(self, pair: str) -> float: ...
