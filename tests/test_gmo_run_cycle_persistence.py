from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from apps.dex_bot.domain.model.types import NoSignalDecision, OhlcvBar
from apps.gmo_bot.app.usecases.run_cycle import (
    RunCycleDependencies,
    _should_persist_run_record,
    run_cycle,
)
from apps.gmo_bot.domain.model.types import BotConfig, Pair, RunRecord, TradeRecord


def _merge(dst: dict, src: dict) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge(dst[key], value)
            continue
        dst[key] = deepcopy(value)


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

    def confirm_order(self, order_id: int, timeout_ms: int):
        _ = order_id
        _ = timeout_ms
        raise NotImplementedError

    def get_order(self, order_id: int):
        _ = order_id
        raise NotImplementedError


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


class _MaintenanceMarketData:
    def fetch_bars(self, pair: str, timeframe: str, limit: int) -> list[OhlcvBar]:
        _ = pair
        _ = timeframe
        _ = limit
        raise RuntimeError("GMO API error status=5: ERR-5201: MAINTENANCE. Please wait for a while")


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


class _OpenTradePersistence(_DummyPersistence):
    def __init__(self, config: BotConfig, trade: TradeRecord) -> None:
        super().__init__(config)
        self.trade = trade

    def find_open_trade(self, pair: Pair) -> TradeRecord | None:
        _ = pair
        return self.trade

    def update_trade(self, trade_id: str, updates: dict) -> None:
        if trade_id != self.trade["trade_id"]:
            raise KeyError(trade_id)
        _merge(self.trade, updates)


class _StaticPriceExecution(_DummyExecution):
    def __init__(self, mark_price: float) -> None:
        self.mark_price = mark_price

    def get_mark_price(self, pair: str) -> float:
        _ = pair
        return self.mark_price


class _RateLimitPriceExecution(_DummyExecution):
    def get_mark_price(self, pair: str) -> float:
        _ = pair
        raise RuntimeError("GMO API error status=4: ERR-5003: Requests are too many.")


class GmoRunCyclePersistenceTest(unittest.TestCase):
    def test_partial_close_run_is_persisted_without_failed_result(self) -> None:
        config = _build_config()
        trade: TradeRecord = {
            "trade_id": "trade_1",
            "pair": "SOL/JPY",
            "direction": "SHORT",
            "state": "CONFIRMED",
            "position": {
                "status": "OPEN",
                "stop_price": 9990.0,
                "take_profit_price": 9990.0,
            },
        }
        persistence = _OpenTradePersistence(config, trade)
        deps = RunCycleDependencies(
            execution=_DummyExecution(),
            lock=_DummyLock(),
            logger=_DummyLogger(),
            market_data=_DummyMarketData([]),
            persistence=persistence,
            model_id="gmo_ema_pullback_15m_both_v0",
            now_provider=lambda: datetime(2026, 3, 10, 7, 7, tzinfo=UTC),
        )

        with patch(
            "apps.gmo_bot.app.usecases.run_cycle.close_position",
            return_value=SimpleNamespace(
                status="PARTIALLY_CLOSED",
                summary="PARTIALLY_CLOSED: partial close detected: expected 0.6 SOL, got 0.5 SOL",
            ),
        ):
            run = run_cycle(deps)

        self.assertEqual("PARTIALLY_CLOSED", run["result"])
        self.assertEqual(1, len(persistence.saved_runs))
        self.assertEqual("PARTIALLY_CLOSED", persistence.saved_runs[0]["result"])

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

    def test_bar_close_mismatch_is_skipped_and_does_not_block_retry(self) -> None:
        config = _build_config()
        persistence = _DummyPersistence(config)
        lock = _DummyLock()
        bars = [
            OhlcvBar(
                open_time=datetime(2026, 3, 10, 7, 15, tzinfo=UTC),
                close_time=datetime(2026, 3, 10, 7, 30, tzinfo=UTC),
                open=10000.0,
                high=10100.0,
                low=9900.0,
                close=10050.0,
                volume=1000.0,
            )
        ]
        deps = RunCycleDependencies(
            execution=_DummyExecution(),
            lock=lock,
            logger=_DummyLogger(),
            market_data=_DummyMarketData(bars),
            persistence=persistence,
            model_id="gmo_ema_pullback_15m_both_v0",
            now_provider=lambda: datetime(2026, 3, 10, 7, 50, tzinfo=UTC),
        )

        first = run_cycle(deps)
        second = run_cycle(deps)
        expected_bar_close_iso = datetime(2026, 3, 10, 7, 45, tzinfo=UTC).isoformat().replace("+00:00", "Z")

        self.assertEqual("SKIPPED", first["result"])
        self.assertEqual(expected_bar_close_iso, first["bar_close_time_iso"])
        self.assertEqual("SKIPPED", second["result"])
        self.assertFalse(lock.has_entry_attempt(expected_bar_close_iso))
        self.assertEqual([], persistence.saved_runs)

    def test_market_data_maintenance_is_skipped_and_persisted(self) -> None:
        persistence = _DummyPersistence(_build_config())
        deps = RunCycleDependencies(
            execution=_DummyExecution(),
            lock=_DummyLock(),
            logger=_DummyLogger(),
            market_data=_MaintenanceMarketData(),
            persistence=persistence,
            model_id="gmo_ema_pullback_15m_both_v0",
            now_provider=lambda: datetime(2026, 3, 14, 0, 20, tzinfo=UTC),
        )

        run = run_cycle(deps)

        self.assertEqual("SKIPPED", run["result"])
        self.assertEqual("SKIPPED: market data unavailable (maintenance)", run["summary"])
        self.assertIn("ERR-5201", str(run.get("reason")))
        self.assertEqual(1, len(persistence.saved_runs))
        self.assertEqual("SKIPPED", persistence.saved_runs[0]["result"])
        self.assertEqual("SKIPPED: market data unavailable (maintenance)", persistence.saved_runs[0]["summary"])

    def test_active_stop_loss_reconciliation_pending_returns_hold(self) -> None:
        config = _build_config()
        trade: TradeRecord = {
            "trade_id": "trade_stop_pending",
            "pair": "SOL/JPY",
            "direction": "LONG",
            "state": "CONFIRMED",
            "execution": {
                "take_profit_order_status": "CLIENT_MANAGED",
                "stop_loss_order_id": 123,
                "stop_loss_order_status": "WAITING",
            },
            "position": {
                "status": "OPEN",
                "stop_price": 10010.0,
                "take_profit_price": 10100.0,
            },
        }
        persistence = _OpenTradePersistence(config, trade)
        deps = RunCycleDependencies(
            execution=_DummyExecution(),
            lock=_DummyLock(),
            logger=_DummyLogger(),
            market_data=_DummyMarketData([]),
            persistence=persistence,
            model_id="gmo_ema_pullback_15m_both_v0",
            now_provider=lambda: datetime(2026, 3, 17, 3, 40, tzinfo=UTC),
        )

        with patch(
            "apps.gmo_bot.app.usecases.run_cycle.reconcile_protective_exit_execution",
            return_value=SimpleNamespace(status="PENDING", close_result=None),
        ) as reconcile_mock, patch(
            "apps.gmo_bot.app.usecases.run_cycle.close_position"
        ) as close_position_mock:
            run = run_cycle(deps)

        reconcile_mock.assert_called_once()
        close_position_mock.assert_not_called()
        self.assertEqual("HOLD", run["result"])
        self.assertEqual("HOLD: protective stop order triggered and exchange execution is pending", run["summary"])

    def test_active_stop_loss_reconciliation_closed_returns_closed(self) -> None:
        config = _build_config()
        trade: TradeRecord = {
            "trade_id": "trade_stop_closed",
            "pair": "SOL/JPY",
            "direction": "LONG",
            "state": "CONFIRMED",
            "execution": {
                "take_profit_order_status": "CLIENT_MANAGED",
                "stop_loss_order_id": 123,
                "stop_loss_order_status": "WAITING",
            },
            "position": {
                "status": "OPEN",
                "stop_price": 10010.0,
                "take_profit_price": 10100.0,
            },
        }
        persistence = _OpenTradePersistence(config, trade)
        deps = RunCycleDependencies(
            execution=_DummyExecution(),
            lock=_DummyLock(),
            logger=_DummyLogger(),
            market_data=_DummyMarketData([]),
            persistence=persistence,
            model_id="gmo_ema_pullback_15m_both_v0",
            now_provider=lambda: datetime(2026, 3, 17, 3, 40, tzinfo=UTC),
        )

        with patch(
            "apps.gmo_bot.app.usecases.run_cycle.reconcile_protective_exit_execution",
            return_value=SimpleNamespace(
                status="CLOSED",
                close_result=SimpleNamespace(status="CLOSED", summary="CLOSED: reason=STOP_LOSS"),
            ),
        ) as reconcile_mock, patch(
            "apps.gmo_bot.app.usecases.run_cycle.close_position"
        ) as close_position_mock:
            run = run_cycle(deps)

        reconcile_mock.assert_called_once()
        close_position_mock.assert_not_called()
        self.assertEqual("CLOSED", run["result"])
        self.assertEqual("CLOSED: reason=STOP_LOSS", run["summary"])

    def test_close_position_pending_is_mapped_to_hold(self) -> None:
        config = _build_config()
        trade: TradeRecord = {
            "trade_id": "trade_stop_pending_close",
            "pair": "SOL/JPY",
            "direction": "LONG",
            "state": "CONFIRMED",
            "execution": {
                "take_profit_order_status": "CLIENT_MANAGED",
                "stop_loss_order_status": "FAILED",
            },
            "position": {
                "status": "OPEN",
                "stop_price": 10010.0,
                "take_profit_price": 10100.0,
            },
        }
        persistence = _OpenTradePersistence(config, trade)
        deps = RunCycleDependencies(
            execution=_DummyExecution(),
            lock=_DummyLock(),
            logger=_DummyLogger(),
            market_data=_DummyMarketData([]),
            persistence=persistence,
            model_id="gmo_ema_pullback_15m_both_v0",
            now_provider=lambda: datetime(2026, 3, 17, 3, 40, tzinfo=UTC),
        )

        with patch(
            "apps.gmo_bot.app.usecases.run_cycle.arm_protective_exit_orders",
            return_value=SimpleNamespace(status="FAILED", summary="FAILED"),
        ), patch(
            "apps.gmo_bot.app.usecases.run_cycle.close_position",
            return_value=SimpleNamespace(status="PENDING", summary="PENDING: protective exit execution pending settlement details"),
        ):
            run = run_cycle(deps)

        self.assertEqual("HOLD", run["result"])
        self.assertEqual("PENDING: protective exit execution pending settlement details", run["summary"])

    def test_take_profit_trigger_is_latched_and_retried_after_price_falls_back(self) -> None:
        config = _build_config()
        trade: TradeRecord = {
            "trade_id": "trade_tp_latched",
            "pair": "SOL/JPY",
            "direction": "LONG",
            "state": "CONFIRMED",
            "execution": {
                "take_profit_order_status": "CLIENT_MANAGED",
                "stop_loss_order_id": 123,
                "stop_loss_order_status": "WAITING",
            },
            "position": {
                "status": "OPEN",
                "entry_price": 10050.0,
                "stop_price": 10010.0,
                "take_profit_price": 10100.0,
            },
        }
        persistence = _OpenTradePersistence(config, trade)

        first_deps = RunCycleDependencies(
            execution=_StaticPriceExecution(10110.0),
            lock=_DummyLock(),
            logger=_DummyLogger(),
            market_data=_DummyMarketData([]),
            persistence=persistence,
            model_id="gmo_ema_pullback_15m_both_v0",
            now_provider=lambda: datetime(2026, 3, 17, 3, 40, tzinfo=UTC),
        )
        with patch(
            "apps.gmo_bot.app.usecases.run_cycle.close_position",
            return_value=SimpleNamespace(status="FAILED", summary="FAILED: tp close failed"),
        ):
            first_run = run_cycle(first_deps)

        self.assertEqual("FAILED", first_run["result"])
        self.assertEqual("2026-03-17T03:40:00Z", trade["execution"]["take_profit_triggered_at_iso"])
        self.assertEqual(10110.0, trade["execution"]["take_profit_trigger_price"])

        second_deps = RunCycleDependencies(
            execution=_StaticPriceExecution(10105.0),
            lock=_DummyLock(),
            logger=_DummyLogger(),
            market_data=_DummyMarketData([]),
            persistence=persistence,
            model_id="gmo_ema_pullback_15m_both_v0",
            now_provider=lambda: datetime(2026, 3, 17, 3, 40, 5, tzinfo=UTC),
        )
        with patch(
            "apps.gmo_bot.app.usecases.run_cycle.close_position",
            return_value=SimpleNamespace(status="FAILED", summary="FAILED: tp retry failed"),
        ) as close_position_mock:
            second_run = run_cycle(second_deps)

        close_position_mock.assert_called_once()
        close_input = close_position_mock.call_args.args[1]
        self.assertEqual("TAKE_PROFIT", close_input.close_reason)
        self.assertEqual(10105.0, close_input.close_price)
        self.assertEqual("FAILED", second_run["result"])
        self.assertEqual("FAILED: tp retry failed", second_run["summary"])

    def test_stop_loss_takes_precedence_over_latched_take_profit(self) -> None:
        config = _build_config()
        trade: TradeRecord = {
            "trade_id": "trade_tp_latched_stop",
            "pair": "SOL/JPY",
            "direction": "LONG",
            "state": "CONFIRMED",
            "execution": {
                "take_profit_order_status": "CLIENT_MANAGED",
                "take_profit_triggered_at_iso": "2026-03-17T03:40:00Z",
                "take_profit_trigger_price": 10110.0,
                "stop_loss_order_id": 123,
                "stop_loss_order_status": "WAITING",
            },
            "position": {
                "status": "OPEN",
                "entry_price": 10050.0,
                "stop_price": 10010.0,
                "take_profit_price": 10100.0,
            },
        }
        persistence = _OpenTradePersistence(config, trade)
        deps = RunCycleDependencies(
            execution=_StaticPriceExecution(10000.0),
            lock=_DummyLock(),
            logger=_DummyLogger(),
            market_data=_DummyMarketData([]),
            persistence=persistence,
            model_id="gmo_ema_pullback_15m_both_v0",
            now_provider=lambda: datetime(2026, 3, 17, 3, 40, 10, tzinfo=UTC),
        )

        with patch(
            "apps.gmo_bot.app.usecases.run_cycle.reconcile_protective_exit_execution",
            return_value=SimpleNamespace(status="PENDING", close_result=None),
        ) as reconcile_mock, patch(
            "apps.gmo_bot.app.usecases.run_cycle.close_position"
        ) as close_position_mock:
            run = run_cycle(deps)

        reconcile_mock.assert_called_once()
        close_position_mock.assert_not_called()
        self.assertEqual("HOLD", run["result"])
        self.assertEqual("HOLD: protective stop order triggered and exchange execution is pending", run["summary"])

    def test_take_profit_latch_is_cleared_after_excessive_pullback(self) -> None:
        config = _build_config()
        trade: TradeRecord = {
            "trade_id": "trade_tp_latch_clear",
            "pair": "SOL/JPY",
            "direction": "LONG",
            "state": "CONFIRMED",
            "execution": {
                "take_profit_order_status": "CLIENT_MANAGED",
                "take_profit_triggered_at_iso": "2026-03-17T03:40:00Z",
                "take_profit_trigger_price": 10110.0,
                "stop_loss_order_id": 123,
                "stop_loss_order_status": "WAITING",
            },
            "position": {
                "status": "OPEN",
                "entry_price": 10050.0,
                "stop_price": 10010.0,
                "take_profit_price": 10100.0,
            },
        }
        persistence = _OpenTradePersistence(config, trade)
        deps = RunCycleDependencies(
            execution=_StaticPriceExecution(10090.0),
            lock=_DummyLock(),
            logger=_DummyLogger(),
            market_data=_DummyMarketData([]),
            persistence=persistence,
            model_id="gmo_ema_pullback_15m_both_v0",
            now_provider=lambda: datetime(2026, 3, 17, 3, 40, 10, tzinfo=UTC),
        )

        with patch("apps.gmo_bot.app.usecases.run_cycle.close_position") as close_position_mock:
            run = run_cycle(deps)

        close_position_mock.assert_not_called()
        self.assertEqual("HOLD", run["result"])
        self.assertEqual("HOLD: open position exists and no exit trigger fired on this bar", run["summary"])
        self.assertNotIn("take_profit_triggered_at_iso", trade["execution"])
        self.assertNotIn("take_profit_trigger_price", trade["execution"])

    def test_mark_price_rate_limit_is_mapped_to_hold_for_open_trade(self) -> None:
        config = _build_config()
        trade: TradeRecord = {
            "trade_id": "trade_rate_limit",
            "pair": "SOL/JPY",
            "direction": "LONG",
            "state": "CONFIRMED",
            "execution": {
                "take_profit_order_status": "CLIENT_MANAGED",
                "stop_loss_order_id": 123,
                "stop_loss_order_status": "WAITING",
            },
            "position": {
                "status": "OPEN",
                "stop_price": 10010.0,
                "take_profit_price": 10100.0,
            },
        }
        persistence = _OpenTradePersistence(config, trade)
        deps = RunCycleDependencies(
            execution=_RateLimitPriceExecution(),
            lock=_DummyLock(),
            logger=_DummyLogger(),
            market_data=_DummyMarketData([]),
            persistence=persistence,
            model_id="gmo_ema_pullback_15m_both_v0",
            now_provider=lambda: datetime(2026, 3, 17, 3, 40, tzinfo=UTC),
        )

        run = run_cycle(deps)

        self.assertEqual("HOLD", run["result"])
        self.assertEqual("HOLD: market price temporarily unavailable (rate limit)", run["summary"])
        self.assertIn("ERR-5003", str(run.get("reason")))

    def test_should_persist_run_record_matches_runtime_policy(self) -> None:
        self.assertTrue(_should_persist_run_record({"result": "OPENED"}))
        self.assertTrue(_should_persist_run_record({"result": "CLOSED"}))
        self.assertTrue(_should_persist_run_record({"result": "PARTIALLY_CLOSED"}))
        self.assertTrue(_should_persist_run_record({"result": "FAILED"}))
        self.assertTrue(
            _should_persist_run_record({"result": "SKIPPED", "summary": "SKIPPED: market data unavailable (maintenance)"})
        )
        self.assertFalse(_should_persist_run_record({"result": "NO_SIGNAL"}))
        self.assertFalse(_should_persist_run_record({"result": "HOLD"}))
        self.assertFalse(_should_persist_run_record({"result": "SKIPPED_ENTRY"}))
        self.assertFalse(_should_persist_run_record({"result": "SKIPPED", "summary": "SKIPPED: max_trades_per_day reached"}))


if __name__ == "__main__":
    unittest.main()
