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
from apps.dex_bot.domain.strategy.models.ema_trend_pullback_v0 import evaluate_ema_trend_pullback_v0
from apps.dex_bot.domain.strategy.models.storm_short_v0 import evaluate_storm_short_v0
from apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0 import (
    evaluate_ema_trend_pullback_15m_v0,
)
from apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v2 import (
    evaluate_ema_trend_pullback_15m_v2,
)
from apps.gmo_bot.domain.strategy.models.supertrend_15m_v0 import (
    evaluate_supertrend_15m_v0,
)
from apps.gmo_bot.domain.strategy.models.donchian_breakout_15m_v0 import (
    evaluate_donchian_breakout_15m_v0,
)
from apps.gmo_bot.domain.strategy.models.mean_reversion_15m_v0 import (
    evaluate_mean_reversion_15m_v0,
)


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
            direction=direction,
            strategy=strategy,
            risk=risk,
            exit=exit,
            execution=execution,
        )

    if strategy["name"] == "ema_trend_pullback_15m_v2":
        return evaluate_ema_trend_pullback_15m_v2(
            bars=bars,
            direction=direction,
            strategy=strategy,
            risk=risk,
            exit=exit,
            execution=execution,
        )

    if strategy["name"] == "supertrend_15m_v0":
        return evaluate_supertrend_15m_v0(
            bars=bars,
            direction=direction,
            strategy=strategy,
            risk=risk,
            exit=exit,
            execution=execution,
        )

    if strategy["name"] == "donchian_breakout_15m_v0":
        return evaluate_donchian_breakout_15m_v0(
            bars=bars,
            direction=direction,
            strategy=strategy,
            risk=risk,
            exit=exit,
            execution=execution,
        )

    if strategy["name"] == "mean_reversion_15m_v0":
        return evaluate_mean_reversion_15m_v0(
            bars=bars,
            direction=direction,
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
