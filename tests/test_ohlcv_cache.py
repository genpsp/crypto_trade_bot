from __future__ import annotations

import unittest
from unittest.mock import patch

from pybot.adapters.market_data.ohlcv_provider import OhlcvProvider


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        _ = ex
        self.store[key] = value
        return True


class FakeResponse:
    def __init__(self, payload: list[list]) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> list[list]:
        return self._payload


class OhlcvCacheTest(unittest.TestCase):
    def test_fetch_bars_uses_cache_for_identical_request(self) -> None:
        redis = FakeRedis()
        provider = OhlcvProvider(redis=redis, cache_ttl_seconds=30)
        payload = [
            [1700000000000, "80.0", "81.0", "79.0", "80.5", "1000"],
            [1700000900000, "80.5", "82.0", "80.1", "81.3", "1200"],
        ]

        with patch(
            "pybot.adapters.market_data.ohlcv_provider.requests.get",
            return_value=FakeResponse(payload),
        ) as requests_get:
            first = provider.fetch_bars("SOL/USDC", "15m", 2)
            second = provider.fetch_bars("SOL/USDC", "15m", 2)

        self.assertEqual(1, requests_get.call_count)
        self.assertEqual(2, len(first))
        self.assertEqual(2, len(second))


if __name__ == "__main__":
    unittest.main()
