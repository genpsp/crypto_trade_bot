"""Round 4 Phase 1: market-making の adverse selection 実測

収集済み板スナップショット（collect_gmo_orderbook.py 出力）から、maker の realized edge を分解する
中心の問い: クォート spread + maker rebate は、約定後の mid ドリフト（adverse selection）を差し引いても正に残るか

maker fill 近似（スナップショット粒度・lookahead 無し）:
- 各スナップ t で直近窓の order flow（ofi）が sell 優勢(ofi<0)なら bid 側 maker が約定し long 在庫を持つ
  buy 優勢(ofi>0)なら ask 側 maker が約定し short 在庫を持つ
- fill 価格 = touched 側の best（bid_t or ask_t）、評価は t+k スナップの mid
- maker_pnl_bps = sign * (mid_{t+k} - fill_price)/mid_t*1e4
                = half_spread（即時取得）+ signed_mid_drift（負なら adverse selection）
- net_bps = maker_pnl_bps + rebate_bps

分解: net = half_spread + drift(adverse) + rebate
go/no-go: net が複数日で頑健に正か、imbalance 条件付けで adverse を下げられるか
ネガコン: flow を無視した全スナップ擬似約定（baseline）と、flow ラベルの置換検定

注意: スナップ粒度の近似で真の fill-level 再構成ではない（WS trade tape で精緻化）。第一次の adverse 方向性の読み

Usage:
    python -m research.scripts.analyze_mm_adverse_selection \
        --glob 'research/data/raw/orderbook/gmo_sol_ob_*.csv' --rebate-bps 3.0
"""
from __future__ import annotations

import argparse
import csv
import glob as globmod
import math
import random
import statistics as st
from datetime import datetime
from typing import Any


def _parse_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _load(paths: list[str]) -> list[dict[str, float]]:
    rows: list[dict[str, Any]] = []
    seen: set[float] = set()
    for p in sorted(paths):
        with open(p) as f:
            for r in csv.DictReader(f):
                try:
                    t = _parse_ts(r["timestamp"])
                    mid = float(r["mid"]); bid = float(r["best_bid"]); ask = float(r["best_ask"])
                except (KeyError, ValueError):
                    continue
                if not (mid > 0 and bid > 0 and ask > bid) or t in seen:
                    continue
                seen.add(t)
                rows.append({
                    "t": t, "mid": mid, "bid": bid, "ask": ask,
                    "spread_bps": float(r.get("spread_bps", "nan") or "nan"),
                    "imbalance": float(r.get("imbalance", "0") or 0),
                    "ofi": float(r.get("ofi", "0") or 0),
                    "trade_n": float(r.get("trade_n", "0") or 0),
                })
    rows.sort(key=lambda x: x["t"])
    return rows


def _maker_fills(rows, k, ofi_min, rebate_bps, force_side=None):
    """各スナップで flow 駆動の maker 約定を近似し net edge(bps) を返す"""
    out = []
    for i in range(len(rows) - k):
        r, fwd = rows[i], rows[i + k]
        # 時間連続性チェック（k スナップ先が極端に飛んでいたら除外）
        if fwd["t"] - r["t"] > k * 60:  # 15s 間隔想定で k*60s を上限
            continue
        ofi = r["ofi"]
        if force_side is None:
            if abs(ofi) < ofi_min or r["trade_n"] <= 0:
                continue
            side = "bid" if ofi < 0 else "ask"  # sell 優勢→bid hit（long化）
        else:
            side = force_side
        fill = r["bid"] if side == "bid" else r["ask"]
        sign = 1.0 if side == "bid" else -1.0  # long=+1, short=-1
        maker_pnl = sign * (fwd["mid"] - fill) / r["mid"] * 1e4
        half_spread = (r["ask"] - r["bid"]) / 2 / r["mid"] * 1e4
        drift = sign * (fwd["mid"] - r["mid"]) / r["mid"] * 1e4
        out.append({
            "net": maker_pnl + rebate_bps, "maker_pnl": maker_pnl,
            "half_spread": half_spread, "drift": drift, "side": side,
            "imb": r["imbalance"], "ofi": ofi,
        })
    return out


def _summ(vals):
    if not vals:
        return {"n": 0, "mean": 0.0, "median": 0.0, "pos%": 0.0, "sd": 0.0}
    return {"n": len(vals), "mean": round(st.mean(vals), 2), "median": round(st.median(vals), 2),
            "pos%": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
            "sd": round(st.pstdev(vals), 2)}


def _perm_pvalue(flow_net, baseline_net, n=2000, seed=1337):
    """flow 駆動 fill の net が baseline(全スナップ擬似約定) を上回るかの置換検定"""
    if not flow_net or not baseline_net:
        return 1.0
    obs = st.mean(flow_net) - st.mean(baseline_net)
    pool = flow_net + baseline_net
    rng = random.Random(seed)
    na = len(flow_net)
    cnt = 0
    for _ in range(n):
        rng.shuffle(pool)
        diff = st.mean(pool[:na]) - st.mean(pool[na:])
        if diff >= obs:
            cnt += 1
    return (cnt + 1) / (n + 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="research/data/raw/orderbook/gmo_sol_ob_*.csv")
    ap.add_argument("--rebate-bps", type=float, default=3.0, help="maker rebate bps（現物SOL=3.0, SOL_JPY=0）")
    ap.add_argument("--ofi-min", type=float, default=0.2, help="flow 駆動 fill とみなす |ofi| 下限")
    ap.add_argument("--horizons", default="1,2,4,8", help="評価先スナップ数（15s刻み想定）")
    args = ap.parse_args()

    paths = globmod.glob(args.glob)
    rows = _load(paths)
    if rows:
        span_h = (rows[-1]["t"] - rows[0]["t"]) / 3600
        valid_sp = [r["spread_bps"] for r in rows if math.isfinite(r["spread_bps"])]
        print(f"[mm] files={len(paths)} snapshots={len(rows)} span={span_h:.1f}h "
              f"median_spread={st.median(valid_sp):.2f}bps rebate={args.rebate_bps}bps")
    else:
        print(f"[mm] no usable snapshots in {args.glob}")
        return
    if len(rows) < 50:
        print(f"[mm] ⚠ データ不足（{len(rows)} snap）— 蓄積後に再実行。ハーネスは動作確認のみ")

    print(f"\nmaker net edge 分解（net = half_spread + drift(adverse) + rebate {args.rebate_bps}bps）")
    print("| horizon | n | net mean | net med | net pos% | half_sprd | drift(adverse) | rebate | baseline net | perm_p |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for k in [int(x) for x in args.horizons.split(",")]:
        fills = _maker_fills(rows, k, args.ofi_min, args.rebate_bps)
        # ネガコン: flow 無視で bid/ask 両側を全スナップ擬似約定
        base = _maker_fills(rows, k, 0.0, args.rebate_bps, force_side="bid") + \
               _maker_fills(rows, k, 0.0, args.rebate_bps, force_side="ask")
        if not fills:
            print(f"| {k} | 0 | — | — | — | — | — | — | — | — |")
            continue
        net = [f["net"] for f in fills]
        hs = st.mean([f["half_spread"] for f in fills])
        dr = st.mean([f["drift"] for f in fills])
        s = _summ(net)
        bnet = [f["net"] for f in base]
        bmean = round(st.mean(bnet), 2) if bnet else 0.0
        pp = _perm_pvalue(net, bnet)
        print(f"| {k} ({k*15}s) | {s['n']} | {s['mean']:+.2f} | {s['median']:+.2f} | {s['pos%']:.0f}% | "
              f"{hs:+.2f} | {dr:+.2f} | +{args.rebate_bps:.1f} | {bmean:+.2f} | {pp:.3f} |")

    # imbalance 条件付け（fill 側が imbalance と整合/逆行で adverse が変わるか）= quote skew の指針
    print(f"\nimbalance 条件付け（horizon=4, fill 側 vs imbalance 符号）")
    fills4 = _maker_fills(rows, 4, args.ofi_min, args.rebate_bps)
    aligned = [f["net"] for f in fills4 if (f["side"] == "bid") == (f["imb"] > 0)]   # 在庫方向が imbalance と同方向
    against = [f["net"] for f in fills4 if (f["side"] == "bid") != (f["imb"] > 0)]
    print(f"  imbalance 整合側 fill: {_summ(aligned)}")
    print(f"  imbalance 逆行側 fill: {_summ(against)}")


if __name__ == "__main__":
    main()
