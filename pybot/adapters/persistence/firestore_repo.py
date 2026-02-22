from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from google.cloud.firestore import Client
from google.cloud.firestore_v1.base_query import FieldFilter

from pybot.app.ports.persistence_port import PersistencePort
from pybot.domain.model.types import BotConfig, Pair, RunRecord, TradeRecord
from pybot.infra.config.firestore_config_repo import FirestoreConfigRepository


def sanitize_firestore_value(value: Any) -> Any:
    if isinstance(value, list):
        return [sanitize_firestore_value(item) for item in value]
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested_value in value.items():
            if nested_value is not None:
                sanitized[key] = sanitize_firestore_value(nested_value)
        return sanitized
    return value


SKIP_RUN_RESULTS = {"SKIPPED", "SKIPPED_ENTRY"}


def _extract_run_date(run: RunRecord) -> str:
    value = run.get("bar_close_time_iso") or run.get("executed_at_iso")
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.date().isoformat()
        except ValueError:
            pass
    return datetime.now(tz=UTC).date().isoformat()


def _build_skip_run_doc_id(run: RunRecord) -> str:
    result = str(run.get("result", "SKIPPED")).lower()
    reason_key = str(run.get("reason") or run.get("summary") or "UNKNOWN")
    digest = hashlib.sha1(f"{result}|{reason_key}".encode("utf-8")).hexdigest()[:12]
    return f"{result}_{digest}"


class FirestoreRepository(PersistencePort):
    def __init__(
        self,
        firestore: Client,
        config_repo: FirestoreConfigRepository,
        mode: str,
        model_id: str,
    ):
        self.firestore = firestore
        self.config_repo = config_repo
        self.mode = mode
        self.model_id = model_id
        self.trades_collection_name = "paper_trades" if mode == "PAPER" else "trades"
        self.runs_collection_name = "paper_runs" if mode == "PAPER" else "runs"

    def _model_doc(self):
        return self.firestore.collection("models").document(self.model_id)

    def _touch_model_metadata(self) -> None:
        self._model_doc().set(
            sanitize_firestore_value(
                {
                    "model_id": self.model_id,
                    "mode": self.mode,
                    "updated_at_iso": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
                }
            ),
            merge=True,
        )

    def _trades_collection(self):
        return self._model_doc().collection(self.trades_collection_name)

    def _runs_collection(self):
        return self._model_doc().collection(self.runs_collection_name)

    def get_current_config(self) -> BotConfig:
        return self.config_repo.get_current_config(self.model_id)

    def create_trade(self, trade: TradeRecord) -> None:
        self._touch_model_metadata()
        payload: TradeRecord = dict(trade)
        payload.setdefault("model_id", self.model_id)
        self._trades_collection().document(trade["trade_id"]).set(sanitize_firestore_value(payload))

    def update_trade(self, trade_id: str, updates: dict) -> None:
        self._touch_model_metadata()
        payload = dict(updates)
        payload.setdefault("model_id", self.model_id)
        self._trades_collection().document(trade_id).set(sanitize_firestore_value(payload), merge=True)

    def find_open_trade(self, pair: Pair) -> TradeRecord | None:
        snapshot = (
            self._trades_collection()
            .where(filter=FieldFilter("state", "==", "CONFIRMED"))
            .get()
        )
        if len(snapshot) == 0:
            return None

        candidates = [
            doc.to_dict()
            for doc in snapshot
            if isinstance(doc.to_dict(), dict) and doc.to_dict().get("pair") == pair
        ]
        candidates.sort(key=lambda trade: trade.get("created_at", ""), reverse=True)
        return candidates[0] if candidates else None

    def count_trades_for_utc_day(self, pair: Pair, day_start_iso: str, day_end_iso: str) -> int:
        snapshot = (
            self._trades_collection()
            .where(filter=FieldFilter("created_at", ">=", day_start_iso))
            .where(filter=FieldFilter("created_at", "<=", day_end_iso))
            .get()
        )
        return len([doc for doc in snapshot if doc.to_dict().get("pair") == pair])

    def save_run(self, run: RunRecord) -> None:
        self._touch_model_metadata()
        runs_collection = self._runs_collection()
        run_date = _extract_run_date(run)
        day_ref = runs_collection.document(run_date)
        day_ref.set(
            sanitize_firestore_value(
                {
                    "run_date": run_date,
                    "updated_at_iso": run.get("executed_at_iso"),
                }
            ),
            merge=True,
        )

        payload: RunRecord = dict(run)
        payload.setdefault("model_id", self.model_id)
        payload["run_date"] = run_date

        result = payload.get("result")
        if result in SKIP_RUN_RESULTS:
            skip_doc_id = _build_skip_run_doc_id(payload)
            skip_ref = day_ref.collection("items").document(skip_doc_id)
            existing = skip_ref.get()
            if existing.exists:
                existing_data = existing.to_dict() or {}
                previous_count = int(existing_data.get("occurrence_count", 1))
                payload["occurrence_count"] = previous_count + 1
                payload["first_executed_at_iso"] = existing_data.get(
                    "first_executed_at_iso", payload.get("executed_at_iso")
                )
                payload["last_executed_at_iso"] = payload.get("executed_at_iso")
                payload["latest_run_id"] = payload.get("run_id")
                skip_ref.set(sanitize_firestore_value(payload), merge=True)
                return

            payload["occurrence_count"] = 1
            payload["first_executed_at_iso"] = payload.get("executed_at_iso")
            payload["last_executed_at_iso"] = payload.get("executed_at_iso")
            payload["latest_run_id"] = payload.get("run_id")
            skip_ref.set(sanitize_firestore_value(payload))
            return

        day_ref.collection("items").document(run["run_id"]).set(sanitize_firestore_value(payload))
