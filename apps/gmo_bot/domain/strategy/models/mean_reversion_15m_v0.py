"""Mean reversion (Bollinger Band reversal) entry signal.

P3-V chop-focused counter-trend entry (results in
docs/gmo_bot_exploration_findings.md §3 — REJECTED, systematic adverse
selection). Trend-following systems (EMA pullback, Supertrend, Donchian)
failed across all Phase 1 axes; this strategy bets on price returning to the
BB midline when far from it.

Entry:
- LONG when close < lower_band AND chop confirmed (ADX < adx_chop_max)
- SHORT when close > upper_band AND chop confirmed

Stop: opposite-side ATR cushion outside the band
TP: R-multiple of risk (from exit.take_profit_r_multiple)

Optional: filter out high-volatility regimes via long_atr_pct_max /
short_atr_pct_max (set high default so disabled by default).
"""

from __future__ import annotations

import math
from typing import Any

from apps.dex_bot.domain.model.types import (
    ExecutionConfig,
    ExitConfig,
    ModelDirection,
    OhlcvBar,
    RiskConfig,
    StrategyConfig,
    StrategyDecision,
)
from apps.dex_bot.domain.strategy.shared.decision_builders import (
    build_entry_signal,
    build_no_signal,
)


STRATEGY_NAME = "mean_reversion_15m_v0"


def _wilder_smooth(values: list[float], period: int) -> list[float]:
    n = len(values)
    if n == 0 or period <= 0:
        return [0.0] * n
    smoothed: list[float] = [0.0] * n
    running = 0.0
    for index in range(n):
        if index < period - 1:
            running += values[index]
        elif index == period - 1:
            running += values[index]
            smoothed[index] = running / period
        else:
            previous = smoothed[index - 1]
            smoothed[index] = previous - (previous / period) + (values[index] / period)
    return smoothed


def _adx_at(bars: list[OhlcvBar], period: int) -> float:
    """Return ADX at the last bar. 0.0 before warm-up."""
    n = len(bars)
    if n < period * 2 + 1:
        return 0.0
    plus_dm: list[float] = [0.0] * n
    minus_dm: list[float] = [0.0] * n
    tr: list[float] = [bars[0].high - bars[0].low]
    for i in range(1, n):
        up_move = bars[i].high - bars[i - 1].high
        down_move = bars[i - 1].low - bars[i].low
        plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0.0
        tr.append(
            max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - bars[i - 1].close),
                abs(bars[i].low - bars[i - 1].close),
            )
        )
    tr_s = _wilder_smooth(tr, period)
    plus_dm_s = _wilder_smooth(plus_dm, period)
    minus_dm_s = _wilder_smooth(minus_dm, period)
    dx: list[float] = [0.0] * n
    for i in range(n):
        if tr_s[i] <= 0:
            continue
        plus_di = 100 * plus_dm_s[i] / tr_s[i]
        minus_di = 100 * minus_dm_s[i] / tr_s[i]
        denom = plus_di + minus_di
        dx[i] = 100 * abs(plus_di - minus_di) / denom if denom > 0 else 0.0
    adx_series = _wilder_smooth(dx, period)
    return adx_series[-1]


def _atr_at(bars: list[OhlcvBar], period: int) -> float:
    if len(bars) < period + 1:
        return 0.0
    tr_sum = 0.0
    for i in range(len(bars) - period, len(bars)):
        prev_close = bars[i - 1].close if i > 0 else bars[i].close
        tr = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - prev_close),
            abs(bars[i].low - prev_close),
        )
        tr_sum += tr
    return tr_sum / period


def _bollinger_bands(
    bars: list[OhlcvBar], period: int, num_std: float
) -> tuple[float, float, float] | None:
    """Return (lower, mid, upper) BB at the last completed bar (index -2).

    We compute the bands on bars[:-1] so that the band at "entry candidate"
    bar is based on the prior N closed bars, avoiding lookahead.
    """
    if len(bars) < period + 1:
        return None
    closes = [bar.close for bar in bars[-(period + 1) : -1]]
    if len(closes) < period:
        return None
    mid = sum(closes) / period
    variance = sum((c - mid) ** 2 for c in closes) / period
    std = math.sqrt(variance)
    if std <= 0:
        return None
    return mid - num_std * std, mid, mid + num_std * std


def evaluate_mean_reversion_15m_v0(
    *,
    bars: list[OhlcvBar],
    direction: ModelDirection,
    strategy: StrategyConfig,
    risk: RiskConfig,
    exit: ExitConfig,
    execution: ExecutionConfig,
) -> StrategyDecision:
    bb_period = int(strategy.get("bb_period", 20))
    bb_num_std = float(strategy.get("bb_num_std", 2.0))
    adx_period = int(strategy.get("adx_period", 14))
    adx_chop_max = float(strategy.get("adx_chop_max", 25.0))
    atr_period = int(strategy.get("atr_period", 14))
    stop_atr_cushion = float(strategy.get("stop_atr_cushion", 0.5))
    long_atr_pct_max = float(strategy.get("long_atr_pct_max", 1.5))
    short_atr_pct_max = float(strategy.get("short_atr_pct_max", 1.5))

    warmup_needed = max(bb_period + 1, adx_period * 2 + 1, atr_period + 1)
    if len(bars) < warmup_needed:
        return build_no_signal(
            summary=f"NO_SIGNAL: warmup not reached (bars={len(bars)} need={warmup_needed})",
            reason="EMA_NOT_STABLE",
            diagnostics={"bars_count": len(bars)},
        )

    bands = _bollinger_bands(bars, bb_period, bb_num_std)
    if bands is None:
        return build_no_signal(
            summary="NO_SIGNAL: BB degenerate (std=0)",
            reason="EMA_NOT_STABLE",
            diagnostics={"bb_period": bb_period},
        )
    lower, mid, upper = bands
    current = bars[-1]
    current_close = current.close

    long_signal = current_close < lower
    short_signal = current_close > upper
    if not long_signal and not short_signal:
        return build_no_signal(
            summary="NO_SIGNAL: close within BB",
            reason="EMA_TREND_FILTER_FAILED",
            diagnostics={"bb_lower": lower, "bb_upper": upper, "close": current_close},
        )

    adx_value = _adx_at(bars, adx_period)
    if adx_value > adx_chop_max:
        return build_no_signal(
            summary=f"NO_SIGNAL: ADX {adx_value:.1f} > chop_max {adx_chop_max:.1f}",
            reason="EMA_TREND_FILTER_FAILED",
            diagnostics={"adx": adx_value, "adx_chop_max": adx_chop_max},
        )

    atr_value = _atr_at(bars, atr_period)
    atr_pct = (atr_value / current_close * 100) if current_close > 0 else 0.0

    if long_signal:
        if direction not in ("LONG", "BOTH"):
            return build_no_signal(
                summary="NO_SIGNAL: long signal but direction forbids LONG",
                reason="EMA_TREND_FILTER_FAILED",
                diagnostics={"mean_reversion_direction": "LONG"},
            )
        if atr_pct > long_atr_pct_max:
            return build_no_signal(
                summary=f"NO_SIGNAL: ATR% {atr_pct:.2f} > long_max {long_atr_pct_max:.2f}",
                reason="LONG_ATR_REGIME_TOO_HOT",
                diagnostics={"atr_pct": atr_pct},
            )
        entry_price = current_close
        stop_price = current.low - stop_atr_cushion * atr_value
        if stop_price >= entry_price:
            return build_no_signal(
                summary="NO_SIGNAL: invalid long stop above entry",
                reason="INVALID_RISK_AFTER_FILL",
                diagnostics={"stop_candidate": stop_price, "entry": entry_price},
            )
        entry_direction = "LONG"
    else:
        if direction not in ("SHORT", "BOTH"):
            return build_no_signal(
                summary="NO_SIGNAL: short signal but direction forbids SHORT",
                reason="EMA_TREND_FILTER_FAILED",
                diagnostics={"mean_reversion_direction": "SHORT"},
            )
        if atr_pct > short_atr_pct_max:
            return build_no_signal(
                summary=f"NO_SIGNAL: ATR% {atr_pct:.2f} > short_max {short_atr_pct_max:.2f}",
                reason="SHORT_ATR_REGIME_TOO_HOT",
                diagnostics={"atr_pct": atr_pct},
            )
        entry_price = current_close
        stop_price = current.high + stop_atr_cushion * atr_value
        if stop_price <= entry_price:
            return build_no_signal(
                summary="NO_SIGNAL: invalid short stop below entry",
                reason="INVALID_RISK_AFTER_FILL",
                diagnostics={"stop_candidate": stop_price, "entry": entry_price},
            )
        entry_direction = "SHORT"

    risk_per_unit = abs(entry_price - stop_price)
    take_profit_r_multiple = float(exit.get("take_profit_r_multiple", 2.0))
    take_profit_price = (
        entry_price + take_profit_r_multiple * risk_per_unit
        if entry_direction == "LONG"
        else entry_price - take_profit_r_multiple * risk_per_unit
    )

    return build_entry_signal(
        summary=f"ENTER: mean reversion {entry_direction}",
        ema_fast=mid,
        ema_slow=mid,
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        diagnostics={
            "atr": atr_value,
            "atr_pct": atr_pct,
            "position_size_multiplier": 1.0,
            "entry_direction": entry_direction,
            "bb_lower": lower,
            "bb_mid": mid,
            "bb_upper": upper,
            "bb_period": bb_period,
            "bb_num_std": bb_num_std,
            "adx": adx_value,
        },
    )


__all__ = ["STRATEGY_NAME", "evaluate_mean_reversion_15m_v0"]
