from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest
from typing import Any
from unittest.mock import patch

from pybot.app.usecases.close_position import ClosePositionResult
from pybot.app.usecases.open_position import OpenPositionResult
from pybot.app.usecases.run_cycle import RunCycleDependencies, run_cycle
from pybot.domain.model.types import (
    BotConfig,
    EntrySignalDecision,
    NoSignalDecision,
    OhlcvBar,
    Pair,
    RunRecord,
    TradeRecord,
)
from pybot.domain.risk.short_regime_guard import SHORT_REGIME_GUARD_REASON
from pybot.domain.risk.short_stop_loss_cooldown import SHORT_STOP_LOSS_COOLDOWN_REASON


def _build_config() -> BotConfig:
    return {
        "enabled": True,
        "network": "mainnet-beta",
        "pair": "SOL/USDC",
        "direction": "LONG",
        "signal_timeframe": "15m",
        "strategy": {
            "name": "ema_trend_pullback_15m_v0",
            "ema_fast_period": 21,
            "ema_slow_period": 55,
            "swing_low_lookback_bars": 6,
            "entry": "ON_BAR_CLOSE",
        },
        "risk": {
            "max_loss_per_trade_pct": 0.5,
            "max_trades_per_day": 3,
            "volatile_atr_pct_threshold": 1.3,
            "storm_atr_pct_threshold": 1.4,
            "volatile_size_multiplier": 0.75,
            "storm_size_multiplier": 0.5,
        },
        "execution": {
            "mode": "LIVE",
            "swap_provider": "JUPITER",
            "slippage_bps": 12,
            "min_notional_usdc": 20.0,
            "only_direct_routes": False,
        },
        "exit": {"stop": "SWING_LOW", "take_profit_r_multiple": 1.5},
        "meta": {"config_version": 2, "note": "test"},
    }


class DummyExecution:
    def get_mark_price(self, pair: str) -> float:
        _ = pair
        return 100.0


class DummyLock:
    def __init__(self) -> None:
        self.locked = False
        self.entry_attempts: set[str] = set()
        self.inflight: set[str] = set()

    def acquire_runner_lock(self, ttl_seconds: int) -> bool:
        _ = ttl_seconds
        if self.locked:
            return False
        self.locked = True
        return True

    def release_runner_lock(self) -> None:
        self.locked = False

    def mark_entry_attempt(self, bar_close_time_iso: str, ttl_seconds: int) -> bool:
        _ = ttl_seconds
        if bar_close_time_iso in self.entry_attempts:
            return False
        self.entry_attempts.add(bar_close_time_iso)
        return True

    def has_entry_attempt(self, bar_close_time_iso: str) -> bool:
        return bar_close_time_iso in self.entry_attempts

    def set_inflight_tx(self, signature: str, ttl_seconds: int) -> None:
        self.inflight.add(signature)
        _ = ttl_seconds

    def has_inflight_tx(self, signature: str) -> bool:
        return signature in self.inflight

    def clear_inflight_tx(self, signature: str) -> None:
        self.inflight.discard(signature)


class DummyLogger:
    def info(self, message: str, context: dict[str, Any] | None = None) -> None:
        _ = message
        _ = context

    def warn(self, message: str, context: dict[str, Any] | None = None) -> None:
        _ = message
        _ = context

    def error(self, message: str, context: dict[str, Any] | None = None) -> None:
        _ = message
        _ = context


class DummyMarketData:
    def __init__(self, bars: list[OhlcvBar]) -> None:
        self.bars = bars

    def fetch_bars(self, pair: str, timeframe: str, limit: int) -> list[OhlcvBar]:
        _ = pair
        _ = timeframe
        _ = limit
        return self.bars


class DummyPersistence:
    def __init__(
        self,
        config: BotConfig,
        open_trade: TradeRecord | None = None,
        *,
        trades_today: int = 0,
        recent_closed_trades: list[TradeRecord] | None = None,
    ) -> None:
        self.config = config
        self.open_trade = open_trade
        self.trades_today = trades_today
        self.recent_closed_trades = recent_closed_trades or []
        self.saved_runs: list[RunRecord] = []

    def get_current_config(self) -> BotConfig:
        return self.config

    def create_trade(self, trade: TradeRecord) -> None:
        _ = trade

    def update_trade(self, trade_id: str, updates: dict) -> None:
        _ = trade_id
        _ = updates

    def find_open_trade(self, pair: Pair) -> TradeRecord | None:
        _ = pair
        return self.open_trade

    def count_trades_for_utc_day(self, pair: Pair, day_start_iso: str, day_end_iso: str) -> int:
        _ = pair
        _ = day_start_iso
        _ = day_end_iso
        return self.trades_today

    def list_recent_closed_trades(self, pair: Pair, limit: int) -> list[TradeRecord]:
        _ = pair
        return self.recent_closed_trades[:limit]

    def save_run(self, run: RunRecord) -> None:
        self.saved_runs.append(run)


class RunCycleSlippageSkipTest(unittest.TestCase):
    def test_slippage_skip_from_open_position_is_saved_as_skipped_run(self) -> None:
        config = _build_config()
        bar_close = datetime(2026, 2, 25, 10, 0, tzinfo=UTC)
        bars = [
            OhlcvBar(
                open_time=bar_close - timedelta(minutes=15),
                close_time=bar_close,
                open=99.5,
                high=100.5,
                low=99.0,
                close=100.0,
                volume=1000.0,
            )
        ]
        persistence = DummyPersistence(config)
        deps = RunCycleDependencies(
            execution=DummyExecution(),
            lock=DummyLock(),
            logger=DummyLogger(),
            market_data=DummyMarketData(bars),
            persistence=persistence,
            model_id="core_long_15m_v0",
            now_provider=lambda: datetime(2026, 2, 25, 10, 7, tzinfo=UTC),
        )
        decision = EntrySignalDecision(
            type="ENTER",
            summary="enter",
            ema_fast=101.0,
            ema_slow=100.0,
            entry_price=100.0,
            stop_price=99.0,
            take_profit_price=101.5,
        )

        with patch("pybot.app.usecases.run_cycle.evaluate_strategy_for_model", return_value=decision), patch(
            "pybot.app.usecases.run_cycle.open_position",
            return_value=OpenPositionResult(
                status="SKIPPED",
                trade_id="trade_slippage_skip",
                summary="SKIPPED: slippage exceeded",
            ),
        ):
            run = run_cycle(deps)

        self.assertEqual("SKIPPED", run["result"])
        self.assertEqual("SKIPPED: slippage exceeded", run["summary"])
        self.assertEqual("trade_slippage_skip", run["trade_id"])
        self.assertGreaterEqual(len(persistence.saved_runs), 1)
        self.assertEqual("SKIPPED", persistence.saved_runs[-1]["result"])

    def test_exit_slippage_skip_is_saved_as_skipped_run(self) -> None:
        config = _build_config()
        bar_close = datetime(2026, 2, 25, 10, 0, tzinfo=UTC)
        bars = [
            OhlcvBar(
                open_time=bar_close - timedelta(minutes=15),
                close_time=bar_close,
                open=99.5,
                high=100.5,
                low=99.0,
                close=100.0,
                volume=1000.0,
            )
        ]
        open_trade: TradeRecord = {
            "trade_id": "trade_exit_slippage_skip",
            "model_id": "core_long_15m_v0",
            "bar_close_time_iso": "2026-02-25T09:45:00Z",
            "pair": "SOL/USDC",
            "direction": "LONG",
            "state": "CONFIRMED",
            "config_version": 2,
            "execution": {},
            "position": {
                "status": "OPEN",
                "quantity_sol": 0.4,
                "entry_price": 99.0,
                "stop_price": 97.0,
                "take_profit_price": 99.5,
                "entry_time_iso": "2026-02-25T09:46:00Z",
            },
            "created_at": "2026-02-25T09:46:00Z",
            "updated_at": "2026-02-25T09:46:00Z",
        }
        persistence = DummyPersistence(config, open_trade=open_trade)
        deps = RunCycleDependencies(
            execution=DummyExecution(),
            lock=DummyLock(),
            logger=DummyLogger(),
            market_data=DummyMarketData(bars),
            persistence=persistence,
            model_id="core_long_15m_v0",
            now_provider=lambda: datetime(2026, 2, 25, 10, 7, tzinfo=UTC),
        )

        with patch(
            "pybot.app.usecases.run_cycle.close_position",
            return_value=ClosePositionResult(
                status="SKIPPED",
                trade_id="trade_exit_slippage_skip",
                summary="SKIPPED: exit slippage exceeded",
            ),
        ):
            run = run_cycle(deps)

        self.assertEqual("SKIPPED", run["result"])
        self.assertEqual("SKIPPED: exit slippage exceeded", run["summary"])
        self.assertEqual("trade_exit_slippage_skip", run["trade_id"])
        self.assertGreaterEqual(len(persistence.saved_runs), 1)
        self.assertEqual("SKIPPED", persistence.saved_runs[-1]["result"])

    def test_loss_streak_reduces_daily_trade_cap_and_skips_entry(self) -> None:
        config = _build_config()
        bar_close = datetime(2026, 2, 25, 10, 0, tzinfo=UTC)
        bars = [
            OhlcvBar(
                open_time=bar_close - timedelta(minutes=15),
                close_time=bar_close,
                open=99.5,
                high=100.5,
                low=99.0,
                close=100.0,
                volume=1000.0,
            )
        ]
        recent_closed_trades: list[TradeRecord] = [
            {"trade_id": "loss_1", "pair": "SOL/USDC", "state": "CLOSED", "close_reason": "STOP_LOSS"},
            {"trade_id": "loss_2", "pair": "SOL/USDC", "state": "CLOSED", "close_reason": "STOP_LOSS"},
            {"trade_id": "win_1", "pair": "SOL/USDC", "state": "CLOSED", "close_reason": "TAKE_PROFIT"},
        ]
        persistence = DummyPersistence(
            config,
            trades_today=2,
            recent_closed_trades=recent_closed_trades,
        )
        deps = RunCycleDependencies(
            execution=DummyExecution(),
            lock=DummyLock(),
            logger=DummyLogger(),
            market_data=DummyMarketData(bars),
            persistence=persistence,
            model_id="core_long_15m_v0",
            now_provider=lambda: datetime(2026, 2, 25, 10, 7, tzinfo=UTC),
        )

        run = run_cycle(deps)

        self.assertEqual("SKIPPED", run["result"])
        self.assertEqual("SKIPPED: max_trades_per_day reached", run["summary"])
        self.assertIn("LOSS_STREAK_2", run["reason"])
        self.assertIn("LOSS_STREAK_GE_2", run["reason"])
        self.assertEqual(2, run["metrics"]["effective_max_trades_per_day"])
        self.assertEqual(2, run["metrics"]["consecutive_stop_loss_streak"])

    def test_recent_take_profit_resets_loss_streak_for_dynamic_cap(self) -> None:
        config = _build_config()
        bar_close = datetime(2026, 2, 25, 10, 0, tzinfo=UTC)
        bars = [
            OhlcvBar(
                open_time=bar_close - timedelta(minutes=15),
                close_time=bar_close,
                open=99.5,
                high=100.5,
                low=99.0,
                close=100.0,
                volume=1000.0,
            )
        ]
        recent_closed_trades: list[TradeRecord] = [
            {"trade_id": "win_1", "pair": "SOL/USDC", "state": "CLOSED", "close_reason": "TAKE_PROFIT"},
            {"trade_id": "loss_1", "pair": "SOL/USDC", "state": "CLOSED", "close_reason": "STOP_LOSS"},
            {"trade_id": "loss_2", "pair": "SOL/USDC", "state": "CLOSED", "close_reason": "STOP_LOSS"},
        ]
        persistence = DummyPersistence(
            config,
            trades_today=2,
            recent_closed_trades=recent_closed_trades,
        )
        deps = RunCycleDependencies(
            execution=DummyExecution(),
            lock=DummyLock(),
            logger=DummyLogger(),
            market_data=DummyMarketData(bars),
            persistence=persistence,
            model_id="core_long_15m_v0",
            now_provider=lambda: datetime(2026, 2, 25, 10, 7, tzinfo=UTC),
        )
        no_signal = NoSignalDecision(type="NO_SIGNAL", summary="NO_SIGNAL: test", reason="TEST_REASON")

        with patch("pybot.app.usecases.run_cycle.evaluate_strategy_for_model", return_value=no_signal):
            run = run_cycle(deps)

        self.assertEqual("NO_SIGNAL", run["result"])
        self.assertEqual(0, run["metrics"]["consecutive_stop_loss_streak"])
        self.assertEqual(3, run["metrics"]["effective_max_trades_per_day"])
        self.assertEqual("BASE", run["metrics"]["dynamic_trade_cap_reason"])

    def test_entry_direction_from_strategy_diagnostics_is_passed_to_open_position(self) -> None:
        config = _build_config()
        bar_close = datetime(2026, 2, 25, 10, 0, tzinfo=UTC)
        bars = [
            OhlcvBar(
                open_time=bar_close - timedelta(minutes=15),
                close_time=bar_close,
                open=99.5,
                high=100.5,
                low=99.0,
                close=100.0,
                volume=1000.0,
            )
        ]
        persistence = DummyPersistence(config)
        deps = RunCycleDependencies(
            execution=DummyExecution(),
            lock=DummyLock(),
            logger=DummyLogger(),
            market_data=DummyMarketData(bars),
            persistence=persistence,
            model_id="core_long_15m_v0",
            now_provider=lambda: datetime(2026, 2, 25, 10, 7, tzinfo=UTC),
        )
        decision = EntrySignalDecision(
            type="ENTER",
            summary="enter short by upper trend",
            ema_fast=99.0,
            ema_slow=100.0,
            entry_price=100.0,
            stop_price=101.0,
            take_profit_price=98.0,
            diagnostics={"entry_direction": "SHORT"},
        )
        captured_entry_direction: dict[str, str | None] = {"value": None}

        def _capture_open_position(_deps: Any, input_data: Any) -> OpenPositionResult:
            captured_entry_direction["value"] = input_data.entry_direction
            return OpenPositionResult(
                status="OPENED",
                trade_id="trade_opened_short",
                summary="OPENED: short entry",
            )

        with patch("pybot.app.usecases.run_cycle.evaluate_strategy_for_model", return_value=decision), patch(
            "pybot.app.usecases.run_cycle.open_position",
            side_effect=_capture_open_position,
        ):
            run = run_cycle(deps)

        self.assertEqual("OPENED", run["result"])
        self.assertEqual("SHORT", captured_entry_direction["value"])
        self.assertEqual("SHORT", run["metrics"]["entry_direction"])

    def test_short_entry_is_blocked_during_post_stop_loss_cooldown(self) -> None:
        config = _build_config()
        bar_close = datetime(2026, 2, 25, 10, 0, tzinfo=UTC)
        bars = [
            OhlcvBar(
                open_time=bar_close - timedelta(minutes=15),
                close_time=bar_close,
                open=99.5,
                high=100.5,
                low=99.0,
                close=100.0,
                volume=1000.0,
            )
        ]
        recent_closed_trades: list[TradeRecord] = [
            {
                "trade_id": "short_loss_1",
                "pair": "SOL/USDC",
                "state": "CLOSED",
                "direction": "SHORT",
                "close_reason": "STOP_LOSS",
                "position": {"exit_time_iso": "2026-02-25T09:45:00Z"},
                "updated_at": "2026-02-25T09:45:10Z",
            }
        ]
        persistence = DummyPersistence(config, recent_closed_trades=recent_closed_trades)
        deps = RunCycleDependencies(
            execution=DummyExecution(),
            lock=DummyLock(),
            logger=DummyLogger(),
            market_data=DummyMarketData(bars),
            persistence=persistence,
            model_id="core_long_15m_v0",
            now_provider=lambda: datetime(2026, 2, 25, 10, 7, tzinfo=UTC),
        )
        decision = EntrySignalDecision(
            type="ENTER",
            summary="enter short by upper trend",
            ema_fast=99.0,
            ema_slow=100.0,
            entry_price=100.0,
            stop_price=101.0,
            take_profit_price=98.0,
            diagnostics={"entry_direction": "SHORT"},
        )

        with patch("pybot.app.usecases.run_cycle.evaluate_strategy_for_model", return_value=decision), patch(
            "pybot.app.usecases.run_cycle.open_position",
            side_effect=AssertionError("open_position should not be called during cooldown"),
        ):
            run = run_cycle(deps)

        self.assertEqual("NO_SIGNAL", run["result"])
        self.assertEqual(SHORT_STOP_LOSS_COOLDOWN_REASON, run["reason"])
        self.assertEqual(True, run["metrics"]["short_stop_loss_cooldown_active"])
        self.assertEqual(1, run["metrics"]["short_stop_loss_cooldown_bars_since"])
        self.assertEqual(7, run["metrics"]["short_stop_loss_cooldown_remaining_bars"])

    def test_short_entry_is_blocked_by_short_regime_guard(self) -> None:
        config = _build_config()
        bar_close = datetime(2026, 2, 25, 10, 0, tzinfo=UTC)
        bars = [
            OhlcvBar(
                open_time=bar_close - timedelta(minutes=15),
                close_time=bar_close,
                open=99.5,
                high=100.5,
                low=99.0,
                close=100.0,
                volume=1000.0,
            )
        ]
        recent_closed_trades: list[TradeRecord] = [
            {
                "trade_id": "short_loss_1",
                "pair": "SOL/USDC",
                "state": "CLOSED",
                "direction": "SHORT",
                "close_reason": "STOP_LOSS",
                "position": {"exit_time_iso": "2026-02-25T07:30:00Z"},
                "updated_at": "2026-02-25T07:30:10Z",
            },
            {
                "trade_id": "short_loss_2",
                "pair": "SOL/USDC",
                "state": "CLOSED",
                "direction": "SHORT",
                "close_reason": "STOP_LOSS",
                "position": {"exit_time_iso": "2026-02-25T07:15:00Z"},
                "updated_at": "2026-02-25T07:15:10Z",
            },
            {
                "trade_id": "short_loss_3",
                "pair": "SOL/USDC",
                "state": "CLOSED",
                "direction": "SHORT",
                "close_reason": "STOP_LOSS",
                "position": {"exit_time_iso": "2026-02-25T07:00:00Z"},
                "updated_at": "2026-02-25T07:00:10Z",
            },
            {
                "trade_id": "short_loss_4",
                "pair": "SOL/USDC",
                "state": "CLOSED",
                "direction": "SHORT",
                "close_reason": "STOP_LOSS",
                "position": {"exit_time_iso": "2026-02-25T06:45:00Z"},
                "updated_at": "2026-02-25T06:45:10Z",
            },
            {
                "trade_id": "short_loss_5",
                "pair": "SOL/USDC",
                "state": "CLOSED",
                "direction": "SHORT",
                "close_reason": "STOP_LOSS",
                "position": {"exit_time_iso": "2026-02-25T06:30:00Z"},
                "updated_at": "2026-02-25T06:30:10Z",
            },
            {
                "trade_id": "short_loss_6",
                "pair": "SOL/USDC",
                "state": "CLOSED",
                "direction": "SHORT",
                "close_reason": "STOP_LOSS",
                "position": {"exit_time_iso": "2026-02-25T06:15:00Z"},
                "updated_at": "2026-02-25T06:15:10Z",
            },
        ]
        persistence = DummyPersistence(config, recent_closed_trades=recent_closed_trades)
        deps = RunCycleDependencies(
            execution=DummyExecution(),
            lock=DummyLock(),
            logger=DummyLogger(),
            market_data=DummyMarketData(bars),
            persistence=persistence,
            model_id="core_long_15m_v0",
            now_provider=lambda: datetime(2026, 2, 25, 10, 7, tzinfo=UTC),
        )
        decision = EntrySignalDecision(
            type="ENTER",
            summary="enter short by upper trend",
            ema_fast=99.0,
            ema_slow=100.0,
            entry_price=100.0,
            stop_price=101.0,
            take_profit_price=98.0,
            diagnostics={"entry_direction": "SHORT"},
        )

        with patch("pybot.app.usecases.run_cycle.evaluate_strategy_for_model", return_value=decision), patch(
            "pybot.app.usecases.run_cycle.open_position",
            side_effect=AssertionError("open_position should not be called during short regime guard"),
        ):
            run = run_cycle(deps)

        self.assertEqual("NO_SIGNAL", run["result"])
        self.assertEqual(SHORT_REGIME_GUARD_REASON, run["reason"])
        self.assertEqual(True, run["metrics"]["short_regime_guard_active"])
        self.assertEqual(6, run["metrics"]["short_regime_guard_consecutive_stop_losses"])
        self.assertEqual(6, run["metrics"]["short_regime_guard_recent_short_trades"])
        self.assertEqual(0.0, run["metrics"]["short_regime_guard_recent_short_win_rate_pct"])
        self.assertEqual(86, run["metrics"]["short_regime_guard_remaining_bars"])


if __name__ == "__main__":
    unittest.main()
