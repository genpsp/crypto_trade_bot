from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from shared.indicators.ta import atr_series, ema_series, rsi_series
from apps.dex_bot.domain.model.types import (
    ExecutionConfig,
    ExitConfig,
    ModelDirection,
    OhlcvBar,
    RiskConfig,
    StrategyConfig,
    StrategyDecision,
)
from apps.dex_bot.domain.risk.swing_low_stop import (
    calculate_swing_high,
    calculate_swing_low,
    calculate_take_profit_price,
    calculate_take_profit_price_for_short,
    tighten_stop_for_long,
    tighten_stop_for_short,
)
from apps.dex_bot.domain.strategy.shared.decision_builders import build_entry_signal, build_no_signal
from apps.dex_bot.domain.strategy.shared.market_context import (
    build_ema_market_context,
    calculate_minimum_bars,
)

# 15mモデルは2hモデルより直近の押し目を重視
PULLBACK_LOOKBACK_BARS = 6
# 短期足の高値追いを抑制
MAX_DISTANCE_FROM_EMA_FAST_PCT = 0.9
# ショート時はブレイクダウン確認を厳格化（0-7 bars即損切り対策）
SHORT_BREAKDOWN_LOOKBACK_BARS = 6
# 15mでは過小ストップを除外するため下限を設定
MIN_STOP_DISTANCE_PCT = 0.3
RSI_PERIOD = 14
RSI_LONG_LOWER_BOUND = 50
RSI_LONG_UPPER_BOUND = 68
RSI_SHORT_LOWER_BOUND = 32
RSI_SHORT_UPPER_BOUND = 50
# 4hトレンドが弱い時のみ2hトレンド一致を要求
LONG_WEAK_UPPER_TREND_MIN_GAP_PCT = 0.05
LONG_WEAK_TREND_CONFIRM_TIMEFRAME_MINUTES = 120
# ショート時は4h EMA乖離が小さい弱トレンドを除外
SHORT_UPPER_TREND_MIN_GAP_PCT = 0.05
# ショート時は4h fast EMA がまだ上向きなら見送る
SHORT_UPPER_FAST_SLOPE_MAX_PCT = 0.1
# ショート時は直近3本の4h close drift が強く上向く局面を避ける
SHORT_UPPER_CLOSE_DRIFT_MAX_PCT = 0.5
ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 1.5
# LONG は高ATR帯での追随が崩れやすいため上限を置く
LONG_ATR_PCT_MAX = 0.7
UPPER_TREND_TIMEFRAME_MINUTES = 240
UPPER_TREND_EMA_FAST_PERIOD = 9
UPPER_TREND_EMA_SLOW_PERIOD = 34

_CLOSE_MINUTES_CACHE: dict[datetime, int] = {}
_CLOSE_MINUTES_CACHE_MAX_SIZE = 200_000


def _resolve_close_minutes(close_time: datetime) -> int:
    cached = _CLOSE_MINUTES_CACHE.get(close_time)
    if cached is not None:
        return cached

    resolved = int(close_time.astimezone(UTC).timestamp() // 60)
    if len(_CLOSE_MINUTES_CACHE) >= _CLOSE_MINUTES_CACHE_MAX_SIZE:
        _CLOSE_MINUTES_CACHE.clear()
    _CLOSE_MINUTES_CACHE[close_time] = resolved
    return resolved


def _resolve_upper_bucket_index(close_time: datetime, timeframe_minutes: int) -> int:
    close_minutes = _resolve_close_minutes(close_time)
    return (close_minutes + timeframe_minutes - 1) // timeframe_minutes


def _build_upper_timeframe_closes(bars: list[OhlcvBar], timeframe_minutes: int) -> list[float]:
    if not bars:
        return []

    upper_closes: list[float] = []
    current_bucket_index: int | None = None
    current_bucket_close: float | None = None

    for bar in bars:
        bucket_index = _resolve_upper_bucket_index(bar.close_time, timeframe_minutes)
        if current_bucket_index is None:
            current_bucket_index = bucket_index
            current_bucket_close = bar.close
            continue

        if bucket_index != current_bucket_index:
            if current_bucket_close is not None:
                upper_closes.append(current_bucket_close)
            current_bucket_index = bucket_index

        current_bucket_close = bar.close

    latest_close_minutes = _resolve_close_minutes(bars[-1].close_time)
    # Keep the last bucket only when the current bar is exactly on timeframe close.
    if current_bucket_close is not None and latest_close_minutes % timeframe_minutes == 0:
        upper_closes.append(current_bucket_close)

    return upper_closes


def _evaluate_upper_timeframe_trend(
    bars: list[OhlcvBar],
    timeframe_minutes: int = UPPER_TREND_TIMEFRAME_MINUTES,
) -> tuple[str, float | None, float | None, int]:
    upper_closes = _build_upper_timeframe_closes(bars, timeframe_minutes)
    upper_ema_fast_values = ema_series(upper_closes, UPPER_TREND_EMA_FAST_PERIOD)
    upper_ema_slow_values = ema_series(upper_closes, UPPER_TREND_EMA_SLOW_PERIOD)
    upper_ema_fast = upper_ema_fast_values[-1] if upper_ema_fast_values else None
    upper_ema_slow = upper_ema_slow_values[-1] if upper_ema_slow_values else None
    if upper_ema_fast is None or upper_ema_slow is None:
        return "UNAVAILABLE", upper_ema_fast, upper_ema_slow, len(upper_closes)
    return (
        ("UP" if upper_ema_fast > upper_ema_slow else "DOWN"),
        upper_ema_fast,
        upper_ema_slow,
        len(upper_closes),
    )


def _resolve_position_size_multiplier(atr_pct: float | None, risk: RiskConfig) -> tuple[str, float]:
    if atr_pct is None or not math.isfinite(atr_pct):
        return "NORMAL", 1.0
    if atr_pct >= risk["storm_atr_pct_threshold"]:
        return "STORM", risk["storm_size_multiplier"]
    if atr_pct >= risk["volatile_atr_pct_threshold"]:
        return "VOLATILE", risk["volatile_size_multiplier"]
    return "NORMAL", 1.0


def _calculate_ema_gap_pct(ema_fast: float | None, ema_slow: float | None) -> float | None:
    if ema_fast is None or ema_slow is None or not math.isfinite(ema_fast) or not math.isfinite(ema_slow):
        return None
    if ema_slow == 0:
        return None
    return (abs(ema_fast - ema_slow) / abs(ema_slow)) * 100


def _calculate_pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    if not math.isfinite(current) or not math.isfinite(previous) or previous == 0:
        return None
    return ((current - previous) / abs(previous)) * 100


def _calculate_upper_trend_regime_metrics(
    bars: list[OhlcvBar],
    timeframe_minutes: int = UPPER_TREND_TIMEFRAME_MINUTES,
) -> tuple[float | None, float | None]:
    upper_closes = _build_upper_timeframe_closes(bars, timeframe_minutes)
    upper_ema_fast_values = ema_series(upper_closes, UPPER_TREND_EMA_FAST_PERIOD)
    upper_fast_slope_pct = None
    if len(upper_ema_fast_values) >= 2:
        upper_fast_slope_pct = _calculate_pct_change(
            upper_ema_fast_values[-1],
            upper_ema_fast_values[-2],
        )
    upper_close_drift_pct_3 = None
    if len(upper_closes) >= 3:
        upper_close_drift_pct_3 = _calculate_pct_change(
            upper_closes[-1],
            upper_closes[-3],
        )
    return upper_fast_slope_pct, upper_close_drift_pct_3


def evaluate_ema_trend_pullback_15m_v0(
    bars: list[OhlcvBar],
    strategy: StrategyConfig,
    risk: RiskConfig,
    exit: ExitConfig,
    execution: ExecutionConfig,
    direction: ModelDirection = "BOTH",
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

    upper_trend_state, upper_ema_fast, upper_ema_slow, upper_bars_count = _evaluate_upper_timeframe_trend(
        bars,
        UPPER_TREND_TIMEFRAME_MINUTES,
    )
    diagnostics["upper_trend_timeframe"] = f"{UPPER_TREND_TIMEFRAME_MINUTES // 60}h"
    diagnostics["upper_trend_bars_count"] = upper_bars_count
    diagnostics["upper_trend_ema_fast"] = upper_ema_fast
    diagnostics["upper_trend_ema_slow"] = upper_ema_slow
    diagnostics["upper_trend_state"] = upper_trend_state
    upper_trend_gap_pct = _calculate_ema_gap_pct(upper_ema_fast, upper_ema_slow)
    upper_fast_slope_pct, upper_close_drift_pct_3 = _calculate_upper_trend_regime_metrics(
        bars,
        UPPER_TREND_TIMEFRAME_MINUTES,
    )
    diagnostics["upper_trend_gap_pct"] = upper_trend_gap_pct
    diagnostics["upper_fast_slope_pct"] = upper_fast_slope_pct
    diagnostics["upper_close_drift_pct_3"] = upper_close_drift_pct_3
    diagnostics["model_direction"] = direction
    if upper_trend_state == "UNAVAILABLE":
        return build_no_signal(
            "NO_SIGNAL: upper timeframe trend EMA is not stable yet",
            "UPPER_TREND_EMA_NOT_STABLE",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    entry_direction = "LONG" if upper_trend_state == "UP" else "SHORT"
    if direction == "LONG" and entry_direction != "LONG":
        return build_no_signal(
            "NO_SIGNAL: model direction LONG blocks short regime",
            "MODEL_DIRECTION_LONG_ONLY",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if direction == "SHORT" and entry_direction != "SHORT":
        return build_no_signal(
            "NO_SIGNAL: model direction SHORT blocks long regime",
            "MODEL_DIRECTION_SHORT_ONLY",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    diagnostics["entry_direction"] = entry_direction
    if (
        entry_direction == "LONG"
        and upper_trend_gap_pct is not None
        and upper_trend_gap_pct < LONG_WEAK_UPPER_TREND_MIN_GAP_PCT
    ):
        weak_trend_state_2h, weak_trend_ema_fast_2h, weak_trend_ema_slow_2h, weak_trend_bars_count_2h = (
            _evaluate_upper_timeframe_trend(
                bars,
                LONG_WEAK_TREND_CONFIRM_TIMEFRAME_MINUTES,
            )
        )
        diagnostics["weak_long_trend_2h_timeframe"] = f"{LONG_WEAK_TREND_CONFIRM_TIMEFRAME_MINUTES // 60}h"
        diagnostics["weak_long_trend_2h_state"] = weak_trend_state_2h
        diagnostics["weak_long_trend_2h_bars_count"] = weak_trend_bars_count_2h
        diagnostics["weak_long_trend_2h_ema_fast"] = weak_trend_ema_fast_2h
        diagnostics["weak_long_trend_2h_ema_slow"] = weak_trend_ema_slow_2h
        if weak_trend_state_2h != "UP":
            return build_no_signal(
                (
                    "NO_SIGNAL: weak 4h uptrend is not confirmed by 2h "
                    f"(4h_gap={upper_trend_gap_pct:.4f}, 2h_state={weak_trend_state_2h})"
                ),
                "LONG_WEAK_UPPER_TREND_NOT_CONFIRMED_BY_2H",
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                diagnostics=diagnostics,
            )
    if entry_direction == "SHORT":
        if upper_trend_gap_pct is None or upper_trend_gap_pct < SHORT_UPPER_TREND_MIN_GAP_PCT:
            return build_no_signal(
                (
                    "NO_SIGNAL: upper timeframe downtrend is too weak for short "
                    f"(gap={upper_trend_gap_pct if upper_trend_gap_pct is not None else 'N/A'})"
                ),
                "SHORT_UPPER_TREND_TOO_WEAK",
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                diagnostics=diagnostics,
            )
        if (
            upper_fast_slope_pct is not None
            and upper_fast_slope_pct > SHORT_UPPER_FAST_SLOPE_MAX_PCT
        ):
            return build_no_signal(
                (
                    "NO_SIGNAL: short upper fast slope is still positive "
                    f"(slope={upper_fast_slope_pct:.4f})"
                ),
                "SHORT_UPPER_FAST_SLOPE_TOO_POSITIVE",
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                diagnostics=diagnostics,
            )
        if (
            upper_close_drift_pct_3 is not None
            and upper_close_drift_pct_3 > SHORT_UPPER_CLOSE_DRIFT_MAX_PCT
        ):
            return build_no_signal(
                (
                    "NO_SIGNAL: short upper close drift is too positive "
                    f"(drift_3={upper_close_drift_pct_3:.4f})"
                ),
                "SHORT_UPPER_CLOSE_DRIFT_TOO_POSITIVE",
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                diagnostics=diagnostics,
            )
    if entry_direction == "LONG" and ema_fast <= ema_slow:
        return build_no_signal(
            (
                "NO_SIGNAL: trend filter failed for long "
                f"(EMA{strategy['ema_fast_period']}={ema_fast:.4f} <= "
                f"EMA{strategy['ema_slow_period']}={ema_slow:.4f})"
            ),
            "EMA_TREND_FILTER_FAILED",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if entry_direction == "SHORT" and ema_fast >= ema_slow:
        return build_no_signal(
            (
                "NO_SIGNAL: trend filter failed for short "
                f"(EMA{strategy['ema_fast_period']}={ema_fast:.4f} >= "
                f"EMA{strategy['ema_slow_period']}={ema_slow:.4f})"
            ),
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
        low = lows[index]
        high = highs[index]
        close = closes[index]
        if bar_ema_fast is None or math.isnan(bar_ema_fast):
            continue
        if entry_direction == "LONG":
            if low <= bar_ema_fast or close < bar_ema_fast:
                has_pullback = True
                break
        else:
            if high >= bar_ema_fast or close > bar_ema_fast:
                has_pullback = True
                break

    diagnostics["pullback_found"] = has_pullback
    if not has_pullback:
        no_signal_summary = "NO_SIGNAL: pullback condition not found"
        no_signal_reason = "PULLBACK_NOT_FOUND"
        if entry_direction == "SHORT":
            no_signal_summary = "NO_SIGNAL: short pullback condition not found"
            no_signal_reason = "SHORT_PULLBACK_NOT_FOUND"
        return build_no_signal(
            no_signal_summary,
            no_signal_reason,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    has_reclaim = entry_price > ema_fast if entry_direction == "LONG" else entry_price < ema_fast
    diagnostics["reclaim_found"] = has_reclaim
    if not has_reclaim:
        no_signal_summary = (
            f"NO_SIGNAL: reclaim condition not found (close={entry_price:.4f} <= EMA{strategy['ema_fast_period']}={ema_fast:.4f})"
        )
        no_signal_reason = "RECLAIM_NOT_FOUND"
        if entry_direction == "SHORT":
            no_signal_summary = (
                f"NO_SIGNAL: short reclaim condition not found "
                f"(close={entry_price:.4f} >= EMA{strategy['ema_fast_period']}={ema_fast:.4f})"
            )
            no_signal_reason = "SHORT_RECLAIM_NOT_FOUND"
        return build_no_signal(
            no_signal_summary,
            no_signal_reason,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    if entry_direction == "SHORT":
        short_breakdown_start_index = max(0, latest_index - SHORT_BREAKDOWN_LOOKBACK_BARS)
        short_breakdown_reference_low = min(lows[short_breakdown_start_index:latest_index])
        short_breakdown_confirmed = entry_price < short_breakdown_reference_low
        diagnostics["short_breakdown_reference_low"] = short_breakdown_reference_low
        diagnostics["short_breakdown_confirmed"] = short_breakdown_confirmed
        if not short_breakdown_confirmed:
            return build_no_signal(
                (
                    "NO_SIGNAL: short breakdown is not confirmed "
                    f"(close={entry_price:.4f} >= ref_low={short_breakdown_reference_low:.4f})"
                ),
                "SHORT_BREAKDOWN_NOT_CONFIRMED",
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                diagnostics=diagnostics,
            )

    if entry_direction == "LONG":
        distance_from_ema_fast_pct = ((entry_price - ema_fast) / entry_price) * 100
    else:
        distance_from_ema_fast_pct = ((ema_fast - entry_price) / entry_price) * 100
    diagnostics["distance_from_ema_fast_pct"] = distance_from_ema_fast_pct
    if distance_from_ema_fast_pct > MAX_DISTANCE_FROM_EMA_FAST_PCT:
        no_signal_summary = "NO_SIGNAL: entry is too far from EMA fast"
        no_signal_reason = "CHASE_ENTRY_TOO_FAR_FROM_EMA"
        if entry_direction == "SHORT":
            no_signal_summary = "NO_SIGNAL: short entry is too far from EMA fast"
            no_signal_reason = "SHORT_CHASE_ENTRY_TOO_FAR_FROM_EMA"
        return build_no_signal(
            no_signal_summary,
            no_signal_reason,
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
    if entry_direction == "LONG" and rsi_value < RSI_LONG_LOWER_BOUND:
        return build_no_signal(
            "NO_SIGNAL: RSI is too low",
            "RSI_TOO_LOW",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if entry_direction == "LONG" and rsi_value > RSI_LONG_UPPER_BOUND:
        return build_no_signal(
            "NO_SIGNAL: RSI is too high",
            "RSI_TOO_HIGH",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if entry_direction == "SHORT" and rsi_value < RSI_SHORT_LOWER_BOUND:
        return build_no_signal(
            "NO_SIGNAL: RSI is too low for short",
            "SHORT_RSI_TOO_LOW",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if entry_direction == "SHORT" and rsi_value > RSI_SHORT_UPPER_BOUND:
        return build_no_signal(
            "NO_SIGNAL: RSI is too high for short",
            "SHORT_RSI_TOO_HIGH",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    atr_values = atr_series(highs, lows, closes, ATR_PERIOD)
    latest_atr = atr_values[-1] if atr_values else None
    atr_pct = ((latest_atr / entry_price) * 100) if latest_atr is not None else None
    volatility_regime, position_size_multiplier = _resolve_position_size_multiplier(atr_pct, risk)
    diagnostics["atr"] = latest_atr
    diagnostics["atr_pct"] = atr_pct
    diagnostics["volatility_regime"] = volatility_regime
    diagnostics["position_size_multiplier"] = position_size_multiplier
    if entry_direction == "LONG" and atr_pct is not None and atr_pct >= LONG_ATR_PCT_MAX:
        return build_no_signal(
            f"NO_SIGNAL: long ATR regime is too hot (atr_pct={atr_pct:.4f})",
            "LONG_ATR_REGIME_TOO_HOT",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if volatility_regime == "STORM" and position_size_multiplier <= 0:
        return build_no_signal(
            "NO_SIGNAL: storm entries are disabled by storm_size_multiplier=0",
            "STORM_SIZE_MULTIPLIER_DISABLED",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if entry_direction == "LONG":
        swing_low_stop = calculate_swing_low(lows, strategy["swing_low_lookback_bars"])
        stop_candidate = tighten_stop_for_long(entry_price, swing_low_stop, risk["max_loss_per_trade_pct"])
        diagnostics["swing_low_stop"] = swing_low_stop
        diagnostics["stop_candidate"] = stop_candidate
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
    else:
        swing_high_stop = calculate_swing_high(highs, strategy["swing_low_lookback_bars"])
        stop_candidate = tighten_stop_for_short(entry_price, swing_high_stop, risk["max_loss_per_trade_pct"])
        diagnostics["swing_high_stop"] = swing_high_stop
        diagnostics["stop_candidate"] = stop_candidate
        if latest_atr is not None and math.isfinite(latest_atr) and latest_atr > 0:
            atr_stop = entry_price + latest_atr * ATR_STOP_MULTIPLIER
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
    if entry_direction == "LONG" and final_stop >= entry_price:
        return build_no_signal(
            "NO_SIGNAL: stop is not below entry",
            "INVALID_RISK_STRUCTURE",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )
    if entry_direction == "SHORT" and final_stop <= entry_price:
        return build_no_signal(
            "NO_SIGNAL: short stop is not above entry",
            "INVALID_SHORT_RISK_STRUCTURE",
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    if entry_direction == "LONG":
        stop_distance_pct = ((entry_price - final_stop) / entry_price) * 100
    else:
        stop_distance_pct = ((final_stop - entry_price) / entry_price) * 100
    diagnostics["stop_distance_pct"] = stop_distance_pct
    if stop_distance_pct < MIN_STOP_DISTANCE_PCT:
        no_signal_summary = "NO_SIGNAL: stop is too tight"
        no_signal_reason = "STOP_TOO_TIGHT"
        if entry_direction == "SHORT":
            no_signal_summary = "NO_SIGNAL: short stop is too tight"
            no_signal_reason = "SHORT_STOP_TOO_TIGHT"
        return build_no_signal(
            no_signal_summary,
            no_signal_reason,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            diagnostics=diagnostics,
        )

    if entry_direction == "LONG":
        take_profit_price = calculate_take_profit_price(
            entry_price,
            final_stop,
            exit["take_profit_r_multiple"],
        )
    else:
        take_profit_price = calculate_take_profit_price_for_short(
            entry_price,
            final_stop,
            exit["take_profit_r_multiple"],
        )
    diagnostics["take_profit_price"] = take_profit_price

    return build_entry_signal(
        (
            "ENTER: 15m trend/pullback/reclaim with 4h trend gate, "
            f"direction={entry_direction}, "
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
