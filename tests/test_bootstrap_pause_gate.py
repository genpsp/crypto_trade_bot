from __future__ import annotations

import unittest

from apps.dex_bot.infra.bootstrap import _compute_dex_close_metrics, _should_execute_cycle


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

    def test_compute_dex_close_metrics_uses_short_base_delta_profit(self) -> None:
        gross_pnl, fee, net_pnl = _compute_dex_close_metrics(
            {
                "direction": "SHORT",
                "position": {
                    "quote_amount_usdc": 100.0,
                    "quantity_sol": 1.0,
                    "entry_price": 100.0,
                    "exit_price": 80.0,
                },
                "execution": {
                    "exit_result": {
                        "spent_quote_usdc": 100.0,
                        "filled_base_sol": 1.25,
                    }
                },
            }
        )

        self.assertAlmostEqual(20.0, gross_pnl)
        self.assertAlmostEqual(0.0, fee)
        self.assertAlmostEqual(20.0, net_pnl)


if __name__ == "__main__":
    unittest.main()
