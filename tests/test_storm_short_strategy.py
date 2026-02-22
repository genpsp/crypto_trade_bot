from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from pybot.domain.model.types import ExecutionConfig, ExitConfig, OhlcvBar, RiskConfig, StrategyConfig
from pybot.domain.strategy.models.storm_short_v0 import evaluate_storm_short_v0


def _build_bars(closes: list[float], spread: float) -> list[OhlcvBar]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars: list[OhlcvBar] = []
    for index, close in enumerate(closes):
        open_time = start + timedelta(hours=2 * index)
        close_time = open_time + timedelta(hours=2)
        bars.append(
            OhlcvBar(
                open_time=open_time,
                close_time=close_time,
                open=close + 0.2,
                high=close + spread / 2,
                low=close - spread / 2,
                close=close,
                volume=1_000.0,
            )
        )
    return bars


class StormShortStrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy: StrategyConfig = {
            "name": "storm_short_v0",
            "ema_fast_period": 5,
            "ema_slow_period": 13,
            "swing_low_lookback_bars": 6,
            "entry": "ON_BAR_CLOSE",
        }
        self.risk: RiskConfig = {
            "max_loss_per_trade_pct": 3.0,
            "max_trades_per_day": 1,
            "volatile_atr_pct_threshold": 1.3,
            "storm_atr_pct_threshold": 1.4,
            "volatile_size_multiplier": 0.8,
            "storm_size_multiplier": 0.1,
        }
        self.exit: ExitConfig = {
            "stop": "SWING_LOW",
            "take_profit_r_multiple": 2.4,
        }
        self.execution: ExecutionConfig = {
            "mode": "PAPER",
            "swap_provider": "JUPITER",
            "slippage_bps": 12,
            "min_notional_usdc": 20.0,
            "only_direct_routes": False,
        }

    def test_enter_when_storm_and_short_trend(self) -> None:
        closes = [110.0, 109.6, 110.0, 109.4, 109.8, 109.2, 109.5, 108.9, 109.2, 108.7, 109.0, 108.5, 108.8, 108.3, 108.6, 108.1]
        bars = _build_bars(closes, spread=1.6)
        bars[-3].high = bars[-3].high + 3.0
        risk_for_enter = dict(self.risk)
        risk_for_enter["max_loss_per_trade_pct"] = 4.0

        decision = evaluate_storm_short_v0(
            bars=bars,
            strategy=self.strategy,
            risk=risk_for_enter,
            exit=self.exit,
            execution=self.execution,
        )

        self.assertEqual("ENTER", decision.type)
        self.assertLess(decision.entry_price, decision.stop_price)
        self.assertLess(decision.take_profit_price, decision.entry_price)

    def test_no_signal_when_not_storm(self) -> None:
        closes = [110.0, 109.6, 109.1, 108.7, 108.2, 107.8, 107.3, 106.9, 106.5, 106.0, 105.6, 105.1, 104.7, 104.2, 103.8, 103.3]
        bars = _build_bars(closes, spread=0.4)

        decision = evaluate_storm_short_v0(
            bars=bars,
            strategy=self.strategy,
            risk=self.risk,
            exit=self.exit,
            execution=self.execution,
        )

        self.assertEqual("NO_SIGNAL", decision.type)
        self.assertEqual("STORM_REGIME_REQUIRED", decision.reason)

    def test_no_signal_when_short_trend_filter_fails(self) -> None:
        closes = [100.0, 100.4, 100.9, 101.3, 101.8, 102.2, 102.7, 103.1, 103.6, 104.0, 104.5, 104.9, 105.4, 105.8, 106.3, 106.7]
        bars = _build_bars(closes, spread=2.4)

        decision = evaluate_storm_short_v0(
            bars=bars,
            strategy=self.strategy,
            risk=self.risk,
            exit=self.exit,
            execution=self.execution,
        )

        self.assertEqual("NO_SIGNAL", decision.type)
        self.assertEqual("EMA_SHORT_TREND_FILTER_FAILED", decision.reason)


if __name__ == "__main__":
    unittest.main()
