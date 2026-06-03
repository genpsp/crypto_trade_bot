"""alt-data 候補1: STABLES(ステーブル供給)→BTC signal の proper 検証

同一 signal ロジックを STABLES と Tier4 ネガコン群に適用し、
STABLES が null 分布の何パーセンタイルかで本物性を判定（negative-control 法）。
long バイアス confound（供給は単調増→常に long 化）も de-trend 版と always-long で診断。

Usage:
    python -m research.scripts.validate_stables_signal --predictand BTC
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import os
import statistics

_AG = {"CORN", "WHEAT", "SOYBEAN", "COFFEE", "COCOA", "SUGAR", "COTTON", "CATTLE", "HOGS", "OJ"}
_EQ = {"KO", "PG", "MCD", "XLU", "XLP", "EWJ", "EWZ"}


def _load(n: str) -> dict[str, float]:
    d = {}
    for row in csv.DictReader(open(f"research/data/raw/altdata/{n}.csv")):
        try:
            d[row["date"]] = float(row["close"])
        except (ValueError, KeyError):
            pass
    return d


def _sharpe(x: list[float]) -> float:
    if len(x) < 2:
        return 0.0
    sd = statistics.pstdev(x)
    return statistics.mean(x) / sd * math.sqrt(365) if sd > 0 else 0.0


def _signal_returns(feat: dict[str, float], btc: dict[str, float], dates: list[str], mode: str):
    """mode: ls_growth(5d成長符号 L/S) / lf_growth(long/flat) / ls_z(60d z-score L/S 脱トレンド)"""
    rets, longs = [], 0
    for i in range(61, len(dates) - 1):
        if mode in ("ls_growth", "lf_growth"):
            s = feat[dates[i]] / feat[dates[i - 5]] - 1.0
        else:  # ls_z: 60d z-score（単調トレンドを除去）
            win = [feat[dates[j]] for j in range(i - 60, i + 1)]
            mu, sd = statistics.mean(win), statistics.pstdev(win)
            s = (feat[dates[i]] - mu) / sd if sd > 0 else 0.0
        b = math.log(btc[dates[i + 1]] / btc[dates[i]])
        if mode == "lf_growth":
            rets.append(b if s >= 0 else 0.0)
        else:
            rets.append((1 if s >= 0 else -1) * b)
        longs += 1 if s >= 0 else 0
    return rets, longs / len(rets) if rets else 0.0


def _walk_forward_pos(rets: list[float], k: int = 10) -> float:
    size = max(1, len(rets) // k)
    pos = tot = 0
    for w in range(k):
        seg = rets[w * size:(w + 1) * size]
        if len(seg) >= 5:
            tot += 1
            pos += 1 if _sharpe(seg) > 0 else 0
    return pos / tot if tot else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--predictand", default="BTC")
    args = ap.parse_args()

    btc = _load(args.predictand)
    controls = [os.path.basename(p)[:-4] for p in glob.glob("research/data/raw/altdata/*.csv")
                if os.path.basename(p)[:-4] in (_AG | _EQ)]
    series = {"STABLES": _load("STABLES"), **{c: _load(c) for c in controls}}
    common = set(btc)
    for s in series.values():
        common &= set(s)
    dates = sorted(common)
    bh = [math.log(btc[dates[i + 1]] / btc[dates[i]]) for i in range(61, len(dates) - 1)]
    print(f"[validate] predictand={args.predictand} dates={len(dates)} {dates[0]}..{dates[-1]}")
    print(f"[validate] BTC buy&hold Sharpe={_sharpe(bh):+.2f}  always-long ref\n")

    for mode in ("ls_growth", "lf_growth", "ls_z"):
        rows = []
        for name, feat in series.items():
            rets, longfrac = _signal_returns(feat, btc, dates, mode)
            cut = int(len(rets) * 0.7)
            rows.append((name, _sharpe(rets), _sharpe(rets[:cut]), _sharpe(rets[cut:]),
                         _walk_forward_pos(rets), longfrac))
        nulls = [r for r in rows if r[0] in _AG]  # 真 null=農産物
        stab = next(r for r in rows if r[0] == "STABLES")
        null_full = sorted(r[1] for r in nulls)
        pct = sum(1 for v in null_full if v < stab[1]) / len(null_full) * 100
        print(f"## mode={mode}")
        print(f"  STABLES: full Sharpe={stab[1]:+.2f} IS={stab[2]:+.2f} OOS={stab[3]:+.2f} "
              f"WF_pos={stab[4]:.0%} long率={stab[5]:.0%}")
        print(f"  農産物null(n={len(nulls)}) full Sharpe: median={statistics.median(null_full):+.2f} "
              f"max={max(null_full):+.2f}  → STABLES percentile={pct:.0f}%")
        eq = sorted(r[1] for r in rows if r[0] in _EQ)
        print(f"  株式null(β保有,参考): median={statistics.median(eq):+.2f} max={max(eq):+.2f}\n")


if __name__ == "__main__":
    main()
