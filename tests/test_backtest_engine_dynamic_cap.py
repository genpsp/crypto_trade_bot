from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest
from unittest.mock import patch

from pybot.domain.model.types import BotConfig, EntrySignalDecision, OhlcvBar
from pybot.domain.risk.short_stop_loss_cooldown import SHORT_STOP_LOSS_COOLDOWN_REASON
from research.src.domain.backtest_engine import run_backtest


def _build_config(*, strategy_name: str) -> BotConfig:
    return {
        "enabled": True,
        "network": "mainnet-beta",
        "pair": "SOL/USDC",
        "direction": "LONG",
        "signal_timeframe": "15m",
        "strategy": {
            "name": strategy_name,
            "ema_fast_period": 21,
            "ema_slow_period": 55,
            "swing_low_lookback_bars": 6,
            "entry": "ON_BAR_CLOSE",
        },
        "risk": {
            "max_loss_per_trade_pct": 3.0,
            "max_trades_per_day": 3,
            "volatile_atr_pct_threshold": 1.3,
            "storm_atr_pct_threshold": 1.4,
            "volatile_size_multiplier": 0.75,
            "storm_size_multiplier": 0.5,
        },
        "execution": {
            "mode": "PAPER",
            "swap_provider": "JUPITER",
            "slippage_bps": 0,
            "min_notional_usdc": 20.0,
            "only_direct_routes": False,
        },
        "exit": {"stop": "SWING_LOW", "take_profit_r_multiple": 2.0},
        "meta": {"config_version": 2, "note": "test"},
    }


def _build_bars() -> list[OhlcvBar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars: list[OhlcvBar] = []
    bar_specs = [
        # first entry bar
        (100.0, 100.2, 99.8, 100.0),
        # stop-loss for first trade
        (100.0, 101.0, 98.8, 99.3),
        # second entry bar
        (99.3, 100.2, 99.0, 100.0),
        # stop-loss for second trade
        (100.0, 101.0, 98.7, 99.2),
        # bar that would be third entry if cap were not reduced
        (99.2, 100.2, 99.0, 99.8),
    ]
    for index, (open_, high, low, close) in enumerate(bar_specs):
        open_time = start + timedelta(minutes=15 * index)
        close_time = open_time + timedelta(minutes=15)
        bars.append(
            OhlcvBar(
                open_time=open_time,
                close_time=close_time,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=1_000.0,
            )
        )
    return bars


def _build_enter_decision() -> EntrySignalDecision:
    return EntrySignalDecision(
        type="ENTER",
        summary="enter",
        ema_fast=101.0,
        ema_slow=100.0,
        entry_price=100.0,
        stop_price=99.0,
        take_profit_price=102.0,
    )


class BacktestEngineDynamicCapTest(unittest.TestCase):
    def test_15m_strategy_reduces_daily_cap_after_two_consecutive_stop_losses(self) -> None:
        config = _build_config(strategy_name="ema_trend_pullback_15m_v0")
        bars = _build_bars()
        decisions = [_build_enter_decision(), _build_enter_decision()]

        with patch(
            "research.src.domain.backtest_engine.evaluate_strategy_for_model",
            side_effect=decisions,
        ) as mocked_strategy:
            report = run_backtest(bars=bars, config=config)

        self.assertEqual(2, mocked_strategy.call_count)
        self.assertEqual(2, report.summary.decision_enter_count)
        self.assertEqual(2, report.summary.closed_trades)
        self.assertEqual(0, report.summary.open_trades)
        self.assertEqual(2, report.summary.losses)
        self.assertEqual(0, report.summary.wins)
        self.assertEqual(1, report.no_signal_reason_counts["MAX_TRADES_PER_DAY_REACHED"])

    def test_non_15m_strategy_keeps_base_daily_cap_even_after_losses(self) -> None:
        config = _build_config(strategy_name="ema_trend_pullback_v0")
        bars = _build_bars()
        decisions = [_build_enter_decision(), _build_enter_decision(), _build_enter_decision()]

        with patch(
            "research.src.domain.backtest_engine.evaluate_strategy_for_model",
            side_effect=decisions,
        ) as mocked_strategy:
            report = run_backtest(bars=bars, config=config)

        self.assertEqual(3, mocked_strategy.call_count)
        self.assertEqual(3, report.summary.decision_enter_count)
        self.assertEqual(2, report.summary.closed_trades)
        self.assertEqual(1, report.summary.open_trades)
        self.assertNotIn("MAX_TRADES_PER_DAY_REACHED", report.no_signal_reason_counts)

    def test_short_entry_is_blocked_for_8_bars_after_short_stop_loss(self) -> None:
        config = _build_config(strategy_name="ema_trend_pullback_15m_v0")
        bars = [
            OhlcvBar(
                open_time=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
                close_time=datetime(2026, 1, 2, 0, 15, tzinfo=UTC),
                open=100.0,
                high=100.3,
                low=99.7,
                close=100.0,
                volume=1_000.0,
            ),
            OhlcvBar(
                open_time=datetime(2026, 1, 2, 0, 15, tzinfo=UTC),
                close_time=datetime(2026, 1, 2, 0, 30, tzinfo=UTC),
                open=100.0,
                high=101.3,
                low=99.6,
                close=100.8,
                volume=1_000.0,
            ),
            OhlcvBar(
                open_time=datetime(2026, 1, 2, 0, 30, tzinfo=UTC),
                close_time=datetime(2026, 1, 2, 0, 45, tzinfo=UTC),
                open=100.8,
                high=100.9,
                low=99.9,
                close=100.1,
                volume=1_000.0,
            ),
        ]
        short_enter = EntrySignalDecision(
            type="ENTER",
            summary="short enter",
            ema_fast=99.0,
            ema_slow=100.0,
            entry_price=100.0,
            stop_price=101.0,
            take_profit_price=98.0,
            diagnostics={"entry_direction": "SHORT"},
        )

        with patch(
            "research.src.domain.backtest_engine.evaluate_strategy_for_model",
            return_value=short_enter,
        ) as mocked_strategy:
            report = run_backtest(bars=bars, config=config)

        self.assertEqual(2, mocked_strategy.call_count)
        self.assertEqual(1, report.summary.decision_enter_count)
        self.assertEqual(1, report.summary.closed_trades)
        self.assertEqual(1, report.summary.losses)
        self.assertEqual(1, report.no_signal_reason_counts[SHORT_STOP_LOSS_COOLDOWN_REASON])


if __name__ == "__main__":
    unittest.main()
