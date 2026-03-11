from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from research.src.adapters.csv_bar_repository import read_bars_from_csv, write_json
from research.src.domain.backtest_engine import run_backtest
from research.src.infra.research_config import load_bot_config

import apps.gmo_bot.domain.strategy.models.ema_trend_pullback_15m_v0 as gmo_strategy


DEFAULT_CONFIG = "research/models/gmo_ema_pullback_15m_both_v0/config/current.json"
DEFAULT_BARS = "research/data/raw/soljpy_15m_1y.csv"
DEFAULT_OUTPUT = "research/data/processed/gmo_15m_param_sweep_latest.json"


@dataclass(frozen=True)
class SweepCase:
    name: str
    config_overrides: dict[str, dict[str, Any]]
    strategy_overrides: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze GMO 15m parameter sensitivity on offline backtest")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="JSON config file path")
    parser.add_argument("--bars", default=DEFAULT_BARS, help="OHLCV CSV file path")
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="output report JSON path",
    )
    return parser.parse_args()


def _deep_merge_config(config: dict[str, Any], overrides: dict[str, dict[str, Any]]) -> dict[str, Any]:
    merged = deepcopy(config)
    for section, values in overrides.items():
        if section not in merged:
            raise KeyError(f"unknown config section: {section}")
        target = merged[section]
        if not isinstance(target, dict):
            raise TypeError(f"config section must be object: {section}")
        target.update(values)
    return merged


def _build_cases() -> list[SweepCase]:
    return [
        SweepCase("baseline", {}, {}),
        SweepCase("max_trades=2", {"risk": {"max_trades_per_day": 2}}, {}),
        SweepCase("max_trades=3", {"risk": {"max_trades_per_day": 3}}, {}),
        SweepCase("volatile_size=0.55", {"risk": {"volatile_size_multiplier": 0.55}}, {}),
        SweepCase("volatile_size=0.4", {"risk": {"volatile_size_multiplier": 0.4}}, {}),
        SweepCase("tp=1.6", {"exit": {"take_profit_r_multiple": 1.6}}, {}),
        SweepCase("long_gap=0.1", {}, {"LONG_WEAK_UPPER_TREND_MIN_GAP_PCT": 0.1}),
        SweepCase("long_gap=0.15", {}, {"LONG_WEAK_UPPER_TREND_MIN_GAP_PCT": 0.15}),
        SweepCase("chase=0.7", {}, {"MAX_DISTANCE_FROM_EMA_FAST_PCT": 0.7}),
        SweepCase("rsi_long_upper=64", {}, {"RSI_LONG_UPPER_BOUND": 64}),
        SweepCase("short_gap=0.1", {}, {"SHORT_UPPER_TREND_MIN_GAP_PCT": 0.1}),
        SweepCase(
            "tp=1.6 + short_gap=0.1",
            {"exit": {"take_profit_r_multiple": 1.6}},
            {"SHORT_UPPER_TREND_MIN_GAP_PCT": 0.1},
        ),
        SweepCase(
            "tp=1.6 + max_trades=2",
            {"exit": {"take_profit_r_multiple": 1.6}, "risk": {"max_trades_per_day": 2}},
            {},
        ),
        SweepCase(
            "max_trades=2 + short_gap=0.1",
            {"risk": {"max_trades_per_day": 2}},
            {"SHORT_UPPER_TREND_MIN_GAP_PCT": 0.1},
        ),
        SweepCase(
            "tp=1.6 + max_trades=2 + short_gap=0.1",
            {"exit": {"take_profit_r_multiple": 1.6}, "risk": {"max_trades_per_day": 2}},
            {"SHORT_UPPER_TREND_MIN_GAP_PCT": 0.1},
        ),
    ]


def _summarize_report(report: Any) -> dict[str, Any]:
    closed_trades = [
        trade
        for trade in report.trades
        if trade.exit_reason != "OPEN" and trade.scaled_pnl_pct is not None
    ]
    half_index = len(closed_trades) // 2

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in closed_trades:
        cumulative += float(trade.scaled_pnl_pct)
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)

    total_scaled_pnl_pct = round(sum(float(trade.scaled_pnl_pct) for trade in closed_trades), 6)
    second_half_scaled_pnl_pct = round(
        sum(float(trade.scaled_pnl_pct) for trade in closed_trades[half_index:]),
        6,
    )
    return_to_dd = round(total_scaled_pnl_pct / abs(max_drawdown), 6) if max_drawdown else None
    multiplier_counts = Counter(float(trade.position_size_multiplier or 0.0) for trade in closed_trades)

    return {
        "trades": len(closed_trades),
        "total_scaled_pnl_pct": total_scaled_pnl_pct,
        "max_drawdown_pct_points": round(max_drawdown, 6),
        "return_to_dd": return_to_dd,
        "second_half_scaled_pnl_pct": second_half_scaled_pnl_pct,
        "wins": report.summary.wins,
        "losses": report.summary.losses,
        "win_rate_pct": report.summary.win_rate_pct,
        "position_size_multiplier_counts": dict(sorted(multiplier_counts.items())),
    }


def _run_case(
    case: SweepCase,
    base_config: dict[str, Any],
    bars: list[Any],
) -> dict[str, Any]:
    config = _deep_merge_config(base_config, case.config_overrides)
    originals: dict[str, Any] = {}

    for attr_name, value in case.strategy_overrides.items():
        originals[attr_name] = getattr(gmo_strategy, attr_name)
        setattr(gmo_strategy, attr_name, value)

    try:
        report = run_backtest(bars, config)
    finally:
        for attr_name, value in originals.items():
            setattr(gmo_strategy, attr_name, value)

    return {
        "name": case.name,
        "config_overrides": case.config_overrides,
        "strategy_overrides": case.strategy_overrides,
        "summary": _summarize_report(report),
    }


def main() -> None:
    args = parse_args()
    base_config = load_bot_config(args.config)
    bars = read_bars_from_csv(args.bars)
    results = [_run_case(case, base_config, bars) for case in _build_cases()]

    baseline = next(result for result in results if result["name"] == "baseline")
    baseline_total = baseline["summary"]["total_scaled_pnl_pct"]
    baseline_dd = abs(baseline["summary"]["max_drawdown_pct_points"])
    baseline_second_half = baseline["summary"]["second_half_scaled_pnl_pct"]

    for result in results:
        summary = result["summary"]
        summary["delta_total_scaled_pnl_pct"] = round(
            summary["total_scaled_pnl_pct"] - baseline_total,
            6,
        )
        summary["delta_abs_drawdown_pct_points"] = round(
            abs(summary["max_drawdown_pct_points"]) - baseline_dd,
            6,
        )
        summary["delta_second_half_scaled_pnl_pct"] = round(
            summary["second_half_scaled_pnl_pct"] - baseline_second_half,
            6,
        )

    ranked = sorted(
        results,
        key=lambda item: (
            item["summary"]["return_to_dd"] if item["summary"]["return_to_dd"] is not None else float("-inf"),
            item["summary"]["total_scaled_pnl_pct"],
        ),
        reverse=True,
    )

    payload = {
        "config": str(Path(args.config)),
        "bars": str(Path(args.bars)),
        "results": results,
        "ranked_by_return_to_dd": [result["name"] for result in ranked],
    }
    write_json(args.output, payload)

    print("[research] baseline", baseline["summary"])
    print("[research] top candidates by return_to_dd")
    for result in ranked[:5]:
        print(f"  - {result['name']}: {result['summary']}")
    print("[research] report", args.output)


if __name__ == "__main__":
    main()
