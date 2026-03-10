from __future__ import annotations

import unittest
from typing import Any

from apps.gmo_bot.infra.config.firestore_config_repo import (
    GLOBAL_CONTROL_COLLECTION_ID,
    GLOBAL_CONTROL_DOC_ID,
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
    def __init__(self, docs: dict[str, Any], path: str):
        self._docs = docs
        self._path = path

    def get(self) -> _FakeSnapshot:
        if self._path in self._docs:
            return _FakeSnapshot(self._path.rsplit("/", 1)[-1], self._docs[self._path], True)
        return _FakeSnapshot(self._path.rsplit("/", 1)[-1], None, False)

    def collection(self, collection_name: str) -> "_FakeCollectionRef":
        return _FakeCollectionRef(self._docs, f"{self._path}/{collection_name}")


class _FakeCollectionRef:
    def __init__(self, docs: dict[str, Any], base_path: str):
        self._docs = docs
        self._base_path = base_path

    def stream(self) -> list[_FakeSnapshot]:
        prefix = f"{self._base_path}/"
        snapshots: list[_FakeSnapshot] = []
        for path, payload in self._docs.items():
            if not path.startswith(prefix):
                continue
            suffix = path[len(prefix) :]
            if "/" in suffix:
                continue
            snapshots.append(_FakeSnapshot(suffix, payload, True))
        return snapshots

    def document(self, doc_id: str) -> _FakeDocumentRef:
        return _FakeDocumentRef(self._docs, f"{self._base_path}/{doc_id}")


class _FakeFirestore:
    def __init__(self, docs: dict[str, Any]):
        self._docs = docs

    def collection(self, collection_name: str) -> _FakeCollectionRef:
        return _FakeCollectionRef(self._docs, collection_name)


class GmoFirestoreConfigRepositoryTest(unittest.TestCase):
    def test_list_model_ids_filters_to_gmo_broker(self) -> None:
        repo = FirestoreConfigRepository(
            _FakeFirestore(
                {
                    "models/ema_pullback_2h_long_v0": {"enabled": True, "mode": "LIVE", "direction": "LONG"},
                    "models/ema_pullback_15m_both_v0": {
                        "enabled": True,
                        "mode": "LIVE",
                        "direction": "BOTH",
                        "broker": "GMO_COIN",
                    },
                    "models/storm_2h_short_v0": {
                        "enabled": True,
                        "mode": "LIVE",
                        "direction": "SHORT",
                        "broker": "GMO_COIN",
                    },
                }
            )  # type: ignore[arg-type]
        )

        self.assertEqual(["ema_pullback_15m_both_v0", "storm_2h_short_v0"], repo.list_model_ids())

    def test_is_global_pause_enabled_uses_shared_control_collection(self) -> None:
        repo = FirestoreConfigRepository(
            _FakeFirestore(
                {
                    f"{GLOBAL_CONTROL_COLLECTION_ID}/{GLOBAL_CONTROL_DOC_ID}": {
                        GLOBAL_CONTROL_PAUSE_FIELD: True,
                    }
                }
            )  # type: ignore[arg-type]
        )

        self.assertTrue(repo.is_global_pause_enabled())

    def test_get_model_metadata_rejects_non_gmo_broker(self) -> None:
        repo = FirestoreConfigRepository(
            _FakeFirestore(
                {
                    "models/ema_pullback_2h_long_v0": {
                        "enabled": True,
                        "mode": "LIVE",
                        "direction": "LONG",
                    },
                    "models/ema_pullback_2h_long_v0/config/current": {
                        "broker": "GMO_COIN",
                        "pair": "SOL/JPY",
                        "signal_timeframe": "2h",
                        "strategy": {
                            "name": "ema_trend_pullback_v0",
                            "ema_fast_period": 5,
                            "ema_slow_period": 13,
                            "swing_low_lookback_bars": 6,
                            "entry": "ON_BAR_CLOSE",
                        },
                        "risk": {
                            "max_loss_per_trade_pct": 3.0,
                            "max_trades_per_day": 2,
                            "volatile_atr_pct_threshold": 1.3,
                            "storm_atr_pct_threshold": 1.4,
                            "volatile_size_multiplier": 0.75,
                            "storm_size_multiplier": 0.5,
                        },
                        "execution": {
                            "broker": "GMO_COIN",
                            "slippage_bps": 3,
                            "min_notional_jpy": 5000.0,
                            "leverage_multiplier": 1.0,
                            "margin_usage_ratio": 0.99,
                        },
                        "exit": {
                            "stop": "SWING_LOW",
                            "take_profit_r_multiple": 1.5,
                        },
                        "meta": {
                            "config_version": 1,
                            "note": "test",
                        },
                    },
                }
            )  # type: ignore[arg-type]
        )

        with self.assertRaises(RuntimeError):
            repo.get_model_metadata("ema_pullback_2h_long_v0")


if __name__ == "__main__":
    unittest.main()
