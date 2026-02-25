from __future__ import annotations

import unittest

from pybot.infra.bootstrap import _should_execute_cycle


class BootstrapPauseGateTest(unittest.TestCase):
    def test_pause_all_runs_when_open_trade_exists(self) -> None:
        should_run = _should_execute_cycle(
            is_five_minute_window=True,
            has_open_trade=True,
            pause_all=True,
        )
        self.assertTrue(should_run)

    def test_pause_all_skips_when_no_open_trade(self) -> None:
        should_run = _should_execute_cycle(
            is_five_minute_window=True,
            has_open_trade=False,
            pause_all=True,
        )
        self.assertFalse(should_run)

    def test_not_paused_runs_on_five_minute_window(self) -> None:
        should_run = _should_execute_cycle(
            is_five_minute_window=True,
            has_open_trade=False,
            pause_all=False,
        )
        self.assertTrue(should_run)

    def test_not_paused_runs_when_open_trade_even_outside_five_minute(self) -> None:
        should_run = _should_execute_cycle(
            is_five_minute_window=False,
            has_open_trade=True,
            pause_all=False,
        )
        self.assertTrue(should_run)

    def test_not_paused_skips_when_no_open_trade_outside_five_minute(self) -> None:
        should_run = _should_execute_cycle(
            is_five_minute_window=False,
            has_open_trade=False,
            pause_all=False,
        )
        self.assertFalse(should_run)


if __name__ == "__main__":
    unittest.main()
