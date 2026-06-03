"""alt-data Track2: crypto 構造/フロー データ取得

- STABLES: USD ステーブルコイン総時価総額（DefiLlama, 全履歴）= オンランプ流動性
- DVOL_BTC / DVOL_ETH: Deribit 実装ボラ指数（~2023-09〜）= positioning/risk
- AGGFUND: 既存 Binance 20 perp funding の日次クロスセクション平均 = 集計レバレッジ

STABLES は長期履歴なので altdata パネル（tier=2）に統合し Track1 と同窓で比較。
DVOL/AGGFUND は短履歴なので別途。全て research/data/raw/altdata/ に保存。

Usage:
    python -m research.scripts.fetch_crypto_structural
"""

from __future__ import annotations

import csv
import glob
import os
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import requests

_OUT = Path("research/data/raw/altdata")


def _write(name: str, rows: list[tuple[str, float]]) -> None:
    rows = sorted(rows)
    with open(_OUT / f"{name}.csv", "w") as f:
        f.write("date,close\n")
        for d, v in rows:
            f.write(f"{d},{v}\n")
    print(f"  [ok] {name:10s} n={len(rows):5d} {rows[0][0]}..{rows[-1][0]}")


def fetch_stables() -> None:
    r = requests.get("https://stablecoins.llama.fi/stablecoincharts/all", timeout=30).json()
    rows = []
    for it in r:
        usd = it.get("totalCirculatingUSD", {}).get("peggedUSD")
        if usd:
            d = datetime.fromtimestamp(int(it["date"]), tz=UTC).date().isoformat()
            rows.append((d, float(usd)))
    _write("STABLES", rows)


def fetch_dvol(currency: str) -> None:
    end = int(time.time() * 1000)
    start = int((time.time() - 3600 * 24 * 365 * 6) * 1000)
    out: dict[str, float] = {}
    cursor_end = end
    for _ in range(8):  # ページング（1000pt/req）
        r = requests.get(
            "https://www.deribit.com/api/v2/public/get_volatility_index_data",
            params={"currency": currency, "start_timestamp": start,
                    "end_timestamp": cursor_end, "resolution": "1D"}, timeout=20,
        ).json()
        data = r.get("result", {}).get("data", [])
        if not data:
            break
        for ts, o, h, l, c in data:
            d = datetime.fromtimestamp(ts / 1000, tz=UTC).date().isoformat()
            out[d] = float(c)
        oldest = min(x[0] for x in data)
        if oldest <= start or len(data) < 1000:
            break
        cursor_end = oldest - 1
        time.sleep(0.2)
    _write(f"DVOL_{currency}", list(out.items()))


def build_aggfund() -> None:
    files = glob.glob("research/data/raw/funding/*_funding_8h.csv")
    by_time: dict[str, list[float]] = defaultdict(list)
    for p in files:
        for row in csv.DictReader(open(p)):
            try:
                by_time[row["funding_time"][:10]].append(float(row["funding_rate"]))
            except (ValueError, KeyError):
                continue
    rows = [(d, sum(v) / len(v)) for d, v in by_time.items() if v]  # 日次クロスセクション平均funding
    if rows:
        _write("AGGFUND", rows)


def main() -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    print("[track2] fetch crypto-structural")
    fetch_stables()
    fetch_dvol("BTC")
    fetch_dvol("ETH")
    build_aggfund()
    # manifest に tier=2 で追記（STABLES のみ長期＝主パネル統合, 他は短期で別途）
    man = _OUT / "_manifest.csv"
    existing = man.read_text().rstrip("\n") if man.exists() else "name,ticker,tier,n,first,last"
    add = []
    for name in ["STABLES", "DVOL_BTC", "DVOL_ETH", "AGGFUND"]:
        p = _OUT / f"{name}.csv"
        if p.exists() and name not in existing:
            lines = p.read_text().strip().split("\n")[1:]
            add.append(f"{name},{name},2,{len(lines)},{lines[0].split(',')[0]},{lines[-1].split(',')[0]}")
    if add:
        man.write_text(existing + "\n" + "\n".join(add) + "\n", encoding="utf-8")
    print(f"[track2] manifest += {len(add)} tier2 series")


if __name__ == "__main__":
    main()
