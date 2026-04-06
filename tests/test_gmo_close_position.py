from __future__ import annotations

import unittest
from copy import deepcopy
from typing import Any

from apps.gmo_bot.app.ports.execution_port import OrderConfirmation, OrderSubmission
from apps.gmo_bot.app.usecases.close_position import (
    ClosePositionDependencies,
    ClosePositionInput,
    close_position,
    reconcile_protective_exit_execution,
)
from apps.gmo_bot.domain.model.types import BotConfig, TradeRecord


def _merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
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
    def __init__(self, trade: TradeRecord) -> None:
        self.trade = trade
        self.updates: list[dict[str, Any]] = []

    def update_trade(self, trade_id: str, updates: dict[str, Any]) -> None:
        if trade_id != self.trade["trade_id"]:
            raise KeyError(trade_id)
        self.updates.append(deepcopy(updates))
        _merge(self.trade, updates)


def _build_config() -> BotConfig:
    return {
        "enabled": True,
        "broker": "GMO_COIN",
        "pair": "SOL/JPY",
        "direction": "SHORT",
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


def _build_open_trade() -> TradeRecord:
    return {
        "trade_id": "2026-03-11T02:45:00Z_gmo_ema_pullback_15m_both_v0_SHORT",
        "model_id": "gmo_ema_pullback_15m_both_v0",
        "bar_close_time_iso": "2026-03-11T02:45:00Z",
        "pair": "SOL/JPY",
        "direction": "SHORT",
        "state": "CONFIRMED",
        "config_version": 1,
        "execution": {
            "entry_fee_jpy": 3.0,
            "entry_order_id": 8218604890,
            "entry_order": {"order_id": 8218604890},
            "take_profit_order_id": 8218604891,
            "take_profit_order_status": "ORDERED",
            "stop_loss_order_id": 8218604892,
            "stop_loss_order_status": "WAITING",
        },
        "position": {
            "status": "OPEN",
            "quantity_sol": 0.6,
            "quote_amount_jpy": 8140.8,
            "entry_price": 13568.0,
            "stop_price": 13651.0,
            "take_profit_price": 13418.6,
            "entry_time_iso": "2026-03-11T02:45:01.084813Z",
            "lots": [{"position_id": 281135490, "size_sol": 0.6}],
        },
        "created_at": "2026-03-11T02:45:00.532624Z",
        "updated_at": "2026-03-11T02:45:00.532624Z",
    }


class _PartialCloseExecution:
    def __init__(self) -> None:
        self.canceled_orders: list[int] = []
        self.confirm_calls = 0

    def submit_close_order(self, request):
        _ = request
        return OrderSubmission(order_id=8218873898 + self.confirm_calls, order={"order_id": 8218873898 + self.confirm_calls})

    def confirm_order(self, order_id: int, timeout_ms: int):
        _ = timeout_ms
        self.confirm_calls += 1
        if self.confirm_calls == 1:
            return OrderConfirmation(
                confirmed=True,
                result={
                    "status": "CONFIRMED",
                    "avg_fill_price": 13683.0,
                    "filled_base_sol": 0.5,
                    "filled_quote_jpy": 6841.5,
                    "fee_jpy": 3.0,
                    "realized_pnl_jpy": -58.0,
                    "execution_ids": ["1530872851"],
                    "lots": [{"position_id": 281135490, "size_sol": 0.5}],
                },
            )
        if self.confirm_calls == 2 and order_id == 8218873899:
            return OrderConfirmation(
                confirmed=True,
                result={
                    "status": "CONFIRMED",
                    "avg_fill_price": 13690.0,
                    "filled_base_sol": 0.1,
                    "filled_quote_jpy": 1369.0,
                    "fee_jpy": 1.0,
                    "realized_pnl_jpy": -12.0,
                    "execution_ids": ["1530872852"],
                    "lots": [{"position_id": 281135490, "size_sol": 0.1}],
                },
            )
        raise AssertionError(order_id)

    def get_mark_price(self, pair: str) -> float:
        _ = pair
        raise NotImplementedError

    def get_available_margin_jpy(self) -> float:
        raise NotImplementedError

    def get_symbol_rule(self, pair: str):
        _ = pair
        raise NotImplementedError

    def cancel_order(self, order_id: int) -> None:
        self.canceled_orders.append(order_id)

    def get_order(self, order_id: int):
        _ = order_id
        return None

    def get_executions(self, order_id: int):
        if order_id != 8218604892:
            return []
        return [
            {
                "executionId": 1530999999,
                "positionId": 281135490,
                "size": "0.6",
                "price": "13651",
                "fee": "3",
                "lossGain": "-49.8",
            }
        ]


class _FinalCloseExecution:
    def __init__(self) -> None:
        self.canceled_orders: list[int] = []

    def submit_close_order(self, request):
        _ = request
        return OrderSubmission(order_id=8219000000, order={"order_id": 8219000000})

    def confirm_order(self, order_id: int, timeout_ms: int):
        _ = order_id
        _ = timeout_ms
        return OrderConfirmation(
            confirmed=True,
            result={
                "status": "CONFIRMED",
                "avg_fill_price": 13944.0,
                "filled_base_sol": 0.1,
                "filled_quote_jpy": 1394.4,
                "fee_jpy": 1.0,
                "realized_pnl_jpy": -38.0,
                "execution_ids": ["1530581068"],
                "lots": [{"position_id": 281135490, "size_sol": 0.1}],
            },
        )

    def get_mark_price(self, pair: str) -> float:
        _ = pair
        raise NotImplementedError

    def get_available_margin_jpy(self) -> float:
        raise NotImplementedError

    def get_symbol_rule(self, pair: str):
        _ = pair
        raise NotImplementedError

    def cancel_order(self, order_id: int) -> None:
        self.canceled_orders.append(order_id)

    def get_order(self, order_id: int):
        _ = order_id
        return None

    def get_executions(self, order_id: int):
        _ = order_id
        return []


class _ProtectiveStopAlreadyFilledExecution:
    def __init__(self) -> None:
        self.canceled_orders: list[int] = []

    def submit_close_order(self, request):
        _ = request
        raise RuntimeError("GMO API error status=1: ERR-254: Not found position.")

    def confirm_order(self, order_id: int, timeout_ms: int):
        _ = timeout_ms
        if order_id != 8218604892:
            return OrderConfirmation(confirmed=False, error="order not confirmed")
        return OrderConfirmation(
            confirmed=True,
            result={
                "status": "CONFIRMED",
                "avg_fill_price": 13651.0,
                "filled_base_sol": 0.6,
                "filled_quote_jpy": 8190.6,
                "fee_jpy": 3.0,
                "realized_pnl_jpy": -49.8,
                "execution_ids": ["1530999999"],
                "lots": [{"position_id": 281135490, "size_sol": 0.6}],
            },
        )

    def get_mark_price(self, pair: str) -> float:
        _ = pair
        raise NotImplementedError

    def get_available_margin_jpy(self) -> float:
        raise NotImplementedError

    def get_symbol_rule(self, pair: str):
        _ = pair
        raise NotImplementedError

    def cancel_order(self, order_id: int) -> None:
        self.canceled_orders.append(order_id)
        raise RuntimeError("GMO API error status=1: ERR-5122: The request is invalid due to the status of the specified order.")

    def get_order(self, order_id: int):
        _ = order_id
        return {"orderId": 8218604892, "status": "EXECUTED"}

    def get_executions(self, order_id: int):
        if order_id != 8218604892:
            return []
        return [
            {
                "executionId": 1530999999,
                "positionId": 281135490,
                "size": "0.6",
                "price": "13651",
                "fee": "3",
                "lossGain": "-49.8",
            }
        ]


class _ProtectiveStopExecutedWithoutExecutionsVisible:
    def submit_close_order(self, request):
        _ = request
        raise RuntimeError("GMO API error status=1: ERR-254: Not found position.")

    def confirm_order(self, order_id: int, timeout_ms: int):
        _ = order_id
        _ = timeout_ms
        return OrderConfirmation(confirmed=False, error="order status=EXECUTED")

    def get_mark_price(self, pair: str) -> float:
        _ = pair
        raise NotImplementedError

    def get_available_margin_jpy(self) -> float:
        raise NotImplementedError

    def get_symbol_rule(self, pair: str):
        _ = pair
        raise NotImplementedError

    def cancel_order(self, order_id: int) -> None:
        _ = order_id
        raise RuntimeError("GMO API error status=1: ERR-5122: The request is invalid due to the status of the specified order.")

    def get_order(self, order_id: int):
        _ = order_id
        return {"orderId": 8218604892, "status": "EXECUTED"}

    def get_executions(self, order_id: int):
        _ = order_id
        return []


class _SequentialMultiLotCloseExecution:
    def __init__(self) -> None:
        self.canceled_orders: list[int] = []
        self.submitted_lots: list[list[dict[str, float | int]]] = []

    def submit_close_order(self, request):
        self.submitted_lots.append(list(request.lots))
        order_id = 9100 + len(self.submitted_lots)
        return OrderSubmission(order_id=order_id, order={"order_id": order_id})

    def confirm_order(self, order_id: int, timeout_ms: int):
        _ = timeout_ms
        if order_id == 9101:
            return OrderConfirmation(
                confirmed=True,
                result={
                    "status": "CONFIRMED",
                    "avg_fill_price": 13683.0,
                    "filled_base_sol": 0.2,
                    "filled_quote_jpy": 2736.6,
                    "fee_jpy": 1.0,
                    "realized_pnl_jpy": -20.0,
                    "execution_ids": ["9101-1"],
                    "lots": [{"position_id": 281135490, "size_sol": 0.2}],
                },
            )
        if order_id == 9102:
            return OrderConfirmation(
                confirmed=True,
                result={
                    "status": "CONFIRMED",
                    "avg_fill_price": 13690.0,
                    "filled_base_sol": 0.4,
                    "filled_quote_jpy": 5476.0,
                    "fee_jpy": 2.0,
                    "realized_pnl_jpy": -38.0,
                    "execution_ids": ["9102-1"],
                    "lots": [{"position_id": 281135491, "size_sol": 0.4}],
                },
            )
        raise AssertionError(order_id)

    def get_mark_price(self, pair: str) -> float:
        _ = pair
        raise NotImplementedError

    def get_available_margin_jpy(self) -> float:
        raise NotImplementedError

    def get_symbol_rule(self, pair: str):
        _ = pair
        raise NotImplementedError

    def cancel_order(self, order_id: int) -> None:
        self.canceled_orders.append(order_id)

    def get_order(self, order_id: int):
        _ = order_id
        return None

    def get_executions(self, order_id: int):
        _ = order_id
        return []


class _ProtectiveStopWithUnknownLotExecution:
    def get_mark_price(self, pair: str) -> float:
        _ = pair
        raise NotImplementedError

    def get_available_margin_jpy(self) -> float:
        raise NotImplementedError

    def get_symbol_rule(self, pair: str):
        _ = pair
        raise NotImplementedError

    def submit_close_order(self, request):
        _ = request
        raise NotImplementedError

    def confirm_order(self, order_id: int, timeout_ms: int):
        _ = order_id
        _ = timeout_ms
        raise NotImplementedError

    def cancel_order(self, order_id: int) -> None:
        _ = order_id

    def get_order(self, order_id: int):
        _ = order_id
        return {"orderId": 8218604892, "status": "EXECUTED"}

    def get_executions(self, order_id: int):
        if order_id != 8218604892:
            return []
        return [
            {
                "executionId": 1530999999,
                "positionId": 281135490,
                "size": "0.4",
                "price": "13651",
                "fee": "2",
                "lossGain": "-49.8",
            },
            {
                "executionId": 1531000000,
                "positionId": 999999999,
                "size": "0.1",
                "price": "13651",
                "fee": "1",
                "lossGain": "-10.0",
            },
        ]


class GmoClosePositionTest(unittest.TestCase):
    def test_partial_fill_retries_remaining_size_and_closes_trade(self) -> None:
        trade = _build_open_trade()
        persistence = _FakePersistence(trade)
        execution = _PartialCloseExecution()

        result = close_position(
            ClosePositionDependencies(
                execution=execution,
                lock=object(),  # unused
                logger=_FakeLogger(),
                persistence=persistence,
            ),
            ClosePositionInput(
                config=_build_config(),
                trade=trade,
                close_reason="STOP_LOSS",
                close_price=13675.0,
            ),
        )

        self.assertEqual("CLOSED", result.status)
        self.assertEqual("CLOSED", trade["state"])
        self.assertEqual("CLOSED", trade["position"]["status"])
        self.assertAlmostEqual(-12.0, trade["execution"]["exit_leg_realized_pnl_jpy"])
        self.assertAlmostEqual(-70.0, trade["execution"]["total_realized_pnl_jpy"])
        self.assertAlmostEqual(-70.0, trade["execution"]["realized_pnl_jpy"])
        self.assertAlmostEqual(4.0, trade["execution"]["exit_fee_jpy"])
        self.assertEqual("CONFIRMED", trade["execution"]["exit_submission_state"])
        self.assertAlmostEqual(13675.0, trade["execution"]["exit_reference_price"])
        self.assertCountEqual([8218604891, 8218604892], execution.canceled_orders)
        self.assertEqual("INACTIVE", trade["execution"]["take_profit_order_status"])
        self.assertEqual("INACTIVE", trade["execution"]["stop_loss_order_status"])

    def test_final_fill_closes_trade_and_keeps_cumulative_realized_pnl(self) -> None:
        trade = _build_open_trade()
        trade["position"]["quantity_sol"] = 0.1
        trade["position"]["quote_amount_jpy"] = 1356.8
        trade["position"]["lots"] = [{"position_id": 281135490, "size_sol": 0.1}]
        trade["execution"]["realized_pnl_jpy"] = -58.0
        trade["execution"]["exit_fee_jpy"] = 3.0
        persistence = _FakePersistence(trade)
        execution = _FinalCloseExecution()

        result = close_position(
            ClosePositionDependencies(
                execution=execution,
                lock=object(),  # unused
                logger=_FakeLogger(),
                persistence=persistence,
            ),
            ClosePositionInput(
                config=_build_config(),
                trade=trade,
                close_reason="STOP_LOSS",
                close_price=13944.0,
            ),
        )

        self.assertEqual("CLOSED", result.status)
        self.assertEqual("CLOSED", trade["state"])
        self.assertEqual("CLOSED", trade["position"]["status"])
        self.assertAlmostEqual(-38.0, trade["execution"]["exit_leg_realized_pnl_jpy"])
        self.assertAlmostEqual(-96.0, trade["execution"]["total_realized_pnl_jpy"])
        self.assertAlmostEqual(-96.0, trade["execution"]["realized_pnl_jpy"])
        self.assertAlmostEqual(4.0, trade["execution"]["exit_fee_jpy"])
        self.assertAlmostEqual(13944.0, trade["execution"]["exit_reference_price"])
        self.assertEqual("STOP_LOSS", trade["close_reason"])
        self.assertCountEqual([8218604891, 8218604892], execution.canceled_orders)
        self.assertEqual("INACTIVE", trade["execution"]["take_profit_order_status"])
        self.assertEqual("INACTIVE", trade["execution"]["stop_loss_order_status"])

    def test_reconciles_protective_stop_fill_when_manual_close_hits_position_not_found(self) -> None:
        trade = _build_open_trade()
        trade["execution"]["stop_loss_order"] = {"order_id": 8218604892, "price": 13651.0}
        persistence = _FakePersistence(trade)
        execution = _ProtectiveStopAlreadyFilledExecution()

        result = close_position(
            ClosePositionDependencies(
                execution=execution,
                lock=object(),
                logger=_FakeLogger(),
                persistence=persistence,
            ),
            ClosePositionInput(
                config=_build_config(),
                trade=trade,
                close_reason="STOP_LOSS",
                close_price=13640.0,
            ),
        )

        self.assertEqual("CLOSED", result.status)
        self.assertEqual("CLOSED", trade["state"])
        self.assertEqual("STOP_LOSS", trade["close_reason"])
        self.assertAlmostEqual(13651.0, trade["execution"]["exit_reference_price"])
        self.assertEqual("EXECUTED", trade["execution"]["stop_loss_order_status"])

    def test_reconcile_protective_stop_returns_pending_when_order_is_executed_but_executions_are_not_visible(self) -> None:
        trade = _build_open_trade()
        trade["execution"]["stop_loss_order"] = {"order_id": 8218604892, "price": 13651.0}
        persistence = _FakePersistence(trade)
        execution = _ProtectiveStopExecutedWithoutExecutionsVisible()

        reconciled = reconcile_protective_exit_execution(
            execution=execution,
            logger=_FakeLogger(),
            persistence=persistence,
            trade=trade,
            close_reason="STOP_LOSS",
        )

        self.assertEqual("PENDING", reconciled.status)
        self.assertEqual("EXECUTED", reconciled.order_status)

    def test_manual_close_splits_multiple_lots_into_single_lot_orders(self) -> None:
        trade = _build_open_trade()
        trade["position"]["quantity_sol"] = 0.6
        trade["position"]["quote_amount_jpy"] = 8212.6
        trade["position"]["lots"] = [
            {"position_id": 281135490, "size_sol": 0.2},
            {"position_id": 281135491, "size_sol": 0.4},
        ]
        persistence = _FakePersistence(trade)
        execution = _SequentialMultiLotCloseExecution()

        result = close_position(
            ClosePositionDependencies(
                execution=execution,
                lock=object(),
                logger=_FakeLogger(),
                persistence=persistence,
            ),
            ClosePositionInput(
                config=_build_config(),
                trade=trade,
                close_reason="MANUAL",
                close_price=13690.0,
            ),
        )

        self.assertEqual("CLOSED", result.status)
        self.assertEqual("CLOSED", trade["state"])
        self.assertEqual(2, len(execution.submitted_lots))
        self.assertEqual([{"position_id": 281135490, "size_sol": 0.2}], execution.submitted_lots[0])
        self.assertEqual([{"position_id": 281135491, "size_sol": 0.4}], execution.submitted_lots[1])

    def test_reconcile_protective_stop_ignores_unknown_lots(self) -> None:
        trade = _build_open_trade()
        trade["position"]["quantity_sol"] = 0.6
        trade["position"]["quote_amount_jpy"] = 8212.6
        trade["position"]["lots"] = [
            {"position_id": 281135490, "size_sol": 0.4},
            {"position_id": 281135491, "size_sol": 0.2},
        ]
        trade["execution"]["stop_loss_order_id"] = 8218604892
        trade["execution"]["stop_loss_order"] = {"order_id": 8218604892, "price": 13651.0}
        trade["execution"]["stop_loss_orders"] = [
            {"order_id": 8218604892, "price": 13651.0, "status": "WAITING", "position_id": 281135490, "size_sol": 0.4},
            {"order_id": 8218604893, "price": 13651.0, "status": "WAITING", "position_id": 281135491, "size_sol": 0.2},
        ]
        persistence = _FakePersistence(trade)
        execution = _ProtectiveStopWithUnknownLotExecution()

        reconciled = reconcile_protective_exit_execution(
            execution=execution,
            logger=_FakeLogger(),
            persistence=persistence,
            trade=trade,
            close_reason="STOP_LOSS",
        )

        self.assertEqual("PARTIALLY_CLOSED", reconciled.status)
        self.assertAlmostEqual(0.2, trade["position"]["quantity_sol"])
        self.assertEqual([{"position_id": 281135491, "size_sol": 0.2}], trade["position"]["lots"])
        self.assertEqual("WAITING", trade["execution"]["stop_loss_order_status"])


if __name__ == "__main__":
    unittest.main()
