"""alt-data 候補2: AGGFUND(集計funding 逆張り)→BTC の proper 検証（5年版）

funding_long(20 perp, ~2021〜)の日次クロスセクション平均funding を作り、
逆張りタイミング（高funding=crowded long=翌日弱気）を検証する。
帰無分布は (a) 循環シフト置換検定（signal を返り値に対しランダム巡回シフト、
自己相関保持で偽相関を測る）、(b) 農産物 null の同一処理。

Usage:
    python -m research.scripts.validate_aggfund_signal --predictand BTC
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import os
import random
import statistics
from collections import defaultdict

_AG = {"CORN", "WHEAT", "SOYBEAN", "COFFEE", "COCOA", "SUGAR", "COTTON", "CATTLE", "HOGS", "OJ"}


def _load_alt(n: str) -> dict[str, float]:
    d = {}
    for row in csv.DictReader(open(f"research/data/raw/altdata/{n}.csv")):
        try:
            d[row["date"]] = float(row["close"])
        except (ValueError, KeyError):
            pass
    return d


def _build_aggfund(funding_dir: str) -> dict[str, float]:
    by_day: dict[str, list[float]] = defaultdict(list)
    for p in glob.glob(os.path.join(funding_dir, "*_funding_8h.csv")):
        for row in csv.DictReader(open(p)):
            try:
                by_day[row["funding_time"][:10]].append(float(row["funding_rate"]))
            except (ValueError, KeyError):
                pass
    return {d: sum(v) / len(v) for d, v in by_day.items() if v}


def _sharpe(x: list[float]) -> float:
    if len(x) < 2:
        return 0.0
    sd = statistics.pstdev(x)
    return statistics.mean(x) / sd * math.sqrt(365) if sd > 0 else 0.0


def _contrarian_z(feat: dict[str, float], btc: dict[str, float], dates: list[str], lookback: int = 30):
    """高 z(funding) → short / 低 → long の逆張り L/S 日次"""
    sig, fwd = [], []
    for i in range(lookback + 1, len(dates) - 1):
        win = [feat[dates[j]] for j in range(i - lookback, i + 1)]
        mu, sd = statistics.mean(win), statistics.pstdev(win)
        z = (feat[dates[i]] - mu) / sd if sd > 0 else 0.0
        b = math.log(btc[dates[i + 1]] / btc[dates[i]])
        sig.append(-1 if z >= 0 else 1)  # 逆張り
        fwd.append(b)
    return sig, fwd


def _strat_sharpe(sig, fwd):
    return _sharpe([sig[i] * fwd[i] for i in range(len(sig))])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--predictand", default="BTC")
    ap.add_argument("--funding-dir", default="research/data/raw/funding_long")
    ap.add_argument("--n-perm", type=int, default=1000)
    args = ap.parse_args()
    random.seed(0)

    btc = _load_alt(args.predictand)
    agg = _build_aggfund(args.funding_dir)
    common = sorted(set(btc) & set(agg))
    print(f"[validate] AGGFUND dates={len(common)} {common[0]}..{common[-1]}")

    sig, fwd = _contrarian_z(agg, btc, common)
    real = _strat_sharpe(sig, fwd)
    cut = int(len(sig) * 0.7)
    is_sh = _strat_sharpe(sig[:cut], fwd[:cut])
    oos_sh = _strat_sharpe(sig[cut:], fwd[cut:])
    bh = _sharpe(fwd)
    print(f"[validate] BTC buy&hold(同窓)={bh:+.2f}\n")
    print(f"## AGGFUND 逆張り L/S: full={real:+.2f} IS={is_sh:+.2f} OOS={oos_sh:+.2f}")

    # (a) 循環シフト置換検定: signal を返り値に対しランダム巡回シフト
    n = len(sig)
    null = []
    for _ in range(args.n_perm):
        sh = random.randint(1, n - 1)
        shifted = sig[sh:] + sig[:sh]
        null.append(_strat_sharpe(shifted, fwd))
    p_perm = sum(1 for v in null if v >= real) / len(null)
    print(f"   循環シフト置換 null(n={args.n_perm}): median={statistics.median(null):+.2f} "
          f"p95={sorted(null)[int(len(null)*0.95)]:+.2f}  → p(real≤null)={p_perm:.3f}")

    # (b) 農産物 null の同一逆張り処理
    ag_sh = []
    for name in _AG:
        f = _load_alt(name)
        cm = sorted(set(btc) & set(f))
        s2, w2 = _contrarian_z(f, btc, cm)
        ag_sh.append(_strat_sharpe(s2, w2))
    ag_sh.sort()
    pct = sum(1 for v in ag_sh if v < real) / len(ag_sh) * 100
    print(f"   農産物null(n={len(ag_sh)}) 同処理: median={statistics.median(ag_sh):+.2f} "
          f"max={max(ag_sh):+.2f}  → AGGFUND percentile={pct:.0f}%")


if __name__ == "__main__":
    main()
