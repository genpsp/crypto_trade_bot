"""Regime-switching meta strategy (Track ②).

単一ロジックでは chop と trend の両立ができない という仮説に対し
レジームで entry を排他ルーティングする上位層

- trend regime（ADX >= router_adx_trend_min）: ema_trend_pullback の entry に委譲
- chop regime（ADX < router_adx_trend_min）: mean_reversion の entry に委譲

両 single は単体では Gate 未達だが（trend は chop で削られ MR は trend で adverse
selection）排他適用で互いの不利レジームを除外できるかを検証する

委譲先は同じ strategy config dict を受け取り 各自のキーのみ読む
（ema は ema_* / MR は bb_* adx_chop_max など）
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
from apps.gmo_bot.domain.strategy.models.mean_reversion_15m_v0 import (
    _adx_at,
    evaluate_mean_reversion_15m_v0,
)


STRATEGY_NAME = "regime_router_15m_v0"


def evaluate_regime_router_15m_v0(
    *,
    bars: list[OhlcvBar],
    direction: ModelDirection,
    strategy: StrategyConfig,
    risk: RiskConfig,
    exit: ExitConfig,
    execution: ExecutionConfig,
) -> StrategyDecision:
    adx_period = int(strategy.get("router_adx_period", 14))
    adx_trend_min = float(strategy.get("router_adx_trend_min", 25.0))

    adx_value = _adx_at(bars, adx_period)
    regime = "trend" if adx_value >= adx_trend_min else "chop"

    if regime == "trend":
        decision = evaluate_ema_trend_pullback_15m_v0(
            bars=bars,
            direction=direction,
            strategy=strategy,
            risk=risk,
            exit=exit,
            execution=execution,
        )
    else:
        decision = evaluate_mean_reversion_15m_v0(
            bars=bars,
            direction=direction,
            strategy=strategy,
            risk=risk,
            exit=exit,
            execution=execution,
        )

    # ルーティング判断を diagnostics に残す（後段の regime 分解用）
    if decision.diagnostics is not None:
        decision.diagnostics["router_regime"] = regime
        decision.diagnostics["router_adx"] = adx_value
    return decision


__all__ = ["STRATEGY_NAME", "evaluate_regime_router_15m_v0"]
