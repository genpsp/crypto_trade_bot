from __future__ import annotations

import math
from typing import Any

from pybot.domain.indicators.ta import atr_series, ema_series, rsi_series
from pybot.domain.model.types import (
    EntrySignalDecision,
    ExecutionConfig,
    ExitConfig,
    NoSignalDecision,
    OhlcvBar,
    RiskConfig,
    StrategyConfig,
    StrategyDecision,
)
from pybot.domain.risk.swing_low_stop import (
    calculate_swing_low,
    calculate_take_profit_price,
    tighten_stop_for_long,
)

PULLBACK_LOOKBACK_BARS = 4
MAX_DISTANCE_FROM_EMA_FAST_PCT = 1.2
MIN_STOP_DISTANCE_PCT = 0.4
RSI_PERIOD = 14
RSI_LOWER_BOUND = 45
RSI_UPPER_BOUND = 70
ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 2


def _no_signal(
    summary: str,
    reason: str,
    ema_fast: float | None = None,
    ema_slow: float | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> NoSignalDecision:
    return NoSignalDecision(
        type="NO_SIGNAL",
        summary=summary,
        reason=reason,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        diagnostics=diagnostics,
    )


def _entry_signal(
    summary: str,
    ema_fast: float,
    ema_slow: float,
    entry_price: float,
    stop_price: float,
    take_profit_price: float,
    diagnostics: dict[str, Any] | None = None,
) -> EntrySignalDecision:
    return EntrySignalDecision(
        type="ENTER",
        summary=summary,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        diagnostics=diagnostics,
    )


def evaluate_ema_trend_pullback_v0(
    bars: list[OhlcvBar],
    strategy: StrategyConfig,
    risk: RiskConfig,
    exit: ExitConfig,
    execution: ExecutionConfig,
) -> StrategyDecision:
    minimum_bars = max(
        strategy["ema_fast_period"],
        strategy["ema_slow_period"],
        strategy["swing_low_lookback_bars"],
        PULLBACK_LOOKBACK_BARS + 1,
        RSI_PERIOD + 1,
        ATR_PERIOD + 1,
    )
    diagnostics: dict[str, Any] = {
        "bars_count": len(bars),
        "minimum_bars_required": minimum_bars,
    }

    if len(bars) < minimum_bars:
        return _no_signal(
            "NO_SIGNAL: not enough bars for strategy calculation",
            f"INSUFFICIENT_BARS_{len(bars)}_OF_{minimum_bars}",
            diagnostics=diagnostics,
        )

    if execution["min_notional_usdc"] <= 0:
        return _no_signal(
            "NO_SIGNAL: min_notional_usdc is invalid",
            "INVALID_MIN_NOTIONAL_USDC",
            diagnostics=diagnostics,
        )

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

    ema_fast = ema_fast_by_bar[-1]
    ema_slow = ema_slow_series[-1] if ema_slow_series else None
    entry_price = closes[-1] if closes else None
    previous_close = closes[-2] if len(closes) >= 2 else None
    previous_ema_fast = ema_fast_by_bar[-2] if len(ema_fast_by_bar) >= 2 else None
    diagnostics["ema_fast"] = ema_fast
    diagnostics["ema_slow"] = ema_slow
    diagnostics["previous_close"] = previous_close
    diagnostics["previous_ema_fast"] = previous_ema_fast

    if (
        ema_fast is None
        or ema_slow is None
        or entry_price is None
        or math.isnan(ema_fast)
        or math.isnan(ema_slow)
    ):
        return _no_signal(
            "NO_SIGNAL: EMA is not stable yet",
            "EMA_NOT_STABLE",
            diagnostics=diagnostics,
        )

    if ema_fast <= ema_slow:
        return _no_signal(
            f"NO_SIGNAL: trend filter failed (EMA{strategy['ema_fast_period']}={ema_fast:.4f} <= EMA{strategy['ema_slow_period']}={ema_slow:.4f})",
            "EMA_TREND_FILTER_FAILED",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    latest_index = len(bars) - 1
    pullback_start_index = max(0, latest_index - PULLBACK_LOOKBACK_BARS)
    has_pullback = False
    for index in range(pullback_start_index, latest_index):
        bar_ema_fast = ema_fast_by_bar[index]
        low = lows[index]
        close = closes[index]
        if bar_ema_fast is None or math.isnan(bar_ema_fast):
            continue
        if low <= bar_ema_fast or close < bar_ema_fast:
            has_pullback = True
            break

    diagnostics["pullback_found"] = has_pullback
    if not has_pullback:
        return _no_signal(
            "NO_SIGNAL: pullback condition not found",
            "PULLBACK_NOT_FOUND",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    has_reclaim = entry_price > ema_fast
    diagnostics["reclaim_found"] = has_reclaim
    if not has_reclaim:
        return _no_signal(
            f"NO_SIGNAL: reclaim condition not found (close={entry_price:.4f} <= EMA{strategy['ema_fast_period']}={ema_fast:.4f})",
            "RECLAIM_NOT_FOUND",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    distance_from_ema_fast_pct = ((entry_price - ema_fast) / entry_price) * 100
    diagnostics["distance_from_ema_fast_pct"] = distance_from_ema_fast_pct
    if distance_from_ema_fast_pct > MAX_DISTANCE_FROM_EMA_FAST_PCT:
        return _no_signal(
            "NO_SIGNAL: entry is too far from EMA fast",
            "CHASE_ENTRY_TOO_FAR_FROM_EMA",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    rsi_values = rsi_series(closes, RSI_PERIOD)
    rsi_value = rsi_values[-1] if rsi_values else None
    diagnostics["rsi"] = rsi_value
    if rsi_value is None or math.isnan(rsi_value):
        return _no_signal(
            "NO_SIGNAL: RSI is not stable yet",
            "RSI_NOT_STABLE",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if rsi_value < RSI_LOWER_BOUND:
        return _no_signal(
            "NO_SIGNAL: RSI is too low",
            "RSI_TOO_LOW",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if rsi_value > RSI_UPPER_BOUND:
        return _no_signal(
            "NO_SIGNAL: RSI is too high",
            "RSI_TOO_HIGH",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    swing_low_stop = calculate_swing_low(lows, strategy["swing_low_lookback_bars"])
    stop_candidate = tighten_stop_for_long(entry_price, swing_low_stop, risk["max_loss_per_trade_pct"])
    atr_values = atr_series(highs, lows, closes, ATR_PERIOD)
    latest_atr = atr_values[-1] if atr_values else None
    diagnostics["swing_low_stop"] = swing_low_stop
    diagnostics["stop_candidate"] = stop_candidate
    diagnostics["atr"] = latest_atr
    if latest_atr is not None and math.isfinite(latest_atr) and latest_atr > 0:
        atr_stop = entry_price - latest_atr * ATR_STOP_MULTIPLIER
        if atr_stop < stop_candidate:
            return _no_signal(
                "NO_SIGNAL: ATR stop conflicts with max loss cap",
                "ATR_STOP_CONFLICT_MAX_LOSS",
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                diagnostics=diagnostics,
            )

    final_stop = stop_candidate
    diagnostics["final_stop"] = final_stop
    if final_stop >= entry_price:
        return _no_signal(
            "NO_SIGNAL: stop is not below entry",
            "INVALID_RISK_STRUCTURE",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    stop_distance_pct = ((entry_price - final_stop) / entry_price) * 100
    diagnostics["stop_distance_pct"] = stop_distance_pct
    if stop_distance_pct < MIN_STOP_DISTANCE_PCT:
        return _no_signal(
            "NO_SIGNAL: stop is too tight",
            "STOP_TOO_TIGHT",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    take_profit_price = calculate_take_profit_price(
        entry_price, final_stop, exit["take_profit_r_multiple"]
    )
    diagnostics["take_profit_price"] = take_profit_price

    return _entry_signal(
        f"ENTER: trend ok + pullback/reclaim, entry={entry_price:.4f}, stop={final_stop:.4f}, tp={take_profit_price:.4f}, rsi={rsi_value:.2f}",
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        entry_price=entry_price,
        stop_price=final_stop,
        take_profit_price=take_profit_price,
        diagnostics=diagnostics,
    )

