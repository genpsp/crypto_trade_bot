from __future__ import annotations

import math
from typing import Any

from pybot.domain.indicators.ta import atr_series, ema_series, rsi_series
from pybot.domain.model.types import (
    ExecutionConfig,
    ExitConfig,
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
from pybot.domain.strategy.shared.decision_builders import build_entry_signal, build_no_signal

# 押し目判定に使う過去バー本数、上げると押し目検知が増えやすくなり、エントリー機会が増える
PULLBACK_LOOKBACK_BARS = 4
# EMAからの乖離許容(%)、上げると高値追いエントリーが増え、下げると厳格になる
MAX_DISTANCE_FROM_EMA_FAST_PCT = 1.2
# 最低ストップ幅(%)、上げると近すぎるストップをより弾き、下げるとエントリーが増える
MIN_STOP_DISTANCE_PCT = 0.4
# RSI計算期間、上げると滑らかで遅く、下げると反応が速くノイズに敏感になる
RSI_PERIOD = 14
# RSI下限、上げると弱い局面をより弾いてエントリー減、下げるとエントリー増
RSI_LOWER_BOUND = 45
# RSI上限、上げると過熱局面の許容が広がりエントリー増、下げると過熱回避が強くなる
RSI_UPPER_BOUND = 70
# ATR計算期間、上げるとボラ判定が安定、下げると短期変動に敏感になる
ATR_PERIOD = 14
# ATRストップ係数、上げるとATR基準が遠くなりATR競合で見送りが増えやすく、下げると逆になる
ATR_STOP_MULTIPLIER = 2


def _resolve_position_size_multiplier(atr_pct: float | None, risk: RiskConfig) -> tuple[str, float]:
    if atr_pct is None or not math.isfinite(atr_pct):
        return "NORMAL", 1.0
    if atr_pct >= risk["storm_atr_pct_threshold"]:
        return "STORM", risk["storm_size_multiplier"]
    if atr_pct >= risk["volatile_atr_pct_threshold"]:
        return "VOLATILE", risk["volatile_size_multiplier"]
    return "NORMAL", 1.0


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
        return build_no_signal(
            "NO_SIGNAL: EMA is not stable yet",
            "EMA_NOT_STABLE",
            diagnostics=diagnostics,
        )

    if ema_fast <= ema_slow:
        return build_no_signal(
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
        return build_no_signal(
            "NO_SIGNAL: pullback condition not found",
            "PULLBACK_NOT_FOUND",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    has_reclaim = entry_price > ema_fast
    diagnostics["reclaim_found"] = has_reclaim
    if not has_reclaim:
        return build_no_signal(
            f"NO_SIGNAL: reclaim condition not found (close={entry_price:.4f} <= EMA{strategy['ema_fast_period']}={ema_fast:.4f})",
            "RECLAIM_NOT_FOUND",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    distance_from_ema_fast_pct = ((entry_price - ema_fast) / entry_price) * 100
    diagnostics["distance_from_ema_fast_pct"] = distance_from_ema_fast_pct
    if distance_from_ema_fast_pct > MAX_DISTANCE_FROM_EMA_FAST_PCT:
        return build_no_signal(
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
        return build_no_signal(
            "NO_SIGNAL: RSI is not stable yet",
            "RSI_NOT_STABLE",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if rsi_value < RSI_LOWER_BOUND:
        return build_no_signal(
            "NO_SIGNAL: RSI is too low",
            "RSI_TOO_LOW",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if rsi_value > RSI_UPPER_BOUND:
        return build_no_signal(
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
    atr_pct = ((latest_atr / entry_price) * 100) if latest_atr is not None else None
    volatility_regime, position_size_multiplier = _resolve_position_size_multiplier(atr_pct, risk)
    diagnostics["swing_low_stop"] = swing_low_stop
    diagnostics["stop_candidate"] = stop_candidate
    diagnostics["atr"] = latest_atr
    diagnostics["atr_pct"] = atr_pct
    diagnostics["volatility_regime"] = volatility_regime
    diagnostics["position_size_multiplier"] = position_size_multiplier
    if latest_atr is not None and math.isfinite(latest_atr) and latest_atr > 0:
        atr_stop = entry_price - latest_atr * ATR_STOP_MULTIPLIER
        if atr_stop < stop_candidate:
            return build_no_signal(
                "NO_SIGNAL: ATR stop conflicts with max loss cap",
                "ATR_STOP_CONFLICT_MAX_LOSS",
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                diagnostics=diagnostics,
            )

    final_stop = stop_candidate
    diagnostics["final_stop"] = final_stop
    if final_stop >= entry_price:
        return build_no_signal(
            "NO_SIGNAL: stop is not below entry",
            "INVALID_RISK_STRUCTURE",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    stop_distance_pct = ((entry_price - final_stop) / entry_price) * 100
    diagnostics["stop_distance_pct"] = stop_distance_pct
    if stop_distance_pct < MIN_STOP_DISTANCE_PCT:
        return build_no_signal(
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

    return build_entry_signal(
        (
            "ENTER: trend ok + pullback/reclaim, "
            f"entry={entry_price:.4f}, stop={final_stop:.4f}, tp={take_profit_price:.4f}, "
            f"rsi={rsi_value:.2f}, regime={volatility_regime}, size_x={position_size_multiplier:.2f}"
        ),
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        entry_price=entry_price,
        stop_price=final_stop,
        take_profit_price=take_profit_price,
        diagnostics=diagnostics,
    )
