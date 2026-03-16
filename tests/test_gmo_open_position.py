from __future__ import annotations

import unittest

from apps.gmo_bot.app.ports.execution_port import (
    OrderConfirmation,
    OrderSubmission,
    ProtectiveExitOrdersSubmission,
    SymbolRule,
)
from apps.gmo_bot.app.usecases.open_position import OpenPositionDependencies, OpenPositionInput, open_position
from apps.gmo_bot.domain.model.types import BotConfig, EntrySignalDecision, TradeRecord


class _FakeExecution:
    def submit_entry_order(self, request):
        self.request = request
        return OrderSubmission(order_id=123, order={"order_id": 123})

    def submit_close_order(self, request):
        raise NotImplementedError

    def submit_protective_exit_orders(self, request):
        self.protective_request = request
        return ProtectiveExitOrdersSubmission(
            take_profit_order=OrderSubmission(order_id=456, order={"order_id": 456}),
            stop_loss_order=OrderSubmission(order_id=789, order={"order_id": 789}),
        )

    def confirm_order(self, order_id: int, timeout_ms: int):
        _ = order_id
        _ = timeout_ms
        return OrderConfirmation(
            confirmed=True,
            result={
                "status": "CONFIRMED",
                "avg_fill_price": 20000.0,
                "filled_base_sol": 5.0,
                "filled_quote_jpy": 100000.0,
                "fee_jpy": 0.0,
                "execution_ids": ["1"],
                "lots": [{"position_id": 999, "size_sol": 5.0}],
            },
        )

    def get_mark_price(self, pair: str) -> float:
        self.pair = pair
        return 20000.0

    def get_available_margin_jpy(self) -> float:
        return 100000.0

    def get_symbol_rule(self, pair: str) -> SymbolRule:
        return SymbolRule(symbol="SOL_JPY", tick_size=1.0, size_step=0.01, min_order_size=0.01)

    def cancel_order(self, order_id: int) -> None:
        _ = order_id

    def get_order(self, order_id: int):
        _ = order_id
        return None

    def get_executions(self, order_id: int):
        _ = order_id
        return []


class _FakePersistence:
    def __init__(self):
        self.trade: TradeRecord | None = None

    def get_current_config(self):
        raise NotImplementedError

    def create_trade(self, trade: TradeRecord) -> None:
        self.trade = trade

    def update_trade(self, trade_id: str, updates: dict) -> None:
        assert self.trade is not None
        self.trade.update(updates)

    def find_open_trade(self, pair: str):
        raise NotImplementedError

    def count_trades_for_utc_day(self, pair: str, day_start_iso: str, day_end_iso: str) -> int:
        raise NotImplementedError

    def list_recent_closed_trades(self, pair: str, limit: int):
        raise NotImplementedError

    def save_run(self, run: dict) -> None:
        raise NotImplementedError


class _FakeLock:
    pass


class _FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class GmoOpenPositionTest(unittest.TestCase):
    def test_open_position_creates_confirmed_trade(self) -> None:
        execution = _FakeExecution()
        persistence = _FakePersistence()
        config: BotConfig = {
            "enabled": True,
            "broker": "GMO_COIN",
            "pair": "SOL/JPY",
            "direction": "LONG",
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
                "mode": "PAPER",
                "broker": "GMO_COIN",
                "slippage_bps": 3,
                "min_notional_jpy": 5000.0,
                "leverage_multiplier": 1.0,
                "margin_usage_ratio": 1.0,
            },
            "exit": {"stop": "SWING_LOW", "take_profit_r_multiple": 2.0},
            "meta": {"config_version": 1, "note": "test"},
        }
        signal = EntrySignalDecision(
            type="ENTER",
            summary="ENTER",
            ema_fast=1.0,
            ema_slow=1.0,
            entry_price=20000.0,
            stop_price=19800.0,
            take_profit_price=20400.0,
            diagnostics={"position_size_multiplier": 1.0, "volatility_regime": "NORMAL"},
        )
        result = open_position(
            OpenPositionDependencies(execution=execution, lock=_FakeLock(), logger=_FakeLogger(), persistence=persistence),
            OpenPositionInput(config=config, signal=signal, bar_close_time_iso="2026-03-08T00:00:00Z", model_id="gmo_test"),
        )
        self.assertEqual(result.status, "OPENED")
        self.assertIsNotNone(persistence.trade)
        assert persistence.trade is not None
        self.assertEqual(persistence.trade["state"], "CONFIRMED")
        self.assertEqual(persistence.trade["position"]["status"], "OPEN")
        self.assertEqual(persistence.trade["position"]["quantity_sol"], 5.0)
        self.assertEqual(20000.0, persistence.trade["execution"]["entry_reference_price"])
        self.assertEqual(456, persistence.trade["execution"]["take_profit_order_id"])
        self.assertEqual(789, persistence.trade["execution"]["stop_loss_order_id"])


if __name__ == "__main__":
    unittest.main()
