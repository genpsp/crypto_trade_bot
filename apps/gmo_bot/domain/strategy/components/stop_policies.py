"""Concrete StopPolicy implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.dex_bot.domain.model.types import Direction
from apps.dex_bot.domain.risk.swing_low_stop import (
    calculate_max_loss_stop_price,
    calculate_max_loss_stop_price_for_short,
    tighten_stop_for_long,
    tighten_stop_for_short,
)
from apps.gmo_bot.domain.strategy.components.base import StopPolicy


@dataclass(frozen=True)
class LegacyTightenedStop(StopPolicy):
    """Reproduces the engine's existing post-fill stop tightening logic.

    The strategy decision delivers a `swing_low_stop` candidate; this policy:
      1. Computes the max-loss-pct stop given the actual fill price
      2. Picks the tighter of (swing stop, max-loss stop)
      3. Falls back to the max-loss stop if the swing stop ended up on the
         wrong side of entry (the legacy engine's `INVALID_RISK_AFTER_FILL`
         pre-empt is preserved by returning None)
    """

    name: str = "legacy_tightened"

    def compute_initial_stop(
        self,
        *,
        decision: Any,
        direction: Direction,
        entry_price: float,
        max_loss_per_trade_pct: float,
        config: dict[str, Any],
    ) -> float | None:
        swing_stop = float(getattr(decision, "stop_price"))
        if direction == "LONG":
            pct_stop = calculate_max_loss_stop_price(entry_price, max_loss_per_trade_pct)
            final_stop = tighten_stop_for_long(entry_price, swing_stop, max_loss_per_trade_pct)
            if final_stop >= entry_price:
                final_stop = pct_stop
            if final_stop >= entry_price:
                return None
            return final_stop
        pct_stop = calculate_max_loss_stop_price_for_short(entry_price, max_loss_per_trade_pct)
        final_stop = tighten_stop_for_short(entry_price, swing_stop, max_loss_per_trade_pct)
        if final_stop <= entry_price:
            final_stop = pct_stop
        if final_stop <= entry_price:
            return None
        return final_stop


__all__ = ["LegacyTightenedStop"]
