from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from pybot.domain.model.types import ExecutionConfig, ExitConfig, OhlcvBar, RiskConfig, StrategyConfig
from pybot.domain.strategy.models.ema_trend_pullback_15m_v0 import (
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


class EmaTrendPullback15mStrategyTest(unittest.TestCase):
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
            "swap_provider": "JUPITER",
            "slippage_bps": 12,
            "min_notional_usdc": 20.0,
            "only_direct_routes": False,
        }
        self.bars = _build_bars(
            [
                100.0652,
                100.0945,
                100.0299,
                100.2566,
                100.1391,
                100.2203,
                100.4596,
                100.3719,
                100.4736,
                100.6003,
                100.4966,
                100.5282,
                100.6896,
                100.7504,
                100.9205,
                100.8633,
                100.8385,
                100.7629,
                100.8454,
                101.0949,
                101.2111,
                101.4008,
                101.4343,
                101.6127,
                101.5334,
                101.5298,
                101.6795,
                101.8498,
                101.8985,
                101.8136,
                101.8003,
                101.7643,
                101.7567,
                101.9605,
                101.2605,
                100.9605,
                101.2605,
                101.7105,
                101.9605,
                102.1605,
            ],
            spread=0.714089895353019,
        )

    def test_enter_baseline(self) -> None:
        with patch(
            "pybot.domain.strategy.models.ema_trend_pullback_15m_v0._evaluate_upper_timeframe_trend",
            return_value=("UP", 102.0, 101.0, 80),
        ):
            decision = evaluate_ema_trend_pullback_15m_v0(
                bars=self.bars,
                strategy=self.strategy,
                risk=self.risk,
                exit=self.exit,
                execution=self.execution,
            )
        self.assertEqual("ENTER", decision.type)

    def test_no_signal_when_storm_size_multiplier_is_zero(self) -> None:
        with (
            patch(
                "pybot.domain.strategy.models.ema_trend_pullback_15m_v0._evaluate_upper_timeframe_trend",
                return_value=("UP", 102.0, 101.0, 80),
            ),
            patch(
                "pybot.domain.strategy.models.ema_trend_pullback_15m_v0._resolve_position_size_multiplier",
                return_value=("STORM", 0.0),
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
        self.assertEqual("STORM_SIZE_MULTIPLIER_DISABLED", decision.reason)

    def test_no_signal_when_upper_trend_is_down(self) -> None:
        with patch(
            "pybot.domain.strategy.models.ema_trend_pullback_15m_v0._evaluate_upper_timeframe_trend",
            return_value=("DOWN", 100.0, 101.0, 80),
        ):
            decision = evaluate_ema_trend_pullback_15m_v0(
                bars=self.bars,
                strategy=self.strategy,
                risk=self.risk,
                exit=self.exit,
                execution=self.execution,
            )

        self.assertEqual("NO_SIGNAL", decision.type)
        self.assertEqual("UPPER_TREND_FILTER_FAILED", decision.reason)

    def test_no_signal_when_upper_trend_is_unavailable(self) -> None:
        with patch(
            "pybot.domain.strategy.models.ema_trend_pullback_15m_v0._evaluate_upper_timeframe_trend",
            return_value=("UNAVAILABLE", None, None, 12),
        ):
            decision = evaluate_ema_trend_pullback_15m_v0(
                bars=self.bars,
                strategy=self.strategy,
                risk=self.risk,
                exit=self.exit,
                execution=self.execution,
            )

        self.assertEqual("NO_SIGNAL", decision.type)
        self.assertEqual("UPPER_TREND_EMA_NOT_STABLE", decision.reason)


if __name__ == "__main__":
    unittest.main()
