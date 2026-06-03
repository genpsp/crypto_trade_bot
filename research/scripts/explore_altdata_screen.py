"""alt-data Round Track1+3: 単変量 IC スクリーン + ネガティブコントロール較正

各 (feature × transform × horizon) で IC(Spearman, lag1 で lookahead 防止)を測り、
Tier1(機序あり) と Tier4(無機序ネガコン) を同一パイプラインに通す。
Tier4 の「有意」率 = パイプラインの経験的偽陽性 baseline（自己相関膨張込み）。
Tier1 がその baseline を超えて初めて本物のシグナルと判定する。

全試行を trial_ledger.csv に記録し DSR/多重検定の n_trials を正直に数える。

Usage:
    python -m research.scripts.explore_altdata_screen --predictand BTC --horizons 1,5
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import os
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _norm_sf(x: float) -> float:
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def _load_series(path: str) -> dict[str, float]:
    out: dict[str, float] = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                out[row["date"]] = float(row["close"])
            except (ValueError, KeyError):
                continue
    return out


def _load_tiers(raw_dir: str) -> dict[str, str]:
    tiers: dict[str, str] = {}
    mpath = os.path.join(raw_dir, "_manifest.csv")
    if os.path.exists(mpath):
        with open(mpath) as f:
            for row in csv.DictReader(f):
                tiers[row["name"]] = row["tier"]
    return tiers


def _spearman(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 10:
        return float("nan")

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)))
    vy = math.sqrt(sum((ry[i] - my) ** 2 for i in range(n)))
    return cov / (vx * vy) if vx > 0 and vy > 0 else float("nan")


def _transform(series: list[float], kind: str) -> list[float | None]:
    n = len(series)
    out: list[float | None] = [None] * n
    if kind == "chg1":
        for i in range(1, n):
            if series[i - 1] != 0:
                out[i] = series[i] / series[i - 1] - 1.0
    elif kind == "chg5":
        for i in range(5, n):
            if series[i - 5] != 0:
                out[i] = series[i] / series[i - 5] - 1.0
    elif kind == "z20":
        for i in range(20, n):
            win = series[i - 20 : i]
            mu = sum(win) / 20.0
            sd = statistics.pstdev(win)
            out[i] = (series[i] - mu) / sd if sd > 0 else None
    return out


@dataclass
class _Trial:
    feature: str
    tier: str
    transform: str
    horizon: int
    ic: float
    n: int
    tstat: float
    p: float
    ic_train: float
    ic_test: float
    sign_stab: float


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="research/data/raw/altdata")
    parser.add_argument("--predictand", default="BTC")
    parser.add_argument("--horizons", default="1,5")
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--ledger", default="research/data/altdata/trial_ledger.csv")
    args = parser.parse_args()

    horizons = [int(h) for h in args.horizons.split(",")]
    tiers = _load_tiers(args.raw_dir)
    files = {os.path.basename(p)[:-4]: p for p in glob.glob(os.path.join(args.raw_dir, "*.csv"))
             if not p.endswith("_manifest.csv")}

    # 共通日付（全 feature が値を持つ＝平日）で整合
    series = {name: _load_series(p) for name, p in files.items()}
    feature_names = [n for n in series if tiers.get(n) in ("1", "2", "3", "4")]
    common = set(series[args.predictand].keys())
    for n in feature_names:
        common &= set(series[n].keys())
    dates = sorted(common)
    px = [series[args.predictand][d] for d in dates]
    # predictand forward log return (lag1: 特徴は t、予測は t+1→t+1+h で lookahead 防止)
    fwd: dict[int, list[float | None]] = {}
    for h in horizons:
        arr: list[float | None] = [None] * len(dates)
        for i in range(len(dates) - h - 1):
            a, b = px[i + 1], px[i + 1 + h]
            if a > 0 and b > 0:
                arr[i] = math.log(b / a)
        fwd[h] = arr

    transforms = ["chg1", "chg5", "z20"]
    trials: list[_Trial] = []
    for name in feature_names:
        raw = [series[name][d] for d in dates]
        for tf in transforms:
            feat = _transform(raw, tf)
            for h in horizons:
                y = fwd[h]
                xs, ys = [], []
                for i in range(len(dates)):
                    if feat[i] is not None and y[i] is not None:
                        xs.append(feat[i]); ys.append(y[i])
                if len(xs) < 60:
                    continue
                ic = _spearman(xs, ys)
                if not math.isfinite(ic):
                    continue
                nn = len(xs)
                tstat = ic * math.sqrt(max(nn - 2, 1)) / math.sqrt(max(1e-9, 1 - ic * ic))
                p = 2.0 * _norm_sf(abs(tstat))
                cut = int(nn * 0.7)
                ic_tr = _spearman(xs[:cut], ys[:cut])
                ic_te = _spearman(xs[cut:], ys[cut:])
                # 符号安定性: 5 sub-period で IC 符号が full と一致する割合
                seg = max(1, nn // 5)
                signs = []
                for s in range(5):
                    sx, sy = xs[s * seg:(s + 1) * seg], ys[s * seg:(s + 1) * seg]
                    sic = _spearman(sx, sy)
                    if math.isfinite(sic):
                        signs.append(1 if (sic > 0) == (ic > 0) else 0)
                stab = sum(signs) / len(signs) if signs else 0.0
                trials.append(_Trial(name, tiers.get(name, "?"), tf, h, round(ic, 4), nn,
                                     round(tstat, 2), round(p, 4),
                                     round(ic_tr, 4) if math.isfinite(ic_tr) else float("nan"),
                                     round(ic_te, 4) if math.isfinite(ic_te) else float("nan"),
                                     round(stab, 2)))

    # trial ledger 出力（honest n_trials）
    Path(args.ledger).parent.mkdir(parents=True, exist_ok=True)
    with open(args.ledger, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["feature", "tier", "transform", "horizon", "ic", "n", "tstat", "p",
                    "ic_train", "ic_test", "sign_stability"])
        for t in trials:
            w.writerow([t.feature, t.tier, t.transform, t.horizon, t.ic, t.n, t.tstat, t.p,
                        t.ic_train, t.ic_test, t.sign_stab])

    n_trials = len(trials)
    print(f"[altdata] predictand={args.predictand} horizons={horizons} dates={len(dates)} "
          f"{dates[0]}..{dates[-1]}  n_trials={n_trials}\n")

    # Tier 別の「有意」率（経験的偽陽性 baseline の較正）
    print("## Tier 別 有意率（|p|<alpha） = ネガコン baseline 較正\n")
    print(f"| tier | trials | sig(p<{args.alpha}) | sig率 | OOS一致(IC_train,test同符号 & test|IC|>0.03) |")
    print("|---|---|---|---|---|")
    for tier in ["1", "2", "3", "4"]:
        ts = [t for t in trials if t.tier == tier]
        if not ts:
            continue
        sig = [t for t in ts if t.p < args.alpha]
        oos = [t for t in ts if math.isfinite(t.ic_test) and (t.ic_train > 0) == (t.ic_test > 0)
               and abs(t.ic_test) > 0.03]
        label = {"1": "Tier1 マクロ", "2": "Tier2 crypto構造", "3": "Tier3 proxy", "4": "Tier4 ネガコン"}[tier]
        print(f"| {label} | {len(ts)} | {len(sig)} | {len(sig)/len(ts)*100:.0f}% | {len(oos)} ({len(oos)/len(ts)*100:.0f}%) |")

    # 上位（|IC| 順）— Tier1/3 のみ、安定性・OOS 付き
    print("\n## 上位特徴（Tier1/3, |IC| 降順 top15）\n")
    print("| feature | tier | tf | h | IC | tstat | p | IC_train | IC_test | sign_stab |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    top = sorted([t for t in trials if t.tier in ("1", "2", "3")], key=lambda t: -abs(t.ic))[:15]
    for t in top:
        print(f"| {t.feature} | {t.tier} | {t.transform} | {t.horizon} | {t.ic:+.3f} | {t.tstat:+.1f} "
              f"| {t.p:.3f} | {t.ic_train:+.3f} | {t.ic_test:+.3f} | {t.sign_stab:.1f} |")

    # Tier4 の最強（偽陽性の実例）
    print("\n## Tier4 ネガコンの最強 |IC| top5（偽陽性の規模感）\n")
    print("| feature | tf | h | IC | p | IC_test |")
    print("|---|---|---|---|---|---|")
    for t in sorted([t for t in trials if t.tier == "4"], key=lambda t: -abs(t.ic))[:5]:
        print(f"| {t.feature} | {t.transform} | {t.horizon} | {t.ic:+.3f} | {t.p:.3f} | {t.ic_test:+.3f} |")
    print(f"\n[altdata] ledger → {args.ledger}")


if __name__ == "__main__":
    main()
