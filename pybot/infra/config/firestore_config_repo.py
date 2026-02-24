from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.cloud.firestore import Client

from pybot.domain.model.types import BotConfig
from pybot.infra.config.schema import parse_config


@dataclass(frozen=True)
class ModelMetadata:
    enabled: bool
    direction: str
    mode: str
    wallet_key_path: str | None


class FirestoreConfigRepository:
    def __init__(self, firestore: Client):
        self.firestore = firestore

    def list_model_ids(self) -> list[str]:
        model_docs = list(self.firestore.collection("models").stream())
        model_ids = [doc.id for doc in model_docs]
        model_ids.sort()
        return model_ids

    def _load_model_payload(self, model_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        model_ref = self.firestore.collection("models").document(model_id)
        model_snapshot = model_ref.get()
        if not model_snapshot.exists:
            raise RuntimeError(f"models/{model_id} document is missing")
        model_data = model_snapshot.to_dict()
        if not isinstance(model_data, dict):
            raise RuntimeError(f"models/{model_id} payload is invalid")

        config_snapshot = model_ref.collection("config").document("current").get()
        if not config_snapshot.exists:
            raise RuntimeError(f"models/{model_id}/config/current document is missing")
        config_payload = config_snapshot.to_dict()
        if not isinstance(config_payload, dict):
            raise RuntimeError(f"models/{model_id}/config/current payload is invalid")

        return model_data, config_payload

    def _parse_model_metadata(self, model_id: str, model_data: dict[str, Any]) -> ModelMetadata:
        enabled = model_data.get("enabled")
        if not isinstance(enabled, bool):
            raise RuntimeError(f"models/{model_id}.enabled must be boolean")
        direction = model_data.get("direction")
        if direction not in ("LONG_ONLY", "SHORT_ONLY"):
            raise RuntimeError(f"models/{model_id}.direction must be LONG_ONLY or SHORT_ONLY")
        mode = model_data.get("mode")
        if mode not in ("PAPER", "LIVE"):
            raise RuntimeError(f"models/{model_id}.mode must be PAPER or LIVE")

        wallet_key_path: str | None = None
        raw_wallet_key_path = model_data.get("wallet_key_path")
        if isinstance(raw_wallet_key_path, str):
            stripped = raw_wallet_key_path.strip()
            if stripped:
                wallet_key_path = stripped

        return ModelMetadata(
            enabled=enabled,
            direction=direction,
            mode=mode,
            wallet_key_path=wallet_key_path,
        )

    def get_model_metadata(self, model_id: str) -> ModelMetadata:
        model_ref = self.firestore.collection("models").document(model_id)
        model_snapshot = model_ref.get()
        if not model_snapshot.exists:
            raise RuntimeError(f"models/{model_id} document is missing")
        model_data = model_snapshot.to_dict()
        if not isinstance(model_data, dict):
            raise RuntimeError(f"models/{model_id} payload is invalid")
        return self._parse_model_metadata(model_id, model_data)

    def get_current_config(self, model_id: str) -> BotConfig:
        model_data, config_payload = self._load_model_payload(model_id)
        model_metadata = self._parse_model_metadata(model_id, model_data)

        normalized: dict[str, Any] = dict(config_payload)
        normalized.pop("enabled", None)
        normalized.pop("direction", None)
        normalized.pop("models", None)

        execution_payload = normalized.get("execution")
        if not isinstance(execution_payload, dict):
            raise RuntimeError(f"models/{model_id}/config/current.execution must be object")
        execution: dict[str, Any] = dict(execution_payload)
        execution.pop("mode", None)
        execution["mode"] = model_metadata.mode

        normalized["enabled"] = model_metadata.enabled
        normalized["direction"] = model_metadata.direction
        normalized["execution"] = execution

        return parse_config(normalized)
