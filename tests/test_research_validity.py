from __future__ import annotations

from datetime import UTC, datetime, timedelta
import tempfile
import unittest
from unittest.mock import patch

from apps.dex_bot.domain.model.types import BotConfig, EntrySignalDecision, OhlcvBar
from research.scripts.build_execution_profile import build_profile
from research.src.domain.backtest_engine import run_backtest
from research.src.eval.gates import evaluate_gate_a
from research.src.eval.shadow_compare import compare_trade_logs
from research.src.eval.statistics import power_analysis


def _config() -> BotConfig:
    return {
        "enabled": True,
        "network": "mainnet-beta",
        "pair": "SOL/USDC",
        "direction": "LONG",
        "signal_timeframe": "15m",
        "strategy": {"name": "ema_trend_pullback_15m_v0", "ema_fast_period": 21, "ema_slow_period": 55, "swing_low_lookback_bars": 6, "entry": "ON_BAR_CLOSE"},
        "risk": {"max_loss_per_trade_pct": 3.0, "max_trades_per_day": 10, "volatile_atr_pct_threshold": 1.3, "storm_atr_pct_threshold": 1.4, "volatile_size_multiplier": 0.75, "storm_size_multiplier": 0.5},
        "execution": {"mode": "PAPER", "swap_provider": "JUPITER", "slippage_bps": 0, "min_notional_usdc": 20.0, "only_direct_routes": False, "initial_quote_balance": 1000.0},
        "exit": {"stop": "SWING_LOW", "take_profit_r_multiple": 2.0},
        "meta": {"config_version": 1, "note": "test"},
    }


def _bars() -> list[OhlcvBar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    specs = [(100.0, 100.2, 99.8, 100.0), (110.0, 113.0, 109.0, 112.0), (112.0, 116.0, 111.0, 115.0)]
    return [
        OhlcvBar(
            open_time=start + timedelta(minutes=15 * idx),
            close_time=start + timedelta(minutes=15 * (idx + 1)),
            open=o,
            high=h,
            low=l,
            close=c,
            volume=1000.0,
        )
        for idx, (o, h, l, c) in enumerate(specs)
    ]


def _decision() -> EntrySignalDecision:
    return EntrySignalDecision(type="ENTER", summary="enter", ema_fast=101.0, ema_slow=100.0, entry_price=100.0, stop_price=99.0, take_profit_price=102.0)


class ResearchValidityTest(unittest.TestCase):
    def test_pessimistic_execution_enters_on_next_open(self) -> None:
        config = _config()
        config["execution"]["model_id"] = "pessimistic_v1"
        config["execution"]["additional_slippage_bps"] = 0.0
        with patch("research.src.domain.backtest_engine.evaluate_strategy_for_model", return_value=_decision()):
            report = run_backtest(_bars()[:2], config)
        self.assertEqual(1, report.summary.open_trades)
        self.assertEqual(110.0, report.trades[0].entry_price)
        self.assertEqual("pessimistic_v1", report.summary.execution_model_id)

    def test_stochastic_execution_can_reject_entries(self) -> None:
        config = _config()
        config["execution"].update({"model_id": "stochastic_v1", "p_reject": 1.0, "seed": 1})
        with patch("research.src.domain.backtest_engine.evaluate_strategy_for_model", return_value=_decision()):
            report = run_backtest(_bars(), config)
        self.assertEqual(0, report.summary.closed_trades)
        self.assertGreater(report.no_signal_reason_counts["ENTRY_REJECTED_BY_EXECUTION_MODEL"], 0)

    def test_gate_a_reports_failed_checks(self) -> None:
        result = evaluate_gate_a({"closed_trades": 3, "total_scaled_pnl_pct_ci_low": -1.0, "return_to_dd_ci_low": 0.1, "dsr_p_value": 0.5})
        self.assertFalse(result["passed"])
        self.assertIn("min_trades", result["failed_checks"])

    def test_shadow_compare_matches_by_time_and_direction(self) -> None:
        live = [{"entry_time": "2026-01-01T00:15:00Z", "direction": "LONG", "entry_price": 101.0, "scaled_pnl_pct": 1.0}]
        backtest = [{"entry_time": "2026-01-01T00:16:00Z", "direction": "LONG", "entry_price": 100.0, "scaled_pnl_pct": 0.8}]
        diff = compare_trade_logs(live_trades=live, backtest_trades=backtest)
        self.assertEqual(1, diff["summary"]["matched_count"])
        self.assertEqual("EXECUTION", diff["matches"][0]["cause"])

    def test_execution_profile_builds_slippage_and_reject_rates(self) -> None:
        profile = build_profile(
            [
                {"direction": "LONG", "expected_price": 100, "actual_fill_price": 101, "state": "CLOSED"},
                {"direction": "LONG", "expected_price": 100, "actual_fill_price": 100, "state": "REJECTED"},
            ],
            broker="TEST",
            pair="SOL/USDC",
        )
        self.assertEqual(2, profile["sample_count"])
        self.assertAlmostEqual(0.5, profile["p_reject"])
        self.assertIn("LONG", profile["by_direction"])

    def test_power_analysis_returns_positive_recommendation(self) -> None:
        self.assertGreater(power_analysis(0.45, 1.8), 0)


if __name__ == "__main__":
    unittest.main()
