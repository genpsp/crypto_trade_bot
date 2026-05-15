from __future__ import annotations

import math
from typing import Any

from apps.dex_bot.domain.model.types import OhlcvBar

TREND_LABELS = ("BULL", "BEAR", "CHOPPY")
VOLATILITY_LABELS = ("LOW_VOL", "MID_VOL", "HIGH_VOL")
BTC_CORRELATION_LABELS = ("RISK_ON", "RISK_OFF")


def _ema(values: list[float], period: int) -> list[float | None]:
    if not values:
        return []
    alpha = 2 / (max(1, period) + 1)
    result: list[float | None] = []
    current: float | None = None
    for value in values:
        current = value if current is None else (value * alpha + current * (1 - alpha))
        result.append(current)
    return result


def _atr_pct_values(bars: list[OhlcvBar], period: int = 14) -> list[float]:
    true_ranges: list[float] = []
    previous_close: float | None = None
    for bar in bars:
        if previous_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))
        true_ranges.append(max(0.0, tr))
        previous_close = bar.close
    atr_values = _ema(true_ranges, period)
    output: list[float] = []
    for bar, atr in zip(bars, atr_values):
        output.append(0.0 if atr is None or bar.close == 0 else (atr / abs(bar.close)) * 100)
    return output


def _quantile(values: list[float], pct: float) -> float:
    finite = sorted(value for value in values if math.isfinite(value))
    if not finite:
        return 0.0
    rank = (len(finite) - 1) * pct
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return finite[low]
    return finite[low] * (high - rank) + finite[high] * (rank - low)


def tag_market_regimes(bars: list[OhlcvBar]) -> list[dict[str, str]]:
    if not bars:
        return []
    closes = [float(bar.close) for bar in bars]
    ema200 = _ema(closes, 200)
    atr_pct = _atr_pct_values(bars)
    low_vol_cut = _quantile(atr_pct, 1 / 3)
    high_vol_cut = _quantile(atr_pct, 2 / 3)
    tags: list[dict[str, str]] = []
    for index, bar in enumerate(bars):
        ema_value = ema200[index] if index < len(ema200) else None
        lookback = max(0, index - 20)
        previous_ema = ema200[lookback] if lookback < len(ema200) else None
        slope_pct = 0.0
        if ema_value not in (None, 0) and previous_ema not in (None, 0):
            slope_pct = ((float(ema_value) - float(previous_ema)) / abs(float(previous_ema))) * 100
        if ema_value is None:
            trend = "CHOPPY"
        elif bar.close > ema_value and slope_pct >= 0.01:
            trend = "BULL"
        elif bar.close < ema_value and slope_pct <= -0.01:
            trend = "BEAR"
        else:
            trend = "CHOPPY"
        vol_value = atr_pct[index]
        if vol_value <= low_vol_cut:
            volatility = "LOW_VOL"
        elif vol_value >= high_vol_cut:
            volatility = "HIGH_VOL"
        else:
            volatility = "MID_VOL"
        btc_correlation = "RISK_OFF" if trend == "BEAR" else "RISK_ON"
        tags.append({"trend": trend, "volatility": volatility, "btc_correlation": btc_correlation})
    return tags


def attach_regime_tags(bars: list[OhlcvBar]) -> list[OhlcvBar]:
    for bar, tags in zip(bars, tag_market_regimes(bars)):
        setattr(bar, "regime_trend", tags["trend"])
        setattr(bar, "regime_volatility", tags["volatility"])
        setattr(bar, "regime_btc_correlation", tags["btc_correlation"])
        setattr(bar, "regime", dict(tags))
    return bars


def get_bar_regime(bar: OhlcvBar) -> dict[str, str]:
    raw = getattr(bar, "regime", None)
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    return {
        "trend": str(getattr(bar, "regime_trend", "CHOPPY")),
        "volatility": str(getattr(bar, "regime_volatility", "MID_VOL")),
        "btc_correlation": str(getattr(bar, "regime_btc_correlation", "RISK_ON")),
    }
