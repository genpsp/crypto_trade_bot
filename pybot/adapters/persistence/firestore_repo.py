from __future__ import annotations

from copy import deepcopy
import hashlib
from datetime import UTC, datetime
from typing import Any

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore import Client
from google.cloud.firestore_v1 import Increment
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
OPEN_TRADE_STATE = "CONFIRMED"
TERMINAL_TRADE_STATES = {"CLOSED", "FAILED", "CANCELED"}
STATE_COLLECTION_NAME = "state"
OPEN_TRADE_STATE_DOC_ID = "open_trade"
RECENT_CLOSED_STATE_DOC_ID = "recent_closed_trades"
RECENT_CLOSED_TRADES_MAX_ITEMS = 128
TRADE_SNAPSHOT_CACHE_MAX_ITEMS = 256


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


def _deep_merge_dict(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge_dict(dst[key], value)
            continue
        dst[key] = deepcopy(value)


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
        self._current_config_cache: BotConfig | None = None
        self._trade_snapshot_cache: dict[str, TradeRecord] = {}
        self._open_trade_cache: TradeRecord | None = None
        self._open_trade_cache_initialized = False
        self._recent_closed_cache: list[TradeRecord] = []
        self._recent_closed_cache_initialized = False
        self._recent_closed_backfill_attempted = False

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

    def _state_collection(self):
        return self._model_doc().collection(STATE_COLLECTION_NAME)

    def _open_trade_state_doc(self):
        return self._state_collection().document(OPEN_TRADE_STATE_DOC_ID)

    def _recent_closed_state_doc(self):
        return self._state_collection().document(RECENT_CLOSED_STATE_DOC_ID)

    def _cache_trade_snapshot(self, trade_id: str, payload: dict[str, Any], *, merge: bool) -> None:
        if merge and trade_id in self._trade_snapshot_cache:
            merged = deepcopy(self._trade_snapshot_cache[trade_id])
            _deep_merge_dict(merged, payload)
            self._trade_snapshot_cache[trade_id] = merged
        else:
            self._trade_snapshot_cache[trade_id] = deepcopy(payload)

        while len(self._trade_snapshot_cache) > TRADE_SNAPSHOT_CACHE_MAX_ITEMS:
            oldest_trade_id = next(iter(self._trade_snapshot_cache))
            self._trade_snapshot_cache.pop(oldest_trade_id, None)

    def _cached_trade_snapshot(self, trade_id: str) -> TradeRecord | None:
        trade = self._trade_snapshot_cache.get(trade_id)
        if not isinstance(trade, dict):
            return None
        return deepcopy(trade)

    def _load_trade_snapshot(self, trade_id: str, trade_date: str | None = None) -> TradeRecord | None:
        cached = self._cached_trade_snapshot(trade_id)
        if isinstance(cached, dict):
            return cached

        resolved_trade_date = trade_date
        if not isinstance(resolved_trade_date, str) or not _is_day_doc_id(resolved_trade_date):
            resolved_trade_date = _extract_trade_date_from_trade_id(trade_id)
        if not isinstance(resolved_trade_date, str) or not _is_day_doc_id(resolved_trade_date):
            return None

        snapshot = self._trade_items_collection_for_date(resolved_trade_date).document(trade_id).get()
        if not snapshot.exists:
            return None
        payload = snapshot.to_dict()
        if not isinstance(payload, dict):
            return None
        payload.setdefault("trade_date", resolved_trade_date)
        self._cache_trade_day(trade_id, resolved_trade_date)
        self._cache_trade_snapshot(trade_id, payload, merge=False)
        return deepcopy(payload)

    def _set_open_trade_state(self, trade_id: str, trade_date: str, pair: str | None = None) -> None:
        payload: dict[str, Any] = {
            "trade_id": trade_id,
            "trade_date": trade_date,
            "state": OPEN_TRADE_STATE,
            "updated_at_iso": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        }
        if isinstance(pair, str):
            payload["pair"] = pair
        self._open_trade_state_doc().set(sanitize_firestore_value(payload))

    def _clear_open_trade_state(self) -> None:
        try:
            self._open_trade_state_doc().delete()
        except Exception:
            pass

    def _set_open_trade_cache(self, trade: TradeRecord | None) -> None:
        self._open_trade_cache = deepcopy(trade) if isinstance(trade, dict) else None
        self._open_trade_cache_initialized = True

    def _load_open_trade_from_state(self, pair: Pair) -> TradeRecord | None:
        state_snapshot = self._open_trade_state_doc().get()
        if not state_snapshot.exists:
            return None

        state_payload = state_snapshot.to_dict()
        if not isinstance(state_payload, dict):
            self._clear_open_trade_state()
            return None

        state_pair = state_payload.get("pair")
        if isinstance(state_pair, str) and state_pair != pair:
            return None

        trade_id = state_payload.get("trade_id")
        if not isinstance(trade_id, str) or not trade_id:
            self._clear_open_trade_state()
            return None

        raw_trade_date = state_payload.get("trade_date")
        trade_date = raw_trade_date if isinstance(raw_trade_date, str) and _is_day_doc_id(raw_trade_date) else None
        trade = self._load_trade_snapshot(trade_id, trade_date)
        if not isinstance(trade, dict):
            self._clear_open_trade_state()
            return None
        if trade.get("state") != OPEN_TRADE_STATE or trade.get("pair") != pair:
            self._clear_open_trade_state()
            return None

        resolved_trade_date = trade.get("trade_date")
        if isinstance(resolved_trade_date, str) and _is_day_doc_id(resolved_trade_date):
            self._cache_trade_day(trade_id, resolved_trade_date)
        return trade

    def _scan_open_trade(self, pair: Pair) -> TradeRecord | None:
        candidates_by_trade_id: dict[str, TradeRecord] = {}

        day_snapshots = self._trades_collection().stream()
        trade_day_ids = sorted((doc.id for doc in day_snapshots if _is_day_doc_id(doc.id)), reverse=True)
        for trade_date in trade_day_ids:
            snapshot = (
                self._trade_items_collection_for_date(trade_date)
                .where(filter=FieldFilter("state", "==", OPEN_TRADE_STATE))
                .where(filter=FieldFilter("pair", "==", pair))
                .get()
            )
            for doc in snapshot:
                trade = doc.to_dict()
                if not isinstance(trade, dict):
                    continue
                if trade.get("pair") != pair:
                    continue
                if trade.get("state") != OPEN_TRADE_STATE:
                    continue
                trade_id = trade.get("trade_id")
                if not isinstance(trade_id, str):
                    continue
                trade.setdefault("trade_date", trade_date)
                candidates_by_trade_id[trade_id] = trade
                self._cache_trade_day(trade_id, trade_date)
                self._cache_trade_snapshot(trade_id, trade, merge=False)

        if not candidates_by_trade_id:
            return None

        candidates = [trade for trade in candidates_by_trade_id.values() if isinstance(trade, dict)]
        candidates.sort(key=lambda trade: trade.get("created_at", ""), reverse=True)
        return deepcopy(candidates[0])

    def _load_recent_closed_from_state(self) -> list[TradeRecord]:
        if self._recent_closed_cache_initialized:
            return deepcopy(self._recent_closed_cache)

        self._recent_closed_cache_initialized = True
        state_snapshot = self._recent_closed_state_doc().get()
        if not state_snapshot.exists:
            self._recent_closed_cache = []
            return []

        payload = state_snapshot.to_dict()
        if not isinstance(payload, dict):
            self._recent_closed_cache = []
            return []

        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            self._recent_closed_cache = []
            return []

        items: list[TradeRecord] = []
        for item in raw_items:
            if isinstance(item, dict):
                items.append(deepcopy(item))
        self._recent_closed_cache = items
        return deepcopy(items)

    def _save_recent_closed_state(self, trades: list[TradeRecord]) -> None:
        trimmed = trades[:RECENT_CLOSED_TRADES_MAX_ITEMS]
        self._recent_closed_cache = deepcopy(trimmed)
        self._recent_closed_cache_initialized = True
        self._recent_closed_state_doc().set(
            sanitize_firestore_value(
                {
                    "items": trimmed,
                    "updated_at_iso": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
                }
            ),
            merge=True,
        )

    def _append_recent_closed_trade(self, trade: TradeRecord) -> None:
        trade_id = trade.get("trade_id")
        if not isinstance(trade_id, str):
            return

        cached = self._load_recent_closed_from_state()
        merged: list[TradeRecord] = [deepcopy(trade)]
        for item in cached:
            existing_id = item.get("trade_id")
            if isinstance(existing_id, str) and existing_id == trade_id:
                continue
            merged.append(item)
        self._save_recent_closed_state(merged)

    def _scan_recent_closed_trades(self, pair: Pair, limit: int) -> list[TradeRecord]:
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
                self._cache_trade_day(trade_id, trade_date)
                self._cache_trade_snapshot(trade_id, trade, merge=False)

            if len(trades_by_id) >= limit * 3:
                # Heuristic short-circuit to reduce read cost.
                break

        trades = [trade for trade in trades_by_id.values() if isinstance(trade, dict)]
        trades.sort(key=_sort_trade_key, reverse=True)
        return deepcopy(trades[:limit])

    def _refresh_state_from_trade_payload(self, trade_id: str, trade_date: str, payload: dict[str, Any]) -> None:
        raw_state = payload.get("state")
        if not isinstance(raw_state, str):
            return

        if raw_state == OPEN_TRADE_STATE:
            snapshot = self._load_trade_snapshot(trade_id, trade_date)
            if not isinstance(snapshot, dict):
                return
            snapshot.setdefault("trade_date", trade_date)
            pair = snapshot.get("pair")
            pair_value = pair if isinstance(pair, str) else None
            self._set_open_trade_state(trade_id, trade_date, pair=pair_value)
            self._set_open_trade_cache(snapshot)
            return

        if raw_state in TERMINAL_TRADE_STATES:
            if raw_state == "CLOSED":
                snapshot = self._load_trade_snapshot(trade_id, trade_date)
                if isinstance(snapshot, dict):
                    snapshot.setdefault("trade_date", trade_date)
                    self._append_recent_closed_trade(snapshot)
            self._clear_open_trade_state()
            self._set_open_trade_cache(None)

    def get_current_config(self) -> BotConfig:
        if self._current_config_cache is None:
            self._current_config_cache = self.config_repo.get_current_config(self.model_id)
        return self._current_config_cache

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
        self._cache_trade_snapshot(trade["trade_id"], payload, merge=False)
        self._refresh_state_from_trade_payload(trade["trade_id"], trade_date, payload)

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
        self._cache_trade_snapshot(trade_id, payload, merge=True)
        self._refresh_state_from_trade_payload(trade_id, trade_date, payload)

    def find_open_trade(self, pair: Pair) -> TradeRecord | None:
        if self._open_trade_cache_initialized:
            cached = self._open_trade_cache
            if isinstance(cached, dict) and cached.get("pair") == pair and cached.get("state") == OPEN_TRADE_STATE:
                return deepcopy(cached)
            return None

        trade = self._load_open_trade_from_state(pair)
        if not isinstance(trade, dict):
            trade = self._scan_open_trade(pair)
            if isinstance(trade, dict):
                trade_id = trade.get("trade_id")
                trade_date = trade.get("trade_date")
                if isinstance(trade_id, str) and isinstance(trade_date, str):
                    pair_value = trade.get("pair")
                    self._set_open_trade_state(
                        trade_id,
                        trade_date,
                        pair=pair_value if isinstance(pair_value, str) else None,
                    )

        self._set_open_trade_cache(trade)
        if isinstance(trade, dict):
            trade_id = trade.get("trade_id")
            if isinstance(trade_id, str):
                self._cache_trade_snapshot(trade_id, trade, merge=True)
        return deepcopy(trade) if isinstance(trade, dict) else None

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

        cached = self._load_recent_closed_from_state()
        filtered = [
            trade
            for trade in cached
            if isinstance(trade, dict) and trade.get("state") == "CLOSED" and trade.get("pair") == pair
        ]
        if len(filtered) >= limit:
            return deepcopy(filtered[:limit])

        if not self._recent_closed_backfill_attempted:
            self._recent_closed_backfill_attempted = True
            fallback_limit = max(limit * 3, RECENT_CLOSED_TRADES_MAX_ITEMS)
            scanned = self._scan_recent_closed_trades(pair, fallback_limit)
            if scanned:
                self._save_recent_closed_state(scanned)
                filtered = [
                    trade
                    for trade in scanned
                    if isinstance(trade, dict)
                    and trade.get("state") == "CLOSED"
                    and trade.get("pair") == pair
                ]

        return deepcopy(filtered[:limit])

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
            create_payload: RunRecord = dict(payload)
            create_payload["occurrence_count"] = 1
            create_payload["first_executed_at_iso"] = payload.get("executed_at_iso")
            create_payload["last_executed_at_iso"] = payload.get("executed_at_iso")
            create_payload["latest_run_id"] = payload.get("run_id")
            try:
                skip_ref.create(sanitize_firestore_value(create_payload))
            except AlreadyExists:
                update_payload: RunRecord = dict(payload)
                update_payload["occurrence_count"] = Increment(1)
                update_payload["last_executed_at_iso"] = payload.get("executed_at_iso")
                update_payload["latest_run_id"] = payload.get("run_id")
                update_payload.pop("first_executed_at_iso", None)
                skip_ref.set(sanitize_firestore_value(update_payload), merge=True)
            return

        day_ref.collection("items").document(run["run_id"]).set(sanitize_firestore_value(payload))
