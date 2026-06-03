"""Track B: GMO レバレッジ JPY ユニバースの 15m backfill (research 専用)

fetch_gmo_pair_15m_paced の fetch_paced を symbol 直指定でループし
クロスセクション探索用に複数銘柄の 15m CSV を research/data/raw/ に揃える
LIVE 結合の PAIR_SYMBOL_MAP は触らず symbol を直接渡す

Usage:
    python -m research.scripts.fetch_gmo_universe_15m --days 460 --sleep 0.3
"""

from __future__ import annotations

import argparse
from pathlib import Path

from research.scripts.fetch_gmo_pair_15m_paced import fetch_paced
from research.src.adapters.csv_bar_repository import write_bars_to_csv

# 既に cache 済の SOL/BTC/ETH と履歴の浅い SUI を除く GMO レバレッジ JPY 銘柄
_SYMBOLS: dict[str, str] = {
    "BCH_JPY": "bchjpy_15m_1y.csv",
    "LTC_JPY": "ltcjpy_15m_1y.csv",
    "XRP_JPY": "xrpjpy_15m_1y.csv",
    "DOT_JPY": "dotjpy_15m_1y.csv",
    "ATOM_JPY": "atomjpy_15m_1y.csv",
    "ADA_JPY": "adajpy_15m_1y.csv",
    "LINK_JPY": "linkjpy_15m_1y.csv",
    "DOGE_JPY": "dogejpy_15m_1y.csv",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=460)
    parser.add_argument("--sleep", type=float, default=0.3)
    parser.add_argument("--out-dir", default="research/data/raw")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for symbol, fname in _SYMBOLS.items():
        print(f"\n=== fetch {symbol} → {fname} ===")
        bars = fetch_paced(symbol=symbol, days_back=args.days, sleep_seconds=args.sleep)
        path = out_dir / fname
        write_bars_to_csv(path, bars)
        if bars:
            first = bars[0].open_time.isoformat().replace("+00:00", "Z")
            last = bars[-1].open_time.isoformat().replace("+00:00", "Z")
        else:
            first = last = None
        print(f"[universe] {symbol} bars={len(bars)} first={first} last={last} → {path}")


if __name__ == "__main__":
    main()
