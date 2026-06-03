"""Track C: cross-sectional funding carry basket（Binance USDⓈ-M perp）

仮説: funding 単体（Round1 ⑤ SOL）は弱いが、多 perp の
      long 低funding / short 高funding バスケット（8h 回転=低コスト）は別系統。
PnL/period = Σ_a [ w[a]·priceReturn[a] - w[a]·funding[a] ] - turnover·cost
  long(w>0)  は funding>0 で支払い → -w·funding（低/負 funding を long で受取）
  short(w<0) は funding>0 で受取   → 同式が short の受取を表す
判定（Done）: basket の net ann Sharpe が各単体 buyhold を上回り DSR p<0.10

Usage:
    python -m research.scripts.explore_track_c_funding_carry --cost-bps 7.0 --output-json ...
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.src.eval.statistics import deflated_sharpe

_PERIODS_PER_YEAR = 365.25 * 24 / 8  # 8h funding period


def _load(path: str) -> dict[str, tuple[float, float]]:
    """funding_time -> (funding_rate, mark_price)"""
    out: dict[str, tuple[float, float]] = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                out[row["funding_time"]] = (float(row["funding_rate"]), float(row["mark_price"]))
            except (ValueError, KeyError):
                continue
    return out


def _aligned(funding_dir: str) -> tuple[list[str], list[str], dict[str, dict], dict[str, dict]]:
    files = sorted(glob.glob(os.path.join(funding_dir, "*_funding_8h.csv")))
    fund: dict[str, dict[str, float]] = {}
    price: dict[str, dict[str, float]] = {}
    for p in files:
        sym = os.path.basename(p).replace("_funding_8h.csv", "")
        data = _load(p)
        if data:
            fund[sym] = {t: v[0] for t, v in data.items()}
            price[sym] = {t: v[1] for t, v in data.items() if v[1] > 0}
    assets = sorted(fund.keys())
    common = set.intersection(*[set(price[a].keys()) for a in assets])
    times = sorted(common)
    return times, assets, fund, price


@dataclass(frozen=True)
class _Config:
    name: str
    kind: str  # carry | carry_hyst | ew_long | btc_buyhold
    k: int
    rebalance_every: int = 1   # R period 毎にだけ再ランク（間は保有=turnover 0）
    k_exit: int = 0            # hysteresis: rank が k_exit を超えたら手放す（0=無効）


def _returns(cfg: _Config, times, assets, fund, price, cost_bps) -> list[float]:
    cost = cost_bps / 10_000.0
    prev_w = {a: 0.0 for a in assets}
    cur_w = {a: 0.0 for a in assets}
    out: list[float] = []
    for i in range(len(times) - 1):
        t, tn = times[i], times[i + 1]
        if cfg.kind in ("carry", "carry_hyst"):
            f = {a: fund[a][t] for a in assets}
            ranked = sorted(assets, key=lambda a: f[a])
            rank = {a: r for r, a in enumerate(ranked)}  # 0=最低funding
            if cfg.kind == "carry_hyst":
                # 保有は rank が [0,k) / (N-1-k, N-1] を外れる（k_exit 緩衝）まで維持
                w = dict(cur_w)
                kx = cfg.k_exit or cfg.k
                for a in assets:
                    if w[a] > 0 and rank[a] >= kx:
                        w[a] = 0.0          # long 解消（低funding ゾーンを出た）
                    if w[a] < 0 and rank[a] < len(assets) - kx:
                        w[a] = 0.0          # short 解消
                longs = [a for a in ranked[: cfg.k] if w[a] == 0.0]
                shorts = [a for a in ranked[-cfg.k :] if w[a] == 0.0]
                for a in longs:
                    w[a] = 1.0 / cfg.k
                for a in shorts:
                    w[a] = -1.0 / cfg.k
                # 正規化（建玉数が k と一致しない場合があるのでレッグ毎に等分し直す）
                pos = [a for a in assets if w[a] > 0]
                neg = [a for a in assets if w[a] < 0]
                w = {a: 0.0 for a in assets}
                for a in pos:
                    w[a] = 1.0 / len(pos) if pos else 0.0
                for a in neg:
                    w[a] = -1.0 / len(neg) if neg else 0.0
            elif i % cfg.rebalance_every == 0:
                low, high = ranked[: cfg.k], ranked[-cfg.k :]
                w = {a: 0.0 for a in assets}
                for a in low:
                    w[a] += 1.0 / cfg.k   # long 低funding（受取）
                for a in high:
                    w[a] -= 1.0 / cfg.k   # short 高funding（受取）
            else:
                w = dict(cur_w)           # 保有継続（再ランクしない）
            cur_w = dict(w)
        elif cfg.kind == "ew_long":
            w = {a: 1.0 / len(assets) for a in assets}
        elif cfg.kind == "btc_buyhold":
            w = {a: 0.0 for a in assets}
            w["BTCUSDT"] = 1.0
        else:
            raise ValueError(cfg.kind)

        turnover = sum(abs(w[a] - prev_w[a]) for a in assets)
        pnl = 0.0
        for a in assets:
            if w[a] == 0.0:
                continue
            pret = price[a][tn] / price[a][t] - 1.0
            pnl += w[a] * pret - w[a] * fund[a][t]
        out.append(pnl - turnover * cost)
        prev_w = w
    return out


def _sharpe(r: list[float]) -> float:
    vals = [x for x in r if math.isfinite(x)]
    if len(vals) < 2:
        return 0.0
    sd = statistics.pstdev(vals)
    return statistics.mean(vals) / sd if sd > 0 else 0.0


def _rolling(r: list[float], n: int) -> list[float]:
    if not r:
        return []
    size = max(1, len(r) // n)
    out = []
    for w in range(n):
        seg = r[w * size : (w + 1) * size]
        if len(seg) >= 2:
            out.append(_sharpe(seg))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--funding-dir", default="research/data/raw/funding")
    parser.add_argument("--cost-bps", type=float, default=7.0)
    parser.add_argument("--n-windows", type=int, default=12)
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()

    times, assets, fund, price = _aligned(args.funding_dir)
    print(f"[track_c] assets={len(assets)} periods={len(times)}  {times[0]} .. {times[-1]}  cost_bps={args.cost_bps}\n")

    ann = math.sqrt(_PERIODS_PER_YEAR)
    configs = [_Config(f"carry_k{k}", "carry", k) for k in (1, 2, 3, 5)]
    # 低 turnover 化: R period 毎の再ランク（間は保有）
    for k in (3, 5):
        for R in (3, 9, 21):
            configs.append(_Config(f"carry_k{k}_R{R}", "carry", k, rebalance_every=R))
    # hysteresis: 入 rank<k / 出 rank>=k_exit
    for k, kx in ((3, 6), (5, 8), (5, 10)):
        configs.append(_Config(f"carry_hyst_k{k}_x{kx}", "carry_hyst", k, k_exit=kx))
    n_trials = len(configs)
    baselines = [_Config("ew_long", "ew_long", 0), _Config("btc_buyhold", "btc_buyhold", 0)]

    headers = ["config", "ann_Sh", "DSR_p", "roll_mean", "roll_min", "roll_pos%", "periods", "tot%"]
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")
    summary: list[dict[str, Any]] = []

    def evaluate(cfg: _Config, is_trial: bool) -> dict[str, Any]:
        r = _returns(cfg, times, assets, fund, price, args.cost_bps)
        a = _sharpe(r) * ann
        _, dsr_p = deflated_sharpe(r, n_trials=n_trials if is_trial else 1)
        roll = [s * ann for s in _rolling(r, args.n_windows)]
        tot = (math.prod(1.0 + x for x in r) - 1.0) * 100 if r else 0.0
        return {
            "config": cfg.name, "kind": cfg.kind, "k": cfg.k, "ann_sharpe": round(a, 3),
            "dsr_p_value": round(dsr_p, 4),
            "roll_mean_sharpe": round(statistics.mean(roll), 3) if roll else 0.0,
            "roll_min_sharpe": round(min(roll), 3) if roll else 0.0,
            "roll_pos_pct": round(sum(1 for s in roll if s > 0) / len(roll) * 100, 1) if roll else 0.0,
            "periods": len(r), "total_return_pct": round(tot, 2),
        }

    def show(row):
        print("| " + " | ".join([
            row["config"], f"{row['ann_sharpe']:+.2f}", f"{row['dsr_p_value']:.3f}",
            f"{row['roll_mean_sharpe']:+.2f}", f"{row['roll_min_sharpe']:+.2f}",
            f"{row['roll_pos_pct']:.0f}", str(row["periods"]), f"{row['total_return_pct']:+.1f}",
        ]) + " |")

    for cfg in baselines:
        row = evaluate(cfg, False); summary.append(row); show(row)
    print("|" + "|".join(["---"] * len(headers)) + "|")
    for row in sorted([evaluate(c, True) for c in configs], key=lambda x: -x["ann_sharpe"]):
        summary.append(row); show(row)

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(json.dumps({
            "assets": assets, "periods": len(times), "cost_bps": args.cost_bps,
            "n_trials": n_trials, "first": times[0], "last": times[-1], "results": summary,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[track_c] wrote: {args.output_json}")


if __name__ == "__main__":
    main()
