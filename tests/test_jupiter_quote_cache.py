from __future__ import annotations

import unittest
from unittest.mock import patch

from pybot.adapters.execution.jupiter_quote_client import JupiterQuoteClient
from pybot.app.ports.execution_port import SubmitSwapRequest


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
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class JupiterQuoteCacheTest(unittest.TestCase):
    def test_fetch_quote_uses_cache_when_ttl_is_enabled(self) -> None:
        redis = FakeRedis()
        client = JupiterQuoteClient(redis=redis)
        payload = {"inAmount": "1000000000", "outAmount": "82300000"}
        request = SubmitSwapRequest(
            side="SELL_SOL_FOR_USDC",
            amount_atomic=1_000_000_000,
            slippage_bps=1,
            only_direct_routes=False,
        )

        with patch(
            "pybot.adapters.execution.jupiter_quote_client.requests.get",
            return_value=FakeResponse(payload),
        ) as requests_get:
            first = client.fetch_quote(request, cache_ttl_seconds=2)
            second = client.fetch_quote(request, cache_ttl_seconds=2)

        self.assertEqual(1, requests_get.call_count)
        self.assertEqual(first.in_amount_atomic, second.in_amount_atomic)
        self.assertEqual(first.out_amount_atomic, second.out_amount_atomic)


if __name__ == "__main__":
    unittest.main()
