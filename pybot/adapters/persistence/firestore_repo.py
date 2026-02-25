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
TRADE_SKIP_STATES = {"FAILED", "CANCELED"}


def _extract_run_date(run: RunRecord) -> str:
    value = run.get("bar_close_time_iso") or run.get("executed_at_iso")
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.date().isoformat()
        except ValueError:
            pass
    return datetime.now(tz=UTC).date().isoformat()


def _parse_iso_date(value: str) -> str | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.date().isoformat()
    except ValueError:
        return None


def _extract_trade_date_from_trade_id(trade_id: str) -> str | None:
    timestamp_head = trade_id.split("_", 1)[0]
    return _parse_iso_date(timestamp_head)


def _extract_trade_date_from_payload(payload: dict[str, Any]) -> str:
    for key in ("trade_date", "created_at", "bar_close_time_iso"):
        raw_value = payload.get(key)
        if isinstance(raw_value, str):
            parsed_date = _parse_iso_date(raw_value)
            if parsed_date is not None:
                return parsed_date
    trade_id = payload.get("trade_id")
    if isinstance(trade_id, str):
        parsed_from_trade_id = _extract_trade_date_from_trade_id(trade_id)
        if parsed_from_trade_id is not None:
            return parsed_from_trade_id
    return datetime.now(tz=UTC).date().isoformat()


def _extract_day_date(day_start_iso: str, day_end_iso: str) -> str:
    parsed_start = _parse_iso_date(day_start_iso)
    if parsed_start is not None:
        return parsed_start
    parsed_end = _parse_iso_date(day_end_iso)
    if parsed_end is not None:
        return parsed_end
    return datetime.now(tz=UTC).date().isoformat()


def _is_day_doc_id(doc_id: str) -> bool:
    if len(doc_id) != 10:
        return False
    try:
        datetime.fromisoformat(doc_id)
    except ValueError:
        return False
    return True


def _sort_trade_key(trade: dict[str, Any]) -> str:
    position = trade.get("position")
    if isinstance(position, dict):
        exit_time_iso = position.get("exit_time_iso")
        if isinstance(exit_time_iso, str):
            return exit_time_iso
    updated_at = trade.get("updated_at")
    if isinstance(updated_at, str):
        return updated_at
    created_at = trade.get("created_at")
    if isinstance(created_at, str):
        return created_at
    return ""


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
        self._trade_storage_cache: dict[str, str] = {}

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

    def _trade_day_doc(self, trade_date: str):
        return self._trades_collection().document(trade_date)

    def _trade_items_collection_for_date(self, trade_date: str):
        return self._trade_day_doc(trade_date).collection("items")

    def _touch_trade_day(self, trade_date: str, updated_at_iso: str | None = None) -> None:
        self._trade_day_doc(trade_date).set(
            sanitize_firestore_value(
                {
                    "trade_date": trade_date,
                    "updated_at_iso": updated_at_iso or datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
                }
            ),
            merge=True,
        )

    def _cache_trade_day(self, trade_id: str, trade_date: str) -> None:
        self._trade_storage_cache[trade_id] = trade_date

    def _resolve_trade_update_date(self, trade_id: str, payload: dict[str, Any]) -> str:
        cached = self._trade_storage_cache.get(trade_id)
        if isinstance(cached, str) and cached:
            return cached

        payload_trade_date = payload.get("trade_date")
        if isinstance(payload_trade_date, str):
            normalized_payload_trade_date = payload_trade_date.strip()
            if _is_day_doc_id(normalized_payload_trade_date):
                return normalized_payload_trade_date

        return _extract_trade_date_from_trade_id(trade_id) or datetime.now(tz=UTC).date().isoformat()

    def _runs_collection(self):
        return self._model_doc().collection(self.runs_collection_name)

    def get_current_config(self) -> BotConfig:
        return self.config_repo.get_current_config(self.model_id)

    def create_trade(self, trade: TradeRecord) -> None:
        self._touch_model_metadata()
        payload: TradeRecord = dict(trade)
        payload.setdefault("model_id", self.model_id)
        trade_date = _extract_trade_date_from_payload(payload)
        payload["trade_date"] = trade_date
        updated_at_iso = payload.get("updated_at")
        updated_at_iso_value = updated_at_iso if isinstance(updated_at_iso, str) else None
        self._touch_trade_day(trade_date, updated_at_iso_value)
        self._trade_items_collection_for_date(trade_date).document(trade["trade_id"]).set(sanitize_firestore_value(payload))
        self._cache_trade_day(trade["trade_id"], trade_date)

    def update_trade(self, trade_id: str, updates: dict) -> None:
        self._touch_model_metadata()
        payload = dict(updates)
        payload.setdefault("model_id", self.model_id)
        trade_date = self._resolve_trade_update_date(trade_id, payload)
        payload.setdefault("trade_date", trade_date)
        updated_at_iso = payload.get("updated_at")
        updated_at_iso_value = updated_at_iso if isinstance(updated_at_iso, str) else None
        self._touch_trade_day(trade_date, updated_at_iso_value)
        self._trade_items_collection_for_date(trade_date).document(trade_id).set(
            sanitize_firestore_value(payload),
            merge=True,
        )
        self._cache_trade_day(trade_id, trade_date)

    def find_open_trade(self, pair: Pair) -> TradeRecord | None:
        candidates_by_trade_id: dict[str, TradeRecord] = {}

        day_snapshots = self._trades_collection().stream()
        trade_day_ids = sorted((doc.id for doc in day_snapshots if _is_day_doc_id(doc.id)), reverse=True)
        for trade_date in trade_day_ids:
            snapshot = (
                self._trade_items_collection_for_date(trade_date)
                .where(filter=FieldFilter("state", "==", "CONFIRMED"))
                .where(filter=FieldFilter("pair", "==", pair))
                .get()
            )
            for doc in snapshot:
                trade = doc.to_dict()
                if not isinstance(trade, dict):
                    continue
                if trade.get("pair") != pair:
                    continue
                if trade.get("state") != "CONFIRMED":
                    continue
                trade_id = trade.get("trade_id")
                if not isinstance(trade_id, str):
                    continue
                trade.setdefault("trade_date", trade_date)
                candidates_by_trade_id[trade_id] = trade

        if not candidates_by_trade_id:
            return None

        candidates = [trade for trade in candidates_by_trade_id.values() if isinstance(trade, dict)]
        candidates.sort(key=lambda trade: trade.get("created_at", ""), reverse=True)
        selected_trade = candidates[0]
        selected_trade_id = selected_trade.get("trade_id")
        if isinstance(selected_trade_id, str):
            selected_trade_date = selected_trade.get("trade_date")
            if isinstance(selected_trade_date, str):
                self._cache_trade_day(selected_trade_id, selected_trade_date)
        return selected_trade

    def count_trades_for_utc_day(self, pair: Pair, day_start_iso: str, day_end_iso: str) -> int:
        trade_date = _extract_day_date(day_start_iso, day_end_iso)
        trades_by_id: dict[str, TradeRecord] = {}

        day_snapshot = (
            self._trade_items_collection_for_date(trade_date)
            .where(filter=FieldFilter("pair", "==", pair))
            .get()
        )
        for doc in day_snapshot:
            trade = doc.to_dict()
            if not isinstance(trade, dict):
                continue
            if trade.get("pair") != pair:
                continue
            trade_id = trade.get("trade_id")
            if not isinstance(trade_id, str):
                continue
            trades_by_id[trade_id] = trade

        count = 0
        for trade in trades_by_id.values():
            if trade.get("state") in TRADE_SKIP_STATES:
                continue
            count += 1
        return count

    def list_recent_closed_trades(self, pair: Pair, limit: int) -> list[TradeRecord]:
        if limit <= 0:
            return []

        trades_by_id: dict[str, TradeRecord] = {}
        day_snapshots = self._trades_collection().stream()
        trade_day_ids = sorted((doc.id for doc in day_snapshots if _is_day_doc_id(doc.id)), reverse=True)

        for trade_date in trade_day_ids:
            snapshot = (
                self._trade_items_collection_for_date(trade_date)
                .where(filter=FieldFilter("state", "==", "CLOSED"))
                .where(filter=FieldFilter("pair", "==", pair))
                .get()
            )
            for doc in snapshot:
                trade = doc.to_dict()
                if not isinstance(trade, dict):
                    continue
                if trade.get("pair") != pair:
                    continue
                if trade.get("state") != "CLOSED":
                    continue
                trade_id = trade.get("trade_id")
                if not isinstance(trade_id, str):
                    continue
                trade.setdefault("trade_date", trade_date)
                trades_by_id[trade_id] = trade

            if len(trades_by_id) >= limit * 3:
                # Heuristic short-circuit to reduce read cost.
                break

        trades = [trade for trade in trades_by_id.values() if isinstance(trade, dict)]
        trades.sort(key=_sort_trade_key, reverse=True)
        return trades[:limit]

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
