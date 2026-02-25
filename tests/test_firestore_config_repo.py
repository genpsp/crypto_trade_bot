from __future__ import annotations

import unittest
from typing import Any

from pybot.infra.config.firestore_config_repo import (
    GLOBAL_CONTROL_MODEL_DOC_ID,
    GLOBAL_CONTROL_PAUSE_FIELD,
    FirestoreConfigRepository,
)


class _FakeSnapshot:
    def __init__(self, doc_id: str, payload: Any, exists: bool):
        self.id = doc_id
        self._payload = payload
        self.exists = exists

    def to_dict(self) -> Any:
        return self._payload


class _FakeDocumentRef:
    def __init__(self, docs: dict[str, Any], doc_id: str):
        self._docs = docs
        self._doc_id = doc_id

    def get(self) -> _FakeSnapshot:
        if self._doc_id in self._docs:
            return _FakeSnapshot(self._doc_id, self._docs[self._doc_id], True)
        return _FakeSnapshot(self._doc_id, None, False)


class _FakeCollectionRef:
    def __init__(self, docs: dict[str, Any]):
        self._docs = docs

    def stream(self) -> list[_FakeSnapshot]:
        return [_FakeSnapshot(doc_id, payload, True) for doc_id, payload in self._docs.items()]

    def document(self, doc_id: str) -> _FakeDocumentRef:
        return _FakeDocumentRef(self._docs, doc_id)


class _FakeFirestore:
    def __init__(self, models_docs: dict[str, Any]):
        self._models_docs = models_docs

    def collection(self, collection_name: str) -> _FakeCollectionRef:
        if collection_name != "models":
            raise KeyError(collection_name)
        return _FakeCollectionRef(self._models_docs)


class FirestoreConfigRepositoryGlobalControlTest(unittest.TestCase):
    def test_list_model_ids_excludes_control_doc(self) -> None:
        repo = FirestoreConfigRepository(
            _FakeFirestore(
                {
                    GLOBAL_CONTROL_MODEL_DOC_ID: {GLOBAL_CONTROL_PAUSE_FIELD: False},
                    "core_long_v0": {},
                    "storm_short_v0": {},
                }
            )  # type: ignore[arg-type]
        )

        self.assertEqual(["core_long_v0", "storm_short_v0"], repo.list_model_ids())

    def test_is_global_pause_enabled_true(self) -> None:
        repo = FirestoreConfigRepository(
            _FakeFirestore(
                {
                    GLOBAL_CONTROL_MODEL_DOC_ID: {GLOBAL_CONTROL_PAUSE_FIELD: True},
                }
            )  # type: ignore[arg-type]
        )

        self.assertTrue(repo.is_global_pause_enabled())

    def test_is_global_pause_enabled_false_when_doc_missing(self) -> None:
        repo = FirestoreConfigRepository(_FakeFirestore({}))  # type: ignore[arg-type]
        self.assertFalse(repo.is_global_pause_enabled())

    def test_is_global_pause_enabled_false_when_payload_invalid(self) -> None:
        repo = FirestoreConfigRepository(
            _FakeFirestore(
                {
                    GLOBAL_CONTROL_MODEL_DOC_ID: "invalid",
                }
            )  # type: ignore[arg-type]
        )
        self.assertFalse(repo.is_global_pause_enabled())

    def test_is_global_pause_enabled_false_when_field_is_not_true(self) -> None:
        repo = FirestoreConfigRepository(
            _FakeFirestore(
                {
                    GLOBAL_CONTROL_MODEL_DOC_ID: {GLOBAL_CONTROL_PAUSE_FIELD: "true"},
                }
            )  # type: ignore[arg-type]
        )
        self.assertFalse(repo.is_global_pause_enabled())


if __name__ == "__main__":
    unittest.main()
