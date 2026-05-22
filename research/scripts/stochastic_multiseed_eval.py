"""Multi-seed stochastic_v1 evaluation for Phase 2 Done basis.

Per plan §2.3: \"stochastic_v1 + 実 profile seed p05 positive\". This script
runs a single variant on the full bars CSV under stochastic_v1 over N seeds
and reports the distribution of total scaled_pnl (and aggregated per-window
metrics) — most importantly the 5th percentile across seeds.

Usage:

    python -m research.scripts.stochastic_multiseed_eval \\
        --bars research/data/raw/soljpy_15m_to_2026_05.csv \\
        --windows 13 --window-bars 3000 \\
        --variant v2_dir_session+vol+time120 \\
        --profile research/data/execution_profiles/_template.json \\
        --seeds 100
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.src.adapters.csv_bar_repository import read_bars_from_csv
from research.src.data.regime_tagger import attach_regime_tags
from research.src.domain.backtest_engine import run_backtest
from research.src.infra.research_config import load_bot_config
from research.scripts.explore_phase1_axis_sweep import _build_variants, _make_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", required=True)
    parser.add_argument(
        "--base-config",
        default="research/models/gmo_ema_pullback_15m_both_v0/config/current.json",
    )
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--pair", default="SOL/JPY")
    parser.add_argument("--windows", type=int, default=13)
    parser.add_argument("--window-bars", type=int, default=3000)
    parser.add_argument("--variant", required=True)
    parser.add_argument(
        "--profile",
        default="research/data/execution_profiles/_template.json",
    )
    parser.add_argument("--seeds", type=int, default=100)
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

    variants = {v.name: v for v in _build_variants()}
    if args.variant not in variants:
        raise SystemExit(f"unknown variant: {args.variant}")
    variant = variants[args.variant]

    print(
        f"\n## Multi-seed stochastic_v1 — variant={args.variant} "
        f"profile={args.profile} seeds={args.seeds}\n"
    )

    seed_results: list[dict] = []
    for seed in range(args.seeds):
        per_window: list[float] = []
        for window_index in range(args.windows):
            start = window_index * args.window_bars
            end = start + args.window_bars
            bars = all_bars[start:end]
            if len(bars) < args.window_bars:
                break
            config = _make_config(
                base_config,
                variant,
                args.timeframe,
                args.pair,
                execution_model="stochastic_v1",
                execution_profile_path=args.profile,
                execution_seed=seed,
            )
            report = run_backtest(bars=bars, config=config)
            closed = [t for t in report.trades if t.exit_reason != "OPEN"]
            sum_scaled = sum(t.scaled_pnl_pct or 0.0 for t in closed)
            per_window.append(round(sum_scaled, 4))
        total = sum(per_window)
        win_min = min(per_window) if per_window else 0.0
        win_mean = (sum(per_window) / len(per_window)) if per_window else 0.0
        pos_rate = (
            sum(1 for v in per_window if v > 0) / len(per_window) * 100
            if per_window
            else 0.0
        )
        seed_results.append(
            {
                "seed": seed,
                "total_scaled_pnl_pct": round(total, 2),
                "per_window": per_window,
                "min": round(win_min, 2),
                "mean": round(win_mean, 2),
                "pos_rate_pct": round(pos_rate, 1),
            }
        )
        if seed % 10 == 0:
            print(
                f"  seed {seed:3d}: total={total:+7.2f}  mean={win_mean:+5.2f}  "
                f"pos_rate={pos_rate:5.1f}%  min={win_min:+6.2f}"
            )

    totals = sorted(r["total_scaled_pnl_pct"] for r in seed_results)
    means = sorted(r["mean"] for r in seed_results)
    pos_rates = sorted(r["pos_rate_pct"] for r in seed_results)
    mins = sorted(r["min"] for r in seed_results)
    n = len(totals)

    def p(arr: list[float], pct: float) -> float:
        idx = max(0, min(n - 1, int(pct * n / 100)))
        return arr[idx]

    summary = {
        "seeds": n,
        "total_p05": p(totals, 5),
        "total_p50": p(totals, 50),
        "total_p95": p(totals, 95),
        "mean_p05": p(means, 5),
        "mean_p50": p(means, 50),
        "mean_p95": p(means, 95),
        "pos_rate_p05": p(pos_rates, 5),
        "pos_rate_p50": p(pos_rates, 50),
        "pos_rate_p95": p(pos_rates, 95),
        "min_p05": p(mins, 5),
        "min_p50": p(mins, 50),
        "min_p95": p(mins, 95),
        "fraction_total_positive": sum(1 for v in totals if v > 0) / n,
    }
    print("\n## Summary (across seeds)")
    for k, v in summary.items():
        print(f"  {k:25s}: {v}")

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(
            json.dumps(
                {
                    "variant": args.variant,
                    "profile": args.profile,
                    "seeds": args.seeds,
                    "windows": args.windows,
                    "window_bars": args.window_bars,
                    "summary": summary,
                    "seed_results": seed_results,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n[multiseed] wrote: {args.output_json}")


if __name__ == "__main__":
    main()
