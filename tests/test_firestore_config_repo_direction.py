from __future__ import annotations

import unittest
from typing import Any

from pybot.infra.config.firestore_config_repo import FirestoreConfigRepository


def _build_15m_current_config() -> dict[str, Any]:
    return {
        "network": "mainnet-beta",
        "pair": "SOL/USDC",
        "signal_timeframe": "15m",
        "strategy": {
            "name": "ema_trend_pullback_15m_v0",
            "ema_fast_period": 9,
            "ema_slow_period": 34,
            "swing_low_lookback_bars": 12,
            "entry": "ON_BAR_CLOSE",
        },
        "risk": {
            "max_loss_per_trade_pct": 1.2,
            "max_trades_per_day": 4,
            "volatile_atr_pct_threshold": 0.9,
            "storm_atr_pct_threshold": 1.4,
            "volatile_size_multiplier": 0.7,
            "storm_size_multiplier": 0.35,
        },
        "execution": {
            "swap_provider": "JUPITER",
            "slippage_bps": 15,
            "min_notional_usdc": 20.0,
            "only_direct_routes": False,
        },
        "exit": {
            "stop": "SWING_LOW",
            "take_profit_r_multiple": 1.8,
        },
        "meta": {
            "config_version": 2,
            "note": "test",
        },
    }


def _build_2h_current_config() -> dict[str, Any]:
    return {
        "network": "mainnet-beta",
        "pair": "SOL/USDC",
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
            "swap_provider": "JUPITER",
            "slippage_bps": 15,
            "min_notional_usdc": 20.0,
            "only_direct_routes": False,
        },
        "exit": {
            "stop": "SWING_LOW",
            "take_profit_r_multiple": 1.5,
        },
        "meta": {
            "config_version": 2,
            "note": "test",
        },
    }


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


class FirestoreConfigRepositoryDirectionValidationTest(unittest.TestCase):
    def test_get_model_metadata_errors_when_direction_missing_for_15m_strategy(self) -> None:
        firestore = _FakeFirestore(
            {
                "models/ema_pullback_15m_both_v0": {
                    "model_id": "ema_pullback_15m_both_v0",
                    "enabled": True,
                    "mode": "LIVE",
                },
                "models/ema_pullback_15m_both_v0/config/current": _build_15m_current_config(),
            }
        )
        repo = FirestoreConfigRepository(firestore)  # type: ignore[arg-type]

        with self.assertRaises(RuntimeError):
            repo.get_model_metadata("ema_pullback_15m_both_v0")

    def test_get_current_config_errors_when_direction_missing_for_15m_strategy(self) -> None:
        firestore = _FakeFirestore(
            {
                "models/ema_pullback_15m_both_v0": {
                    "model_id": "ema_pullback_15m_both_v0",
                    "enabled": True,
                    "mode": "LIVE",
                },
                "models/ema_pullback_15m_both_v0/config/current": _build_15m_current_config(),
            }
        )
        repo = FirestoreConfigRepository(firestore)  # type: ignore[arg-type]

        with self.assertRaises(RuntimeError):
            repo.get_current_config("ema_pullback_15m_both_v0")

    def test_missing_direction_still_errors_for_non_15m_strategy(self) -> None:
        firestore = _FakeFirestore(
            {
                "models/ema_pullback_2h_long_v0": {
                    "model_id": "ema_pullback_2h_long_v0",
                    "enabled": True,
                    "mode": "LIVE",
                },
                "models/ema_pullback_2h_long_v0/config/current": _build_2h_current_config(),
            }
        )
        repo = FirestoreConfigRepository(firestore)  # type: ignore[arg-type]

        with self.assertRaises(RuntimeError):
            repo.get_model_metadata("ema_pullback_2h_long_v0")


if __name__ == "__main__":
    unittest.main()
