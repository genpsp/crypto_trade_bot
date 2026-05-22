"""Rolling-window evaluation of Track A exit-policy candidates.

Slices the CSV into N contiguous chunks (rolling forward, no overlap) and
re-runs the smoke matrix on each. Outputs per-window sum_scaled_pnl_pct so we
can see whether the winning exit policy from the long backtest holds up across
time slices.

This is a stripped-down stand-in for the full `run_sweep --type walk_forward`
flow — useful for fast iteration before committing to a full sweep run.

Usage:

    python -m research.scripts.explore_track_a_rolling \\
        --windows 10 \\
        --window-bars 3000

Each window is `window_bars` consecutive bars. Windows step by `window_bars`
(non-overlapping). The most-recent `windows * window_bars` bars are used.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.src.adapters.csv_bar_repository import read_bars_from_csv
from research.src.data.regime_tagger import attach_regime_tags
from research.src.domain.backtest_engine import run_backtest
from research.src.infra.research_config import load_bot_config
from research.scripts.explore_track_a_exit_policies import _build_cases, _make_config


@dataclass
class _WindowResult:
    case_name: str
    window_index: int
    closed_trades: int
    sum_scaled_pnl_pct: float
    win_rate_pct: float


def _evaluate(case_name: str, components: dict | None, bars, base_config) -> _WindowResult:
    from research.scripts.explore_track_a_exit_policies import _Case
    from research.src.domain.backtest_engine import run_backtest as _rb

    case = _Case(name=case_name, components=components)
    config = _make_config(base_config, case)
    report = _rb(bars=bars, config=config)
    closed = [trade for trade in report.trades if trade.exit_reason != "OPEN"]
    if not closed:
        return _WindowResult(case_name, 0, 0, 0.0, 0.0)
    wins = sum(1 for trade in closed if (trade.pnl_pct or 0) > 0)
    sum_scaled = sum(trade.scaled_pnl_pct or 0.0 for trade in closed)
    win_rate = (wins / len(closed)) * 100
    return _WindowResult(case_name, 0, len(closed), round(sum_scaled, 4), round(win_rate, 2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="research/data/raw/soljpy_15m_to_2026_05.csv")
    parser.add_argument(
        "--base-config",
        default="research/models/gmo_ema_pullback_15m_both_v0/config/current.json",
    )
    parser.add_argument("--windows", type=int, default=10)
    parser.add_argument("--window-bars", type=int, default=3000)
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()

    base_config = load_bot_config(args.base_config)
    all_bars = read_bars_from_csv(args.bars)
    total_needed = args.windows * args.window_bars
    if total_needed > len(all_bars):
        raise SystemExit(
            f"need {total_needed} bars but CSV has {len(all_bars)}; "
            "lower --windows or --window-bars"
        )
    all_bars = all_bars[-total_needed:]
    attach_regime_tags(all_bars)

    cases = _build_cases()
    # per-case list of per-window scaled pnl values
    case_to_window_pnl: dict[str, list[float]] = {case.name: [] for case in cases}
    case_to_window_trades: dict[str, list[int]] = {case.name: [] for case in cases}
    case_to_window_wr: dict[str, list[float]] = {case.name: [] for case in cases}

    for window_index in range(args.windows):
        start = window_index * args.window_bars
        end = start + args.window_bars
        bars = all_bars[start:end]
        if len(bars) < args.window_bars:
            break
        for case in cases:
            config = _make_config(base_config, case)
            report = run_backtest(bars=bars, config=config)
            closed = [trade for trade in report.trades if trade.exit_reason != "OPEN"]
            sum_scaled = sum(trade.scaled_pnl_pct or 0.0 for trade in closed)
            wins = sum(1 for trade in closed if (trade.pnl_pct or 0) > 0)
            wr = (wins / len(closed) * 100) if closed else 0.0
            case_to_window_pnl[case.name].append(round(sum_scaled, 4))
            case_to_window_trades[case.name].append(len(closed))
            case_to_window_wr[case.name].append(round(wr, 2))

    # Summary table
    print("\n## Per-window sum_scaled_pnl_pct\n")
    headers = ["case"] + [f"w{i}" for i in range(args.windows)] + ["min", "mean", "pos_rate%"]
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")
    summary: list[dict[str, Any]] = []
    for case in cases:
        pnls = case_to_window_pnl[case.name]
        if not pnls:
            continue
        win_min = min(pnls)
        win_mean = sum(pnls) / len(pnls)
        pos_rate = sum(1 for v in pnls if v > 0) / len(pnls) * 100
        cells = [case.name] + [f"{v:+.2f}" for v in pnls] + [
            f"{win_min:+.2f}",
            f"{win_mean:+.2f}",
            f"{pos_rate:.1f}",
        ]
        print("| " + " | ".join(cells) + " |")
        summary.append(
            {
                "case": case.name,
                "per_window_scaled_pnl_pct": pnls,
                "min": win_min,
                "mean": win_mean,
                "pos_rate_pct": pos_rate,
                "per_window_trades": case_to_window_trades[case.name],
                "per_window_win_rate_pct": case_to_window_wr[case.name],
            }
        )

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[track-a-rolling] wrote summary: {args.output_json}")


if __name__ == "__main__":
    main()
