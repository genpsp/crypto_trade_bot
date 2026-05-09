from __future__ import annotations

import unittest
from typing import Any, cast

from apps.gmo_bot.adapters.persistence.firestore_repo import FirestoreRepository


class _SetOnlyDocument:
    def __init__(self, doc_id: str):
        self.doc_id = doc_id
        self.set_calls: list[tuple[dict[str, Any], bool]] = []
        self.children: dict[str, "_SetOnlyCollection"] = {}

    def set(self, payload: dict[str, Any], merge: bool = False) -> None:
        self.set_calls.append((payload, merge))

    def create(self, payload: dict[str, Any]) -> None:
        self.set_calls.append((payload, False))

    def collection(self, name: str) -> "_SetOnlyCollection":
        collection = self.children.get(name)
        if collection is None:
            collection = _SetOnlyCollection()
            self.children[name] = collection
        return collection


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
    def __init__(self) -> None:
        super().__init__(
            firestore=None,  # type: ignore[arg-type]
            config_repo=None,  # type: ignore[arg-type]
            mode="LIVE",
            model_id="gmo_ema_pullback_15m_both_v0",
        )
        self.runs_collection = _SetOnlyCollection()
        self.touch_calls = 0

    def _touch_model_metadata(self) -> None:  # type: ignore[override]
        self.touch_calls += 1

    def _runs_collection(self):  # type: ignore[override]
        return self.runs_collection


class GmoFirestoreRepositoryRunSaveTest(unittest.TestCase):
    def test_save_run_does_not_touch_model_metadata(self) -> None:
        repo = _SaveRunRepo()
        run: dict[str, Any] = {
            "run_id": "run_1",
            "result": "FAILED",
            "summary": "FAILED: test",
            "executed_at_iso": "2026-03-17T03:50:10Z",
            "bar_close_time_iso": "2026-03-17T03:45:00Z",
        }

        repo.save_run(cast(Any, run))

        self.assertEqual(0, repo.touch_calls)
        self.assertIn("2026-03-17", repo.runs_collection.docs)



class _UpdateTradeCacheRepo(FirestoreRepository):
    def __init__(self) -> None:
        super().__init__(
            firestore=None,  # type: ignore[arg-type]
            config_repo=None,  # type: ignore[arg-type]
            mode="LIVE",
            model_id="gmo_ema_pullback_15m_both_v0",
        )
        self.trade_items = _SetOnlyCollection()

    def _touch_model_metadata(self) -> None:  # type: ignore[override]
        return None

    def _touch_trade_day(self, trade_date: str, updated_at_iso: str | None = None) -> None:  # type: ignore[override]
        _ = trade_date
        _ = updated_at_iso
        return None

    def _trade_items_collection_for_date(self, trade_date: str):  # type: ignore[override]
        _ = trade_date
        return self.trade_items

    def _set_open_trade_state(self, trade_id: str, trade_date: str, pair: str | None = None) -> None:  # type: ignore[override]
        _ = trade_id
        _ = trade_date
        _ = pair
        return None


class GmoFirestoreRepositoryOpenTradeCacheTest(unittest.TestCase):
    def test_execution_only_update_refreshes_open_trade_cache(self) -> None:
        repo = _UpdateTradeCacheRepo()
        trade: dict[str, Any] = {
            "trade_id": "2026-03-17T03:45:00Z_gmo_ema_pullback_15m_both_v0_LONG",
            "trade_date": "2026-03-17",
            "pair": "SOL/JPY",
            "state": "CONFIRMED",
            "execution": {"take_profit_order_status": "CLIENT_MANAGED"},
            "position": {"status": "OPEN"},
        }
        repo._cache_trade_day(trade["trade_id"], trade["trade_date"])
        repo._cache_trade_snapshot(trade["trade_id"], trade, merge=False)
        repo._set_open_trade_cache(trade)  # type: ignore[arg-type]

        repo.update_trade(
            trade["trade_id"],
            {
                "execution": {
                    "stop_loss_order_status": "WAITING",
                    "stop_loss_orders": [{"order_id": 123, "status": "WAITING"}],
                }
            },
        )

        refreshed = repo.find_open_trade("SOL/JPY")

        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertEqual("WAITING", refreshed["execution"]["stop_loss_order_status"])
        self.assertEqual([{"order_id": 123, "status": "WAITING"}], refreshed["execution"]["stop_loss_orders"])

class _DailyBalanceDoc:
    def __init__(self, doc_id: str, payload: Any | None = None):
        self.id = doc_id
        self._payload = payload or {}
        self.set_calls: list[tuple[dict[str, Any], bool]] = []

    def set(self, payload: dict[str, Any], merge: bool = False) -> None:
        self._payload.update(payload)
        self.set_calls.append((payload, merge))

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class _DailyBalanceCollection:
    def __init__(self):
        self.docs: dict[str, _DailyBalanceDoc] = {}

    def document(self, doc_id: str) -> _DailyBalanceDoc:
        doc = self.docs.get(doc_id)
        if doc is None:
            doc = _DailyBalanceDoc(doc_id)
            self.docs[doc_id] = doc
        return doc

    def stream(self) -> list[_DailyBalanceDoc]:
        return list(self.docs.values())


class _DailyBalanceRepo(FirestoreRepository):
    def __init__(self) -> None:
        super().__init__(
            firestore=None,  # type: ignore[arg-type]
            config_repo=None,  # type: ignore[arg-type]
            mode="LIVE",
            model_id="gmo_ema_pullback_15m_both_v0",
        )
        self.daily_balance_collection = _DailyBalanceCollection()
        self.touch_calls = 0

    def _touch_model_metadata(self) -> None:  # type: ignore[override]
        self.touch_calls += 1

    def _daily_balance_collection(self):  # type: ignore[override]
        return self.daily_balance_collection


class GmoFirestoreRepositoryDailyBalanceTest(unittest.TestCase):
    def test_save_daily_balance_stores_available_margin_snapshot(self) -> None:
        repo = _DailyBalanceRepo()

        repo.save_daily_balance(
            {
                "snapshot_date_jst": "2026-05-09",
                "snapshot_at_iso": "2026-05-09T00:05:00+09:00",
                "balance_jpy": 123456.0,
                "source": "GMO_AVAILABLE_MARGIN",
            }
        )

        doc = repo.daily_balance_collection.docs["2026-05-09"]
        payload, merge = doc.set_calls[0]
        self.assertTrue(merge)
        self.assertEqual("gmo_ema_pullback_15m_both_v0", payload["model_id"])
        self.assertEqual(123456.0, payload["balance_jpy"])


if __name__ == "__main__":
    unittest.main()
