from __future__ import annotations

import unittest
from copy import deepcopy
from typing import Any
from unittest.mock import patch

from pybot.app.ports.execution_port import SwapConfirmation, SwapSubmission
from pybot.app.usecases.open_position import (
    OpenPositionDependencies,
    OpenPositionInput,
    open_position,
)
from pybot.domain.model.types import BotConfig, EntrySignalDecision, Pair, TradeRecord


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


class InMemoryLock:
    def __init__(self) -> None:
        self.inflight: set[str] = set()

    def set_inflight_tx(self, signature: str, ttl_seconds: int) -> None:
        _ = ttl_seconds
        self.inflight.add(signature)

    def has_inflight_tx(self, signature: str) -> bool:
        return signature in self.inflight

    def clear_inflight_tx(self, signature: str) -> None:
        self.inflight.discard(signature)


class InMemoryPersistence:
    def __init__(self) -> None:
        self.trades: dict[str, TradeRecord] = {}

    def create_trade(self, trade: TradeRecord) -> None:
        self.trades[trade["trade_id"]] = deepcopy(trade)

    def update_trade(self, trade_id: str, updates: dict[str, Any]) -> None:
        current = self.trades.get(trade_id)
        if current is None:
            raise KeyError(f"trade not found: {trade_id}")
        _merge(current, updates)

    def find_open_trade(self, pair: Pair) -> TradeRecord | None:
        _ = pair
        return None

    def count_trades_for_utc_day(self, pair: Pair, day_start_iso: str, day_end_iso: str) -> int:
        _ = pair
        _ = day_start_iso
        _ = day_end_iso
        return 0


def _build_config() -> BotConfig:
    return {
        "enabled": True,
        "network": "mainnet-beta",
        "pair": "SOL/USDC",
        "direction": "LONG",
        "signal_timeframe": "15m",
        "strategy": {
            "name": "ema_trend_pullback_15m_v0",
            "ema_fast_period": 9,
            "ema_slow_period": 34,
            "swing_low_lookback_bars": 6,
            "entry": "ON_BAR_CLOSE",
        },
        "risk": {
            "max_loss_per_trade_pct": 1.2,
            "max_trades_per_day": 4,
            "volatile_atr_pct_threshold": 1.3,
            "storm_atr_pct_threshold": 1.5,
            "volatile_size_multiplier": 0.8,
            "storm_size_multiplier": 0.4,
        },
        "execution": {
            "mode": "LIVE",
            "swap_provider": "JUPITER",
            "slippage_bps": 3,
            "min_notional_usdc": 20.0,
            "only_direct_routes": False,
        },
        "exit": {"stop": "SWING_LOW", "take_profit_r_multiple": 2.4},
        "meta": {"config_version": 1, "note": "test"},
    }


def _build_signal() -> EntrySignalDecision:
    return EntrySignalDecision(
        type="ENTER",
        summary="ENTER: test signal",
        ema_fast=86.2,
        ema_slow=85.1,
        entry_price=86.5,
        stop_price=85.8,
        take_profit_price=88.0,
        diagnostics=None,
    )


class OpenPositionZeroAmountRetryTest(unittest.TestCase):
    def test_open_position_retries_on_custom_6024_and_opens(self) -> None:
        class RetryThenSuccessExecution:
            def __init__(self) -> None:
                self.submit_calls = 0
                self.confirm_calls = 0

            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                self.submit_calls += 1
                if self.submit_calls == 1:
                    raise RuntimeError(
                        "RPC sendTransaction failed: {'code': -32002, 'message': "
                        "'Transaction simulation failed: Error processing Instruction 3: "
                        "custom program error: 0x1788'}"
                    )
                return SwapSubmission(
                    tx_signature=f"entry_sig_{self.submit_calls}",
                    in_amount_atomic=39_000_000,
                    out_amount_atomic=450_000_000,
                    order={"tx_signature": f"entry_sig_{self.submit_calls}"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 86.666666,
                        "spent_quote_usdc": 39.0,
                        "filled_base_sol": 0.45,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                self.confirm_calls += 1
                return SwapConfirmation(confirmed=True)

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 86.5

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 40.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 0.5

            def get_transaction_fee_lamports(self, tx_signature: str) -> int:
                _ = tx_signature
                return 5_000

        persistence = InMemoryPersistence()
        execution = RetryThenSuccessExecution()

        with patch("pybot.app.usecases.open_position.time.sleep", return_value=None):
            result = open_position(
                OpenPositionDependencies(
                    execution=execution,
                    lock=InMemoryLock(),
                    logger=InMemoryLogger(),
                    persistence=persistence,
                ),
                OpenPositionInput(
                    config=_build_config(),
                    signal=_build_signal(),
                    bar_close_time_iso="2026-03-02T04:00:00Z",
                    model_id="ema_pullback_15m_both_v0",
                ),
            )

        self.assertEqual("OPENED", result.status)
        self.assertIn("after 2 attempts", result.summary)
        self.assertEqual(2, execution.submit_calls)
        self.assertEqual(1, execution.confirm_calls)
        trade = next(iter(persistence.trades.values()))
        self.assertEqual("CONFIRMED", trade["state"])
        self.assertEqual("OPEN", trade["position"]["status"])

    def test_open_position_cancels_after_zero_amount_retry_budget_exhausted(self) -> None:
        class AlwaysZeroAmountExecution:
            def __init__(self) -> None:
                self.submit_calls = 0

            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                self.submit_calls += 1
                raise RuntimeError("Jupiter quote route contains zero-amount leg")

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=False, error="unused")

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 86.5

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 40.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 0.5

        persistence = InMemoryPersistence()
        execution = AlwaysZeroAmountExecution()

        with patch("pybot.app.usecases.open_position.time.sleep", return_value=None):
            result = open_position(
                OpenPositionDependencies(
                    execution=execution,
                    lock=InMemoryLock(),
                    logger=InMemoryLogger(),
                    persistence=persistence,
                ),
                OpenPositionInput(
                    config=_build_config(),
                    signal=_build_signal(),
                    bar_close_time_iso="2026-03-02T04:15:00Z",
                    model_id="ema_pullback_15m_both_v0",
                ),
            )

        self.assertEqual("SKIPPED", result.status)
        self.assertIn("route/liquidity unavailable", result.summary)
        self.assertEqual(3, execution.submit_calls)
        trade = next(iter(persistence.trades.values()))
        self.assertEqual("CANCELED", trade["state"])


if __name__ == "__main__":
    unittest.main()
