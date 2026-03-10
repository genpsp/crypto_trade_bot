from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal

from apps.gmo_bot.app.ports.execution_port import SymbolRule

SmokeDirection = Literal["LONG", "SHORT"]
SmokeOrderSide = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class SmokeRoundtripPlan:
    pair: str
    direction: SmokeDirection
    entry_side: SmokeOrderSide
    close_side: SmokeOrderSide
    size_sol: float
    reference_price: float
    estimated_notional_jpy: float


def build_smoke_roundtrip_plan(
    *,
    direction: SmokeDirection,
    mark_price: float,
    symbol_rule: SymbolRule,
    requested_size_sol: float | None = None,
    max_notional_jpy: float = 500.0,
) -> SmokeRoundtripPlan:
    if mark_price <= 0:
        raise RuntimeError("mark_price must be > 0")
    if max_notional_jpy <= 0:
        raise RuntimeError("max_notional_jpy must be > 0")

    raw_size_sol = symbol_rule.min_order_size if requested_size_sol is None else requested_size_sol
    normalized_size_sol = _round_down_to_step(raw_size_sol, symbol_rule.size_step)
    if normalized_size_sol < symbol_rule.min_order_size:
        raise RuntimeError("requested size rounds below GMO min_order_size")

    estimated_notional_jpy = round(normalized_size_sol * mark_price, 2)
    if estimated_notional_jpy > max_notional_jpy:
        raise RuntimeError(
            f"estimated notional {estimated_notional_jpy:.2f} JPY exceeds max_notional_jpy {max_notional_jpy:.2f} JPY"
        )

    entry_side: SmokeOrderSide = "BUY" if direction == "LONG" else "SELL"
    close_side: SmokeOrderSide = "SELL" if direction == "LONG" else "BUY"
    return SmokeRoundtripPlan(
        pair="SOL/JPY",
        direction=direction,
        entry_side=entry_side,
        close_side=close_side,
        size_sol=normalized_size_sol,
        reference_price=mark_price,
        estimated_notional_jpy=estimated_notional_jpy,
    )


def _round_down_to_step(value: float, step: float) -> float:
    if value <= 0 or step <= 0:
        return 0.0
    scaled = math.floor(value / step)
    return round(scaled * step, 10)
