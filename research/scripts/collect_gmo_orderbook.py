"""GMO SOL_JPY order book + trades スナップショット収集スクリプト

15 秒ごとに REST で取得し、日次 CSV に追記する。
microstructure 分析（bid-ask spread、order flow imbalance、trade 方向）用データを蓄積する。

Usage:
    python -m research.scripts.collect_gmo_orderbook \
        --output-dir research/data/raw/orderbook \
        --interval 15 \
        --depth 10
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PUBLIC_API = "https://api.coin.z.com/public"
SYMBOL = "SOL_JPY"


def _get(path: str, params: dict | None = None) -> dict:
    url = f"{PUBLIC_API}{path}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def fetch_orderbook(depth: int = 10) -> dict | None:
    try:
        d = _get("/v1/orderbooks", {"symbol": SYMBOL})
        if d.get("status") != 0:
            return None
        data = d["data"]
        asks = data.get("asks", [])[:depth]
        bids = data.get("bids", [])[:depth]
        return {"asks": asks, "bids": bids}
    except Exception as e:
        print(f"[orderbook] error: {e}", file=sys.stderr)
        return None


def fetch_trades(count: int = 20) -> list[dict] | None:
    try:
        d = _get("/v1/trades", {"symbol": SYMBOL, "page": 1, "count": count})
        if d.get("status") != 0:
            return None
        return d["data"]["list"]
    except Exception as e:
        print(f"[trades] error: {e}", file=sys.stderr)
        return None


def compute_snapshot(ob: dict, depth: int) -> dict:
    asks = ob["asks"]
    bids = ob["bids"]
    best_ask = float(asks[0]["price"]) if asks else float("nan")
    best_bid = float(bids[0]["price"]) if bids else float("nan")
    spread = best_ask - best_bid
    spread_bps = spread / best_bid * 10000 if best_bid > 0 else float("nan")
    mid = (best_ask + best_bid) / 2 if asks and bids else float("nan")

    ask_vol = sum(float(a["size"]) for a in asks[:depth])
    bid_vol = sum(float(b["size"]) for b in bids[:depth])
    total_vol = ask_vol + bid_vol
    imbalance = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0.0

    row: dict = {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "spread_bps": round(spread_bps, 3),
        "mid": mid,
        "bid_vol": bid_vol,
        "ask_vol": ask_vol,
        "imbalance": round(imbalance, 4),
    }
    # 各 depth レベルの price/size
    for i in range(depth):
        if i < len(bids):
            row[f"bid_{i+1}_price"] = float(bids[i]["price"])
            row[f"bid_{i+1}_size"] = float(bids[i]["size"])
        if i < len(asks):
            row[f"ask_{i+1}_price"] = float(asks[i]["price"])
            row[f"ask_{i+1}_size"] = float(asks[i]["size"])
    return row


def compute_trade_stats(trades: list[dict], window_secs: int = 60) -> dict:
    """直近 window_secs 秒の trades 集計"""
    now = datetime.now(timezone.utc)
    recent = [t for t in trades
              if (now - datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))).total_seconds() <= window_secs]
    buy_vol = sum(float(t["size"]) for t in recent if t["side"] == "BUY")
    sell_vol = sum(float(t["size"]) for t in recent if t["side"] == "SELL")
    total = buy_vol + sell_vol
    ofi = (buy_vol - sell_vol) / total if total > 0 else 0.0  # order flow imbalance
    return {
        "trade_n": len(recent),
        "buy_vol": round(buy_vol, 4),
        "sell_vol": round(sell_vol, 4),
        "ofi": round(ofi, 4),  # +1=全量 BUY, -1=全量 SELL
    }


def get_output_path(output_dir: Path) -> Path:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return output_dir / f"gmo_soljpy_ob_{date_str}.csv"


OB_FIELDS: list[str] = []  # 初回スナップショット時に決定


def append_row(path: Path, row: dict) -> None:
    global OB_FIELDS
    write_header = not path.exists()
    if not OB_FIELDS:
        OB_FIELDS = list(row.keys())
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OB_FIELDS)
        if write_header:
            w.writeheader()
        w.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="research/data/raw/orderbook")
    parser.add_argument("--interval", type=int, default=15, help="polling interval (seconds)")
    parser.add_argument("--depth", type=int, default=10, help="orderbook depth levels")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[collect] GMO {SYMBOL} orderbook collection started")
    print(f"  interval={args.interval}s  depth={args.depth}  output={out_dir}")

    last_trade_ts: str | None = None
    n_collected = 0

    while True:
        t_start = time.monotonic()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        ob = fetch_orderbook(depth=args.depth)
        trades = fetch_trades(count=50)

        if ob is None or not ob["bids"] or not ob["asks"]:
            print(f"[{ts}] orderbook fetch failed, skipping", file=sys.stderr)
        else:
            snap = compute_snapshot(ob, args.depth)
            trade_stats = compute_trade_stats(trades or [], window_secs=args.interval * 2)

            row = {"timestamp": ts, **snap, **trade_stats}
            path = get_output_path(out_dir)
            append_row(path, row)
            n_collected += 1

            if n_collected % 20 == 1:
                print(f"[{ts}] mid={snap['mid']:.1f} spread={snap['spread_bps']:.1f}bps "
                      f"imbalance={snap['imbalance']:+.3f} ofi={trade_stats['ofi']:+.3f} "
                      f"n={n_collected}")

        elapsed = time.monotonic() - t_start
        sleep_time = max(0, args.interval - elapsed)
        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
