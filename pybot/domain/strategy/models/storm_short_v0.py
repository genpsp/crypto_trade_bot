from __future__ import annotations

import math
from typing import Any

from pybot.domain.indicators.ta import atr_series, rsi_series
from pybot.domain.model.types import (
    ExecutionConfig,
    ExitConfig,
    OhlcvBar,
    RiskConfig,
    StrategyConfig,
    StrategyDecision,
)
from pybot.domain.risk.swing_low_stop import (
    calculate_swing_high,
    calculate_take_profit_price_for_short,
    tighten_stop_for_short,
)
from pybot.domain.strategy.shared.decision_builders import build_entry_signal, build_no_signal
from pybot.domain.strategy.shared.market_context import (
    build_ema_market_context,
    calculate_minimum_bars,
)

PULLBACK_LOOKBACK_BARS = 4
MAX_DISTANCE_FROM_EMA_FAST_PCT = 1.2
MIN_STOP_DISTANCE_PCT = 0.4
RSI_PERIOD = 14
RSI_LOWER_BOUND = 30
RSI_UPPER_BOUND = 65
ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 2


def evaluate_storm_short_v0(
    bars: list[OhlcvBar],
    strategy: StrategyConfig,
    risk: RiskConfig,
    exit: ExitConfig,
    execution: ExecutionConfig,
) -> StrategyDecision:
    minimum_bars = calculate_minimum_bars(
        strategy,
        PULLBACK_LOOKBACK_BARS + 1,
        RSI_PERIOD + 1,
        ATR_PERIOD + 1,
    )
    diagnostics: dict[str, Any] = {
        "bars_count": len(bars),
        "minimum_bars_required": minimum_bars,
    }
    if len(bars) < minimum_bars:
        return build_no_signal(
            "NO_SIGNAL: not enough bars for strategy calculation",
            f"INSUFFICIENT_BARS_{len(bars)}_OF_{minimum_bars}",
            diagnostics=diagnostics,
        )
    if execution["min_notional_usdc"] <= 0:
        return build_no_signal(
            "NO_SIGNAL: min_notional_usdc is invalid",
            "INVALID_MIN_NOTIONAL_USDC",
            diagnostics=diagnostics,
        )

    context = build_ema_market_context(bars, strategy)
    closes = context.closes
    highs = context.highs
    lows = context.lows
    ema_fast_by_bar = context.ema_fast_by_bar
    ema_fast = context.ema_fast
    ema_slow = context.ema_slow
    entry_price = context.entry_price
    previous_close = context.previous_close
    previous_ema_fast = context.previous_ema_fast
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
        return build_no_signal(
            "NO_SIGNAL: EMA is not stable yet",
            "EMA_NOT_STABLE",
            diagnostics=diagnostics,
        )

    atr_values = atr_series(highs, lows, closes, ATR_PERIOD)
    latest_atr = atr_values[-1] if atr_values else None
    atr_pct = ((latest_atr / entry_price) * 100) if latest_atr is not None else None
    diagnostics["atr"] = latest_atr
    diagnostics["atr_pct"] = atr_pct
    diagnostics["volatility_regime"] = "STORM" if atr_pct and atr_pct >= risk["storm_atr_pct_threshold"] else "NORMAL"
    diagnostics["position_size_multiplier"] = risk["storm_size_multiplier"]

    if atr_pct is None or not math.isfinite(atr_pct):
        return build_no_signal(
            "NO_SIGNAL: ATR is not stable yet",
            "ATR_NOT_STABLE",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if atr_pct < risk["storm_atr_pct_threshold"]:
        return build_no_signal(
            "NO_SIGNAL: storm regime is required for storm short model",
            "STORM_REGIME_REQUIRED",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if risk["storm_size_multiplier"] <= 0:
        return build_no_signal(
            "NO_SIGNAL: storm entries are disabled by storm_size_multiplier=0",
            "STORM_SIZE_MULTIPLIER_DISABLED",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    if ema_fast >= ema_slow:
        return build_no_signal(
            f"NO_SIGNAL: short trend filter failed (EMA{strategy['ema_fast_period']}={ema_fast:.4f} >= EMA{strategy['ema_slow_period']}={ema_slow:.4f})",
            "EMA_SHORT_TREND_FILTER_FAILED",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    latest_index = len(bars) - 1
    pullback_start_index = max(0, latest_index - PULLBACK_LOOKBACK_BARS)
    has_pullback = False
    for index in range(pullback_start_index, latest_index):
        bar_ema_fast = ema_fast_by_bar[index]
        high = highs[index]
        close = closes[index]
        if bar_ema_fast is None or math.isnan(bar_ema_fast):
            continue
        if high >= bar_ema_fast or close > bar_ema_fast:
            has_pullback = True
            break
    diagnostics["pullback_found"] = has_pullback
    if not has_pullback:
        return build_no_signal(
            "NO_SIGNAL: short pullback condition not found",
            "SHORT_PULLBACK_NOT_FOUND",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    has_reclaim = entry_price < ema_fast
    diagnostics["reclaim_found"] = has_reclaim
    if not has_reclaim:
        return build_no_signal(
            f"NO_SIGNAL: short reclaim condition not found (close={entry_price:.4f} >= EMA{strategy['ema_fast_period']}={ema_fast:.4f})",
            "SHORT_RECLAIM_NOT_FOUND",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    distance_from_ema_fast_pct = ((ema_fast - entry_price) / entry_price) * 100
    diagnostics["distance_from_ema_fast_pct"] = distance_from_ema_fast_pct
    if distance_from_ema_fast_pct > MAX_DISTANCE_FROM_EMA_FAST_PCT:
        return build_no_signal(
            "NO_SIGNAL: short entry is too far from EMA fast",
            "SHORT_CHASE_ENTRY_TOO_FAR_FROM_EMA",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    rsi_values = rsi_series(closes, RSI_PERIOD)
    rsi_value = rsi_values[-1] if rsi_values else None
    diagnostics["rsi"] = rsi_value
    if rsi_value is None or math.isnan(rsi_value):
        return build_no_signal(
            "NO_SIGNAL: RSI is not stable yet",
            "RSI_NOT_STABLE",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if rsi_value < RSI_LOWER_BOUND:
        return build_no_signal(
            "NO_SIGNAL: RSI is too low for short",
            "SHORT_RSI_TOO_LOW",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if rsi_value > RSI_UPPER_BOUND:
        return build_no_signal(
            "NO_SIGNAL: RSI is too high for short",
            "SHORT_RSI_TOO_HIGH",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    swing_high_stop = calculate_swing_high(highs, strategy["swing_low_lookback_bars"])
    stop_candidate = tighten_stop_for_short(entry_price, swing_high_stop, risk["max_loss_per_trade_pct"])
    diagnostics["swing_high_stop"] = swing_high_stop
    diagnostics["stop_candidate"] = stop_candidate
    if latest_atr is not None and math.isfinite(latest_atr) and latest_atr > 0:
        atr_stop = entry_price + latest_atr * ATR_STOP_MULTIPLIER
        diagnostics["atr_stop"] = atr_stop
        if atr_stop > stop_candidate:
            return build_no_signal(
                "NO_SIGNAL: ATR stop conflicts with max loss cap",
                "ATR_STOP_CONFLICT_MAX_LOSS",
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                diagnostics=diagnostics,
            )

    final_stop = stop_candidate
    diagnostics["final_stop"] = final_stop
    if final_stop <= entry_price:
        return build_no_signal(
            "NO_SIGNAL: short stop is not above entry",
            "INVALID_SHORT_RISK_STRUCTURE",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    stop_distance_pct = ((final_stop - entry_price) / entry_price) * 100
    diagnostics["stop_distance_pct"] = stop_distance_pct
    if stop_distance_pct < MIN_STOP_DISTANCE_PCT:
        return build_no_signal(
            "NO_SIGNAL: short stop is too tight",
            "SHORT_STOP_TOO_TIGHT",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    take_profit_price = calculate_take_profit_price_for_short(
        entry_price, final_stop, exit["take_profit_r_multiple"]
    )
    diagnostics["take_profit_price"] = take_profit_price

    return build_entry_signal(
        (
            "ENTER: storm short with pullback/reclaim, "
            f"entry={entry_price:.4f}, stop={final_stop:.4f}, tp={take_profit_price:.4f}, "
            f"rsi={rsi_value:.2f}, regime=STORM, size_x={risk['storm_size_multiplier']:.2f}"
        ),
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        entry_price=entry_price,
        stop_price=final_stop,
        take_profit_price=take_profit_price,
        diagnostics=diagnostics,
    )
