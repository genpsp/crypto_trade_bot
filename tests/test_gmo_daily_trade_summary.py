from __future__ import annotations

import unittest
from datetime import UTC, datetime

from apps.gmo_bot.infra.alerting.daily_trade_summary import build_daily_trade_summary_report


class GmoDailyTradeSummaryTest(unittest.TestCase):
    def test_build_daily_trade_summary_report_aggregates_jpy_trade_and_run_metrics(self) -> None:
        report = build_daily_trade_summary_report(
            target_date_jst="2026-02-26",
            generated_at_utc=datetime(2026, 2, 26, 15, 5, tzinfo=UTC),
            model_payloads=[
                (
                    "gmo_ema_pullback_15m_both_v0",
                    [
                        {
                            "trade_id": "long_win",
                            "direction": "LONG",
                            "state": "CLOSED",
                            "created_at": "2026-02-25T15:10:00Z",
                            "position": {
                                "quote_amount_jpy": 6500.0,
                                "quantity_sol": 0.5,
                                "entry_trigger_price": 13000.0,
                                "entry_price": 13020.0,
                                "exit_trigger_price": 13200.0,
                                "exit_price": 13250.0,
                                "exit_time_iso": "2026-02-25T18:00:00Z",
                            },
                            "execution": {
                                "entry_reference_price": 13010.0,
                                "exit_reference_price": 13240.0,
                                "entry_fee_jpy": 3.0,
                                "exit_fee_jpy": 4.0,
                                "exit_result": {"filled_quote_jpy": 6625.0},
                            },
                        },
                        {
                            "trade_id": "short_loss",
                            "direction": "SHORT",
                            "state": "CLOSED",
                            "created_at": "2026-02-25T16:10:00Z",
                            "position": {
                                "quote_amount_jpy": 6800.0,
                                "quantity_sol": 0.5,
                                "entry_trigger_price": 13600.0,
                                "entry_price": 13600.0,
                                "exit_trigger_price": 13700.0,
                                "exit_price": 13800.0,
                                "exit_time_iso": "2026-02-25T19:00:00Z",
                            },
                            "execution": {
                                "entry_reference_price": 13610.0,
                                "exit_reference_price": 13790.0,
                                "entry_fee_jpy": 3.0,
                                "exit_fee_jpy": 3.0,
                                "exit_result": {"filled_quote_jpy": 6900.0},
                            },
                        },
                        {
                            "trade_id": "failed_trade",
                            "state": "FAILED",
                            "created_at": "2026-02-25T20:00:00Z",
                        },
                        {
                            "trade_id": "canceled_trade",
                            "state": "CANCELED",
                            "created_at": "2026-02-25T21:00:00Z",
                        },
                    ],
                    [
                        {"result": "FAILED", "executed_at_iso": "2026-02-25T15:30:00Z"},
                        {"result": "SKIPPED_ENTRY", "executed_at_iso": "2026-02-25T16:30:00Z"},
                        {"result": "NO_SIGNAL", "executed_at_iso": "2026-02-25T17:30:00Z"},
                    ],
                )
            ],
        )

        self.assertEqual("2026-02-26", report.target_date_jst)
        self.assertEqual(1, len(report.model_summaries))

        summary = report.model_summaries[0]
        self.assertEqual("gmo_ema_pullback_15m_both_v0", summary.model_id)
        self.assertEqual(2, summary.closed_trades)
        self.assertEqual(1, summary.win_trades)
        self.assertEqual(1, summary.loss_trades)
        self.assertAlmostEqual(25.0, summary.realized_pnl_jpy)
        self.assertAlmostEqual(13.0, summary.estimated_fees_jpy)
        self.assertEqual(1, summary.failed_runs)
        self.assertEqual(1, summary.skipped_runs)
        self.assertEqual(1, summary.failed_trades)
        self.assertEqual(1, summary.canceled_trades)
        self.assertEqual(4, summary.slippage_samples)
        self.assertAlmostEqual(7.4596, summary.avg_slippage_bps, places=4)

        self.assertEqual(2, report.total_closed_trades)
        self.assertEqual(1, report.total_win_trades)
        self.assertEqual(1, report.total_loss_trades)
        self.assertAlmostEqual(25.0, report.total_realized_pnl_jpy)
        self.assertAlmostEqual(13.0, report.total_estimated_fees_jpy)
        self.assertEqual(1, report.total_failed_runs)
        self.assertEqual(1, report.total_skipped_runs)
        self.assertEqual(1, report.total_failed_trades)
        self.assertEqual(1, report.total_canceled_trades)

    def test_build_daily_trade_summary_report_prefers_stored_realized_pnl_on_partial_close(self) -> None:
        report = build_daily_trade_summary_report(
            target_date_jst="2026-03-11",
            generated_at_utc=datetime(2026, 3, 11, 15, 5, tzinfo=UTC),
            model_payloads=[
                (
                    "gmo_ema_pullback_15m_both_v0",
                    [
                        {
                            "trade_id": "partial_short",
                            "direction": "SHORT",
                            "state": "CLOSED",
                            "created_at": "2026-03-11T02:45:00Z",
                            "position": {
                                "quote_amount_jpy": 1356.8,
                                "quantity_sol": 0.1,
                                "entry_trigger_price": 13574.0,
                                "entry_price": 13568.0,
                                "exit_trigger_price": 13944.0,
                                "exit_price": 13944.0,
                                "exit_time_iso": "2026-03-11T04:33:00Z",
                            },
                            "execution": {
                                "entry_reference_price": 13570.0,
                                "exit_reference_price": 13940.0,
                                "entry_fee_jpy": 3.0,
                                "exit_fee_jpy": 4.0,
                                "realized_pnl_jpy": -95.1,
                                "exit_result": {
                                    "filled_base_sol": 0.1,
                                    "filled_quote_jpy": 1394.4,
                                },
                            },
                        },
                        {
                            "trade_id": "broken_partial_short",
                            "direction": "SHORT",
                            "state": "CLOSED",
                            "created_at": "2026-03-11T02:45:00Z",
                            "position": {
                                "quote_amount_jpy": 8140.8,
                                "quantity_sol": 0.6,
                                "entry_trigger_price": 13574.0,
                                "entry_price": 13568.0,
                                "exit_trigger_price": 13675.0,
                                "exit_price": 13683.0,
                                "exit_time_iso": "2026-03-11T04:33:00Z",
                            },
                            "execution": {
                                "entry_fee_jpy": 3.0,
                                "exit_fee_jpy": 3.0,
                                "exit_result": {
                                    "filled_base_sol": 0.5,
                                    "filled_quote_jpy": 6841.5,
                                },
                            },
                        },
                    ],
                    [],
                )
            ],
        )

        summary = report.model_summaries[0]
        self.assertEqual(2, summary.closed_trades)
        self.assertEqual(0, summary.win_trades)
        self.assertEqual(1, summary.loss_trades)
        self.assertAlmostEqual(-95.1, summary.realized_pnl_jpy)
        self.assertAlmostEqual(13.0, summary.estimated_fees_jpy)
        self.assertEqual(2, summary.slippage_samples)

    def test_build_daily_trade_summary_report_prefers_explicit_total_realized_pnl_jpy(self) -> None:
        report = build_daily_trade_summary_report(
            target_date_jst="2026-03-30",
            generated_at_utc=datetime(2026, 3, 30, 15, 5, tzinfo=UTC),
            model_payloads=[
                (
                    "gmo_ema_pullback_15m_both_v0",
                    [
                        {
                            "trade_id": "partial_short_total",
                            "direction": "SHORT",
                            "state": "CLOSED",
                            "created_at": "2026-03-29T15:10:00Z",
                            "position": {
                                "quote_amount_jpy": 1319.4,
                                "quantity_sol": 0.1,
                                "entry_price": 13194.0,
                                "exit_price": 12904.0,
                                "exit_time_iso": "2026-03-29T22:42:58Z",
                            },
                            "execution": {
                                "total_realized_pnl_jpy": -56.0,
                                "realized_pnl_jpy": 29.0,
                                "entry_fee_jpy": 3.0,
                                "exit_fee_jpy": 1.0,
                                "exit_result": {
                                    "realized_pnl_jpy": 29.0,
                                    "filled_quote_jpy": 1290.4,
                                    "filled_base_sol": 0.1,
                                },
                            },
                        }
                    ],
                    [],
                )
            ],
        )

        summary = report.model_summaries[0]
        self.assertAlmostEqual(-56.0, summary.realized_pnl_jpy)

    def test_build_daily_trade_summary_report_ignores_legacy_trigger_based_slippage_values(self) -> None:
        report = build_daily_trade_summary_report(
            target_date_jst="2026-03-11",
            generated_at_utc=datetime(2026, 3, 11, 15, 5, tzinfo=UTC),
            model_payloads=[
                (
                    "gmo_ema_pullback_15m_both_v0",
                    [
                        {
                            "trade_id": "legacy_trade",
                            "direction": "LONG",
                            "state": "CLOSED",
                            "created_at": "2026-03-11T02:45:00Z",
                            "position": {
                                "quote_amount_jpy": 6500.0,
                                "quantity_sol": 0.5,
                                "entry_trigger_price": 13000.0,
                                "entry_price": 13100.0,
                                "exit_trigger_price": 13200.0,
                                "exit_price": 13250.0,
                                "exit_time_iso": "2026-03-11T04:33:00Z",
                            },
                            "execution": {
                                "entry_fee_jpy": 3.0,
                                "exit_fee_jpy": 3.0,
                                "exit_result": {"filled_quote_jpy": 6625.0},
                            },
                        }
                    ],
                    [],
                )
            ],
        )

        summary = report.model_summaries[0]
        self.assertEqual(0, summary.slippage_samples)
        self.assertAlmostEqual(0.0, summary.avg_slippage_bps)


if __name__ == "__main__":
    unittest.main()
