from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from apps.gmo_bot.adapters.market_data.ohlcv_provider import OhlcvProvider


class _FakeGmoClient:
    def __init__(self, rows_by_key: dict[tuple[str, str, str], list[dict]]):
        self.rows_by_key = rows_by_key

    def get_klines(self, symbol: str, interval: str, date: str) -> list[dict]:
        return list(self.rows_by_key.get((symbol, interval, date), []))


class GmoOhlcvProviderTest(unittest.TestCase):
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
        provider = OhlcvProvider(_FakeGmoClient({("SOL_JPY", "1hour", date_token): rows}))
        bars = provider.fetch_bars("SOL/JPY", "2h", 2)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].open, 100.0)
        self.assertEqual(bars[0].close, 101.5)
        self.assertEqual(bars[1].open, 102.0)
        self.assertEqual(bars[1].close, 103.5)


if __name__ == "__main__":
    unittest.main()
