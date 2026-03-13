from __future__ import annotations

import unittest

from apps.gmo_bot.adapters.execution.gmo_margin_execution import GmoMarginExecutionAdapter


class _FakeClient:
    pass


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


if __name__ == "__main__":
    unittest.main()
