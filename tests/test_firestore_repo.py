from __future__ import annotations

import unittest
from typing import Any, cast

from pybot.adapters.persistence.firestore_repo import (
    FirestoreRepository,
    _build_skip_run_doc_id,
    _extract_trade_date_from_trade_id,
)
from pybot.domain.model.types import BotConfig


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


class _CountingConfigRepo:
    def __init__(self, config: BotConfig):
        self._config = config
        self.calls = 0

    def get_current_config(self, model_id: str) -> BotConfig:
        _ = model_id
        self.calls += 1
        return self._config


class _SetOnlyDocument:
    def __init__(self, doc_id: str):
        self.doc_id = doc_id
        self.set_calls: list[tuple[dict[str, Any], bool]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.children: dict[str, "_SetOnlyCollection"] = {}

    def set(self, payload: dict[str, Any], merge: bool = False) -> None:
        self.set_calls.append((payload, merge))

    def create(self, payload: dict[str, Any]) -> None:
        self.create_calls.append(payload)

    def collection(self, name: str) -> "_SetOnlyCollection":
        collection = self.children.get(name)
        if collection is None:
            collection = _SetOnlyCollection()
            self.children[name] = collection
        return collection

    def get(self) -> Any:
        raise AssertionError("unexpected read from save_run path")


class _SetOnlyCollection:
    def __init__(self):
        self.docs: dict[str, _SetOnlyDocument] = {}

    def document(self, doc_id: str) -> _SetOnlyDocument:
        doc = self.docs.get(doc_id)
        if doc is None:
            doc = _SetOnlyDocument(doc_id)
            self.docs[doc_id] = doc
        return doc


class _SaveRunRepo(FirestoreRepository):
    def __init__(self):
        super().__init__(
            firestore=None,  # type: ignore[arg-type]
            config_repo=None,  # type: ignore[arg-type]
            mode="LIVE",
            model_id="core_long_15m_v0",
        )
        self.runs_collection = _SetOnlyCollection()

    def _touch_model_metadata(self) -> None:  # type: ignore[override]
        return None

    def _runs_collection(self):  # type: ignore[override]
        return self.runs_collection


class _OpenTradeStateCachingRepo(FirestoreRepository):
    def __init__(self, state_trade: dict[str, Any] | None):
        super().__init__(
            firestore=None,  # type: ignore[arg-type]
            config_repo=None,  # type: ignore[arg-type]
            mode="LIVE",
            model_id="core_long_15m_v0",
        )
        self.state_trade = state_trade
        self.load_state_calls = 0
        self.scan_calls = 0

    def _load_open_trade_from_state(self, pair: Any) -> dict[str, Any] | None:  # type: ignore[override]
        _ = pair
        self.load_state_calls += 1
        return self.state_trade

    def _scan_open_trade(self, pair: Any) -> dict[str, Any] | None:  # type: ignore[override]
        _ = pair
        self.scan_calls += 1
        return None

    def _set_open_trade_state(self, trade_id: str, trade_date: str, pair: str | None = None) -> None:  # type: ignore[override]
        _ = trade_id
        _ = trade_date
        _ = pair
        return None


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


class FirestoreRepositoryConfigCacheTest(unittest.TestCase):
    def test_get_current_config_uses_single_firestore_read_after_first_load(self) -> None:
        config_repo = _CountingConfigRepo(
            cast(
                BotConfig,
                {
                    "enabled": True,
                    "pair": "SOL/USDC",
                },
            )
        )
        repo = FirestoreRepository(
            firestore=None,  # type: ignore[arg-type]
            config_repo=config_repo,  # type: ignore[arg-type]
            mode="LIVE",
            model_id="core_long_15m_v0",
        )

        first = repo.get_current_config()
        second = repo.get_current_config()

        self.assertEqual(1, config_repo.calls)
        self.assertIs(first, second)


class FirestoreRepositoryRunSaveNoReadTest(unittest.TestCase):
    def test_save_run_skipped_updates_without_read_before_write(self) -> None:
        repo = _SaveRunRepo()
        run: dict[str, Any] = {
            "run_id": "run_1",
            "result": "SKIPPED",
            "summary": "SKIPPED: lock",
            "executed_at_iso": "2026-02-27T00:00:00Z",
            "bar_close_time_iso": "2026-02-27T00:00:00Z",
        }

        repo.save_run(cast(Any, run))

        day_doc = repo.runs_collection.docs["2026-02-27"]
        item_doc = day_doc.collection("items").docs[_build_skip_run_doc_id(cast(Any, run))]
        self.assertEqual(1, len(item_doc.create_calls))
        self.assertEqual(0, len(item_doc.set_calls))
        payload = item_doc.create_calls[0]
        self.assertEqual(1, payload["occurrence_count"])
        self.assertEqual("run_1", payload["latest_run_id"])
        self.assertEqual("2026-02-27T00:00:00Z", payload["first_executed_at_iso"])
        self.assertEqual("2026-02-27T00:00:00Z", payload["last_executed_at_iso"])


class FirestoreRepositoryOpenTradeStateCacheTest(unittest.TestCase):
    def test_find_open_trade_reads_state_once_and_uses_memory_cache(self) -> None:
        state_trade = {
            "trade_id": "2026-02-27T00:00:00Z_core_long_15m_v0_LONG",
            "trade_date": "2026-02-27",
            "pair": "SOL/USDC",
            "state": "CONFIRMED",
        }
        repo = _OpenTradeStateCachingRepo(state_trade=state_trade)

        first = repo.find_open_trade("SOL/USDC")
        second = repo.find_open_trade("SOL/USDC")

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(1, repo.load_state_calls)
        self.assertEqual(0, repo.scan_calls)


if __name__ == "__main__":
    unittest.main()
