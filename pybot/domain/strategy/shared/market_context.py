from __future__ import annotations

from dataclasses import dataclass

from pybot.domain.indicators.ta import ema_series
from pybot.domain.model.types import OhlcvBar, StrategyConfig


@dataclass(frozen=True)
class EmaMarketContext:
    closes: list[float]
    highs: list[float]
    lows: list[float]
    ema_fast_by_bar: list[float | None]
    ema_fast: float | None
    ema_slow: float | None
    entry_price: float | None
    previous_close: float | None
    previous_ema_fast: float | None


def calculate_minimum_bars(strategy: StrategyConfig, *extra_requirements: int) -> int:
    return max(
        strategy["ema_fast_period"],
        strategy["ema_slow_period"],
        strategy["swing_low_lookback_bars"],
        *extra_requirements,
    )


def build_ema_market_context(bars: list[OhlcvBar], strategy: StrategyConfig) -> EmaMarketContext:
    closes = [bar.close for bar in bars]
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]

    ema_fast_series = ema_series(closes, strategy["ema_fast_period"])
    ema_slow_series = ema_series(closes, strategy["ema_slow_period"])
    ema_fast_offset = len(closes) - len(ema_fast_series)
    ema_fast_by_bar: list[float | None] = []
    for index, _close in enumerate(closes):
        ema_index = index - ema_fast_offset
        ema_fast_by_bar.append(ema_fast_series[ema_index] if ema_index >= 0 else None)

    ema_fast = ema_fast_by_bar[-1] if ema_fast_by_bar else None
    ema_slow = ema_slow_series[-1] if ema_slow_series else None
    entry_price = closes[-1] if closes else None
    previous_close = closes[-2] if len(closes) >= 2 else None
    previous_ema_fast = ema_fast_by_bar[-2] if len(ema_fast_by_bar) >= 2 else None

    return EmaMarketContext(
        closes=closes,
        highs=highs,
        lows=lows,
        ema_fast_by_bar=ema_fast_by_bar,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        entry_price=entry_price,
        previous_close=previous_close,
        previous_ema_fast=previous_ema_fast,
    )
