from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest
from unittest.mock import patch

from apps.dex_bot.domain.model.types import NoSignalDecision, OhlcvBar
from apps.gmo_bot.domain.strategy.registry import evaluate_strategy_for_model
from research.src.domain.backtest_engine import run_backtest


def _build_no_signal() -> NoSignalDecision:
    return NoSignalDecision(type="NO_SIGNAL", summary="NO_SIGNAL: test", reason="TEST_REASON")


class GmoStrategyRegistryTest(unittest.TestCase):
    def test_registry_routes_15m_strategy_to_gmo_specific_model(self) -> None:
        with patch(
            "apps.gmo_bot.domain.strategy.registry.evaluate_ema_trend_pullback_15m_v0",
            return_value=_build_no_signal(),
        ) as mocked_gmo_strategy:
            decision = evaluate_strategy_for_model(
                direction="BOTH",
                bars=[],
                strategy={
                    "name": "ema_trend_pullback_15m_v0",
                    "ema_fast_period": 9,
                    "ema_slow_period": 34,
                    "swing_low_lookback_bars": 12,
                    "entry": "ON_BAR_CLOSE",
                },
                risk={
                    "max_loss_per_trade_pct": 1.2,
                    "max_trades_per_day": 4,
                    "volatile_atr_pct_threshold": 0.9,
                    "storm_atr_pct_threshold": 1.4,
                    "volatile_size_multiplier": 0.7,
                    "storm_size_multiplier": 0.35,
                },
                exit={"stop": "SWING_LOW", "take_profit_r_multiple": 1.8},
                execution={"min_notional_usdc": 5000.0, "slippage_bps": 3},
            )

        self.assertEqual("NO_SIGNAL", decision.type)
        mocked_gmo_strategy.assert_called_once()

    def test_backtest_uses_gmo_registry_for_gmo_broker(self) -> None:
        bars = [
            OhlcvBar(
                open_time=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                close_time=datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=10.0,
            ),
            OhlcvBar(
                open_time=datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
                close_time=datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=10.0,
            ),
        ]
        config = {
            "enabled": True,
            "network": "gmo-coin",
            "broker": "GMO_COIN",
            "pair": "SOL/JPY",
            "direction": "BOTH",
            "signal_timeframe": "15m",
            "strategy": {
                "name": "ema_trend_pullback_15m_v0",
                "ema_fast_period": 9,
                "ema_slow_period": 34,
                "swing_low_lookback_bars": 12,
                "entry": "ON_BAR_CLOSE",
            },
            "risk": {
                "max_loss_per_trade_pct": 1.2,
                "max_trades_per_day": 4,
                "volatile_atr_pct_threshold": 0.9,
                "storm_atr_pct_threshold": 1.4,
                "volatile_size_multiplier": 0.7,
                "storm_size_multiplier": 0.35,
            },
            "execution": {
                "mode": "LIVE",
                "broker": "GMO_COIN",
                "swap_provider": "GMO_COIN",
                "slippage_bps": 3,
                "min_notional_usdc": 5000.0,
                "min_notional_jpy": 5000.0,
                "initial_quote_balance": 1_000_000.0,
                "leverage_multiplier": 1.0,
                "margin_usage_ratio": 0.99,
            },
            "exit": {"stop": "SWING_LOW", "take_profit_r_multiple": 1.8},
            "meta": {"config_version": 1, "note": "test"},
        }

        with patch(
            "research.src.domain.backtest_engine.gmo_evaluate_strategy_for_model",
            return_value=_build_no_signal(),
        ) as mocked_gmo_registry, patch(
            "research.src.domain.backtest_engine.dex_evaluate_strategy_for_model",
            return_value=_build_no_signal(),
        ) as mocked_dex_registry:
            report = run_backtest(bars=bars, config=config)

        self.assertEqual(0, report.summary.decision_enter_count)
        self.assertEqual(2, report.summary.decision_no_signal_count)
        self.assertEqual(2, mocked_gmo_registry.call_count)
        self.assertEqual(0, mocked_dex_registry.call_count)


if __name__ == "__main__":
    unittest.main()
