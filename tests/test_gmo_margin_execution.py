from __future__ import annotations

import unittest
from unittest.mock import patch

from apps.gmo_bot.adapters.execution.gmo_margin_execution import GmoMarginExecutionAdapter
from apps.gmo_bot.app.ports.execution_port import SubmitProtectiveExitOrdersRequest, SymbolRule


class _FakeClient:
    def __init__(self) -> None:
        self.close_order_calls: list[dict[str, object]] = []
        self.canceled_order_ids: list[int] = []
        self.open_positions: list[dict[str, object]] = [
            {"positionId": 10, "size": "0.5", "orderdSize": "0"}
        ]
        self.active_orders: list[dict[str, object]] = []

    def create_close_order(self, **kwargs):
        self.close_order_calls.append(kwargs)
        return 1000 + len(self.close_order_calls)

    def get_open_positions(self, symbol: str):
        _ = symbol
        return self.open_positions

    def get_active_orders(self, symbol: str):
        _ = symbol
        return self.active_orders

    def cancel_order(self, order_id: int) -> None:
        self.canceled_order_ids.append(order_id)


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


class GmoMarginExecutionAdapterTest(unittest.TestCase):
    def test_aggregate_executions_sums_loss_gain(self) -> None:
        adapter = GmoMarginExecutionAdapter(client=_FakeClient(), logger=_FakeLogger())

        result = adapter._aggregate_executions(  # noqa: SLF001
            [
                {
                    "executionId": 1,
                    "positionId": 10,
                    "size": "0.4",
                    "price": "13498",
                    "fee": "2",
                    "lossGain": "-100",
                },
                {
                    "executionId": 2,
                    "positionId": 10,
                    "size": "0.1",
                    "price": "13504",
                    "fee": "1",
                    "lossGain": "-25",
                },
            ]
        )

        self.assertAlmostEqual(0.5, result["filled_base_sol"])
        self.assertAlmostEqual(13499.2, result["avg_fill_price"])
        self.assertAlmostEqual(6749.6, result["filled_quote_jpy"])
        self.assertAlmostEqual(3.0, result["fee_jpy"])
        self.assertAlmostEqual(-125.0, result["realized_pnl_jpy"])
        self.assertEqual([{"position_id": 10, "size_sol": 0.5}], result["lots"])

    def test_submit_protective_exit_orders_places_stop_close_order(self) -> None:
        adapter = GmoMarginExecutionAdapter(client=_FakeClient(), logger=_FakeLogger())
        with patch.object(
            adapter,
            "get_symbol_rule",
            return_value=SymbolRule(symbol="SOL_JPY", tick_size=1.0, size_step=0.01, min_order_size=0.01),
        ):
            submission = adapter.submit_protective_exit_orders(
                SubmitProtectiveExitOrdersRequest(
                    side="SELL",
                    lots=[{"position_id": 10, "size_sol": 0.5}],
                    take_profit_price=14500.0,
                    stop_price=13900.0,
                )
            )

        self.assertIsNone(submission.take_profit_order)
        self.assertEqual(1001, submission.stop_loss_order.order_id)
        fake_client = adapter.client
        self.assertEqual(1, len(fake_client.close_order_calls))
        self.assertEqual("STOP", fake_client.close_order_calls[0]["execution_type"])

    def test_submit_protective_exit_orders_rounds_long_exit_prices_with_safe_direction(self) -> None:
        adapter = GmoMarginExecutionAdapter(client=_FakeClient(), logger=_FakeLogger())
        with patch.object(
            adapter,
            "get_symbol_rule",
            return_value=SymbolRule(symbol="SOL_JPY", tick_size=1.0, size_step=0.01, min_order_size=0.01),
        ):
            adapter.submit_protective_exit_orders(
                SubmitProtectiveExitOrdersRequest(
                    side="SELL",
                    lots=[{"position_id": 10, "size_sol": 0.5}],
                    take_profit_price=15191.228,
                    stop_price=14775.54,
                )
            )

        fake_client = adapter.client
        self.assertEqual(14776.0, fake_client.close_order_calls[0]["price"])

    def test_submit_protective_exit_orders_rounds_short_exit_prices_with_safe_direction(self) -> None:
        adapter = GmoMarginExecutionAdapter(client=_FakeClient(), logger=_FakeLogger())
        with patch.object(
            adapter,
            "get_symbol_rule",
            return_value=SymbolRule(symbol="SOL_JPY", tick_size=1.0, size_step=0.01, min_order_size=0.01),
        ):
            adapter.submit_protective_exit_orders(
                SubmitProtectiveExitOrdersRequest(
                    side="BUY",
                    lots=[{"position_id": 10, "size_sol": 0.5}],
                    take_profit_price=15191.228,
                    stop_price=14775.54,
                )
            )

        fake_client = adapter.client
        self.assertEqual(14775.0, fake_client.close_order_calls[0]["price"])

    def test_submit_protective_exit_orders_keeps_exact_tick_prices_with_decimal_tick(self) -> None:
        adapter = GmoMarginExecutionAdapter(client=_FakeClient(), logger=_FakeLogger())
        with patch.object(
            adapter,
            "get_symbol_rule",
            return_value=SymbolRule(symbol="SOL_JPY", tick_size=0.1, size_step=0.01, min_order_size=0.01),
        ):
            adapter.submit_protective_exit_orders(
                SubmitProtectiveExitOrdersRequest(
                    side="BUY",
                    lots=[{"position_id": 10, "size_sol": 0.5}],
                    take_profit_price=0.3,
                    stop_price=1.2,
                )
            )

        fake_client = adapter.client
        self.assertEqual(1.2, fake_client.close_order_calls[0]["price"])

    def test_submit_protective_exit_orders_cancels_conflicting_close_order_and_retries(self) -> None:
        client = _FakeClient()
        client.open_positions = [{"positionId": 10, "size": "0.5", "orderdSize": "0.5"}]
        client.active_orders = [
            {"orderId": 777, "side": "SELL", "settleType": "CLOSE", "status": "ORDERED"}
        ]

        def cancel_order(order_id: int) -> None:
            client.canceled_order_ids.append(order_id)
            client.open_positions = [{"positionId": 10, "size": "0.5", "orderdSize": "0"}]

        client.cancel_order = cancel_order

        adapter = GmoMarginExecutionAdapter(client=client, logger=_FakeLogger())
        with patch.object(
            adapter,
            "get_symbol_rule",
            return_value=SymbolRule(symbol="SOL_JPY", tick_size=1.0, size_step=0.01, min_order_size=0.01),
        ):
            submission = adapter.submit_protective_exit_orders(
                SubmitProtectiveExitOrdersRequest(
                    side="SELL",
                    lots=[{"position_id": 10, "size_sol": 0.5}],
                    take_profit_price=15191.228,
                    stop_price=14775.54,
                )
            )

        self.assertEqual([777], client.canceled_order_ids)
        self.assertEqual(1001, submission.stop_loss_order.order_id)
        self.assertEqual(1, len(client.close_order_calls))


if __name__ == "__main__":
    unittest.main()
