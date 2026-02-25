from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest
from typing import Any
from unittest.mock import patch

from pybot.app.usecases.open_position import OpenPositionResult
from pybot.app.usecases.run_cycle import RunCycleDependencies, run_cycle
from pybot.domain.model.types import BotConfig, EntrySignalDecision, OhlcvBar, Pair, RunRecord, TradeRecord


def _build_config() -> BotConfig:
    return {
        "enabled": True,
        "network": "mainnet-beta",
        "pair": "SOL/USDC",
        "direction": "LONG_ONLY",
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
        _ = signature
        _ = ttl_seconds

    def clear_inflight_tx(self, signature: str) -> None:
        _ = signature


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


if __name__ == "__main__":
    unittest.main()
