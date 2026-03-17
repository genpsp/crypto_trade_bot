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


if __name__ == "__main__":
    unittest.main()
