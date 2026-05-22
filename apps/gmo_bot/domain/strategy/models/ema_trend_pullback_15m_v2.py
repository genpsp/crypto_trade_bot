"""v2 strategy entry point: same entry signal as v0, but composable.

The signal evaluation itself is identical to v0 — v2's value is that the engine,
when dispatched to this strategy, drives a per-bar ExitPolicy / RegimeGate /
StopPolicy / SizingPolicy bundle resolved from `strategy.components`. The
default bundle reproduces v0 numerics; alternate components are swapped in via
config without touching strategy code.

See docs/gmo_bot_logic_exploration_plan.md §2 for the design.
"""

from __future__ import annotations

from apps.dex_bot.domain.model.types import (
    ExecutionConfig,
    ExitConfig,
    ModelDirection,
    OhlcvBar,
    RiskConfig,
    StrategyConfig,
    StrategyDecision,
)
from apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0 import (
    evaluate_ema_trend_pullback_15m_v0,
)


STRATEGY_NAME = "ema_trend_pullback_15m_v2"


def evaluate_ema_trend_pullback_15m_v2(
    *,
    bars: list[OhlcvBar],
    direction: ModelDirection,
    strategy: StrategyConfig,
    risk: RiskConfig,
    exit: ExitConfig,
    execution: ExecutionConfig,
) -> StrategyDecision:
    """Delegate the entry decision to v0; the v2 surface is the engine-level
    component bundle, not the signal itself."""
    return evaluate_ema_trend_pullback_15m_v0(
        bars=bars,
        direction=direction,
        strategy=strategy,
        risk=risk,
        exit=exit,
        execution=execution,
    )


__all__ = ["STRATEGY_NAME", "evaluate_ema_trend_pullback_15m_v2"]
