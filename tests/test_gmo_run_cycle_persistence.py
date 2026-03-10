from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest
from unittest.mock import patch

from apps.dex_bot.domain.model.types import NoSignalDecision, OhlcvBar
from apps.gmo_bot.app.usecases.run_cycle import (
    RunCycleDependencies,
    _should_persist_run_record,
    run_cycle,
)
from apps.gmo_bot.domain.model.types import BotConfig, Pair, RunRecord, TradeRecord


def _build_config() -> BotConfig:
    return {
        "enabled": True,
        "broker": "GMO_COIN",
        "pair": "SOL/JPY",
        "direction": "BOTH",
        "signal_timeframe": "15m",
        "strategy": {
            "name": "ema_trend_pullback_15m_v0",
            "ema_fast_period": 9,
            "ema_slow_period": 34,
            "swing_low_lookback_bars": 8,
            "entry": "ON_BAR_CLOSE",
        },
        "risk": {
            "max_loss_per_trade_pct": 1.0,
            "max_trades_per_day": 3,
            "volatile_atr_pct_threshold": 1.3,
            "storm_atr_pct_threshold": 1.5,
            "volatile_size_multiplier": 0.8,
            "storm_size_multiplier": 0.4,
        },
        "execution": {
            "mode": "LIVE",
            "broker": "GMO_COIN",
            "slippage_bps": 3,
            "min_notional_jpy": 5000.0,
            "leverage_multiplier": 1.0,
            "margin_usage_ratio": 1.0,
        },
        "exit": {"stop": "SWING_LOW", "take_profit_r_multiple": 2.0},
        "meta": {"config_version": 1, "note": "test"},
    }


class _DummyExecution:
    def get_mark_price(self, pair: str) -> float:
        _ = pair
        return 10000.0


class _DummyLock:
    def __init__(self) -> None:
        self.locked = False
        self.entry_attempts: set[str] = set()

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

    def clear_entry_attempt(self, bar_close_time_iso: str) -> None:
        self.entry_attempts.discard(bar_close_time_iso)


class _DummyLogger:
    def info(self, message: str, context=None) -> None:
        _ = message
        _ = context

    def warn(self, message: str, context=None) -> None:
        _ = message
        _ = context

    def error(self, message: str, context=None) -> None:
        _ = message
        _ = context


class _DummyMarketData:
    def __init__(self, bars: list[OhlcvBar]) -> None:
        self.bars = bars

    def fetch_bars(self, pair: str, timeframe: str, limit: int) -> list[OhlcvBar]:
        _ = pair
        _ = timeframe
        _ = limit
        return self.bars


class _DummyPersistence:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
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
        return None

    def count_trades_for_utc_day(self, pair: Pair, day_start_iso: str, day_end_iso: str) -> int:
        _ = pair
        _ = day_start_iso
        _ = day_end_iso
        return 0

    def list_recent_closed_trades(self, pair: Pair, limit: int) -> list[TradeRecord]:
        _ = pair
        _ = limit
        return []

    def save_run(self, run: RunRecord) -> None:
        self.saved_runs.append(run)


class GmoRunCyclePersistenceTest(unittest.TestCase):
    def test_no_signal_run_is_not_persisted(self) -> None:
        config = _build_config()
        bar_close = datetime(2026, 3, 10, 7, 0, tzinfo=UTC)
        bars = [
            OhlcvBar(
                open_time=bar_close - timedelta(minutes=15),
                close_time=bar_close,
                open=10000.0,
                high=10100.0,
                low=9900.0,
                close=10050.0,
                volume=1000.0,
            )
        ]
        persistence = _DummyPersistence(config)
        deps = RunCycleDependencies(
            execution=_DummyExecution(),
            lock=_DummyLock(),
            logger=_DummyLogger(),
            market_data=_DummyMarketData(bars),
            persistence=persistence,
            model_id="gmo_ema_pullback_15m_both_v0",
            now_provider=lambda: datetime(2026, 3, 10, 7, 7, tzinfo=UTC),
        )

        with patch(
            "apps.gmo_bot.app.usecases.run_cycle.evaluate_strategy_for_model",
            return_value=NoSignalDecision(type="NO_SIGNAL", summary="NO_SIGNAL: test", reason="TEST_REASON"),
        ):
            run = run_cycle(deps)

        self.assertEqual("NO_SIGNAL", run["result"])
        self.assertEqual([], persistence.saved_runs)

    def test_failed_run_is_persisted(self) -> None:
        persistence = _DummyPersistence(_build_config())
        deps = RunCycleDependencies(
            execution=_DummyExecution(),
            lock=_DummyLock(),
            logger=_DummyLogger(),
            market_data=_DummyMarketData([]),
            persistence=persistence,
            model_id="gmo_ema_pullback_15m_both_v0",
            now_provider=lambda: datetime(2026, 3, 10, 7, 7, tzinfo=UTC),
        )

        run = run_cycle(deps)

        self.assertEqual("FAILED", run["result"])
        self.assertEqual(1, len(persistence.saved_runs))
        self.assertEqual("FAILED", persistence.saved_runs[0]["result"])

    def test_should_persist_run_record_matches_runtime_policy(self) -> None:
        self.assertTrue(_should_persist_run_record({"result": "OPENED"}))
        self.assertTrue(_should_persist_run_record({"result": "CLOSED"}))
        self.assertTrue(_should_persist_run_record({"result": "FAILED"}))
        self.assertFalse(_should_persist_run_record({"result": "NO_SIGNAL"}))
        self.assertFalse(_should_persist_run_record({"result": "HOLD"}))
        self.assertFalse(_should_persist_run_record({"result": "SKIPPED_ENTRY"}))
        self.assertFalse(_should_persist_run_record({"result": "SKIPPED", "summary": "SKIPPED: max_trades_per_day reached"}))


if __name__ == "__main__":
    unittest.main()
