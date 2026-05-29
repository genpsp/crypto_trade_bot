from __future__ import annotations

import unittest
from unittest.mock import patch

from apps.dex_bot.domain.model.types import NoSignalDecision
from apps.dex_bot.domain.strategy.registry import evaluate_strategy_for_model, resolve_required_history_bars

_STRATEGY_BASE = {
    "ema_fast_period": 9,
    "ema_slow_period": 34,
    "swing_low_lookback_bars": 8,
    "entry": "ON_BAR_CLOSE",
}
_RISK = {
    "max_loss_per_trade_pct": 1.2,
    "max_trades_per_day": 3,
    "volatile_atr_pct_threshold": 1.3,
    "storm_atr_pct_threshold": 1.5,
    "volatile_size_multiplier": 0.8,
    "storm_size_multiplier": 0.4,
}
_EXIT = {"stop": "SWING_LOW", "take_profit_r_multiple": 2.0}
_EXECUTION = {"min_notional_usdc": 20.0, "slippage_bps": 15}


def _no_signal() -> NoSignalDecision:
    return NoSignalDecision(type="NO_SIGNAL", summary="test", reason="TEST")


class ResolveRequiredHistoryBarsTest(unittest.TestCase):
    # gmo_bot 側 2026-05-22 インシデント（cd5b5a8）の同型バグが dex_bot にも残っていたので回帰ガードを敷く
    # strategy.name を v0 → v2 にリネームすると 15m 取得が 600 → 300 に落ち
    # 上位足 EMA slow=34 を計算できず NO_SIGNAL ("UPPER_TREND_EMA_NOT_STABLE") を永久に返す
    # 上位足を導出する戦略を新たに追加する際は必ずここに登録する
    def test_v0_requires_upper_trend_history(self) -> None:
        self.assertEqual(600, resolve_required_history_bars({"name": "ema_trend_pullback_15m_v0"}))

    def test_v2_requires_upper_trend_history(self) -> None:
        self.assertEqual(600, resolve_required_history_bars({"name": "ema_trend_pullback_15m_v2"}))

    def test_default_for_unregistered_strategy(self) -> None:
        self.assertEqual(300, resolve_required_history_bars({"name": "storm_short_v0"}))
        self.assertEqual(300, resolve_required_history_bars({"name": "ema_trend_pullback_v0"}))
        self.assertEqual(300, resolve_required_history_bars({"name": "supertrend_15m_v0"}))
        self.assertEqual(300, resolve_required_history_bars({"name": "donchian_breakout_15m_v0"}))
        self.assertEqual(300, resolve_required_history_bars({"name": "mean_reversion_15m_v0"}))


class EvaluateStrategyForModelDispatchTest(unittest.TestCase):
    def test_dispatch_ema_trend_pullback_15m_v2(self) -> None:
        strategy = {**_STRATEGY_BASE, "name": "ema_trend_pullback_15m_v2"}
        with patch(
            "apps.dex_bot.domain.strategy.registry.evaluate_ema_trend_pullback_15m_v2",
            return_value=_no_signal(),
        ) as mocked:
            decision = evaluate_strategy_for_model(
                direction="BOTH",
                bars=[],
                strategy=strategy,
                risk=_RISK,
                exit=_EXIT,
                execution=_EXECUTION,
            )
        self.assertEqual("NO_SIGNAL", decision.type)
        mocked.assert_called_once()

    def test_dispatch_supertrend_15m_v0(self) -> None:
        strategy = {**_STRATEGY_BASE, "name": "supertrend_15m_v0"}
        with patch(
            "apps.dex_bot.domain.strategy.registry.evaluate_supertrend_15m_v0",
            return_value=_no_signal(),
        ) as mocked:
            decision = evaluate_strategy_for_model(
                direction="LONG",
                bars=[],
                strategy=strategy,
                risk=_RISK,
                exit=_EXIT,
                execution=_EXECUTION,
            )
        self.assertEqual("NO_SIGNAL", decision.type)
        mocked.assert_called_once()

    def test_dispatch_donchian_breakout_15m_v0(self) -> None:
        strategy = {**_STRATEGY_BASE, "name": "donchian_breakout_15m_v0"}
        with patch(
            "apps.dex_bot.domain.strategy.registry.evaluate_donchian_breakout_15m_v0",
            return_value=_no_signal(),
        ) as mocked:
            decision = evaluate_strategy_for_model(
                direction="LONG",
                bars=[],
                strategy=strategy,
                risk=_RISK,
                exit=_EXIT,
                execution=_EXECUTION,
            )
        self.assertEqual("NO_SIGNAL", decision.type)
        mocked.assert_called_once()

    def test_dispatch_mean_reversion_15m_v0(self) -> None:
        strategy = {**_STRATEGY_BASE, "name": "mean_reversion_15m_v0"}
        with patch(
            "apps.dex_bot.domain.strategy.registry.evaluate_mean_reversion_15m_v0",
            return_value=_no_signal(),
        ) as mocked:
            decision = evaluate_strategy_for_model(
                direction="LONG",
                bars=[],
                strategy=strategy,
                risk=_RISK,
                exit=_EXIT,
                execution=_EXECUTION,
            )
        self.assertEqual("NO_SIGNAL", decision.type)
        mocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
