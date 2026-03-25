from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

import requests

from apps.gmo_bot.adapters.market_data.ohlcv_provider import OhlcvProvider


class _FakeGmoClient:
    def __init__(
        self,
        rows_by_key: dict[tuple[str, str, str], list[dict]],
        errors_by_key: dict[tuple[str, str, str], Exception] | None = None,
    ):
        self.rows_by_key = rows_by_key
        self.errors_by_key = errors_by_key or {}

    def get_klines(self, symbol: str, interval: str, date: str) -> list[dict]:
        error = self.errors_by_key.get((symbol, interval, date))
        if error is not None:
            raise error
        return list(self.rows_by_key.get((symbol, interval, date), []))


class GmoOhlcvProviderTest(unittest.TestCase):
    def test_fetch_bars_normalizes_15m_bar_times_and_excludes_open_bar(self) -> None:
        now = datetime(2026, 3, 10, 7, 46, tzinfo=UTC)
        date_token = now.strftime("%Y%m%d")
        rows = [
            {
                "openTime": str(int(datetime(2026, 3, 10, 7, 30, 5, tzinfo=UTC).timestamp() * 1000)),
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100.5",
                "volume": "10",
            },
            {
                "openTime": str(int(datetime(2026, 3, 10, 7, 45, 0, tzinfo=UTC).timestamp() * 1000)),
                "open": "101",
                "high": "102",
                "low": "100",
                "close": "101.5",
                "volume": "11",
            },
        ]
        provider = OhlcvProvider(
            _FakeGmoClient({("SOL_JPY", "15min", date_token): rows}),
            now_provider=lambda: now,
        )

        bars = provider.fetch_bars("SOL/JPY", "15m", 2)

        self.assertEqual(1, len(bars))
        self.assertEqual(datetime(2026, 3, 10, 7, 30, tzinfo=UTC), bars[0].open_time)
        self.assertEqual(datetime(2026, 3, 10, 7, 45, tzinfo=UTC), bars[0].close_time)
        self.assertEqual(100.0, bars[0].open)
        self.assertEqual(100.5, bars[0].close)

    def test_fetch_bars_aggregates_1h_into_2h(self) -> None:
        now = datetime.now(tz=UTC)
        base = (now - timedelta(hours=4)).replace(minute=0, second=0, microsecond=0)
        if base.hour % 2 != 0:
            base -= timedelta(hours=1)
        date_token = now.strftime("%Y%m%d")
        rows = []
        for index in range(4):
            open_time = base + timedelta(hours=index)
            rows.append(
                {
                    "openTime": str(int(open_time.timestamp() * 1000)),
                    "open": str(100 + index),
                    "high": str(101 + index),
                    "low": str(99 + index),
                    "close": str(100.5 + index),
                    "volume": "10",
                }
            )
        provider = OhlcvProvider(
            _FakeGmoClient({("SOL_JPY", "1hour", date_token): rows}),
            now_provider=lambda: now,
        )
        bars = provider.fetch_bars("SOL/JPY", "2h", 2)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].open, 100.0)
        self.assertEqual(bars[0].close, 101.5)
        self.assertEqual(bars[1].open, 102.0)
        self.assertEqual(bars[1].close, 103.5)

    def test_fetch_bars_backfills_more_than_40_days_for_large_15m_request(self) -> None:
        now = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)
        rows_by_key: dict[tuple[str, str, str], list[dict]] = {}
        start_open = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

        for day_offset in range(45):
            day_start = start_open + timedelta(days=day_offset)
            date_token = day_start.strftime("%Y%m%d")
            rows: list[dict] = []
            for bar_offset in range(96):
                open_time = day_start + timedelta(minutes=15 * bar_offset)
                rows.append(
                    {
                        "openTime": str(int(open_time.timestamp() * 1000)),
                        "open": "100",
                        "high": "101",
                        "low": "99",
                        "close": "100.5",
                        "volume": "10",
                    }
                )
            rows_by_key[("SOL_JPY", "15min", date_token)] = rows

        provider = OhlcvProvider(
            _FakeGmoClient(rows_by_key),
            now_provider=lambda: now,
        )

        bars = provider.fetch_bars("SOL/JPY", "15m", 45 * 96)

        self.assertEqual(45 * 96, len(bars))
        self.assertEqual(datetime(2026, 1, 1, 0, 0, tzinfo=UTC), bars[0].open_time)
        self.assertEqual(datetime(2026, 2, 14, 23, 45, tzinfo=UTC), bars[-1].open_time)

    def test_fetch_bars_uses_jst_6am_boundary_for_intraday_date_token(self) -> None:
        now = datetime(2026, 3, 10, 22, 30, tzinfo=UTC)
        rows = [
            {
                "openTime": str(int(datetime(2026, 3, 10, 21, 45, tzinfo=UTC).timestamp() * 1000)),
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100.5",
                "volume": "10",
            }
        ]
        provider = OhlcvProvider(
            _FakeGmoClient({("SOL_JPY", "15min", "20260311"): rows}),
            now_provider=lambda: now,
        )

        bars = provider.fetch_bars("SOL/JPY", "15m", 1)

        self.assertEqual(1, len(bars))
        self.assertEqual(datetime(2026, 3, 10, 21, 45, tzinfo=UTC), bars[0].open_time)

    def test_fetch_bars_falls_back_to_previous_date_when_new_jst_bucket_404s(self) -> None:
        now = datetime(2026, 3, 24, 21, 0, tzinfo=UTC)
        previous_bucket_rows = [
            {
                "openTime": str(int(datetime(2026, 3, 24, 20, 45, tzinfo=UTC).timestamp() * 1000)),
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100.5",
                "volume": "10",
            }
        ]
        response = requests.Response()
        response.status_code = 404
        boundary_error = requests.HTTPError(
            "404 Client Error: Not Found for url: https://api.coin.z.com/public/v1/klines",
            response=response,
        )
        provider = OhlcvProvider(
            _FakeGmoClient(
                {("SOL_JPY", "15min", "20260324"): previous_bucket_rows},
                errors_by_key={("SOL_JPY", "15min", "20260325"): boundary_error},
            ),
            now_provider=lambda: now,
        )

        bars = provider.fetch_bars("SOL/JPY", "15m", 1)

        self.assertEqual(1, len(bars))
        self.assertEqual(datetime(2026, 3, 24, 20, 45, tzinfo=UTC), bars[0].open_time)


if __name__ == "__main__":
    unittest.main()
