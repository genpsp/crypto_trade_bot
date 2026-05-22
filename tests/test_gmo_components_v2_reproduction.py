"""Reproduction test for the ema_trend_pullback_15m_v2 component bundle.

When v2 is configured with the default bundle (FixedRExit, LegacyTightenedStop,
DiagnosticsSizing, NullRegimeGate), the engine must produce trades that are
byte-identical to running the legacy v0 strategy on the same bars. This test
guards the Done basis of S1.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from apps.dex_bot.domain.model.types import BotConfig, OhlcvBar
from apps.dex_bot.domain.strategy.shared.decision_builders import build_entry_signal, build_no_signal
from research.src.domain.backtest_engine import run_backtest


def _build_bars(count: int) -> list[OhlcvBar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars: list[OhlcvBar] = []
    price = 100.0
    for index in range(count):
        open_time = start + timedelta(minutes=15 * index)
        close_time = open_time + timedelta(minutes=15)
        # Trend up gently to keep ATR small and trades deterministic.
        price += 0.01
        bars.append(
            OhlcvBar(
                open_time=open_time,
                close_time=close_time,
                open=price - 0.05,
                high=price + 0.25,
                low=price - 0.25,
                close=price,
                volume=1_000.0,
            )
        )
    return bars


def _build_config(strategy_name: str, components: dict | None = None) -> BotConfig:
    strategy_config: dict = {
        "name": strategy_name,
        "ema_fast_period": 9,
        "ema_slow_period": 34,
        "swing_low_lookback_bars": 12,
        "entry": "ON_BAR_CLOSE",
    }
    if components is not None:
        strategy_config["components"] = components
    return {
        "enabled": True,
        "network": "mainnet-beta",
        "broker": "GMO_COIN",
        "pair": "SOL/JPY",
        "direction": "LONG",
        "signal_timeframe": "15m",
        "strategy": strategy_config,
        "risk": {
            "max_loss_per_trade_pct": 1.5,
            "max_trades_per_day": 4,
            "volatile_atr_pct_threshold": 5.0,
            "storm_atr_pct_threshold": 9.0,
            "volatile_size_multiplier": 1.0,
            "storm_size_multiplier": 1.0,
        },
        "execution": {
            "mode": "PAPER",
            "broker": "GMO_COIN",
            "swap_provider": "GMO_COIN",
            "slippage_bps": 0,
            "min_notional_usdc": 1.0,
            "only_direct_routes": False,
        },
        "exit": {"stop": "SWING_LOW", "take_profit_r_multiple": 2.0},
        "meta": {"config_version": 2, "note": "test"},
    }  # type: ignore[return-value]


def _stub_decisions_for_indices(enter_indices: set[int]):
    """Build a stub evaluator that returns ENTER at the requested bar indices."""

    def _stub(*, config, direction, bars, strategy, risk, exit, execution):
        # We don't know absolute bar index from inside the strategy call, so use
        # the close price of the last bar as a synthetic key.
        last_bar = bars[-1]
        last_close = last_bar.close
        # Encode index in close price during _build_bars: index ≈ (close - 100) / 0.01
        approx_index = round((last_close - 100.0) / 0.01)
        if approx_index in enter_indices:
            return build_entry_signal(
                summary="ENTER (stub)",
                ema_fast=last_close,
                ema_slow=last_close,
                entry_price=last_close,
                stop_price=last_close - 1.0,
                take_profit_price=last_close + 2.0,
                diagnostics={
                    "atr": 0.4,
                    "atr_pct": 0.4,
                    "position_size_multiplier": 1.0,
                    "entry_direction": "LONG",
                },
            )
        return build_no_signal(
            summary="NO_SIGNAL (stub)",
            reason="EMA_NOT_STABLE",
            diagnostics={},
        )

    return _stub


class V2DefaultBundleReproducesV0Test(unittest.TestCase):
    """Default-component v2 must yield byte-identical trades to v0 on the same bars."""

    def test_v2_default_bundle_matches_v0_trades(self) -> None:
        bars = _build_bars(300)
        enter_indices = {40, 90, 150, 210}

        stub = _stub_decisions_for_indices(enter_indices)

        with patch(
            "research.src.domain.backtest_engine._evaluate_strategy_for_backtest",
            side_effect=stub,
        ):
            v0_report = run_backtest(bars=bars, config=_build_config("ema_trend_pullback_15m_v0"))

        with patch(
            "research.src.domain.backtest_engine._evaluate_strategy_for_backtest",
            side_effect=stub,
        ):
            v2_report = run_backtest(bars=bars, config=_build_config("ema_trend_pullback_15m_v2"))

        # Same number of trades, same outcomes.
        self.assertGreater(len(v0_report.trades), 0)
        self.assertEqual(len(v0_report.trades), len(v2_report.trades))
        for trade_v0, trade_v2 in zip(v0_report.trades, v2_report.trades):
            self.assertEqual(trade_v0.entry_time, trade_v2.entry_time)
            self.assertEqual(trade_v0.exit_time, trade_v2.exit_time)
            self.assertEqual(trade_v0.exit_reason, trade_v2.exit_reason)
            self.assertEqual(trade_v0.entry_price, trade_v2.entry_price)
            self.assertEqual(trade_v0.stop_price, trade_v2.stop_price)
            self.assertEqual(trade_v0.take_profit_price, trade_v2.take_profit_price)
            self.assertEqual(trade_v0.exit_price, trade_v2.exit_price)
            self.assertEqual(trade_v0.pnl_pct, trade_v2.pnl_pct)
            self.assertEqual(trade_v0.scaled_pnl_pct, trade_v2.scaled_pnl_pct)
            self.assertEqual(trade_v0.r_multiple, trade_v2.r_multiple)
            self.assertEqual(trade_v0.holding_bars, trade_v2.holding_bars)


class V2BreakEvenExitMovesStopTest(unittest.TestCase):
    """A1 (BreakEvenExit) must move the stop to entry once price hits +1R."""

    def test_break_even_stop_avoids_stop_loss_when_price_revisits_entry(self) -> None:
        # Build a deterministic 3-bar trade: entry at bar N, +1R hit at N+1, then
        # price reverses back to entry at N+2. Without BE, the stop would fire on
        # any subsequent dip below entry-1. With BE @ 1R, the stop should ride up
        # to entry, and the revisit to entry triggers an exit at break-even.
        bars: list[OhlcvBar] = []
        start = datetime(2026, 1, 1, tzinfo=UTC)
        for index, (o, h, low, c) in enumerate(
            [
                (100.0, 100.1, 99.9, 100.0),   # 0: entry bar
                (100.0, 102.5, 99.9, 102.5),   # 1: +1R = +2 → BE arms; high hits 2.5
                (102.5, 102.5, 99.9, 100.0),   # 2: low touches 99.9 < entry; stop fires at entry
            ]
        ):
            open_time = start + timedelta(minutes=15 * index)
            bars.append(
                OhlcvBar(
                    open_time=open_time,
                    close_time=open_time + timedelta(minutes=15),
                    open=o,
                    high=h,
                    low=low,
                    close=c,
                    volume=1_000.0,
                )
            )

        stub_decisions = {0: True}

        def _stub(*, config, direction, bars, strategy, risk, exit, execution):
            last_bar = bars[-1]
            # Trigger ENTER on first bar only
            if last_bar.open == 100.0 and last_bar.high == 100.1:
                return build_entry_signal(
                    summary="ENTER (stub)",
                    ema_fast=100.0,
                    ema_slow=100.0,
                    entry_price=100.0,
                    stop_price=98.0,            # 2.0 risk → 1R = 2 reward
                    take_profit_price=110.0,    # far away so it does not fire
                    diagnostics={
                        "atr": 0.5,
                        "atr_pct": 0.5,
                        "position_size_multiplier": 1.0,
                        "entry_direction": "LONG",
                    },
                )
            return build_no_signal(
                summary="NO_SIGNAL (stub)",
                reason="EMA_NOT_STABLE",
                diagnostics={},
            )

        config_v2 = _build_config(
            "ema_trend_pullback_15m_v2",
            components={"exit_policy": {"type": "break_even", "trigger_r": 1.0}},
        )

        with patch(
            "research.src.domain.backtest_engine._evaluate_strategy_for_backtest",
            side_effect=_stub,
        ):
            report = run_backtest(bars=bars, config=config_v2)

        closed = [trade for trade in report.trades if trade.exit_reason != "OPEN"]
        self.assertEqual(1, len(closed))
        trade = closed[0]
        # Stop should have moved to entry (100.0) and the exit price should match.
        self.assertAlmostEqual(100.0, trade.stop_price or 0.0, places=4)
        self.assertEqual("STOP_LOSS", trade.exit_reason)
        # r_multiple at BE should be 0.0 (entry == exit) — but stop_price is now
        # entry, so risk_per_unit is 0; r_multiple falls back to 0.0 by engine
        # convention.
        self.assertEqual(0.0, trade.r_multiple)


class V2TimeExitClosesStaleTradeTest(unittest.TestCase):
    """A4 (TimeExit) must close the position at market after max_holding_bars."""

    def test_time_exit_closes_after_holding_threshold(self) -> None:
        bars: list[OhlcvBar] = []
        start = datetime(2026, 1, 1, tzinfo=UTC)
        # Entry at bar 0; subsequent bars chop sideways without hitting TP/SL.
        specs: list[tuple[float, float, float, float]] = [(100.0, 100.1, 99.9, 100.0)]
        for _ in range(20):
            specs.append((100.0, 100.5, 99.6, 100.0))
        for index, (o, h, low, c) in enumerate(specs):
            open_time = start + timedelta(minutes=15 * index)
            bars.append(
                OhlcvBar(
                    open_time=open_time,
                    close_time=open_time + timedelta(minutes=15),
                    open=o,
                    high=h,
                    low=low,
                    close=c,
                    volume=1_000.0,
                )
            )

        def _stub(*, config, direction, bars, strategy, risk, exit, execution):
            last_bar = bars[-1]
            if last_bar.open == 100.0 and last_bar.high == 100.1:
                return build_entry_signal(
                    summary="ENTER (stub)",
                    ema_fast=100.0,
                    ema_slow=100.0,
                    entry_price=100.0,
                    stop_price=98.0,
                    take_profit_price=110.0,
                    diagnostics={
                        "atr": 0.5,
                        "atr_pct": 0.5,
                        "position_size_multiplier": 1.0,
                        "entry_direction": "LONG",
                    },
                )
            return build_no_signal(
                summary="NO_SIGNAL (stub)",
                reason="EMA_NOT_STABLE",
                diagnostics={},
            )

        config_v2 = _build_config(
            "ema_trend_pullback_15m_v2",
            components={"exit_policy": {"type": "time_exit", "max_holding_bars": 5}},
        )

        with patch(
            "research.src.domain.backtest_engine._evaluate_strategy_for_backtest",
            side_effect=_stub,
        ):
            report = run_backtest(bars=bars, config=config_v2)

        closed = [trade for trade in report.trades if trade.exit_reason != "OPEN"]
        self.assertEqual(1, len(closed))
        trade = closed[0]
        self.assertEqual("TIME_EXIT", trade.exit_reason)
        self.assertEqual(5, trade.holding_bars)


class V2PartialTpRecordsTwoTradesTest(unittest.TestCase):
    """A2 (PartialTpExit) closes the partial portion at +partial_r, leaves a
    runner that exits at TP or SL, producing two BacktestTrade rows for one
    entry."""

    def test_partial_tp_then_runner_take_profit(self) -> None:
        bars: list[OhlcvBar] = []
        start = datetime(2026, 1, 1, tzinfo=UTC)
        specs: list[tuple[float, float, float, float]] = [
            (100.0, 100.1, 99.9, 100.0),   # 0: entry
            (100.0, 102.5, 99.9, 102.5),   # 1: +1R hit → partial fires
            (102.5, 105.5, 102.5, 105.0),  # 2: runner TP at 104 (entry+2R) hits
        ]
        for index, (o, h, low, c) in enumerate(specs):
            open_time = start + timedelta(minutes=15 * index)
            bars.append(
                OhlcvBar(
                    open_time=open_time,
                    close_time=open_time + timedelta(minutes=15),
                    open=o,
                    high=h,
                    low=low,
                    close=c,
                    volume=1_000.0,
                )
            )

        def _stub(*, config, direction, bars, strategy, risk, exit, execution):
            last_bar = bars[-1]
            if last_bar.open == 100.0 and last_bar.high == 100.1:
                return build_entry_signal(
                    summary="ENTER (stub)",
                    ema_fast=100.0,
                    ema_slow=100.0,
                    entry_price=100.0,
                    stop_price=98.0,            # 2.0 risk; +1R = 102; +2R = 104
                    take_profit_price=104.0,
                    diagnostics={
                        "atr": 0.5,
                        "atr_pct": 0.5,
                        "position_size_multiplier": 1.0,
                        "entry_direction": "LONG",
                    },
                )
            return build_no_signal(
                summary="NO_SIGNAL (stub)",
                reason="EMA_NOT_STABLE",
                diagnostics={},
            )

        config_v2 = _build_config(
            "ema_trend_pullback_15m_v2",
            components={
                "exit_policy": {
                    "type": "partial_tp",
                    "partial_r": 1.0,
                    "partial_fraction": 0.5,
                }
            },
        )
        # Loosen the max-loss cap so the legacy stop tightening keeps the swing
        # stop at 98.0 (matches what the stub returned).
        config_v2["risk"] = dict(config_v2["risk"])
        config_v2["risk"]["max_loss_per_trade_pct"] = 5.0

        with patch(
            "research.src.domain.backtest_engine._evaluate_strategy_for_backtest",
            side_effect=_stub,
        ):
            report = run_backtest(bars=bars, config=config_v2)

        records = [trade for trade in report.trades if trade.exit_reason != "OPEN"]
        self.assertEqual(2, len(records))
        partial, runner = records
        self.assertEqual("PARTIAL_TAKE_PROFIT", partial.exit_reason)
        self.assertAlmostEqual(102.0, partial.exit_price or 0.0, places=4)
        self.assertEqual("TAKE_PROFIT", runner.exit_reason)
        self.assertAlmostEqual(104.0, runner.exit_price or 0.0, places=4)
        # Partial row carries half the initial notional; runner row carries the
        # full initial notional (its scaled_pnl_pct denominator is the original
        # deployed capital so it stays comparable to non-partial trades).
        self.assertAlmostEqual(50.0, partial.effective_notional_usdc or 0.0, places=1)
        self.assertAlmostEqual(100.0, runner.effective_notional_usdc or 0.0, places=1)
        # Combined R reflects 0.5R from partial (entry-stop=2, profit=1 → 0.5R wait)
        # actually partial pnl=1 unit, risk=2 → 0.5R; but partial_r config says 1.0R so
        # partial fires at +1R (+2), so partial r_multiple = 1.0.
        self.assertAlmostEqual(1.0, partial.r_multiple or 0.0, places=4)
        self.assertAlmostEqual(2.0, runner.r_multiple or 0.0, places=4)


if __name__ == "__main__":
    unittest.main()
