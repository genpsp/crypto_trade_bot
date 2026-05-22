"""Track A (exit policy) exploration runner — operates on the live CSV cache.

Compares the v0 baseline against a fixed set of v2 component bundles using
ideal_v1 execution (deterministic; stochastic profile is not required to learn
the relative ranking of exit policies).

Usage:

    python -m research.scripts.explore_track_a_exit_policies \\
        --bars research/data/raw/soljpy_15m_to_2026_05.csv \\
        --tail-bars 30000

Prints a single Markdown table with: closed_trades, win_rate_pct,
sum_pnl_pct, sum_scaled_pnl_pct, mean_r_multiple per exit policy. Intended for
quick iteration; full statistical evaluation lives in `run_sweep`.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.dex_bot.domain.model.types import OhlcvBar
from research.src.adapters.csv_bar_repository import read_bars_from_csv
from research.src.data.regime_tagger import attach_regime_tags
from research.src.domain.backtest_engine import run_backtest
from research.src.infra.research_config import load_bot_config


@dataclass(frozen=True)
class _Case:
    name: str
    components: dict[str, Any] | None  # None → v0 baseline


def _build_cases() -> list[_Case]:
    return [
        _Case(name="v0_baseline", components=None),
        _Case(
            name="v2_default_bundle",
            components={},
        ),
        _Case(
            name="A1_BE_1_0R",
            components={"exit_policy": {"type": "break_even", "trigger_r": 1.0}},
        ),
        _Case(
            name="A1_BE_0_8R",
            components={"exit_policy": {"type": "break_even", "trigger_r": 0.8}},
        ),
        _Case(
            name="A1_BE_1_2R",
            components={"exit_policy": {"type": "break_even", "trigger_r": 1.2}},
        ),
        _Case(
            name="A3_Chandelier_ATR_2_0",
            components={"exit_policy": {"type": "chandelier", "atr_multiple": 2.0}},
        ),
        _Case(
            name="A3_Chandelier_ATR_2_5",
            components={"exit_policy": {"type": "chandelier", "atr_multiple": 2.5}},
        ),
        _Case(
            name="A3_Chandelier_ATR_3_0",
            components={"exit_policy": {"type": "chandelier", "atr_multiple": 3.0}},
        ),
        _Case(
            name="A4_Time_30bar",
            components={"exit_policy": {"type": "time_exit", "max_holding_bars": 30}},
        ),
        _Case(
            name="A4_Time_60bar",
            components={"exit_policy": {"type": "time_exit", "max_holding_bars": 60}},
        ),
        _Case(
            name="A4_Time_120bar",
            components={"exit_policy": {"type": "time_exit", "max_holding_bars": 120}},
        ),
        _Case(
            name="A4_Time_120bar_BE_cap",
            components={
                "exit_policy": {
                    "type": "time_exit",
                    "max_holding_bars": 120,
                    "prefer_breakeven": True,
                }
            },
        ),
        _Case(
            name="A2_Partial50_at_1R_runner_2R",
            components={
                "exit_policy": {
                    "type": "partial_tp",
                    "partial_r": 1.0,
                    "partial_fraction": 0.5,
                }
            },
        ),
        _Case(
            name="A2_Partial50_at_1R_plus_BE",
            components={
                "exit_policy": {
                    "type": "composite",
                    "policies": [
                        {
                            "type": "partial_tp",
                            "partial_r": 1.0,
                            "partial_fraction": 0.5,
                        },
                        {"type": "break_even", "trigger_r": 1.0},
                    ],
                }
            },
        ),
        _Case(
            name="A1_plus_A4_BE_then_TimeBE",
            components={
                "exit_policy": {
                    "type": "composite",
                    "policies": [
                        {"type": "break_even", "trigger_r": 1.0},
                        {
                            "type": "time_exit",
                            "max_holding_bars": 120,
                            "prefer_breakeven": True,
                        },
                    ],
                }
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
    sum_pnl = sum(trade.pnl_pct or 0.0 for trade in closed)
    sum_scaled = sum(trade.scaled_pnl_pct or 0.0 for trade in closed)
    r_values = [trade.r_multiple for trade in closed if trade.r_multiple is not None]
    mean_r = sum(r_values) / len(r_values) if r_values else 0.0
    win_rate = (len(wins) / len(closed) * 100) if closed else 0.0
    exit_breakdown: dict[str, int] = {}
    for trade in closed:
        exit_breakdown[trade.exit_reason] = exit_breakdown.get(trade.exit_reason, 0) + 1
    return {
        "case": case_name,
        "closed_trades": len(closed),
        "wins": len(wins),
        "win_rate_pct": round(win_rate, 2),
        "sum_pnl_pct": round(sum_pnl, 4),
        "sum_scaled_pnl_pct": round(sum_scaled, 4),
        "mean_r_multiple": round(mean_r, 4),
        "exit_breakdown": exit_breakdown,
    }


def _render_table(rows: list[dict[str, Any]]) -> str:
    headers = [
        "case",
        "closed",
        "wins",
        "WR%",
        "sum_pnl%",
        "sum_scaled%",
        "mean_R",
        "exits",
    ]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        exits = ",".join(f"{k}={v}" for k, v in sorted(row["exit_breakdown"].items()))
        lines.append(
            "| "
            + " | ".join(
                [
                    row["case"],
                    str(row["closed_trades"]),
                    str(row["wins"]),
                    f"{row['win_rate_pct']:.2f}",
                    f"{row['sum_pnl_pct']:+.4f}",
                    f"{row['sum_scaled_pnl_pct']:+.4f}",
                    f"{row['mean_r_multiple']:+.4f}",
                    exits,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def run(
    *,
    base_config_path: str,
    bars_path: str,
    tail_bars: int | None,
    cases: list[_Case] | None = None,
) -> list[dict[str, Any]]:
    base_config = load_bot_config(base_config_path)
    bars: list[OhlcvBar] = read_bars_from_csv(bars_path)
    if tail_bars is not None and tail_bars > 0 and tail_bars < len(bars):
        bars = bars[-tail_bars:]
    attach_regime_tags(bars)
    rows: list[dict[str, Any]] = []
    for case in cases or _build_cases():
        config = _make_config(base_config, case)
        report = run_backtest(bars=bars, config=config)
        rows.append(_summarize(case.name, report))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-config",
        default="research/models/gmo_ema_pullback_15m_both_v0/config/current.json",
    )
    parser.add_argument(
        "--bars",
        default="research/data/raw/soljpy_15m_to_2026_05.csv",
    )
    parser.add_argument("--tail-bars", type=int, default=30000)
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()

    rows = run(
        base_config_path=args.base_config,
        bars_path=args.bars,
        tail_bars=args.tail_bars,
    )
    print(_render_table(rows))
    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[track-a] wrote summary: {args.output_json}")


if __name__ == "__main__":
    main()
