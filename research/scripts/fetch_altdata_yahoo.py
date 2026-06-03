"""alt-data Round: Yahoo Finance から日次系列を取得（predictand + Tier1 + Tier4）

Tier1 = 強いマクロ/クロスアセット機序、Tier4 = 無機序ネガティブコントロール（野菜等）。
両者を同一パイプラインに通し偽陽性率 baseline を較正するため一括取得する。
research/data/raw/altdata/{name}.csv（date,close）に保存。

Usage:
    python -m research.scripts.fetch_altdata_yahoo --start 2021-01-01
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

# (name, yahoo_ticker, tier)  tier: P=predictand / 1 / 3 / 4
_TICKERS: list[tuple[str, str, str]] = [
    # predictand
    ("BTC", "BTC-USD", "P"), ("SOL", "SOL-USD", "P"), ("ETH", "ETH-USD", "P"),
    # Tier 1: マクロ/クロスアセット（強い機序）
    ("DXY", "DX-Y.NYB", "1"), ("USDJPY", "JPY=X", "1"),
    ("US10Y", "^TNX", "1"), ("US5Y", "^FVX", "1"), ("US13W", "^IRX", "1"),
    ("SPX", "^GSPC", "1"), ("NDX", "^IXIC", "1"), ("VIX", "^VIX", "1"),
    ("HYG", "HYG", "1"), ("LQD", "LQD", "1"),
    ("GOLD", "GC=F", "1"), ("COPPER", "HG=F", "1"), ("OIL", "CL=F", "1"),
    # Tier 3: crypto-equity プロキシ
    ("COIN", "COIN", "3"), ("MSTR", "MSTR", "3"),
    # Tier 4: 無機序ネガティブコントロール（野菜・農産物・無関係資産）
    ("CORN", "ZC=F", "4"), ("WHEAT", "ZW=F", "4"), ("SOYBEAN", "ZS=F", "4"),
    ("COFFEE", "KC=F", "4"), ("COCOA", "CC=F", "4"), ("SUGAR", "SB=F", "4"),
    ("COTTON", "CT=F", "4"), ("CATTLE", "LE=F", "4"), ("HOGS", "HE=F", "4"),
    ("OJ", "OJ=F", "4"),
    ("KO", "KO", "4"), ("PG", "PG", "4"), ("MCD", "MCD", "4"),
    ("EWJ", "EWJ", "4"), ("EWZ", "EWZ", "4"), ("XLU", "XLU", "4"), ("XLP", "XLP", "4"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2026-06-02")
    parser.add_argument("--out-dir", default="research/data/raw/altdata")
    args = parser.parse_args()

    import yfinance as yf

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest: list[str] = []
    for name, ticker, tier in _TICKERS:
        try:
            df = yf.download(ticker, start=args.start, end=args.end, progress=False, auto_adjust=True)
            if df is None or df.empty:
                print(f"  [skip] {name:8s} {ticker:10s} empty")
                continue
            close = df["Close"]
            # MultiIndex 列対策
            if hasattr(close, "columns"):
                close = close.iloc[:, 0]
            ser = close.dropna()
            path = out / f"{name}.csv"
            with open(path, "w") as f:
                f.write("date,close\n")
                for ts, v in ser.items():
                    f.write(f"{ts.date()},{float(v)}\n")
            manifest.append(f"{name},{ticker},{tier},{len(ser)},{ser.index[0].date()},{ser.index[-1].date()}")
            print(f"  [ok]   {name:8s} {ticker:10s} tier={tier} n={len(ser):5d} {ser.index[0].date()}..{ser.index[-1].date()}")
        except Exception as e:
            print(f"  [err]  {name:8s} {ticker:10s} {e}")
    (out / "_manifest.csv").write_text(
        "name,ticker,tier,n,first,last\n" + "\n".join(manifest) + "\n", encoding="utf-8"
    )
    print(f"\n[altdata] wrote {len(manifest)} series → {out}")


if __name__ == "__main__":
    main()
