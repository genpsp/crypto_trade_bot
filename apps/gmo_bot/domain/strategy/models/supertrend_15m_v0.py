"""Supertrend(period, atr_multiple) entry signal for SOL/JPY 15m.

Track C alternative trend detection replacing the EMA-pullback signal (results
in docs/gmo_bot_exploration_findings.md §3 — REJECTED, catastrophic in chop).
Uses an ATR-band Supertrend whose flip direction defines the entry direction.

Decision contract is identical to the v0 strategy so the existing engine
machinery (StopPolicy / ExitPolicy / RegimeGate via the component bundle)
works unchanged. Stop price = the most recent Supertrend level at the flip
bar; the engine then applies max-loss tightening as usual.
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


STRATEGY_NAME = "supertrend_15m_v0"


@dataclass(frozen=True)
class SupertrendValue:
    upper_band: float
    lower_band: float
    final_upper: float
    final_lower: float
    direction: int  # +1 = uptrend (price > Supertrend level), -1 = downtrend
    level: float  # the Supertrend "stop line"


def _compute_atr(bars: list[OhlcvBar], period: int) -> list[float]:
    n = len(bars)
    if n == 0:
        return []
    tr: list[float] = [bars[0].high - bars[0].low]
    for i in range(1, n):
        tr.append(
            max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - bars[i - 1].close),
                abs(bars[i].low - bars[i - 1].close),
            )
        )
    out: list[float] = [0.0] * n
    running = 0.0
    for i in range(n):
        running += tr[i]
        if i >= period:
            running -= tr[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def compute_supertrend(
    bars: list[OhlcvBar], period: int, atr_multiple: float
) -> list[SupertrendValue]:
    """Return per-bar Supertrend state. Values before the period warm-up have
    direction=+1 and level=0.0 (treated as "no signal yet" by callers).
    """
    n = len(bars)
    atr = _compute_atr(bars, period)
    result: list[SupertrendValue] = [
        SupertrendValue(0, 0, 0, 0, 1, 0.0) for _ in range(n)
    ]
    if n == 0:
        return result

    prev_final_upper = 0.0
    prev_final_lower = 0.0
    prev_direction = 1
    prev_close = bars[0].close

    for i in range(n):
        bar = bars[i]
        hl2 = (bar.high + bar.low) / 2.0
        upper_band = hl2 + atr_multiple * atr[i]
        lower_band = hl2 - atr_multiple * atr[i]

        if i == 0 or atr[i] == 0.0:
            final_upper = upper_band
            final_lower = lower_band
            direction = 1
            level = lower_band
        else:
            # Final upper band: keep the lower of the two if previous final upper
            # was respected (close > prev final upper); else carry forward
            if upper_band < prev_final_upper or prev_close > prev_final_upper:
                final_upper = upper_band
            else:
                final_upper = prev_final_upper

            if lower_band > prev_final_lower or prev_close < prev_final_lower:
                final_lower = lower_band
            else:
                final_lower = prev_final_lower

            # Direction flip
            if prev_direction == 1 and bar.close < final_lower:
                direction = -1
            elif prev_direction == -1 and bar.close > final_upper:
                direction = 1
            else:
                direction = prev_direction

            level = final_lower if direction == 1 else final_upper

        result[i] = SupertrendValue(
            upper_band=upper_band,
            lower_band=lower_band,
            final_upper=final_upper,
            final_lower=final_lower,
            direction=direction,
            level=level,
        )
        prev_final_upper = final_upper
        prev_final_lower = final_lower
        prev_direction = direction
        prev_close = bar.close

    return result


def evaluate_supertrend_15m_v0(
    *,
    bars: list[OhlcvBar],
    direction: ModelDirection,
    strategy: StrategyConfig,
    risk: RiskConfig,
    exit: ExitConfig,
    execution: ExecutionConfig,
) -> StrategyDecision:
    period = int(strategy.get("supertrend_period", 10))
    atr_multiple = float(strategy.get("supertrend_atr_multiple", 3.0))

    if len(bars) < period * 3:
        return build_no_signal(
            summary=f"NO_SIGNAL: supertrend warmup not reached (bars={len(bars)})",
            reason="EMA_NOT_STABLE",  # reuse existing reason for engine counters
            diagnostics={"bars_count": len(bars)},
        )

    series = compute_supertrend(bars, period, atr_multiple)
    current = series[-1]
    previous = series[-2]

    # Entry only on the flip bar
    if current.direction == previous.direction:
        return build_no_signal(
            summary="NO_SIGNAL: no Supertrend flip on this bar",
            reason="EMA_TREND_FILTER_FAILED",
            diagnostics={
                "supertrend_direction": current.direction,
                "supertrend_level": current.level,
            },
        )

    entry_price = float(bars[-1].close)
    flip_to_long = current.direction == 1
    flip_to_short = current.direction == -1

    if flip_to_long:
        if direction not in ("LONG", "BOTH"):
            return build_no_signal(
                summary="NO_SIGNAL: long flip but direction config forbids LONG",
                reason="EMA_TREND_FILTER_FAILED",
                diagnostics={"supertrend_direction": current.direction},
            )
        stop_price = current.level  # final_lower band
        if stop_price >= entry_price:
            return build_no_signal(
                summary="NO_SIGNAL: long Supertrend level above entry",
                reason="INVALID_RISK_AFTER_FILL",
                diagnostics={"stop_candidate": stop_price, "entry": entry_price},
            )
        entry_direction_str = "LONG"
    elif flip_to_short:
        if direction not in ("SHORT", "BOTH"):
            return build_no_signal(
                summary="NO_SIGNAL: short flip but direction config forbids SHORT",
                reason="EMA_TREND_FILTER_FAILED",
                diagnostics={"supertrend_direction": current.direction},
            )
        stop_price = current.level  # final_upper band
        if stop_price <= entry_price:
            return build_no_signal(
                summary="NO_SIGNAL: short Supertrend level below entry",
                reason="INVALID_RISK_AFTER_FILL",
                diagnostics={"stop_candidate": stop_price, "entry": entry_price},
            )
        entry_direction_str = "SHORT"
    else:
        return build_no_signal(
            summary="NO_SIGNAL: ambiguous Supertrend direction",
            reason="EMA_TREND_FILTER_FAILED",
            diagnostics={"supertrend_direction": current.direction},
        )

    # R-multiple TP — engine recomputes via calculate_take_profit_price using
    # `exit.take_profit_r_multiple`, so this value is just an initial placeholder.
    risk_per_unit = abs(entry_price - stop_price)
    take_profit_r_multiple = float(exit.get("take_profit_r_multiple", 2.0))
    take_profit_price = (
        entry_price + take_profit_r_multiple * risk_per_unit
        if entry_direction_str == "LONG"
        else entry_price - take_profit_r_multiple * risk_per_unit
    )

    # ATR for downstream chandelier / atr-pct sizing
    atr_value = _compute_atr(bars, period)[-1]

    return build_entry_signal(
        summary=f"ENTER: supertrend flip to {entry_direction_str}",
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
            "supertrend_period": period,
            "supertrend_atr_multiple": atr_multiple,
            "supertrend_level": stop_price,
        },
    )


__all__ = ["STRATEGY_NAME", "compute_supertrend", "evaluate_supertrend_15m_v0"]
