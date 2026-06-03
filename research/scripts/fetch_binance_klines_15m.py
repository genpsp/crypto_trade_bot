"""③ 長期再検証用: Binance USDⓈ-M perp の 15m klines を取得

fapi/v1/klines を startTime ページング（1000本/req=~10.4日）で遡る。
explore_track3_cross_sectional が読む形式（open_time,close 他）で保存。
research/data/raw/binance15m/{SYM}_15m.csv

Usage:
    python -m research.scripts.fetch_binance_klines_15m --start 2022-06-01
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

_BASE = "https://fapi.binance.com/fapi/v1/klines"
_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]


def _fetch(symbol: str, start_ms: int, sleep_s: float) -> list[dict]:
    rows: list[dict] = []
    cursor = start_ms
    seen: set[int] = set()
    while True:
        r = requests.get(_BASE, params={"symbol": symbol, "interval": "15m",
                                        "startTime": cursor, "limit": 1000}, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        new = 0
        for k in batch:
            ot = int(k[0])
            if ot in seen:
                continue
            seen.add(ot)
            rows.append({
                "open_time": datetime.fromtimestamp(ot / 1000, tz=UTC).isoformat().replace("+00:00", "Z"),
                "open": k[1], "high": k[2], "low": k[3], "close": k[4], "volume": k[5],
            })
            new += 1
        last = int(batch[-1][0])
        if new == 0 or len(batch) < 1000:
            break
        cursor = last + 1
        time.sleep(sleep_s)
    rows.sort(key=lambda x: x["open_time"])
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2022-06-01")
    ap.add_argument("--sleep", type=float, default=0.15)
    ap.add_argument("--out-dir", default="research/data/raw/binance15m")
    ap.add_argument("--symbols", default=",".join(_SYMBOLS))
    args = ap.parse_args()

    start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=UTC).timestamp() * 1000)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for sym in args.symbols.split(","):
        rows = _fetch(sym, start_ms, args.sleep)
        path = out / f"{sym}_15m.csv"
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["open_time", "open", "high", "low", "close", "volume"])
            w.writeheader()
            w.writerows(rows)
        print(f"[15m] {sym:9s} n={len(rows):6d} {rows[0]['open_time'][:10]}..{rows[-1]['open_time'][:10]} → {path}")


if __name__ == "__main__":
    main()
