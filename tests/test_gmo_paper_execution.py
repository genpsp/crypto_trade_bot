from __future__ import annotations

import unittest

from apps.gmo_bot.adapters.execution.paper_execution import PaperExecutionAdapter
from apps.gmo_bot.app.ports.execution_port import SubmitCloseOrderRequest, SubmitEntryOrderRequest


class _FakeLogger:
    def info(self, message: str, context=None) -> None:
        _ = message
        _ = context


class GmoPaperExecutionAdapterTest(unittest.TestCase):
    def test_confirm_order_returns_entry_submission_result(self) -> None:
        adapter = PaperExecutionAdapter(_FakeLogger())
        submission = adapter.submit_entry_order(
            SubmitEntryOrderRequest(
                side="BUY",
                size_sol=0.5,
                slippage_bps=3,
                reference_price=20_000.0,
            )
        )

        confirmation = adapter.confirm_order(submission.order_id, timeout_ms=1_000)

        self.assertTrue(confirmation.confirmed)
        self.assertIsNotNone(confirmation.result)
        assert confirmation.result is not None
        self.assertEqual("SIMULATED", confirmation.result["status"])
        self.assertEqual(0.5, confirmation.result["filled_base_sol"])
        self.assertEqual(10_000.0, confirmation.result["filled_quote_jpy"])

    def test_confirm_order_returns_close_submission_result(self) -> None:
        adapter = PaperExecutionAdapter(_FakeLogger())
        submission = adapter.submit_close_order(
            SubmitCloseOrderRequest(
                side="SELL",
                lots=[{"position_id": 123, "size_sol": 0.2}, {"position_id": 124, "size_sol": 0.3}],
                slippage_bps=3,
                reference_price=21_000.0,
            )
        )

        confirmation = adapter.confirm_order(submission.order_id, timeout_ms=1_000)

        self.assertTrue(confirmation.confirmed)
        self.assertIsNotNone(confirmation.result)
        assert confirmation.result is not None
        self.assertEqual("SIMULATED", confirmation.result["status"])
        self.assertEqual(0.5, confirmation.result["filled_base_sol"])
        self.assertEqual(10_500.0, confirmation.result["filled_quote_jpy"])

    def test_confirm_order_fails_for_unknown_paper_order_id(self) -> None:
        adapter = PaperExecutionAdapter(_FakeLogger())

        confirmation = adapter.confirm_order(999, timeout_ms=1_000)

        self.assertFalse(confirmation.confirmed)
        self.assertIn("paper order not found", str(confirmation.error))


if __name__ == "__main__":
    unittest.main()
