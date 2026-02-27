from __future__ import annotations

import unittest
from datetime import UTC, datetime

from pybot.infra.alerting.daily_trade_summary import (
    build_daily_summary_report,
    build_daily_summary_window,
    iter_utc_day_ids,
)


class DailyTradeSummaryTest(unittest.TestCase):
    def test_iter_utc_day_ids_for_jst_day_returns_two_days(self) -> None:
        window = build_daily_summary_window("2026-02-26")
        self.assertEqual(["2026-02-25", "2026-02-26"], iter_utc_day_ids(window))

    def test_build_daily_summary_report_aggregates_trade_and_run_metrics(self) -> None:
        trades_model_a = [
            {
                "trade_id": "t1",
                "direction": "LONG",
                "state": "CLOSED",
                "created_at": "2026-02-25T15:10:00Z",
                "position": {
                    "quote_amount_usdc": 100.0,
                    "quantity_sol": 1.0,
                    "entry_trigger_price": 100.0,
                    "entry_price": 100.5,
                    "exit_trigger_price": 102.0,
                    "exit_price": 103.0,
                    "exit_time_iso": "2026-02-26T01:00:00Z",
                },
                "execution": {
                    "exit_result": {"spent_quote_usdc": 103.0},
                    "entry_fee_lamports": 5_000,
                    "exit_fee_lamports": 6_000,
                },
            },
            {
                "trade_id": "t_failed",
                "state": "FAILED",
                "created_at": "2026-02-25T16:00:00Z",
            },
            {
                "trade_id": "t_cancel",
                "state": "CANCELED",
                "created_at": "2026-02-25T17:00:00Z",
            },
            {
                "trade_id": "t_outside",
                "direction": "LONG",
                "state": "CLOSED",
                "created_at": "2026-02-24T00:00:00Z",
                "position": {
                    "quote_amount_usdc": 10.0,
                    "quantity_sol": 0.1,
                    "entry_price": 100.0,
                    "exit_price": 101.0,
                    "exit_time_iso": "2026-02-24T02:00:00Z",
                },
                "execution": {"exit_result": {"spent_quote_usdc": 10.1}},
            },
        ]
        runs_model_a = [
            {"result": "FAILED", "executed_at_iso": "2026-02-25T15:30:00Z"},
            {"result": "SKIPPED_ENTRY", "last_executed_at_iso": "2026-02-25T16:30:00Z"},
            {"result": "NO_SIGNAL", "executed_at_iso": "2026-02-25T17:30:00Z"},
            {"result": "FAILED", "executed_at_iso": "2026-02-24T12:00:00Z"},
        ]

        trades_model_b = [
            {
                "trade_id": "s1",
                "direction": "SHORT",
                "state": "CLOSED",
                "created_at": "2026-02-25T15:20:00Z",
                "position": {
                    "quote_amount_usdc": 80.0,
                    "quantity_sol": 0.9,
                    "entry_trigger_price": 88.0,
                    "entry_price": 87.0,
                    "exit_trigger_price": 89.0,
                    "exit_price": 91.0,
                    "exit_time_iso": "2026-02-25T22:00:00Z",
                },
                "execution": {
                    "exit_result": {"spent_quote_usdc": 82.0},
                },
            }
        ]
        runs_model_b = [{"result": "SKIPPED", "executed_at_iso": "2026-02-25T18:00:00Z"}]

        report = build_daily_summary_report(
            target_date_jst="2026-02-26",
            generated_at_utc=datetime(2026, 2, 26, 15, 5, tzinfo=UTC),
            model_payloads=[
                ("ema_pullback_15m_both_v0", trades_model_a, runs_model_a),
                ("storm_2h_short_v0", trades_model_b, runs_model_b),
            ],
        )

        self.assertEqual(2, len(report.model_summaries))

        model_a = report.model_summaries[0]
        self.assertEqual("ema_pullback_15m_both_v0", model_a.model_id)
        self.assertEqual(1, model_a.closed_trades)
        self.assertEqual(1, model_a.win_trades)
        self.assertEqual(0, model_a.loss_trades)
        self.assertAlmostEqual(3.0, model_a.realized_pnl_usdc)
        self.assertGreater(model_a.estimated_fees_usdc, 0.0)
        self.assertEqual(1, model_a.failed_runs)
        self.assertEqual(1, model_a.skipped_runs)
        self.assertEqual(1, model_a.failed_trades)
        self.assertEqual(1, model_a.canceled_trades)
        self.assertEqual(2, model_a.slippage_samples)

        model_b = report.model_summaries[1]
        self.assertEqual("storm_2h_short_v0", model_b.model_id)
        self.assertEqual(1, model_b.closed_trades)
        self.assertEqual(0, model_b.win_trades)
        self.assertEqual(1, model_b.loss_trades)
        self.assertAlmostEqual(-2.0, model_b.realized_pnl_usdc)
        self.assertEqual(0, model_b.failed_runs)
        self.assertEqual(1, model_b.skipped_runs)

        self.assertEqual(2, report.total_closed_trades)
        self.assertEqual(1, report.total_win_trades)
        self.assertEqual(1, report.total_loss_trades)
        self.assertAlmostEqual(1.0, report.total_realized_pnl_usdc)
        self.assertEqual(1, report.total_failed_runs)
        self.assertEqual(2, report.total_skipped_runs)
        self.assertEqual(1, report.total_failed_trades)
        self.assertEqual(1, report.total_canceled_trades)


if __name__ == "__main__":
    unittest.main()
