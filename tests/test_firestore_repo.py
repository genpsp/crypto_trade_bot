from __future__ import annotations

import unittest
from typing import Any

from pybot.adapters.persistence.firestore_repo import FirestoreRepository


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
    def __init__(self, docs: list[_FakeDoc]):
        super().__init__(firestore=None, config_repo=None, mode="LIVE", model_id="test_model")  # type: ignore[arg-type]
        self._docs = docs

    def _trades_collection(self) -> _FakeQuery:  # type: ignore[override]
        return _FakeQuery(self._docs)


class FirestoreRepositoryCountTradesTest(unittest.TestCase):
    def test_count_trades_for_utc_day_excludes_failed_and_canceled_states(self) -> None:
        repo = _RepositoryUnderTest(
            [
                _FakeDoc({"pair": "SOL/USDC", "state": "FAILED"}),
                _FakeDoc({"pair": "SOL/USDC", "state": "CANCELED"}),
                _FakeDoc({"pair": "SOL/USDC", "state": "CLOSED"}),
                _FakeDoc({"pair": "SOL/USDC", "state": "CONFIRMED"}),
                _FakeDoc({"pair": "BTC/USDC", "state": "CLOSED"}),
            ]
        )

        count = repo.count_trades_for_utc_day(
            pair="SOL/USDC",
            day_start_iso="2026-02-25T00:00:00Z",
            day_end_iso="2026-02-25T23:59:59Z",
        )

        self.assertEqual(2, count)

    def test_count_trades_for_utc_day_ignores_invalid_payload(self) -> None:
        repo = _RepositoryUnderTest(
            [
                _FakeDoc(None),
                _FakeDoc({"pair": "SOL/USDC", "state": "FAILED"}),
                _FakeDoc({"pair": "SOL/USDC", "state": "CLOSED"}),
            ]
        )

        count = repo.count_trades_for_utc_day(
            pair="SOL/USDC",
            day_start_iso="2026-02-25T00:00:00Z",
            day_end_iso="2026-02-25T23:59:59Z",
        )

        self.assertEqual(1, count)


if __name__ == "__main__":
    unittest.main()
