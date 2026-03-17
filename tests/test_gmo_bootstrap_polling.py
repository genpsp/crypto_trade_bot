from __future__ import annotations

import unittest

from apps.gmo_bot.infra.bootstrap import _should_log_cycle_result


class GmoBootstrapPollingTest(unittest.TestCase):
    def test_normal_cycle_logs_hold_result(self) -> None:
        self.assertTrue(_should_log_cycle_result(run_result="HOLD", high_frequency_poll=False))

    def test_high_frequency_poll_suppresses_hold_result(self) -> None:
        self.assertFalse(_should_log_cycle_result(run_result="HOLD", high_frequency_poll=True))

    def test_high_frequency_poll_still_logs_close_result(self) -> None:
        self.assertTrue(_should_log_cycle_result(run_result="CLOSED", high_frequency_poll=True))


if __name__ == "__main__":
    unittest.main()
