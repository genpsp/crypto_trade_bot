from __future__ import annotations

import unittest
from typing import Any

from pybot.adapters.persistence.firestore_repo import FirestoreRepository, _extract_trade_date_from_trade_id


class _FakeDoc:
    def __init__(self, payload: Any):
        self._payload = payload

    def to_dict(self) -> Any:
        return self._payload


class _FakeQuery:
    def __init__(self, docs: list[_FakeDoc]):
        self._docs = docs

    def where(self, *args: Any, **kwargs: Any) -> "_FakeQuery":
        _ = args
        _ = kwargs
        return self

    def get(self) -> list[_FakeDoc]:
        return self._docs


class _RepositoryUnderTest(FirestoreRepository):
    def __init__(self, day_docs: dict[str, list[_FakeDoc]]):
        super().__init__(firestore=None, config_repo=None, mode="LIVE", model_id="test_model")  # type: ignore[arg-type]
        self._day_docs = day_docs

    def _trade_items_collection_for_date(self, trade_date: str) -> _FakeQuery:  # type: ignore[override]
        return _FakeQuery(self._day_docs.get(trade_date, []))


class FirestoreRepositoryCountTradesTest(unittest.TestCase):
    def test_count_trades_for_utc_day_excludes_failed_and_canceled_states(self) -> None:
        repo = _RepositoryUnderTest(
            day_docs={
                "2026-02-25": [
                    _FakeDoc({"trade_id": "t1", "pair": "SOL/USDC", "state": "FAILED"}),
                    _FakeDoc({"trade_id": "t2", "pair": "SOL/USDC", "state": "CANCELED"}),
                    _FakeDoc({"trade_id": "t3", "pair": "SOL/USDC", "state": "CLOSED"}),
                    _FakeDoc({"trade_id": "t4", "pair": "SOL/USDC", "state": "CONFIRMED"}),
                    _FakeDoc({"trade_id": "t5", "pair": "BTC/USDC", "state": "CLOSED"}),
                ]
            },
        )

        count = repo.count_trades_for_utc_day(
            pair="SOL/USDC",
            day_start_iso="2026-02-25T00:00:00Z",
            day_end_iso="2026-02-25T23:59:59Z",
        )

        self.assertEqual(2, count)

    def test_count_trades_for_utc_day_ignores_invalid_payload(self) -> None:
        repo = _RepositoryUnderTest(
            day_docs={
                "2026-02-25": [
                    _FakeDoc(None),
                    _FakeDoc({"trade_id": "t1", "pair": "SOL/USDC", "state": "FAILED"}),
                    _FakeDoc({"trade_id": "t2", "pair": "SOL/USDC", "state": "CLOSED"}),
                ]
            },
        )

        count = repo.count_trades_for_utc_day(
            pair="SOL/USDC",
            day_start_iso="2026-02-25T00:00:00Z",
            day_end_iso="2026-02-25T23:59:59Z",
        )

        self.assertEqual(1, count)

class FirestoreRepositoryTradeIdDateParseTest(unittest.TestCase):
    def test_extract_trade_date_from_trade_id(self) -> None:
        self.assertEqual(
            "2026-02-25",
            _extract_trade_date_from_trade_id("2026-02-25T08:45:00Z_core_long_15m_v0_LONG"),
        )

    def test_extract_trade_date_from_trade_id_returns_none_when_invalid(self) -> None:
        self.assertIsNone(_extract_trade_date_from_trade_id("invalid_trade_id"))


if __name__ == "__main__":
    unittest.main()
