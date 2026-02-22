from __future__ import annotations

from pybot.domain.model.types import (
    Direction,
    ExecutionConfig,
    ExitConfig,
    OhlcvBar,
    RiskConfig,
    StrategyConfig,
    StrategyDecision,
)
from pybot.domain.strategy.models.ema_trend_pullback_v0 import evaluate_ema_trend_pullback_v0
from pybot.domain.strategy.models.storm_short_v0 import evaluate_storm_short_v0


def evaluate_strategy_for_model(
    direction: Direction,
    bars: list[OhlcvBar],
    strategy: StrategyConfig,
    risk: RiskConfig,
    exit: ExitConfig,
    execution: ExecutionConfig,
) -> StrategyDecision:
    if strategy["name"] == "storm_short_v0":
        if direction != "SHORT_ONLY":
            raise ValueError("storm_short_v0 requires model.direction=SHORT_ONLY")
        return evaluate_storm_short_v0(bars=bars, strategy=strategy, risk=risk, exit=exit, execution=execution)

    if direction != "LONG_ONLY":
        raise ValueError("ema_trend_pullback_v0 requires model.direction=LONG_ONLY")
    return evaluate_ema_trend_pullback_v0(
        bars=bars,
        strategy=strategy,
        risk=risk,
        exit=exit,
        execution=execution,
    )
