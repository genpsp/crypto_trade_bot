from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import random
import unittest
from unittest.mock import patch

from apps.dex_bot.domain.model.types import EntrySignalDecision, NoSignalDecision, OhlcvBar
from apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0 import _build_upper_timeframe_closes
from research.src.domain.backtest_engine import run_backtest
from tests.test_research_validity import _config


def _random_walk_bars(count: int) -> list[OhlcvBar]:
    rng = random.Random(42)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    price = 100.0
    bars: list[OhlcvBar] = []
    for index in range(count):
        open_time = start + timedelta(minutes=15 * index)
        close_time = open_time + timedelta(minutes=15)
        price = max(1.0, price + rng.uniform(-0.5, 0.5))
        bars.append(OhlcvBar(open_time=open_time, close_time=close_time, open=price, high=price + 0.2, low=price - 0.2, close=price, volume=1000.0))
    return bars


class LookaheadAuditTest(unittest.TestCase):
    def test_backtest_strategy_window_never_contains_future_bars(self) -> None:
        bars = _random_walk_bars(8)
        config = _config()
        seen_last_times = []

        def fake_strategy(*, bars, **kwargs):
            seen_last_times.append(bars[-1].close_time)
            return NoSignalDecision(type="NO_SIGNAL", summary="none", reason="TEST")

        with patch("research.src.domain.backtest_engine.evaluate_strategy_for_model", side_effect=fake_strategy):
            run_backtest(bars, config)
        self.assertEqual([bar.close_time for bar in bars], seen_last_times)

    def test_shuffled_bars_do_not_create_large_positive_expectation_with_simple_strategy(self) -> None:
        bars = _random_walk_bars(80)
        shuffled_prices = [(bar.open, bar.high, bar.low, bar.close) for bar in bars]
        rng = random.Random(7)
        rng.shuffle(shuffled_prices)
        shuffled = [OhlcvBar(open_time=bar.open_time, close_time=bar.close_time, open=o, high=h, low=l, close=c, volume=bar.volume) for bar, (o, h, l, c) in zip(bars, shuffled_prices)]
        config = _config()
        config["risk"]["max_trades_per_day"] = 1

        def every_20th(*, bars, **kwargs):
            if len(bars) % 20 == 0:
                price = bars[-1].close
                return EntrySignalDecision(type="ENTER", summary="enter", ema_fast=price, ema_slow=price, entry_price=price, stop_price=price * 0.99, take_profit_price=price * 1.02)
            return NoSignalDecision(type="NO_SIGNAL", summary="none", reason="TEST")

        with patch("research.src.domain.backtest_engine.evaluate_strategy_for_model", side_effect=every_20th):
            report = run_backtest(shuffled, config)
        self.assertLess(abs(report.summary.total_scaled_pnl_pct), 10.0)

    def test_upper_timeframe_excludes_incomplete_future_bucket(self) -> None:
        bars = _random_walk_bars(15)  # up to 03:45 UTC, no completed 4h bucket close
        self.assertEqual([], _build_upper_timeframe_closes(bars, 240))
        bars.append(_random_walk_bars(16)[-1])  # 04:00 UTC close completes the 4h bucket
        self.assertEqual(1, len(_build_upper_timeframe_closes(bars, 240)))


if __name__ == "__main__":
    unittest.main()
