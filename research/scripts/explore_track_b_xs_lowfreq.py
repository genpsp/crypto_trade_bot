"""Track B: 低頻度 cross-sectional momentum/reversal（多銘柄ユニバース）

Track③ のマルチ資産ループを (1) N 銘柄 (2) top-k/bottom-k バスケット
(3) 日次以上の低頻度 rebalance に一般化する
仮説: 15m intraday は reversal が強く momentum は弱い（microstructure）。
      だが日次以上の cross-sectional momentum は別系統で回転低=7bps でもコスト耐性。
      3 銘柄では breadth 不足で gross すら edge 無し → ユニバース拡張で本検証。

判定（Done）: net rolling Sharpe が SOL buyhold を上回り DSR p<0.10、
             turnover×cost が gross の小部分（コスト耐性）

Usage:
    python -m research.scripts.explore_track_b_xs_lowfreq \
        --cost-bps 7.0 --basket-k 3 --n-windows 12 \
        --output-json research/data/runs/track_b_xs/universe.json
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

_BARS_PER_YEAR_15M = 365 * 24 * 4  # 35040
_BARS_PER_DAY = 96

# raw CSV ファイル名 → asset ラベル
_UNIVERSE = {
    "soljpy_15m_to_2026_05.csv": "SOL",
    "btcjpy_15m_1y.csv": "BTC",
    "ethjpy_15m_1y.csv": "ETH",
    "bchjpy_15m_1y.csv": "BCH",
    "ltcjpy_15m_1y.csv": "LTC",
    "xrpjpy_15m_1y.csv": "XRP",
    "dotjpy_15m_1y.csv": "DOT",
    "atomjpy_15m_1y.csv": "ATOM",
    "adajpy_15m_1y.csv": "ADA",
    "linkjpy_15m_1y.csv": "LINK",
    "dogejpy_15m_1y.csv": "DOGE",
}


def _load_closes(path: str) -> dict[str, float]:
    out: dict[str, float] = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            out[row["open_time"]] = float(row["close"])
    return out


def _aligned_panel(
    raw_dir: str, min_coverage: float
) -> tuple[list[str], dict[str, list[float]], list[str]]:
    """全銘柄を読み min_coverage 以上の銘柄だけで共通タイムスタンプ整合"""
    closes: dict[str, dict[str, float]] = {}
    for fname, label in _UNIVERSE.items():
        path = os.path.join(raw_dir, fname)
        if os.path.exists(path):
            closes[label] = _load_closes(path)
    if not closes:
        raise SystemExit("no universe CSVs found")
    # 全銘柄の和集合タイムスタンプに対する各銘柄のカバレッジで足切り
    union: set[str] = set()
    for c in closes.values():
        union |= set(c.keys())
    span = len(union)
    kept = {lbl: c for lbl, c in closes.items() if len(c) / span >= min_coverage}
    dropped = {lbl: round(len(c) / span, 3) for lbl, c in closes.items() if lbl not in kept}
    common = set.intersection(*[set(c.keys()) for c in kept.values()])
    times = sorted(common)
    panel = {lbl: [kept[lbl][t] for t in times] for lbl in kept}
    assets = sorted(kept.keys())
    print(f"[track_b] universe={assets}  dropped(coverage)={dropped}")
    print(f"[track_b] aligned bars={len(times)}  {times[0] if times else '-'} .. {times[-1] if times else '-'}")
    return times, panel, assets


def _returns(prices: list[float]) -> list[float]:
    return [0.0] + [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices))]


@dataclass(frozen=True)
class _Config:
    name: str
    kind: str  # xs_momentum | xs_reversal | ew_basket | sol_buyhold
    lookback: int
    hold: int
    k: int


def _rebalance_returns(
    cfg: _Config,
    times: list[str],
    panel: dict[str, list[float]],
    rets: dict[str, list[float]],
    assets: list[str],
    cost_bps: float,
) -> list[float]:
    n = len(times)
    L, H, k = cfg.lookback, cfg.hold, cfg.k
    cost = cost_bps / 10_000.0
    prev_w: dict[str, float] = {a: 0.0 for a in assets}
    out: list[float] = []
    t = L
    while t + H <= n:
        if cfg.kind in ("xs_momentum", "xs_reversal"):
            score = {a: panel[a][t] / panel[a][t - L] - 1.0 for a in assets}
            ranked = sorted(assets, key=lambda a: score[a])
            weak, strong = ranked[:k], ranked[-k:]
            w = {a: 0.0 for a in assets}
            longs, shorts = (strong, weak) if cfg.kind == "xs_momentum" else (weak, strong)
            for a in longs:
                w[a] += 1.0 / k
            for a in shorts:
                w[a] -= 1.0 / k
        elif cfg.kind == "ew_basket":
            w = {a: 1.0 / len(assets) for a in assets}
        elif cfg.kind == "sol_buyhold":
            w = {a: 0.0 for a in assets}
            w["SOL"] = 1.0
        else:
            raise ValueError(cfg.kind)

        turnover = sum(abs(w[a] - prev_w[a]) for a in assets)
        gross = 0.0
        for a in assets:
            if w[a] == 0.0:
                continue
            compounded = 1.0
            for kk in range(1, H + 1):
                compounded *= 1.0 + rets[a][t + kk]
            gross += w[a] * (compounded - 1.0)
        out.append(gross - turnover * cost)
        prev_w = w
        t += H
    return out


def _annualization(hold: int) -> float:
    return math.sqrt(_BARS_PER_YEAR_15M / hold)


def _sharpe(returns: list[float]) -> float:
    vals = [r for r in returns if math.isfinite(r)]
    if len(vals) < 2:
        return 0.0
    sd = statistics.pstdev(vals)
    return statistics.mean(vals) / sd if sd > 0 else 0.0


def _rolling_sharpes(returns: list[float], n_windows: int) -> list[float]:
    if not returns:
        return []
    size = max(1, len(returns) // n_windows)
    out = []
    for w in range(n_windows):
        seg = returns[w * size : (w + 1) * size]
        if len(seg) >= 2:
            out.append(_sharpe(seg))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="research/data/raw")
    parser.add_argument("--cost-bps", type=float, default=7.0)
    parser.add_argument("--basket-k", type=int, default=3, help="long/short 各レッグの銘柄数")
    parser.add_argument("--min-coverage", type=float, default=0.9, help="この union 比率未満の銘柄は除外")
    parser.add_argument("--n-windows", type=int, default=12)
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()

    times, panel, assets = _aligned_panel(args.raw_dir, args.min_coverage)
    rets = {a: _returns(panel[a]) for a in assets}
    k = min(args.basket_k, len(assets) // 2)
    print(f"[track_b] cost_bps={args.cost_bps} basket_k={k} n_assets={len(assets)}\n")

    # 低頻度 horizon: 1d〜30d lookback × 1d / 3d / 7d hold
    # 低頻度 horizon: 1d〜30d lookback × 1d / 3d / 7d hold
    configs: list[_Config] = []
    for Ld in (1, 3, 7, 14, 30):
        for Hd in (1, 3, 7):
            L, H = Ld * _BARS_PER_DAY, Hd * _BARS_PER_DAY
            configs.append(_Config(f"xs_mom_L{Ld}d_H{Hd}d", "xs_momentum", L, H, k))
            configs.append(_Config(f"xs_rev_L{Ld}d_H{Hd}d", "xs_reversal", L, H, k))
    n_trials = len(configs)
    baselines = [
        _Config("sol_buyhold_H1d", "sol_buyhold", 1, _BARS_PER_DAY, k),
        _Config("ew_basket_H1d", "ew_basket", 1, _BARS_PER_DAY, k),
    ]

    headers = ["config", "ann_Sh", "DSR_p", "roll_mean", "roll_min", "roll_pos%", "rebal", "tot%"]
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")
    summary: list[dict[str, Any]] = []

    def evaluate(cfg: _Config, is_trial: bool) -> dict[str, Any]:
        r = _rebalance_returns(cfg, times, panel, rets, assets, args.cost_bps)
        ann = _sharpe(r) * _annualization(cfg.hold)
        _, dsr_p = deflated_sharpe(r, n_trials=n_trials if is_trial else 1)
        roll = [s * _annualization(cfg.hold) for s in _rolling_sharpes(r, args.n_windows)]
        tot = (math.prod(1.0 + x for x in r) - 1.0) * 100 if r else 0.0
        return {
            "config": cfg.name, "kind": cfg.kind, "lookback": cfg.lookback,
            "hold": cfg.hold, "k": cfg.k, "ann_sharpe": round(ann, 3),
            "dsr_p_value": round(dsr_p, 4),
            "roll_mean_sharpe": round(statistics.mean(roll), 3) if roll else 0.0,
            "roll_min_sharpe": round(min(roll), 3) if roll else 0.0,
            "roll_pos_pct": round(sum(1 for s in roll if s > 0) / len(roll) * 100, 1) if roll else 0.0,
            "rebalances": len(r), "total_return_pct": round(tot, 2),
        }

    def show(row: dict[str, Any]) -> None:
        print("| " + " | ".join([
            row["config"], f"{row['ann_sharpe']:+.2f}", f"{row['dsr_p_value']:.3f}",
            f"{row['roll_mean_sharpe']:+.2f}", f"{row['roll_min_sharpe']:+.2f}",
            f"{row['roll_pos_pct']:.0f}", str(row["rebalances"]), f"{row['total_return_pct']:+.1f}",
        ]) + " |")

    for cfg in baselines:
        row = evaluate(cfg, is_trial=False)
        summary.append(row); show(row)
    print("|" + "|".join(["---"] * len(headers)) + "|")
    for row in sorted([evaluate(c, True) for c in configs], key=lambda x: -x["ann_sharpe"]):
        summary.append(row); show(row)

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(json.dumps({
            "aligned_bars": len(times), "assets": assets, "basket_k": k,
            "cost_bps": args.cost_bps, "n_trials": n_trials,
            "first": times[0] if times else None, "last": times[-1] if times else None,
            "results": summary,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[track_b] wrote: {args.output_json}")


if __name__ == "__main__":
    main()
