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
        collections: dict[str, str] | None = None,
    ):
        self.firestore = firestore
        self.config_repo = config_repo
        self.collections = collections or {"trades": "trades", "runs": "runs"}

    def get_current_config(self) -> BotConfig:
        return self.config_repo.get_current_config()

    def create_trade(self, trade: TradeRecord) -> None:
        self.firestore.collection(self.collections["trades"]).document(trade["trade_id"]).set(
            sanitize_firestore_value(trade)
        )

    def update_trade(self, trade_id: str, updates: dict) -> None:
        self.firestore.collection(self.collections["trades"]).document(trade_id).set(
            sanitize_firestore_value(updates), merge=True
        )

    def find_open_trade(self, pair: Pair) -> TradeRecord | None:
        snapshot = (
            self.firestore.collection(self.collections["trades"])
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
            self.firestore.collection(self.collections["trades"])
            .where(filter=FieldFilter("created_at", ">=", day_start_iso))
            .where(filter=FieldFilter("created_at", "<=", day_end_iso))
            .get()
        )
        return len([doc for doc in snapshot if doc.to_dict().get("pair") == pair])

    def save_run(self, run: RunRecord) -> None:
        runs_collection = self.firestore.collection(self.collections["runs"])
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
