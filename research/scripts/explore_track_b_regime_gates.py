"""Track B (regime gate) exploration on real SOL/JPY 15m data.

Layers a RegimeGate on top of two exit-policy baselines:
  * v0 / v2 default exit (legacy fixed-R)
  * Track A's marginal winner: A4 Time-exit @ 120bar

Outputs single-period and rolling tables, mirroring
`explore_track_a_exit_policies.py`.

Usage:

    python -m research.scripts.explore_track_b_regime_gates \\
        --tail-bars 30000
    python -m research.scripts.explore_track_b_regime_gates \\
        --tail-bars 30000 --rolling-windows 10 --rolling-window-bars 3000
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


@dataclass(frozen=True)
class _Case:
    name: str
    components: dict[str, Any] | None  # None → v0


def _exit_a4_time_120() -> dict[str, Any]:
    return {"type": "time_exit", "max_holding_bars": 120, "prefer_breakeven": False}


def _build_cases() -> list[_Case]:
    return [
        # baselines
        _Case("v0_baseline", None),
        _Case("v2_default", {}),
        _Case("v2_A4_only", {"exit_policy": _exit_a4_time_120()}),
        # Track B with default (fixed-R) exit
        _Case(
            "B1_ADX20-60",
            {"regime_gate": {"type": "adx", "min_adx": 20.0, "max_adx": 60.0}},
        ),
        _Case(
            "B1_ADX15-60",
            {"regime_gate": {"type": "adx", "min_adx": 15.0, "max_adx": 60.0}},
        ),
        _Case(
            "B1_ADX25-60",
            {"regime_gate": {"type": "adx", "min_adx": 25.0, "max_adx": 60.0}},
        ),
        _Case(
            "B2_Donchian_w3",
            {"regime_gate": {"type": "donchian_width", "width_atr_threshold": 3.0}},
        ),
        _Case(
            "B2_Donchian_w4",
            {"regime_gate": {"type": "donchian_width", "width_atr_threshold": 4.0}},
        ),
        _Case(
            "B2_Donchian_w5",
            {"regime_gate": {"type": "donchian_width", "width_atr_threshold": 5.0}},
        ),
        _Case(
            "B5_EquityCurve_lb20",
            {"regime_gate": {"type": "equity_curve", "lookback_trades": 20, "min_trades": 10}},
        ),
        _Case(
            "B5_EquityCurve_lb30",
            {"regime_gate": {"type": "equity_curve", "lookback_trades": 30, "min_trades": 10}},
        ),
        # composite gates
        _Case(
            "B1+B5_ADX20+Equity20",
            {
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {"type": "adx", "min_adx": 20.0},
                        {"type": "equity_curve", "lookback_trades": 20, "min_trades": 10},
                    ],
                }
            },
        ),
        # B + A4 (track A winner) combos
        _Case(
            "B1+A4_ADX20+Time120",
            {
                "regime_gate": {"type": "adx", "min_adx": 20.0},
                "exit_policy": _exit_a4_time_120(),
            },
        ),
        _Case(
            "B5+A4_Equity20+Time120",
            {
                "regime_gate": {"type": "equity_curve", "lookback_trades": 20, "min_trades": 10},
                "exit_policy": _exit_a4_time_120(),
            },
        ),
        _Case(
            "B1+B5+A4",
            {
                "regime_gate": {
                    "type": "composite",
                    "gates": [
                        {"type": "adx", "min_adx": 20.0},
                        {"type": "equity_curve", "lookback_trades": 20, "min_trades": 10},
                    ],
                },
                "exit_policy": _exit_a4_time_120(),
            },
        ),
    ]


def _make_config(base_config: dict[str, Any], case: _Case) -> dict[str, Any]:
    config = json.loads(json.dumps(base_config))
    config["execution"] = dict(config.get("execution", {}))
    config["execution"]["model_id"] = "ideal_v1"
    if case.components is None:
        config["strategy"]["name"] = "ema_trend_pullback_15m_v0"
        return config
    config["strategy"]["name"] = "ema_trend_pullback_15m_v2"
    if case.components:
        config["strategy"]["components"] = case.components
    return config


def _summarize(case_name: str, report: Any) -> dict[str, Any]:
    closed = [trade for trade in report.trades if trade.exit_reason != "OPEN"]
    wins = [trade for trade in closed if (trade.pnl_pct or 0) > 0]
    sum_scaled = sum(trade.scaled_pnl_pct or 0.0 for trade in closed)
    r_values = [trade.r_multiple for trade in closed if trade.r_multiple is not None]
    mean_r = sum(r_values) / len(r_values) if r_values else 0.0
    win_rate = (len(wins) / len(closed) * 100) if closed else 0.0
    # capture top non-signal reasons (e.g. how often the gate blocked)
    blocked_keys = sorted(
        [
            (reason, count)
            for reason, count in report.no_signal_reason_counts.items()
            if reason.startswith("REGIME_GATE_BLOCKED_BY_")
        ],
        key=lambda item: -item[1],
    )
    return {
        "case": case_name,
        "closed_trades": len(closed),
        "wins": len(wins),
        "win_rate_pct": round(win_rate, 2),
        "sum_scaled_pnl_pct": round(sum_scaled, 4),
        "mean_r_multiple": round(mean_r, 4),
        "gate_blocked": dict(blocked_keys),
    }


def _render_table(rows: list[dict[str, Any]]) -> str:
    headers = ["case", "closed", "wins", "WR%", "sum_scaled%", "mean_R", "gate_blocked"]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        blocked = ",".join(f"{k}={v}" for k, v in row["gate_blocked"].items()) or "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    row["case"],
                    str(row["closed_trades"]),
                    str(row["wins"]),
                    f"{row['win_rate_pct']:.2f}",
                    f"{row['sum_scaled_pnl_pct']:+.4f}",
                    f"{row['mean_r_multiple']:+.4f}",
                    blocked,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _render_rolling(
    cases: list[_Case], per_window_pnl: dict[str, list[float]], windows: int
) -> str:
    headers = ["case"] + [f"w{i}" for i in range(windows)] + ["min", "mean", "pos_rate%"]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for case in cases:
        pnls = per_window_pnl[case.name]
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
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-config",
        default="research/models/gmo_ema_pullback_15m_both_v0/config/current.json",
    )
    parser.add_argument("--bars", default="research/data/raw/soljpy_15m_to_2026_05.csv")
    parser.add_argument("--tail-bars", type=int, default=30000)
    parser.add_argument("--rolling-windows", type=int, default=0)
    parser.add_argument("--rolling-window-bars", type=int, default=3000)
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()

    base_config = load_bot_config(args.base_config)
    cases = _build_cases()

    # Single-period table
    bars_all = read_bars_from_csv(args.bars)
    if args.tail_bars and args.tail_bars > 0 and args.tail_bars < len(bars_all):
        bars_all = bars_all[-args.tail_bars :]
    attach_regime_tags(bars_all)
    rows: list[dict[str, Any]] = []
    for case in cases:
        report = run_backtest(bars=bars_all, config=_make_config(base_config, case))
        rows.append(_summarize(case.name, report))
    print("\n## Single-period\n")
    print(_render_table(rows))

    # Rolling
    rolling_summary: list[dict[str, Any]] = []
    if args.rolling_windows > 0:
        bars_full = read_bars_from_csv(args.bars)
        needed = args.rolling_windows * args.rolling_window_bars
        bars_full = bars_full[-needed:]
        attach_regime_tags(bars_full)
        per_window_pnl: dict[str, list[float]] = {case.name: [] for case in cases}
        for window_index in range(args.rolling_windows):
            start = window_index * args.rolling_window_bars
            end = start + args.rolling_window_bars
            bars_w = bars_full[start:end]
            if len(bars_w) < args.rolling_window_bars:
                break
            for case in cases:
                report = run_backtest(bars=bars_w, config=_make_config(base_config, case))
                closed = [trade for trade in report.trades if trade.exit_reason != "OPEN"]
                sum_scaled = sum(trade.scaled_pnl_pct or 0.0 for trade in closed)
                per_window_pnl[case.name].append(round(sum_scaled, 4))
        print("\n## Rolling sum_scaled_pnl_pct\n")
        print(_render_rolling(cases, per_window_pnl, args.rolling_windows))
        rolling_summary = [
            {
                "case": case.name,
                "per_window_scaled_pnl_pct": per_window_pnl[case.name],
                "min": min(per_window_pnl[case.name]) if per_window_pnl[case.name] else 0.0,
                "mean": (
                    sum(per_window_pnl[case.name]) / len(per_window_pnl[case.name])
                    if per_window_pnl[case.name]
                    else 0.0
                ),
                "pos_rate_pct": (
                    sum(1 for v in per_window_pnl[case.name] if v > 0)
                    / len(per_window_pnl[case.name])
                    * 100
                )
                if per_window_pnl[case.name]
                else 0.0,
            }
            for case in cases
        ]

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(
            json.dumps(
                {"single_period": rows, "rolling": rolling_summary},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n[track-b] wrote summary: {args.output_json}")


if __name__ == "__main__":
    main()
