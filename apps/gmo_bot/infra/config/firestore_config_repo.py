from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.cloud.firestore import Client

from apps.gmo_bot.domain.model.types import BotConfig
from apps.gmo_bot.infra.config.schema import parse_config

MODELS_COLLECTION_ID = "gmo_models"
GLOBAL_CONTROL_COLLECTION_ID = "gmo_control"
GLOBAL_CONTROL_DOC_ID = "global"
GLOBAL_CONTROL_PAUSE_FIELD = "pause_all"


@dataclass(frozen=True)
class ModelMetadata:
    enabled: bool
    direction: str
    mode: str


class FirestoreConfigRepository:
    def __init__(self, firestore: Client):
        self.firestore = firestore

    def list_model_ids(self) -> list[str]:
        model_docs = list(self.firestore.collection(MODELS_COLLECTION_ID).stream())
        model_ids = [doc.id for doc in model_docs]
        model_ids.sort()
        return model_ids

    def is_global_pause_enabled(self) -> bool:
        control_snapshot = self.firestore.collection(GLOBAL_CONTROL_COLLECTION_ID).document(
            GLOBAL_CONTROL_DOC_ID
        ).get()
        if not control_snapshot.exists:
            return False
        payload = control_snapshot.to_dict()
        if not isinstance(payload, dict):
            return False
        return payload.get(GLOBAL_CONTROL_PAUSE_FIELD) is True

    def _load_model_payload(self, model_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        model_ref = self.firestore.collection(MODELS_COLLECTION_ID).document(model_id)
        model_snapshot = model_ref.get()
        if not model_snapshot.exists:
            raise RuntimeError(f"{MODELS_COLLECTION_ID}/{model_id} document is missing")
        model_data = model_snapshot.to_dict()
        if not isinstance(model_data, dict):
            raise RuntimeError(f"{MODELS_COLLECTION_ID}/{model_id} payload is invalid")

        config_snapshot = model_ref.collection("config").document("current").get()
        if not config_snapshot.exists:
            raise RuntimeError(f"{MODELS_COLLECTION_ID}/{model_id}/config/current document is missing")
        config_payload = config_snapshot.to_dict()
        if not isinstance(config_payload, dict):
            raise RuntimeError(f"{MODELS_COLLECTION_ID}/{model_id}/config/current payload is invalid")
        return model_data, config_payload

    def _parse_model_metadata(self, model_id: str, model_data: dict[str, Any]) -> ModelMetadata:
        enabled = model_data.get("enabled")
        if not isinstance(enabled, bool):
            raise RuntimeError(f"{MODELS_COLLECTION_ID}/{model_id}.enabled must be boolean")
        direction = model_data.get("direction")
        if direction not in ("LONG", "SHORT", "BOTH"):
            raise RuntimeError(f"{MODELS_COLLECTION_ID}/{model_id}.direction must be LONG, SHORT or BOTH")
        mode = model_data.get("mode")
        if mode not in ("PAPER", "LIVE"):
            raise RuntimeError(f"{MODELS_COLLECTION_ID}/{model_id}.mode must be PAPER or LIVE")
        return ModelMetadata(enabled=enabled, direction=direction, mode=mode)

    def get_model_metadata(self, model_id: str) -> ModelMetadata:
        model_data, _ = self._load_model_payload(model_id)
        return self._parse_model_metadata(model_id, model_data)

    def get_current_config(self, model_id: str) -> BotConfig:
        model_data, config_payload = self._load_model_payload(model_id)
        metadata = self._parse_model_metadata(model_id, model_data)
        normalized: dict[str, Any] = dict(config_payload)
        normalized.pop("enabled", None)
        normalized.pop("direction", None)
        execution_payload = normalized.get("execution")
        if not isinstance(execution_payload, dict):
            raise RuntimeError(f"{MODELS_COLLECTION_ID}/{model_id}/config/current.execution must be object")
        execution = dict(execution_payload)
        execution.pop("mode", None)
        execution["mode"] = metadata.mode
        normalized["enabled"] = metadata.enabled
        normalized["direction"] = metadata.direction
        normalized["execution"] = execution
        return parse_config(normalized)
