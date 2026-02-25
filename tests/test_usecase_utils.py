from __future__ import annotations

import unittest

from pybot.app.usecases.usecase_utils import (
    is_market_condition_error_message,
    is_non_retriable_error_message,
    is_slippage_error_message,
    summarize_error_for_log,
)


class UsecaseUtilsSummaryTest(unittest.TestCase):
    def test_summarize_error_for_log_extracts_nested_message(self) -> None:
        message = (
            "RPC sendTransaction failed: {'code': -32002, 'message': 'Transaction simulation failed: "
            "Error processing Instruction 3: custom program error: 0x1771', 'data': {'logs': ['...']}}"
        )

        summarized = summarize_error_for_log(message)

        self.assertEqual(
            "Transaction simulation failed: Error processing Instruction 3: custom program error: 0x1771",
            summarized,
        )

    def test_summarize_error_for_log_truncates_long_message(self) -> None:
        long_message = "x" * 500

        summarized = summarize_error_for_log(long_message, max_length=30)

        self.assertEqual(30, len(summarized))
        self.assertTrue(summarized.endswith("..."))


class UsecaseUtilsErrorClassificationTest(unittest.TestCase):
    def test_slippage_detection_supports_exact_out_not_matched(self) -> None:
        message = (
            "RPC sendTransaction failed: {'code': -32002, 'message': "
            "'Transaction simulation failed: Error processing Instruction 5: "
            "custom program error: 0x1781'}"
        )

        self.assertTrue(is_slippage_error_message(message))
        self.assertTrue(is_non_retriable_error_message(message))

    def test_market_condition_detection_supports_no_routes(self) -> None:
        message = "Jupiter quote request failed: NO_ROUTES_FOUND"

        self.assertTrue(is_market_condition_error_message(message))
        self.assertTrue(is_non_retriable_error_message(message))

    def test_fatal_jupiter_custom_error_is_non_retriable(self) -> None:
        message = (
            "Transaction simulation failed: Error processing Instruction 3: "
            "custom program error: 0x1778"
        )

        self.assertFalse(is_slippage_error_message(message))
        self.assertTrue(is_non_retriable_error_message(message))

    def test_simulation_failure_is_not_non_retriable_without_specific_reason(self) -> None:
        message = (
            "RPC sendTransaction failed: {'code': -32002, 'message': "
            "'Transaction simulation failed: Error processing Instruction 3: custom program error: 0x1234'}"
        )

        self.assertFalse(is_non_retriable_error_message(message))


if __name__ == "__main__":
    unittest.main()
