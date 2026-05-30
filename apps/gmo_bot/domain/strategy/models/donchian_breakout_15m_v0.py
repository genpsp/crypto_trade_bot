"""Donchian breakout(N) entry signal for SOL/JPY 15m.

Track C pullback-free breakout entry (results in
docs/gmo_bot_exploration_findings.md §3 — REJECTED): LONG when close > max(high)
of last N bars; SHORT when close < min(low) of last N bars.

Stop = the opposite-side Donchian level (LONG: most-recent N-bar low; SHORT:
most-recent N-bar high). TP = R-multiple of risk.
"""

from __future__ import annotations

from dataclasses import dataclass
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


STRATEGY_NAME = "donchian_breakout_15m_v0"


def _atr(bars: list[OhlcvBar], period: int) -> float:
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


def evaluate_donchian_breakout_15m_v0(
    *,
    bars: list[OhlcvBar],
    direction: ModelDirection,
    strategy: StrategyConfig,
    risk: RiskConfig,
    exit: ExitConfig,
    execution: ExecutionConfig,
) -> StrategyDecision:
    period = int(strategy.get("donchian_period", 20))
    atr_period = int(strategy.get("atr_period", 14))

    if len(bars) < max(period, atr_period) + 2:
        return build_no_signal(
            summary=f"NO_SIGNAL: donchian warmup not reached (bars={len(bars)})",
            reason="EMA_NOT_STABLE",
            diagnostics={"bars_count": len(bars)},
        )

    # Donchian uses the N bars BEFORE the entry candidate to define the band.
    lookback_window = bars[-(period + 1) : -1]
    upper = max(bar.high for bar in lookback_window)
    lower = min(bar.low for bar in lookback_window)
    current_close = bars[-1].close
    atr_value = _atr(bars, atr_period)

    long_breakout = current_close > upper
    short_breakout = current_close < lower

    if not long_breakout and not short_breakout:
        return build_no_signal(
            summary="NO_SIGNAL: close within Donchian band",
            reason="EMA_TREND_FILTER_FAILED",
            diagnostics={
                "donchian_upper": upper,
                "donchian_lower": lower,
                "close": current_close,
            },
        )

    entry_price = current_close

    if long_breakout:
        if direction not in ("LONG", "BOTH"):
            return build_no_signal(
                summary="NO_SIGNAL: long breakout but direction config forbids LONG",
                reason="EMA_TREND_FILTER_FAILED",
                diagnostics={"donchian_direction": "LONG"},
            )
        stop_price = lower  # opposite band
        entry_direction_str = "LONG"
        if stop_price >= entry_price:
            return build_no_signal(
                summary="NO_SIGNAL: invalid long stop above entry",
                reason="INVALID_RISK_AFTER_FILL",
                diagnostics={"stop_candidate": stop_price, "entry": entry_price},
            )
    else:
        if direction not in ("SHORT", "BOTH"):
            return build_no_signal(
                summary="NO_SIGNAL: short breakout but direction config forbids SHORT",
                reason="EMA_TREND_FILTER_FAILED",
                diagnostics={"donchian_direction": "SHORT"},
            )
        stop_price = upper
        entry_direction_str = "SHORT"
        if stop_price <= entry_price:
            return build_no_signal(
                summary="NO_SIGNAL: invalid short stop below entry",
                reason="INVALID_RISK_AFTER_FILL",
                diagnostics={"stop_candidate": stop_price, "entry": entry_price},
            )

    risk_per_unit = abs(entry_price - stop_price)
    take_profit_r_multiple = float(exit.get("take_profit_r_multiple", 2.0))
    take_profit_price = (
        entry_price + take_profit_r_multiple * risk_per_unit
        if entry_direction_str == "LONG"
        else entry_price - take_profit_r_multiple * risk_per_unit
    )

    return build_entry_signal(
        summary=f"ENTER: donchian breakout {entry_direction_str}",
        ema_fast=entry_price,
        ema_slow=entry_price,
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        diagnostics={
            "atr": atr_value,
            "atr_pct": (atr_value / entry_price * 100) if entry_price > 0 else 0.0,
            "position_size_multiplier": 1.0,
            "entry_direction": entry_direction_str,
            "donchian_upper": upper,
            "donchian_lower": lower,
            "donchian_period": period,
        },
    )


__all__ = ["STRATEGY_NAME", "evaluate_donchian_breakout_15m_v0"]
