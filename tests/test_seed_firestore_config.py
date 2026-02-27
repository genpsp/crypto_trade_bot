from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "seed-firestore-config.py"
MODULE_SPEC = importlib.util.spec_from_file_location("seed_firestore_config_script", SCRIPT_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError("failed to load seed-firestore-config.py")
seed_firestore_config = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(seed_firestore_config)


class _FakeSnapshot:
    def __init__(self, payload: Any, exists: bool):
        self._payload = payload
        self.exists = exists

    def to_dict(self) -> Any:
        return self._payload


class _FakeDocumentRef:
    def __init__(self, store: dict[str, Any], path: str):
        self._store = store
        self._path = path

    def get(self) -> _FakeSnapshot:
        if self._path in self._store:
            return _FakeSnapshot(self._store[self._path], True)
        return _FakeSnapshot(None, False)

    def set(self, payload: dict[str, Any], merge: bool = False) -> None:
        if merge and self._path in self._store and isinstance(self._store[self._path], dict):
            merged = dict(self._store[self._path])
            merged.update(payload)
            self._store[self._path] = merged
            return
        self._store[self._path] = dict(payload)


class _FakeCollectionRef:
    def __init__(self, store: dict[str, Any], collection_name: str):
        self._store = store
        self._collection_name = collection_name

    def document(self, doc_id: str) -> _FakeDocumentRef:
        return _FakeDocumentRef(self._store, f"{self._collection_name}/{doc_id}")


class _FakeFirestore:
    def __init__(self, store: dict[str, Any]):
        self._store = store

    def document(self, path: str) -> _FakeDocumentRef:
        return _FakeDocumentRef(self._store, path)

    def collection(self, collection_name: str) -> _FakeCollectionRef:
        return _FakeCollectionRef(self._store, collection_name)


class SeedGlobalControlDefaultsTest(unittest.TestCase):
    def _control_doc_path(self) -> str:
        return (
            f"{seed_firestore_config.GLOBAL_CONTROL_COLLECTION_ID}/"
            f"{seed_firestore_config.GLOBAL_CONTROL_DOC_ID}"
        )

    def test_sets_default_when_control_doc_missing(self) -> None:
        store: dict[str, Any] = {}
        firestore = _FakeFirestore(store)

        changed = seed_firestore_config.seed_global_control_defaults(firestore)  # type: ignore[arg-type]

        self.assertTrue(changed)
        self.assertEqual(
            {
                seed_firestore_config.GLOBAL_CONTROL_PAUSE_FIELD: False,
            },
            store[self._control_doc_path()],
        )

    def test_keeps_existing_boolean_value(self) -> None:
        store: dict[str, Any] = {
            self._control_doc_path(): {
                seed_firestore_config.GLOBAL_CONTROL_PAUSE_FIELD: True,
            }
        }
        firestore = _FakeFirestore(store)

        changed = seed_firestore_config.seed_global_control_defaults(firestore)  # type: ignore[arg-type]

        self.assertFalse(changed)
        self.assertTrue(store[self._control_doc_path()][seed_firestore_config.GLOBAL_CONTROL_PAUSE_FIELD])

    def test_resets_invalid_pause_field_to_default_false(self) -> None:
        store: dict[str, Any] = {
            self._control_doc_path(): {
                seed_firestore_config.GLOBAL_CONTROL_PAUSE_FIELD: "true",
            }
        }
        firestore = _FakeFirestore(store)

        changed = seed_firestore_config.seed_global_control_defaults(firestore)  # type: ignore[arg-type]

        self.assertTrue(changed)
        self.assertFalse(store[self._control_doc_path()][seed_firestore_config.GLOBAL_CONTROL_PAUSE_FIELD])

    def test_model_doc_payload_omits_direction_for_15m_strategy(self) -> None:
        config = seed_firestore_config._default_long_15m_config("LIVE")
        payload = seed_firestore_config._build_model_doc_payload("ema_pullback_15m_both_v0", config)
        self.assertNotIn("direction", payload)

    def test_model_doc_payload_keeps_direction_for_non_15m_strategy(self) -> None:
        config = seed_firestore_config._default_long_config("LIVE")
        payload = seed_firestore_config._build_model_doc_payload("ema_pullback_2h_long_v0", config)
        self.assertEqual("LONG", payload["direction"])


if __name__ == "__main__":
    unittest.main()
