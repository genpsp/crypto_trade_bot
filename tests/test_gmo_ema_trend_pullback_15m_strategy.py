from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from apps.dex_bot.domain.model.types import ExecutionConfig, ExitConfig, OhlcvBar, RiskConfig, StrategyConfig
from apps.dex_bot.domain.strategy.shared.market_context import EmaMarketContext
from apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0 import (
    evaluate_ema_trend_pullback_15m_v0,
)


def _build_bars(closes: list[float], spread: float) -> list[OhlcvBar]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars: list[OhlcvBar] = []
    for index, close in enumerate(closes):
        open_time = start + timedelta(minutes=15 * index)
        close_time = open_time + timedelta(minutes=15)
        bars.append(
            OhlcvBar(
                open_time=open_time,
                close_time=close_time,
                open=close - 0.1,
                high=close + spread / 2,
                low=close - spread / 2,
                close=close,
                volume=1_000.0,
            )
        )
    return bars


class GmoEmaTrendPullback15mStrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy: StrategyConfig = {
            "name": "ema_trend_pullback_15m_v0",
            "ema_fast_period": 9,
            "ema_slow_period": 34,
            "swing_low_lookback_bars": 12,
            "entry": "ON_BAR_CLOSE",
        }
        self.risk: RiskConfig = {
            "max_loss_per_trade_pct": 1.2,
            "max_trades_per_day": 4,
            "volatile_atr_pct_threshold": 0.9,
            "storm_atr_pct_threshold": 1.4,
            "volatile_size_multiplier": 0.7,
            "storm_size_multiplier": 0.2,
        }
        self.exit: ExitConfig = {
            "stop": "SWING_LOW",
            "take_profit_r_multiple": 1.8,
        }
        self.execution: ExecutionConfig = {
            "mode": "PAPER",
            "broker": "GMO_COIN",
            "swap_provider": "GMO_COIN",
            "slippage_bps": 3,
            "min_notional_usdc": 5000.0,
        }
        self.bars = _build_bars(
            [101.5, 101.2, 100.8, 100.5, 100.2, 100.0, 99.8],
            spread=0.8,
        )

    def test_no_signal_when_short_upper_fast_slope_is_too_positive(self) -> None:
        short_context = EmaMarketContext(
            closes=[101.5, 101.2, 100.8, 100.5, 100.2, 100.0, 99.8],
            highs=[102.0, 101.7, 101.3, 100.9, 100.6, 100.3, 100.1],
            lows=[101.0, 100.8, 100.4, 100.1, 99.9, 99.7, 99.5],
            ema_fast_by_bar=[101.3, 101.0, 100.7, 100.4, 100.2, 100.1, 100.0],
            ema_fast=100.0,
            ema_slow=101.0,
            entry_price=99.6,
            previous_close=100.0,
            previous_ema_fast=100.1,
        )
        with (
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0.calculate_minimum_bars",
                return_value=1,
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0._evaluate_upper_timeframe_trend",
                return_value=("DOWN", 97.0, 100.0, 80),
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0._calculate_upper_trend_regime_metrics",
                return_value=(0.12, 0.0),
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0.SHORT_UPPER_FAST_SLOPE_MAX_PCT",
                0.1,
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0.build_ema_market_context",
                return_value=short_context,
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0.atr_series",
                return_value=[0.2],
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0.rsi_series",
                return_value=[45.0],
            ),
        ):
            decision = evaluate_ema_trend_pullback_15m_v0(
                bars=self.bars,
                strategy=self.strategy,
                risk=self.risk,
                exit=self.exit,
                execution=self.execution,
            )

        self.assertEqual("NO_SIGNAL", decision.type)
        self.assertEqual("SHORT_UPPER_FAST_SLOPE_TOO_POSITIVE", decision.reason)

    def test_no_signal_when_short_upper_close_drift_is_too_positive(self) -> None:
        short_context = EmaMarketContext(
            closes=[101.5, 101.2, 100.8, 100.5, 100.2, 100.0, 99.8],
            highs=[102.0, 101.7, 101.3, 100.9, 100.6, 100.3, 100.1],
            lows=[101.0, 100.8, 100.4, 100.1, 99.9, 99.7, 99.5],
            ema_fast_by_bar=[101.3, 101.0, 100.7, 100.4, 100.2, 100.1, 100.0],
            ema_fast=100.0,
            ema_slow=101.0,
            entry_price=99.6,
            previous_close=100.0,
            previous_ema_fast=100.1,
        )
        with (
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0.calculate_minimum_bars",
                return_value=1,
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0._evaluate_upper_timeframe_trend",
                return_value=("DOWN", 97.0, 100.0, 80),
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0._calculate_upper_trend_regime_metrics",
                return_value=(0.0, 0.75),
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0.build_ema_market_context",
                return_value=short_context,
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0.atr_series",
                return_value=[0.2],
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0.rsi_series",
                return_value=[45.0],
            ),
        ):
            decision = evaluate_ema_trend_pullback_15m_v0(
                bars=self.bars,
                strategy=self.strategy,
                risk=self.risk,
                exit=self.exit,
                execution=self.execution,
            )

        self.assertEqual("NO_SIGNAL", decision.type)
        self.assertEqual("SHORT_UPPER_CLOSE_DRIFT_TOO_POSITIVE", decision.reason)

    def test_no_signal_when_long_atr_regime_is_too_hot(self) -> None:
        long_context = EmaMarketContext(
            closes=[100.0, 100.2, 100.4, 100.6, 100.8, 101.0, 101.2],
            highs=[100.3, 100.5, 100.7, 100.9, 101.1, 101.3, 101.5],
            lows=[99.7, 99.9, 100.1, 100.3, 100.5, 100.7, 100.9],
            ema_fast_by_bar=[100.0, 100.1, 100.2, 100.4, 100.6, 100.8, 101.0],
            ema_fast=101.0,
            ema_slow=100.5,
            entry_price=101.2,
            previous_close=101.0,
            previous_ema_fast=100.8,
        )
        with (
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0.calculate_minimum_bars",
                return_value=1,
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0._evaluate_upper_timeframe_trend",
                return_value=("UP", 101.0, 100.0, 80),
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0._calculate_upper_trend_regime_metrics",
                return_value=(0.25, 1.2),
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0.build_ema_market_context",
                return_value=long_context,
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0.atr_series",
                return_value=[0.8],
            ),
            patch(
                "apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0.rsi_series",
                return_value=[60.0],
            ),
        ):
            decision = evaluate_ema_trend_pullback_15m_v0(
                bars=self.bars,
                strategy=self.strategy,
                risk=self.risk,
                exit=self.exit,
                execution=self.execution,
            )

        self.assertEqual("NO_SIGNAL", decision.type)
        self.assertEqual("LONG_ATR_REGIME_TOO_HOT", decision.reason)


if __name__ == "__main__":
    unittest.main()
