from __future__ import annotations

import unittest
from copy import deepcopy
from unittest.mock import patch

from apps.gmo_bot.infra.execution.exit_order_monitor import ExitMonitorContext, GmoExitOrderMonitor


def _merge(dst: dict, src: dict) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge(dst[key], value)
            continue
        dst[key] = deepcopy(value)


class _FakeLogger:
    def info(self, message: str, context: dict[str, object] | None = None) -> None:
        _ = message
        _ = context

    def warn(self, message: str, context: dict[str, object] | None = None) -> None:
        _ = message
        _ = context

    def error(self, message: str, context: dict[str, object] | None = None) -> None:
        _ = message
        _ = context


class _FakePersistence:
    def __init__(self, trade: dict) -> None:
        self.trade = trade

    def find_open_trade(self, pair: str):
        _ = pair
        if self.trade.get("state") == "CONFIRMED":
            return self.trade
        return None

    def update_trade(self, trade_id: str, updates: dict) -> None:
        if trade_id != self.trade["trade_id"]:
            raise KeyError(trade_id)
        _merge(self.trade, updates)

    def get_current_config(self):
        return {
            "enabled": True,
            "broker": "GMO_COIN",
            "pair": "SOL/JPY",
            "direction": "LONG",
            "signal_timeframe": "15m",
            "strategy": {"name": "ema_trend_pullback_15m_v0"},
            "risk": {"max_loss_per_trade_pct": 1.0, "max_trades_per_day": 3},
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


class _FakeExecution:
    def __init__(self) -> None:
        self.canceled_orders: list[int] = []
        self.mark_price = 13900.0

    def get_executions(self, order_id: int):
        if order_id == 456:
            return [
                {
                    "executionId": 1,
                    "positionId": 10,
                    "size": "0.5",
                    "price": "14510",
                    "fee": "3",
                }
            ]
        return []

    def _aggregate_executions(self, executions):
        execution = executions[0]
        return {
            "status": "CONFIRMED",
            "avg_fill_price": float(execution["price"]),
            "filled_base_sol": float(execution["size"]),
            "filled_quote_jpy": float(execution["price"]) * float(execution["size"]),
            "fee_jpy": float(execution["fee"]),
            "execution_ids": [str(execution["executionId"])],
            "lots": [{"position_id": execution["positionId"], "size_sol": float(execution["size"])}],
        }

    def cancel_order(self, order_id: int) -> None:
        self.canceled_orders.append(order_id)

    def get_mark_price(self, pair: str) -> float:
        _ = pair
        return self.mark_price

    def submit_close_order(self, request):
        raise NotImplementedError

    def confirm_order(self, order_id: int, timeout_ms: int):
        raise NotImplementedError


class _PartialStopExecution(_FakeExecution):
    def get_executions(self, order_id: int):
        if order_id == 789:
            return [
                {
                    "executionId": 2,
                    "positionId": 10,
                    "size": "0.3",
                    "price": "13910",
                    "fee": "3",
                    "lossGain": "-45",
                }
            ]
        return super().get_executions(order_id)


def _build_trade() -> dict:
    return {
        "trade_id": "trade_1",
        "pair": "SOL/JPY",
        "direction": "LONG",
        "state": "CONFIRMED",
        "execution": {
            "take_profit_order_id": 456,
            "take_profit_order": {"order_id": 456, "price": "14500"},
            "take_profit_order_status": "ORDERED",
            "stop_loss_order_id": 789,
            "stop_loss_order": {"order_id": 789, "price": "13900"},
            "stop_loss_order_status": "WAITING",
        },
        "position": {
            "status": "OPEN",
            "quantity_sol": 0.5,
            "quote_amount_jpy": 7000.0,
            "entry_price": 14000.0,
            "stop_price": 13900.0,
            "take_profit_price": 14500.0,
            "lots": [{"position_id": 10, "size_sol": 0.5}],
        },
    }


class GmoExitOrderMonitorTest(unittest.TestCase):
    def test_execution_event_closes_trade_and_cancels_sibling_order(self) -> None:
        trade = _build_trade()
        execution = _FakeExecution()
        persistence = _FakePersistence(trade)
        monitor = GmoExitOrderMonitor(
            api_client=object(),
            logger=_FakeLogger(),
            context_provider=lambda: [
                ExitMonitorContext(
                    model_id="gmo_ema_pullback_15m_both_v0",
                    pair="SOL/JPY",
                    execution=execution,
                    persistence=persistence,
                    lock=object(),
                )
            ],
        )

        monitor._handle_event({"channel": "executionEvents", "orderId": "456"})

        self.assertEqual("CLOSED", trade["state"])
        self.assertEqual("TAKE_PROFIT", trade["close_reason"])
        self.assertEqual("EXECUTED", trade["execution"]["take_profit_order_status"])
        self.assertEqual("CANCELED", trade["execution"]["stop_loss_order_status"])
        self.assertEqual([789], execution.canceled_orders)

    def test_stop_order_expired_triggers_emergency_close(self) -> None:
        trade = _build_trade()
        execution = _FakeExecution()
        persistence = _FakePersistence(trade)
        monitor = GmoExitOrderMonitor(
            api_client=object(),
            logger=_FakeLogger(),
            context_provider=lambda: [
                ExitMonitorContext(
                    model_id="gmo_ema_pullback_15m_both_v0",
                    pair="SOL/JPY",
                    execution=execution,
                    persistence=persistence,
                    lock=object(),
                )
            ],
        )

        with patch("apps.gmo_bot.infra.execution.exit_order_monitor.close_position") as close_position_mock:
            monitor._handle_event({"channel": "orderEvents", "orderId": "789", "orderStatus": "EXPIRED"})

        close_position_mock.assert_called_once()

    def test_duplicate_partial_execution_event_is_not_double_counted(self) -> None:
        trade = _build_trade()
        trade["execution"]["take_profit_order_status"] = "CLIENT_MANAGED"
        trade["position"]["quantity_sol"] = 0.6
        trade["position"]["quote_amount_jpy"] = 8400.0
        trade["position"]["lots"] = [{"position_id": 10, "size_sol": 0.6}]
        execution = _PartialStopExecution()
        persistence = _FakePersistence(trade)
        monitor = GmoExitOrderMonitor(
            api_client=object(),
            logger=_FakeLogger(),
            context_provider=lambda: [
                ExitMonitorContext(
                    model_id="gmo_ema_pullback_15m_both_v0",
                    pair="SOL/JPY",
                    execution=execution,
                    persistence=persistence,
                    lock=object(),
                )
            ],
        )

        monitor._handle_event({"channel": "executionEvents", "orderId": "789"})
        self.assertEqual("CONFIRMED", trade["state"])
        self.assertAlmostEqual(0.3, trade["position"]["quantity_sol"])
        self.assertEqual("EXECUTED", trade["execution"]["stop_loss_order_status"])

        monitor._handle_event({"channel": "executionEvents", "orderId": "789"})
        self.assertEqual("CONFIRMED", trade["state"])
        self.assertAlmostEqual(0.3, trade["position"]["quantity_sol"])
        self.assertEqual("EXECUTED", trade["execution"]["stop_loss_order_status"])


if __name__ == "__main__":
    unittest.main()
