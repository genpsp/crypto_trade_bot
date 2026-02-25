from __future__ import annotations

import unittest
from copy import deepcopy
from typing import Any
from unittest.mock import patch

from pybot.app.ports.execution_port import SwapConfirmation, SwapSubmission
from pybot.app.usecases.close_position import (
    ClosePositionDependencies,
    ClosePositionInput,
    close_position,
)
from pybot.domain.model.types import BotConfig, TradeRecord


def _merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge(dst[key], value)
            continue
        dst[key] = deepcopy(value)


class InMemoryLogger:
    def info(self, message: str, context: dict[str, Any] | None = None) -> None:
        _ = message
        _ = context

    def warn(self, message: str, context: dict[str, Any] | None = None) -> None:
        _ = message
        _ = context

    def error(self, message: str, context: dict[str, Any] | None = None) -> None:
        _ = message
        _ = context


class InMemoryPersistence:
    def __init__(self, trade: TradeRecord):
        self.trade = trade
        self.updates: list[dict[str, Any]] = []

    def update_trade(self, trade_id: str, updates: dict[str, Any]) -> None:
        if trade_id != self.trade["trade_id"]:
            raise KeyError(f"trade not found: {trade_id}")
        self.updates.append(deepcopy(updates))
        _merge(self.trade, updates)


class SpyLock:
    def __init__(self) -> None:
        self.active: set[str] = set()
        self.set_calls: list[str] = []
        self.clear_calls: list[str] = []

    def set_inflight_tx(self, signature: str, ttl_seconds: int) -> None:
        _ = ttl_seconds
        self.active.add(signature)
        self.set_calls.append(signature)

    def has_inflight_tx(self, signature: str) -> bool:
        return signature in self.active

    def clear_inflight_tx(self, signature: str) -> None:
        self.active.discard(signature)
        self.clear_calls.append(signature)


def _build_config() -> BotConfig:
    return {
        "enabled": True,
        "network": "mainnet-beta",
        "pair": "SOL/USDC",
        "direction": "LONG_ONLY",
        "signal_timeframe": "2h",
        "strategy": {
            "name": "ema_trend_pullback_v0",
            "ema_fast_period": 12,
            "ema_slow_period": 34,
            "swing_low_lookback_bars": 6,
            "entry": "ON_BAR_CLOSE",
        },
        "risk": {
            "max_loss_per_trade_pct": 3.0,
            "max_trades_per_day": 1,
            "volatile_atr_pct_threshold": 1.3,
            "storm_atr_pct_threshold": 1.4,
            "volatile_size_multiplier": 0.8,
            "storm_size_multiplier": 0.1,
        },
        "execution": {
            "mode": "LIVE",
            "swap_provider": "JUPITER",
            "slippage_bps": 12,
            "min_notional_usdc": 20.0,
            "only_direct_routes": False,
        },
        "exit": {"stop": "SWING_LOW", "take_profit_r_multiple": 2.4},
        "meta": {"config_version": 2, "note": "test"},
    }


def _build_open_trade() -> TradeRecord:
    return {
        "trade_id": "2026-02-22T20:00:00Z_core_long_v0_LONG",
        "model_id": "core_long_v0",
        "bar_close_time_iso": "2026-02-22T20:00:00Z",
        "pair": "SOL/USDC",
        "direction": "LONG_ONLY",
        "state": "CONFIRMED",
        "config_version": 2,
        "execution": {"entry_tx_signature": "entry_sig_1"},
        "position": {
            "status": "OPEN",
            "quantity_sol": 0.5,
            "entry_price": 80.0,
            "stop_price": 78.0,
            "take_profit_price": 84.0,
            "entry_time_iso": "2026-02-22T20:01:00Z",
        },
        "created_at": "2026-02-22T20:01:00Z",
        "updated_at": "2026-02-22T20:01:00Z",
    }


class ClosePositionRetryTest(unittest.TestCase):
    def test_stop_loss_retries_until_late_success(self) -> None:
        trade = _build_open_trade()
        persistence = InMemoryPersistence(trade)
        lock = SpyLock()

        class RetryExecution:
            def __init__(self) -> None:
                self.submit_calls = 0
                self.confirm_calls = 0

            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                self.submit_calls += 1
                if self.submit_calls < 5:
                    raise RuntimeError("temporary submit error")
                return SwapSubmission(
                    tx_signature=f"exit_sig_{self.submit_calls}",
                    in_amount_atomic=500_000_000,
                    out_amount_atomic=40_000_000,
                    order={"tx_signature": f"exit_sig_{self.submit_calls}"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 80.0,
                        "spent_quote_usdc": 40.0,
                        "filled_base_sol": 0.5,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                self.confirm_calls += 1
                return SwapConfirmation(confirmed=True)

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 77.5

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        with patch("pybot.app.usecases.close_position.time.sleep", return_value=None):
            result = close_position(
                ClosePositionDependencies(
                    execution=RetryExecution(),
                    lock=lock,
                    logger=InMemoryLogger(),
                    persistence=persistence,
                ),
                ClosePositionInput(
                    config=_build_config(),
                    trade=trade,
                    close_reason="STOP_LOSS",
                    close_price=77.5,
                ),
            )

        self.assertEqual("CLOSED", result.status)
        self.assertIn("after 5 attempts", result.summary)
        self.assertEqual("CLOSED", trade["state"])
        self.assertEqual("CONFIRMED", trade["execution"]["exit_submission_state"])
        self.assertEqual(1, len(lock.set_calls))
        self.assertEqual(1, len(lock.clear_calls))
        self.assertEqual(0, len(lock.active))

    def test_take_profit_uses_default_retry_budget_and_stays_open_on_failure(self) -> None:
        trade = _build_open_trade()
        persistence = InMemoryPersistence(trade)
        lock = SpyLock()

        class AlwaysFailExecution:
            def __init__(self) -> None:
                self.submit_calls = 0

            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                self.submit_calls += 1
                raise RuntimeError("submit failed")

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=False, error="unused")

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 84.0

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        execution = AlwaysFailExecution()
        with patch("pybot.app.usecases.close_position.time.sleep", return_value=None):
            result = close_position(
                ClosePositionDependencies(
                    execution=execution,
                    lock=lock,
                    logger=InMemoryLogger(),
                    persistence=persistence,
                ),
                ClosePositionInput(
                    config=_build_config(),
                    trade=trade,
                    close_reason="TAKE_PROFIT",
                    close_price=84.0,
                ),
            )

        self.assertEqual("FAILED", result.status)
        self.assertEqual(2, execution.submit_calls)
        self.assertEqual("CONFIRMED", trade["state"])
        self.assertEqual("OPEN", trade["position"]["status"])
        self.assertEqual("FAILED", trade["execution"]["exit_submission_state"])
        self.assertIn("attempt 2/2", trade["execution"]["exit_error"])

    def test_stop_loss_retries_on_unconfirmed_then_closes(self) -> None:
        trade = _build_open_trade()
        persistence = InMemoryPersistence(trade)
        lock = SpyLock()

        class RetryOnUnconfirmedExecution:
            def __init__(self) -> None:
                self.submit_calls = 0
                self.confirm_calls = 0

            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                self.submit_calls += 1
                return SwapSubmission(
                    tx_signature=f"exit_sig_{self.submit_calls}",
                    in_amount_atomic=500_000_000,
                    out_amount_atomic=40_000_000,
                    order={"tx_signature": f"exit_sig_{self.submit_calls}"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 80.0,
                        "spent_quote_usdc": 40.0,
                        "filled_base_sol": 0.5,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                self.confirm_calls += 1
                if self.confirm_calls < 3:
                    return SwapConfirmation(confirmed=False, error="confirmation timeout")
                return SwapConfirmation(confirmed=True)

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 77.5

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        execution = RetryOnUnconfirmedExecution()
        with patch("pybot.app.usecases.close_position.time.sleep", return_value=None):
            result = close_position(
                ClosePositionDependencies(
                    execution=execution,
                    lock=lock,
                    logger=InMemoryLogger(),
                    persistence=persistence,
                ),
                ClosePositionInput(
                    config=_build_config(),
                    trade=trade,
                    close_reason="STOP_LOSS",
                    close_price=77.5,
                ),
            )

        self.assertEqual("CLOSED", result.status)
        self.assertEqual(1, execution.submit_calls)
        self.assertEqual(3, execution.confirm_calls)
        self.assertEqual(1, len(lock.set_calls))
        self.assertEqual(1, len(lock.clear_calls))
        self.assertEqual(0, len(lock.active))
        self.assertEqual("CLOSED", trade["state"])
        self.assertEqual("CONFIRMED", trade["execution"]["exit_submission_state"])

    def test_stop_loss_retries_when_confirm_raises_and_clears_inflight(self) -> None:
        trade = _build_open_trade()
        persistence = InMemoryPersistence(trade)
        lock = SpyLock()

        class RaiseThenSuccessExecution:
            def __init__(self) -> None:
                self.submit_calls = 0
                self.confirm_calls = 0

            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                self.submit_calls += 1
                return SwapSubmission(
                    tx_signature=f"exit_sig_{self.submit_calls}",
                    in_amount_atomic=500_000_000,
                    out_amount_atomic=40_000_000,
                    order={"tx_signature": f"exit_sig_{self.submit_calls}"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 80.0,
                        "spent_quote_usdc": 40.0,
                        "filled_base_sol": 0.5,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                self.confirm_calls += 1
                if self.confirm_calls == 1:
                    raise RuntimeError("rpc temp failure")
                return SwapConfirmation(confirmed=True)

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 77.5

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        execution = RaiseThenSuccessExecution()
        with patch("pybot.app.usecases.close_position.time.sleep", return_value=None):
            result = close_position(
                ClosePositionDependencies(
                    execution=execution,
                    lock=lock,
                    logger=InMemoryLogger(),
                    persistence=persistence,
                ),
                ClosePositionInput(
                    config=_build_config(),
                    trade=trade,
                    close_reason="STOP_LOSS",
                    close_price=77.5,
                ),
            )

        self.assertEqual("CLOSED", result.status)
        self.assertEqual(1, execution.submit_calls)
        self.assertEqual(2, execution.confirm_calls)
        self.assertEqual(1, len(lock.set_calls))
        self.assertEqual(1, len(lock.clear_calls))
        self.assertEqual(0, len(lock.active))
        self.assertEqual("CLOSED", trade["state"])
        self.assertEqual("CONFIRMED", trade["execution"]["exit_submission_state"])

    def test_stop_loss_does_not_retry_non_retriable_confirmation_error(self) -> None:
        trade = _build_open_trade()
        persistence = InMemoryPersistence(trade)
        lock = SpyLock()

        class NonRetriableConfirmErrorExecution:
            def __init__(self) -> None:
                self.submit_calls = 0
                self.confirm_calls = 0

            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                self.submit_calls += 1
                return SwapSubmission(
                    tx_signature=f"exit_sig_{self.submit_calls}",
                    in_amount_atomic=500_000_000,
                    out_amount_atomic=40_000_000,
                    order={"tx_signature": f"exit_sig_{self.submit_calls}"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 80.0,
                        "spent_quote_usdc": 40.0,
                        "filled_base_sol": 0.5,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                self.confirm_calls += 1
                return SwapConfirmation(
                    confirmed=False,
                    error="Simulation failed: error processing instruction 0",
                )

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 77.5

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        execution = NonRetriableConfirmErrorExecution()
        with patch("pybot.app.usecases.close_position.time.sleep", return_value=None):
            result = close_position(
                ClosePositionDependencies(
                    execution=execution,
                    lock=lock,
                    logger=InMemoryLogger(),
                    persistence=persistence,
                ),
                ClosePositionInput(
                    config=_build_config(),
                    trade=trade,
                    close_reason="STOP_LOSS",
                    close_price=77.5,
                ),
            )

        self.assertEqual("FAILED", result.status)
        self.assertEqual(1, execution.submit_calls)
        self.assertEqual(1, execution.confirm_calls)
        self.assertEqual(1, len(lock.set_calls))
        self.assertEqual(1, len(lock.clear_calls))
        self.assertEqual(0, len(lock.active))
        self.assertEqual("CONFIRMED", trade["state"])
        self.assertEqual("OPEN", trade["position"]["status"])

    def test_short_close_caps_exit_amount_to_available_quote_balance(self) -> None:
        trade = _build_open_trade()
        trade["trade_id"] = "2026-02-23T18:00:00Z_storm_short_v0_SHORT"
        trade["model_id"] = "storm_short_v0"
        trade["direction"] = "SHORT_ONLY"
        trade["position"] = {
            "status": "OPEN",
            "quantity_sol": 1.129395136,
            "quote_amount_usdc": 88.396203,
            "entry_price": 78.268624,
            "stop_price": 81.12,
            "take_profit_price": 73.706422,
            "entry_time_iso": "2026-02-23T18:00:04.346166Z",
        }
        persistence = InMemoryPersistence(trade)
        lock = SpyLock()

        class ShortClampExecution:
            def __init__(self) -> None:
                self.submitted_amounts: list[int] = []
                self.quote_balances = [88.371078, 0.0]
                self.base_balances = [0.018, 1.12]

            @staticmethod
            def _next(values: list[float]) -> float:
                if len(values) > 1:
                    return values.pop(0)
                return values[0]

            def submit_swap(self, request: Any) -> SwapSubmission:
                self.submitted_amounts.append(request.amount_atomic)
                return SwapSubmission(
                    tx_signature="exit_sig_short_clamped",
                    in_amount_atomic=request.amount_atomic,
                    out_amount_atomic=1_120_000_000,
                    order={"tx_signature": "exit_sig_short_clamped"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 78.9,
                        "spent_quote_usdc": request.amount_atomic / 1_000_000,
                        "filled_base_sol": 1.12,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=True)

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 81.5

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return self._next(self.quote_balances)

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return self._next(self.base_balances)

        execution = ShortClampExecution()
        with patch("pybot.app.usecases.close_position.time.sleep", return_value=None):
            result = close_position(
                ClosePositionDependencies(
                    execution=execution,
                    lock=lock,
                    logger=InMemoryLogger(),
                    persistence=persistence,
                ),
                ClosePositionInput(
                    config=_build_config(),
                    trade=trade,
                    close_reason="STOP_LOSS",
                    close_price=81.5,
                ),
            )

        self.assertEqual("CLOSED", result.status)
        self.assertEqual(1, len(execution.submitted_amounts))
        self.assertEqual(88_371_078, execution.submitted_amounts[0])
        self.assertEqual("CONFIRMED", trade["execution"]["exit_submission_state"])

    def test_long_close_caps_exit_amount_to_available_base_balance(self) -> None:
        trade = _build_open_trade()
        trade["position"]["quantity_sol"] = 0.5
        persistence = InMemoryPersistence(trade)
        lock = SpyLock()

        class LongClampExecution:
            def __init__(self) -> None:
                self.submitted_amounts: list[int] = []
                self.quote_balances = [100.0, 139.2]
                self.base_balances = [0.49, 0.0]

            @staticmethod
            def _next(values: list[float]) -> float:
                if len(values) > 1:
                    return values.pop(0)
                return values[0]

            def submit_swap(self, request: Any) -> SwapSubmission:
                self.submitted_amounts.append(request.amount_atomic)
                return SwapSubmission(
                    tx_signature="exit_sig_long_clamped",
                    in_amount_atomic=request.amount_atomic,
                    out_amount_atomic=39_200_000,
                    order={"tx_signature": "exit_sig_long_clamped"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 80.0,
                        "spent_quote_usdc": 39.2,
                        "filled_base_sol": request.amount_atomic / 1_000_000_000,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=True)

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 77.5

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return self._next(self.quote_balances)

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return self._next(self.base_balances)

        execution = LongClampExecution()
        with patch("pybot.app.usecases.close_position.time.sleep", return_value=None):
            result = close_position(
                ClosePositionDependencies(
                    execution=execution,
                    lock=lock,
                    logger=InMemoryLogger(),
                    persistence=persistence,
                ),
                ClosePositionInput(
                    config=_build_config(),
                    trade=trade,
                    close_reason="TAKE_PROFIT",
                    close_price=84.0,
                ),
            )

        self.assertEqual("CLOSED", result.status)
        self.assertEqual(1, len(execution.submitted_amounts))
        self.assertEqual(490_000_000, execution.submitted_amounts[0])
        self.assertEqual("CONFIRMED", trade["execution"]["exit_submission_state"])

    def test_take_profit_returns_skipped_when_only_slippage_errors_happen(self) -> None:
        trade = _build_open_trade()
        persistence = InMemoryPersistence(trade)
        lock = SpyLock()

        class AlwaysSlippageExecution:
            def __init__(self) -> None:
                self.submit_calls = 0
                self.submitted_slippage_bps: list[int] = []

            def submit_swap(self, request: Any) -> SwapSubmission:
                self.submit_calls += 1
                self.submitted_slippage_bps.append(request.slippage_bps)
                raise RuntimeError(
                    "RPC sendTransaction failed: {'code': -32002, 'message': "
                    "'Transaction simulation failed: Error processing Instruction 5: "
                    "custom program error: 0x1771'}"
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=False, error="unused")

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 84.0

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        config = _build_config()
        config["execution"]["slippage_bps"] = 2
        execution = AlwaysSlippageExecution()
        with patch("pybot.app.usecases.close_position.time.sleep", return_value=None):
            result = close_position(
                ClosePositionDependencies(
                    execution=execution,
                    lock=lock,
                    logger=InMemoryLogger(),
                    persistence=persistence,
                ),
                ClosePositionInput(
                    config=config,
                    trade=trade,
                    close_reason="TAKE_PROFIT",
                    close_price=84.0,
                ),
            )

        self.assertEqual("SKIPPED", result.status)
        self.assertIn("slippage exceeded", result.summary)
        self.assertEqual(2, execution.submit_calls)
        self.assertEqual([2, 4], execution.submitted_slippage_bps)
        self.assertEqual("CONFIRMED", trade["state"])
        self.assertEqual("OPEN", trade["position"]["status"])

    def test_take_profit_returns_skipped_when_exact_out_amount_not_matched_happens(self) -> None:
        trade = _build_open_trade()
        persistence = InMemoryPersistence(trade)
        lock = SpyLock()

        class AlwaysExactOutMismatchExecution:
            def __init__(self) -> None:
                self.submit_calls = 0
                self.submitted_slippage_bps: list[int] = []

            def submit_swap(self, request: Any) -> SwapSubmission:
                self.submit_calls += 1
                self.submitted_slippage_bps.append(request.slippage_bps)
                raise RuntimeError(
                    "RPC sendTransaction failed: {'code': -32002, 'message': "
                    "'Transaction simulation failed: Error processing Instruction 5: "
                    "custom program error: 0x1781'}"
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=False, error="unused")

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 84.0

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        config = _build_config()
        config["execution"]["slippage_bps"] = 2
        execution = AlwaysExactOutMismatchExecution()
        with patch("pybot.app.usecases.close_position.time.sleep", return_value=None):
            result = close_position(
                ClosePositionDependencies(
                    execution=execution,
                    lock=lock,
                    logger=InMemoryLogger(),
                    persistence=persistence,
                ),
                ClosePositionInput(
                    config=config,
                    trade=trade,
                    close_reason="TAKE_PROFIT",
                    close_price=84.0,
                ),
            )

        self.assertEqual("SKIPPED", result.status)
        self.assertIn("slippage exceeded", result.summary)
        self.assertEqual(2, execution.submit_calls)
        self.assertEqual([2, 4], execution.submitted_slippage_bps)
        self.assertEqual("CONFIRMED", trade["state"])
        self.assertEqual("OPEN", trade["position"]["status"])

    def test_take_profit_returns_skipped_when_no_route_is_available(self) -> None:
        trade = _build_open_trade()
        persistence = InMemoryPersistence(trade)
        lock = SpyLock()

        class NoRouteExecution:
            def __init__(self) -> None:
                self.submit_calls = 0

            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                self.submit_calls += 1
                raise RuntimeError("Jupiter quote request failed: NO_ROUTES_FOUND")

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=False, error="unused")

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 84.0

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        result = close_position(
            ClosePositionDependencies(
                execution=NoRouteExecution(),
                lock=lock,
                logger=InMemoryLogger(),
                persistence=persistence,
            ),
            ClosePositionInput(
                config=_build_config(),
                trade=trade,
                close_reason="TAKE_PROFIT",
                close_price=84.0,
            ),
        )

        self.assertEqual("SKIPPED", result.status)
        self.assertIn("route/liquidity unavailable", result.summary)
        self.assertEqual("CONFIRMED", trade["state"])
        self.assertEqual("OPEN", trade["position"]["status"])

    def test_stop_loss_widens_slippage_and_closes(self) -> None:
        trade = _build_open_trade()
        persistence = InMemoryPersistence(trade)
        lock = SpyLock()

        class SlippageThenSuccessExecution:
            def __init__(self) -> None:
                self.submit_calls = 0
                self.submitted_slippage_bps: list[int] = []

            def submit_swap(self, request: Any) -> SwapSubmission:
                self.submit_calls += 1
                self.submitted_slippage_bps.append(request.slippage_bps)
                if self.submit_calls < 3:
                    raise RuntimeError(
                        "RPC sendTransaction failed: {'code': -32002, 'message': "
                        "'Transaction simulation failed: Error processing Instruction 5: "
                        "custom program error: 0x1771'}"
                    )
                return SwapSubmission(
                    tx_signature="exit_sig_stop_loss_after_widen",
                    in_amount_atomic=500_000_000,
                    out_amount_atomic=40_000_000,
                    order={"tx_signature": "exit_sig_stop_loss_after_widen"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 80.0,
                        "spent_quote_usdc": 40.0,
                        "filled_base_sol": 0.5,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=True)

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 77.5

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        config = _build_config()
        config["execution"]["slippage_bps"] = 2
        execution = SlippageThenSuccessExecution()
        with patch("pybot.app.usecases.close_position.time.sleep", return_value=None):
            result = close_position(
                ClosePositionDependencies(
                    execution=execution,
                    lock=lock,
                    logger=InMemoryLogger(),
                    persistence=persistence,
                ),
                ClosePositionInput(
                    config=config,
                    trade=trade,
                    close_reason="STOP_LOSS",
                    close_price=77.5,
                ),
            )

        self.assertEqual("CLOSED", result.status)
        self.assertEqual([2, 4, 8], execution.submitted_slippage_bps)
        self.assertEqual("CLOSED", trade["state"])
        self.assertEqual("CONFIRMED", trade["execution"]["exit_submission_state"])


if __name__ == "__main__":
    unittest.main()
