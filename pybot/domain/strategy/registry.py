from __future__ import annotations

from pybot.domain.model.types import (
    ExecutionConfig,
    ExitConfig,
    ModelDirection,
    OhlcvBar,
    RiskConfig,
    StrategyConfig,
    StrategyDecision,
)
from pybot.domain.strategy.models.ema_trend_pullback_15m_v0 import (
    evaluate_ema_trend_pullback_15m_v0,
)
from pybot.domain.strategy.models.ema_trend_pullback_v0 import evaluate_ema_trend_pullback_v0
from pybot.domain.strategy.models.storm_short_v0 import evaluate_storm_short_v0


def evaluate_strategy_for_model(
    direction: ModelDirection,
    bars: list[OhlcvBar],
    strategy: StrategyConfig,
    risk: RiskConfig,
    exit: ExitConfig,
    execution: ExecutionConfig,
) -> StrategyDecision:
    if strategy["name"] == "storm_short_v0":
        if direction != "SHORT":
            raise ValueError("storm_short_v0 requires model.direction=SHORT")
        return evaluate_storm_short_v0(bars=bars, strategy=strategy, risk=risk, exit=exit, execution=execution)

    if strategy["name"] == "ema_trend_pullback_15m_v0":
        return evaluate_ema_trend_pullback_15m_v0(
            bars=bars,
            strategy=strategy,
            risk=risk,
            exit=exit,
            execution=execution,
        )

    if direction != "LONG":
        raise ValueError("ema_trend_pullback_v0 requires model.direction=LONG")
    return evaluate_ema_trend_pullback_v0(
        bars=bars,
        strategy=strategy,
        risk=risk,
        exit=exit,
        execution=execution,
    )
