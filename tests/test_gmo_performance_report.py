from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

from apps.gmo_bot.app.reporting.attribution import compute_attribution
from apps.gmo_bot.app.reporting.dataset import (
    build_balances_dataframe,
    build_runs_dataframe,
    build_trades_dataframe,
)
from apps.gmo_bot.app.reporting.generate_report import _build_decision_log
from apps.gmo_bot.app.reporting.metrics import annotate_closed_trades, compute_metrics

JST = timezone(timedelta(hours=9))


def _trade(
    *,
    trade_id: str,
    state: str,
    direction: str = "LONG",
    pnl_jpy: float | None = None,
    entry_iso: str = "2026-04-01T09:00:00+09:00",
    exit_iso: str = "2026-04-01T10:30:00+09:00",
    entry_price: float = 100.0,
    exit_price: float = 110.0,
    quantity: float = 1.0,
    close_reason: str = "TAKE_PROFIT",
    entry_fee: float = 5.0,
    exit_fee: float = 5.0,
    entry_reference: float | None = 100.0,
    exit_reference: float | None = 110.0,
) -> dict:
    execution: dict = {
        "entry_fee_jpy": entry_fee,
        "exit_fee_jpy": exit_fee,
        "exit_result": {},
    }
    if pnl_jpy is not None:
        execution["total_realized_pnl_jpy"] = pnl_jpy
    if entry_reference is not None:
        execution["entry_reference_price"] = entry_reference
    if exit_reference is not None:
        execution["exit_reference_price"] = exit_reference

    return {
        "trade_id": trade_id,
        "model_id": "gmo_test",
        "pair": "SOL/JPY",
        "direction": direction,
        "state": state,
        "close_reason": close_reason,
        "created_at": entry_iso,
        "updated_at": exit_iso,
        "position": {
            "entry_time_iso": entry_iso,
            "exit_time_iso": exit_iso,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity_sol": quantity,
            "quote_amount_jpy": entry_price * quantity,
        },
        "execution": execution,
        "signal": {"ema_fast": 100.5, "ema_slow": 99.5},
        "plan": {"r_multiple": 2.0},
    }


class PerformanceReportTest(unittest.TestCase):
    def test_build_trades_dataframe_parses_jst_and_slippage(self) -> None:
        trades = [
            _trade(
                trade_id="t1",
                state="CLOSED",
                entry_iso="2026-04-01T09:00:00+09:00",
                exit_iso="2026-04-01T11:00:00+09:00",
                entry_price=100.0,
                entry_reference=99.0,
                exit_price=110.0,
                exit_reference=111.0,
            )
        ]
        df = build_trades_dataframe(trades)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["trade_id"], "t1")
        self.assertAlmostEqual(df.iloc[0]["entry_slippage_bps"], abs(100 - 99) / 99 * 10_000, places=4)
        self.assertAlmostEqual(df.iloc[0]["exit_slippage_bps"], abs(110 - 111) / 111 * 10_000, places=4)
        self.assertEqual(df.iloc[0]["entry_time_jst"].tzinfo.utcoffset(None), timedelta(hours=9))

    def test_compute_metrics_basic(self) -> None:
        trades = [
            _trade(trade_id="w1", state="CLOSED", pnl_jpy=200.0, exit_iso="2026-04-01T10:00:00+09:00"),
            _trade(trade_id="w2", state="CLOSED", pnl_jpy=100.0, exit_iso="2026-04-02T10:00:00+09:00"),
            _trade(trade_id="l1", state="CLOSED", pnl_jpy=-150.0, exit_iso="2026-04-03T10:00:00+09:00", close_reason="STOP_LOSS"),
            _trade(trade_id="open1", state="CONFIRMED", entry_iso="2026-04-04T10:00:00+09:00"),
        ]
        df = build_trades_dataframe(trades)
        closed = annotate_closed_trades(df)
        self.assertEqual(len(closed), 3)
        metrics = compute_metrics(closed, pd.DataFrame(), total_trades_count=len(trades))
        self.assertEqual(metrics.total_trades, 4)
        self.assertEqual(metrics.closed_trades, 3)
        self.assertEqual(metrics.win_trades, 2)
        self.assertEqual(metrics.loss_trades, 1)
        self.assertAlmostEqual(metrics.win_rate_pct, 66.6666, places=2)
        self.assertEqual(metrics.gross_pnl_jpy, 150.0)
        self.assertEqual(metrics.fees_jpy, 30.0)  # 3 trades × (5 + 5)
        self.assertEqual(metrics.net_pnl_jpy, 120.0)
        self.assertAlmostEqual(metrics.profit_factor, 300.0 / 150.0, places=4)
        self.assertEqual(metrics.longest_loss_streak, 1)

    def test_compute_metrics_drawdown(self) -> None:
        trades = [
            _trade(trade_id="w1", state="CLOSED", pnl_jpy=500.0, exit_iso="2026-04-01T10:00:00+09:00"),
            _trade(trade_id="l1", state="CLOSED", pnl_jpy=-200.0, exit_iso="2026-04-02T10:00:00+09:00"),
            _trade(trade_id="l2", state="CLOSED", pnl_jpy=-100.0, exit_iso="2026-04-03T10:00:00+09:00"),
            _trade(trade_id="w2", state="CLOSED", pnl_jpy=50.0, exit_iso="2026-04-04T10:00:00+09:00"),
        ]
        closed = annotate_closed_trades(build_trades_dataframe(trades))
        metrics = compute_metrics(closed, pd.DataFrame(), total_trades_count=len(trades))
        self.assertEqual(metrics.max_drawdown_jpy, -300.0)
        self.assertAlmostEqual(metrics.max_drawdown_pct, -60.0, places=4)

    def test_compute_metrics_derives_pnl_when_execution_missing(self) -> None:
        trades = [
            _trade(
                trade_id="derived",
                state="CLOSED",
                pnl_jpy=None,
                entry_price=100.0,
                exit_price=120.0,
                quantity=2.0,
            )
        ]
        closed = annotate_closed_trades(build_trades_dataframe(trades))
        self.assertEqual(closed.iloc[0]["pnl_jpy"], 40.0)  # (120-100)*2

    def test_compute_attribution_groups_and_clusters(self) -> None:
        trades = [
            _trade(trade_id="l1", state="CLOSED", pnl_jpy=-50.0, close_reason="STOP_LOSS", exit_iso="2026-04-01T10:00:00+09:00"),
            _trade(trade_id="l2", state="CLOSED", pnl_jpy=-50.0, close_reason="STOP_LOSS", exit_iso="2026-04-02T10:00:00+09:00"),
            _trade(trade_id="l3", state="CLOSED", pnl_jpy=-50.0, close_reason="STOP_LOSS", exit_iso="2026-04-03T10:00:00+09:00"),
            _trade(trade_id="w1", state="CLOSED", pnl_jpy=200.0, close_reason="TAKE_PROFIT", exit_iso="2026-04-04T10:00:00+09:00"),
        ]
        closed = annotate_closed_trades(build_trades_dataframe(trades))
        result = compute_attribution(closed)
        reasons = {row.label: row for row in result.by_close_reason}
        self.assertIn("STOP_LOSS", reasons)
        self.assertEqual(reasons["STOP_LOSS"].count, 3)
        self.assertEqual(reasons["TAKE_PROFIT"].count, 1)
        self.assertEqual(len(result.loss_clusters), 1)
        self.assertEqual(result.loss_clusters[0].trade_count, 3)

    def test_decision_log_groups_no_signal_and_skipped(self) -> None:
        runs = [
            {"run_id": "r1", "result": "NO_SIGNAL", "reason": "RSI_OUT_OF_BOUNDS", "occurrence_count": 5, "executed_at_iso": "2026-04-01T10:00:00+09:00"},
            {"run_id": "r2", "result": "NO_SIGNAL", "reason": "EMA_GAP_TOO_SMALL", "occurrence_count": 3, "executed_at_iso": "2026-04-01T10:00:00+09:00"},
            {"run_id": "r3", "result": "SKIPPED_ENTRY", "reason": "slippage exceeded", "occurrence_count": 2, "executed_at_iso": "2026-04-01T10:00:00+09:00"},
            {"run_id": "r4", "result": "FAILED", "summary": "boom", "reason": "exchange error", "executed_at_iso": "2026-04-01T10:05:00+09:00"},
        ]
        runs_df = build_runs_dataframe(runs)
        log = _build_decision_log(runs_df)
        no_signal = dict(log["no_signal_reasons"])
        self.assertEqual(no_signal["RSI_OUT_OF_BOUNDS"], 5)
        self.assertEqual(no_signal["EMA_GAP_TOO_SMALL"], 3)
        self.assertEqual(log["skipped_reasons"][0][0], "SKIPPED_ENTRY")
        self.assertEqual(len(log["failed_runs"]), 1)
        self.assertEqual(log["failed_runs"][0]["reason"], "exchange error")

    def test_balances_dataframe_sorted_by_date(self) -> None:
        balances = [
            {"snapshot_date_jst": "2026-04-03", "balance_jpy": 1_005_000.0},
            {"snapshot_date_jst": "2026-04-01", "balance_jpy": 1_000_000.0},
            {"snapshot_date_jst": "2026-04-02", "balance_jpy": 998_000.0},
        ]
        df = build_balances_dataframe(balances)
        self.assertEqual(list(df["snapshot_date_jst"]), ["2026-04-01", "2026-04-02", "2026-04-03"])
        metrics = compute_metrics(pd.DataFrame(), df, total_trades_count=0)
        self.assertEqual(metrics.start_balance, 1_000_000.0)
        self.assertEqual(metrics.end_balance, 1_005_000.0)
        self.assertAlmostEqual(metrics.cumulative_return_pct, 0.5, places=4)


if __name__ == "__main__":
    unittest.main()
