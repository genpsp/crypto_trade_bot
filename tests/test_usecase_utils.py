from __future__ import annotations

import unittest

from pybot.app.usecases.usecase_utils import summarize_error_for_log


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


if __name__ == "__main__":
    unittest.main()
