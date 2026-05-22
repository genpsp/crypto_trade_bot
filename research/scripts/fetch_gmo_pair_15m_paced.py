"""Paced 15m OHLCV backfill for GMO_COIN pairs.

The standard `fetch_ohlcv.py` uses provider.fetch_bars_backfill which hits the
GMO public API at full speed and trips ERR-5003 (rate limit) when pulling
multi-month windows. This research-only fetcher walks back day-by-day with a
configurable sleep between requests, accumulating bars into a CSV.

Usage:

    python -m research.scripts.fetch_gmo_pair_15m_paced \\
        --pair BTC/JPY --days 450 --output research/data/raw/btcjpy_15m_1y.csv \\
        --sleep 0.4
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

from apps.dex_bot.domain.model.types import OhlcvBar
from apps.gmo_bot.adapters.execution.gmo_api_client import GmoApiClient
from apps.gmo_bot.adapters.symbol_map import PAIR_SYMBOL_MAP
from apps.gmo_bot.domain.utils.time import JST
from research.src.adapters.csv_bar_repository import write_bars_to_csv


def _date_token_for_jst_day(target_jst_day: datetime) -> str:
    # GMO returns klines for a JST trading day; the token is YYYYMMDD in JST.
    return target_jst_day.strftime("%Y%m%d")


def _row_to_bar(row: dict, interval_seconds: int) -> OhlcvBar | None:
    try:
        open_time_ms = int(row.get("openTime"))
        open_time = datetime.fromtimestamp(open_time_ms / 1000.0, tz=UTC)
        close_time = open_time + timedelta(seconds=interval_seconds)
        return OhlcvBar(
            open_time=open_time,
            close_time=close_time,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )
    except (KeyError, ValueError, TypeError):
        return None


def fetch_paced(
    symbol: str,
    days_back: int,
    sleep_seconds: float,
    interval: str = "15min",
) -> list[OhlcvBar]:
    client = GmoApiClient(api_key="", api_secret="")
    now_jst = datetime.now(tz=JST)
    interval_seconds = 15 * 60 if interval == "15min" else 60 * 60
    bars_by_open_time: dict[datetime, OhlcvBar] = {}
    consecutive_empty = 0
    for back in range(days_back):
        target = now_jst - timedelta(days=back)
        # GMO trading day starts 06:00 JST; use the same convention as the
        # production provider (subtract 6h before formatting).
        token = _date_token_for_jst_day(target - timedelta(hours=6))
        try:
            rows = client.get_klines(symbol=symbol, interval=interval, date=token)
        except requests.HTTPError as error:
            print(f"  [paced] day -{back} {token} HTTP error: {error}")
            time.sleep(sleep_seconds * 3)
            continue
        except RuntimeError as error:
            # Rate limit hit — back off and retry once
            if "ERR-5003" in str(error):
                print(f"  [paced] day -{back} {token} rate-limited, backing off 5s")
                time.sleep(5.0)
                try:
                    rows = client.get_klines(
                        symbol=symbol, interval=interval, date=token
                    )
                except Exception as retry_error:
                    print(f"  [paced] day -{back} {token} retry failed: {retry_error}")
                    continue
            else:
                raise
        new_count = 0
        for row in rows or []:
            bar = _row_to_bar(row, interval_seconds)
            if bar is not None and bar.open_time not in bars_by_open_time:
                bars_by_open_time[bar.open_time] = bar
                new_count += 1
        if new_count == 0:
            consecutive_empty += 1
            if consecutive_empty >= 5:
                print(f"  [paced] day -{back} {token} 5 consecutive empty, stopping")
                break
        else:
            consecutive_empty = 0
        if back % 30 == 0:
            print(
                f"  [paced] day -{back} {token} → fetched={new_count} total={len(bars_by_open_time)}"
            )
        time.sleep(sleep_seconds)

    return [bars_by_open_time[key] for key in sorted(bars_by_open_time.keys())]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", required=True, choices=sorted(PAIR_SYMBOL_MAP.keys()))
    parser.add_argument("--days", type=int, default=450)
    parser.add_argument("--sleep", type=float, default=0.4)
    parser.add_argument("--output", required=True)
    parser.add_argument("--interval", default="15min", choices=["15min", "1hour"])
    args = parser.parse_args()

    symbol = PAIR_SYMBOL_MAP[args.pair]
    print(f"[paced] start pair={args.pair} symbol={symbol} days={args.days} sleep={args.sleep}s")
    bars = fetch_paced(
        symbol=symbol,
        days_back=args.days,
        sleep_seconds=args.sleep,
        interval=args.interval,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_bars_to_csv(output, bars)
    if bars:
        first = bars[0].open_time.isoformat().replace("+00:00", "Z")
        last = bars[-1].open_time.isoformat().replace("+00:00", "Z")
    else:
        first = last = None
    print(
        "[paced] done",
        {
            "pair": args.pair,
            "bars": len(bars),
            "output": str(output),
            "first_open": first,
            "last_open": last,
        },
    )


if __name__ == "__main__":
    main()
