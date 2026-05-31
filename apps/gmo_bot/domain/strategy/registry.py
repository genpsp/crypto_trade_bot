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
from apps.gmo_bot.domain.strategy.models.regime_router_15m_v0 import (
    evaluate_regime_router_15m_v0,
)
from apps.gmo_bot.domain.strategy.models.btc_leadlag_15m_v0 import (
    evaluate_btc_leadlag_15m_v0,
)

# Per-strategy minimum 15m bar history required by run_cycle's OHLCV fetch.
# Strategies that derive an upper timeframe (e.g. 4h EMA slow period = 34
# requires 34 * 16 = 544 15m bars) need a larger window than the 300-bar default.
# Keep this aligned with each strategy's evaluator: under-fetching produces a
# silent permanent NO_SIGNAL ("UPPER_TREND_EMA_NOT_STABLE") loop in production.
_DEFAULT_REQUIRED_HISTORY_BARS = 300
_UPPER_TREND_REQUIRED_HISTORY_BARS = 600
_REQUIRED_HISTORY_BARS_BY_STRATEGY: dict[str, int] = {
    "ema_trend_pullback_15m_v0": _UPPER_TREND_REQUIRED_HISTORY_BARS,
    "ema_trend_pullback_15m_v2": _UPPER_TREND_REQUIRED_HISTORY_BARS,
    # router は trend regime で ema(上位足)に委譲しうるため同じ history が要る
    "regime_router_15m_v0": _UPPER_TREND_REQUIRED_HISTORY_BARS,
}


def resolve_required_history_bars(strategy: StrategyConfig) -> int:
    return _REQUIRED_HISTORY_BARS_BY_STRATEGY.get(strategy["name"], _DEFAULT_REQUIRED_HISTORY_BARS)


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

    if strategy["name"] == "regime_router_15m_v0":
        return evaluate_regime_router_15m_v0(
            bars=bars,
            direction=direction,
            strategy=strategy,
            risk=risk,
            exit=exit,
            execution=execution,
        )

    if strategy["name"] == "btc_leadlag_15m_v0":
        return evaluate_btc_leadlag_15m_v0(
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
