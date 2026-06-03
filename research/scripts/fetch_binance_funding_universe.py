"""Track C: Binance USDⓈ-M perp の funding(8h)+markPrice をユニバースで取得

fapi/v1/fundingRate（認証不要・public）を startTime ページングで遡り
各銘柄を research/data/raw/funding/{sym}_funding_8h.csv に保存
列: funding_time, funding_rate, mark_price（既存 SOL CSV と同形式）

Usage:
    python -m research.scripts.fetch_binance_funding_universe --start 2025-03-01
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

_BASE = "https://fapi.binance.com/fapi/v1/fundingRate"

# 2025-03 以前から履歴のある流動的 USDT perp（XS/carry の breadth 用）
_UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
    "BNBUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT", "BCHUSDT",
    "TRXUSDT", "ATOMUSDT", "NEARUSDT", "FILUSDT", "UNIUSDT", "AAVEUSDT",
    "INJUSDT", "APTUSDT",
]


def _fetch_symbol(symbol: str, start_ms: int, sleep_s: float) -> list[dict]:
    rows: list[dict] = []
    cursor = start_ms
    seen: set[int] = set()
    while True:
        resp = requests.get(
            _BASE, params={"symbol": symbol, "startTime": cursor, "limit": 1000}, timeout=15
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        new = 0
        for it in batch:
            ft = int(it["fundingTime"])
            if ft in seen:
                continue
            seen.add(ft)
            rows.append(
                {
                    "funding_time": datetime.fromtimestamp(ft / 1000, tz=UTC)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "funding_rate": it["fundingRate"],
                    "mark_price": it.get("markPrice", ""),
                }
            )
            new += 1
        last_ft = int(batch[-1]["fundingTime"])
        if new == 0 or len(batch) < 1000:
            break
        cursor = last_ft + 1
        time.sleep(sleep_s)
    rows.sort(key=lambda r: r["funding_time"])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2025-03-01")
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--out-dir", default="research/data/raw/funding")
    args = parser.parse_args()

    start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=UTC).timestamp() * 1000)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for symbol in _UNIVERSE:
        rows = _fetch_symbol(symbol, start_ms, args.sleep)
        path = out_dir / f"{symbol}_funding_8h.csv"
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["funding_time", "funding_rate", "mark_price"])
            w.writeheader()
            w.writerows(rows)
        first = rows[0]["funding_time"] if rows else None
        last = rows[-1]["funding_time"] if rows else None
        print(f"[funding] {symbol:10s} n={len(rows):5d} {first} .. {last} → {path}")
        time.sleep(args.sleep)


if __name__ == "__main__":
    main()
