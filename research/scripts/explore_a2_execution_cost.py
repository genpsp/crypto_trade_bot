"""Track A2: XS reversal を GMO 実コストで monetize できるか検証

Round3 計画 Track A2 step 1-3
- 収集済み板から実 spread を anchor（別途実測: GMO SOL/JPY half-spread ~4.3bps）
- XS reversal の net フロンティアを cost_bps grid で両 venue 比較
  - GMO JPY (SOL/BTC/ETH): close 系列に板バウンスが乗る低流動 venue
  - Binance USDT (SOL/BTC/ETH): close≈mid の流動 venue（spread-free な realizable edge の代理）
- 狙い: +7 gross が「実 alpha」か「GMO close のバウンス phantom」かを実数で切り分け、
        break-even one-way cost を出し、達成可能 maker 実効コストと突き合わせる

Usage:
    python -m research.scripts.explore_a2_execution_cost \
        --output-json research/data/runs/a2_exec_cost/frontier.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

from research.src.eval.statistics import deflated_sharpe
from research.scripts.explore_track3_cross_sectional import (
    _Config,
    _aligned_panel,
    _annualization,
    _rebalance_returns,
    _returns,
    _sharpe,
)

# 実測 anchor（research/data/raw/orderbook/gmo_soljpy_ob_2026-06-05.csv, 65 snap）
GMO_HALF_SPREAD_BPS = 4.3   # median spread 8.5bps の半値
GMO_TAKER_BPS = 7.0         # Round1/2 で使った taker 実効コスト前提

COST_GRID = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.3, 7.0]

# headline + 近傍（最短 horizon ほど強い = よりバウンス由来）
XS_CONFIGS = [
    _Config("xs_rev_L4_H4", "xs_reversal", 4, 4),
    _Config("xs_rev_L4_H16", "xs_reversal", 4, 16),
    _Config("xs_rev_L16_H4", "xs_reversal", 16, 4),
    _Config("xs_rev_L16_H16", "xs_reversal", 16, 16),
]

VENUES = {
    "GMO_JPY": {
        "SOL": "research/data/raw/soljpy_15m_to_2026_05.csv",
        "BTC": "research/data/raw/btcjpy_15m_1y.csv",
        "ETH": "research/data/raw/ethjpy_15m_1y.csv",
    },
    "BINANCE_USDT": {
        "SOL": "research/data/raw/binance15m/SOLUSDT_15m.csv",
        "BTC": "research/data/raw/binance15m/BTCUSDT_15m.csv",
        "ETH": "research/data/raw/binance15m/ETHUSDT_15m.csv",
    },
}


def _net_at_cost(cfg, times, panel, rets, assets, cost_bps, n_trials):
    r = _rebalance_returns(cfg, times, panel, rets, assets, cost_bps)
    ann = _sharpe(r) * _annualization(cfg.hold)
    _, dsr_p = deflated_sharpe(r, n_trials=n_trials)
    tot = (math.prod(1.0 + x for x in r) - 1.0) * 100 if r else 0.0
    # rolling pos%（13窓）
    size = max(1, len(r) // 13)
    roll = [_sharpe(r[w * size:(w + 1) * size]) for w in range(13) if len(r[w * size:(w + 1) * size]) >= 2]
    pos = sum(1 for s in roll if s > 0) / len(roll) * 100 if roll else 0.0
    return {"cost_bps": cost_bps, "ann_sharpe": round(ann, 3), "dsr_p": round(dsr_p, 4),
            "total_ret_pct": round(tot, 1), "roll_pos_pct": round(pos, 0), "rebalances": len(r)}


def _breakeven(frontier: list[dict]) -> float | None:
    """ann_sharpe が正→負に変わる cost を線形補間で推定"""
    for a, b in zip(frontier, frontier[1:]):
        if a["ann_sharpe"] > 0 >= b["ann_sharpe"]:
            x0, y0 = a["cost_bps"], a["ann_sharpe"]
            x1, y1 = b["cost_bps"], b["ann_sharpe"]
            return round(x0 + (x1 - x0) * y0 / (y0 - y1), 2)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-json", default="research/data/runs/a2_exec_cost/frontier.json")
    args = parser.parse_args()

    out: dict[str, Any] = {"gmo_half_spread_bps": GMO_HALF_SPREAD_BPS, "gmo_taker_bps": GMO_TAKER_BPS, "venues": {}}
    n_trials = 24  # Track3 と同じ多重検定母数

    for venue, paths in VENUES.items():
        times, panel = _aligned_panel(paths)
        assets = list(paths.keys())
        rets = {a: _returns(panel[a]) for a in assets}
        print(f"\n{'='*70}\n[{venue}] aligned bars={len(times)}  {times[0]} .. {times[-1]}\n{'='*70}")
        out["venues"][venue] = {"aligned_bars": len(times), "first": times[0], "last": times[-1], "configs": {}}

        for cfg in XS_CONFIGS:
            frontier = [_net_at_cost(cfg, times, panel, rets, assets, c, n_trials) for c in COST_GRID]
            be = _breakeven(frontier)
            out["venues"][venue]["configs"][cfg.name] = {"breakeven_bps": be, "frontier": frontier}
            gross = frontier[0]["ann_sharpe"]
            print(f"\n  {cfg.name}: gross(0bps) ann_Sharpe={gross:+.2f}  break-even one-way={be}bps")
            print("  | cost_bps | " + " | ".join(f"{c}" for c in COST_GRID) + " |")
            print("  | ann_Sh   | " + " | ".join(f"{f['ann_sharpe']:+.2f}" for f in frontier) + " |")
            print("  | tot_ret% | " + " | ".join(f"{f['total_ret_pct']:+.0f}" for f in frontier) + " |")
            print("  | pos%     | " + " | ".join(f"{f['roll_pos_pct']:.0f}" for f in frontier) + " |")

    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[a2] wrote: {args.output_json}")

    # verdict
    print(f"\n{'='*70}\n[A2 判定] GMO half-spread(taker)={GMO_HALF_SPREAD_BPS}bps / taker total={GMO_TAKER_BPS}bps")
    g = out["venues"]["GMO_JPY"]["configs"]["xs_rev_L4_H4"]
    b = out["venues"]["BINANCE_USDT"]["configs"]["xs_rev_L4_H4"]
    print(f"  xs_rev_L4_H4 gross: GMO={g['frontier'][0]['ann_sharpe']:+.2f} vs Binance={b['frontier'][0]['ann_sharpe']:+.2f}")
    print(f"  break-even one-way: GMO={g['breakeven_bps']}bps  Binance={b['breakeven_bps']}bps")
    print(f"  → GMO で taker({GMO_HALF_SPREAD_BPS}bps half) は break-even {'超過=net負' if (g['breakeven_bps'] or 0) < GMO_HALF_SPREAD_BPS else '内=net正の余地'}")


if __name__ == "__main__":
    main()
