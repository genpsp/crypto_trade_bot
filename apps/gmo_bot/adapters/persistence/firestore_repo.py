from __future__ import annotations

from copy import deepcopy
import hashlib
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore import Client
from google.cloud.firestore_v1 import Increment, transactional
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.query import Query

from apps.gmo_bot.app.ports.logger_port import LoggerPort
from apps.gmo_bot.app.ports.persistence_port import PersistencePort
from apps.gmo_bot.domain.model.types import BotConfig, DailyBalanceRecord, Pair, RunRecord, TradeRecord
from apps.gmo_bot.domain.utils.time import JST, format_iso_utc
from apps.gmo_bot.infra.config.firestore_config_repo import FirestoreConfigRepository, MODELS_COLLECTION_ID


def sanitize_firestore_value(value: Any) -> Any:
    """Return a Firestore-safe copy with ``None`` values omitted.

    Passing ``None`` to update/create helpers does not delete a field; it simply
    removes that key from the payload.  Use an explicit Firestore delete sentinel
    at the call site when a persisted field must be deleted.
    """

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
NO_OPEN_TRADE_STATE = "NONE"
TERMINAL_TRADE_STATES = {"CLOSED", "FAILED", "CANCELED"}
STATE_COLLECTION_NAME = "state"
OPEN_TRADE_STATE_DOC_ID = "open_trade"
RECENT_CLOSED_STATE_DOC_ID = "recent_closed_trades"
RECENT_CLOSED_TRADES_MAX_ITEMS = 32
TRADE_SNAPSHOT_CACHE_MAX_ITEMS = 256
# 6.3: cap fallback open-trade scans to a recent window so cost is bounded
# regardless of how many historical day documents exist.
OPEN_TRADE_SCAN_LOOKBACK_DAYS = 30
RECENT_CLOSED_SCAN_LOOKBACK_DAYS = 60
DAILY_BALANCE_COLLECTION_NAME = "daily_balance"
# 6.1: throttle model metadata heartbeat writes (previously written on every
# create_trade/update_trade/save_daily_balance call).
MODEL_METADATA_TOUCH_INTERVAL_SECONDS = 60 * 60


def _extract_run_date(run: RunRecord) -> tuple[str, bool]:
    """Return ``(run_date_jst, used_today_fallback)``.

    6.5: callers should warn when ``used_today_fallback`` is true so silently
    bucketing a run into "today's" day doc is visible in logs.
    """

    value = run.get("bar_close_time_iso") or run.get("executed_at_iso")
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(JST).date().isoformat(), False
        except ValueError:
            pass
    return datetime.now(tz=JST).date().isoformat(), True


def _parse_iso_date(value: str, *, tz: timezone = JST) -> str | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(tz).date().isoformat()
    except ValueError:
        return None


def _extract_trade_date_from_trade_id(trade_id: str) -> str | None:
    timestamp_head = trade_id.split("_", 1)[0]
    return _parse_iso_date(timestamp_head)


def _extract_trade_date_from_payload(payload: dict[str, Any]) -> tuple[str, bool]:
    """Resolve trade_date from a payload.

    Returns ``(trade_date, used_today_fallback)`` so callers can warn when none of
    the payload's date hints resolved and we fell back to JST today.
    """

    for key in ("trade_date", "created_at", "bar_close_time_iso", "updated_at"):
        raw_value = payload.get(key)
        if isinstance(raw_value, str):
            parsed_date = _parse_iso_date(raw_value)
            if parsed_date is not None:
                return parsed_date, False
    trade_id = payload.get("trade_id")
    if isinstance(trade_id, str):
        parsed_from_trade_id = _extract_trade_date_from_trade_id(trade_id)
        if parsed_from_trade_id is not None:
            return parsed_from_trade_id, False
    return datetime.now(tz=JST).date().isoformat(), True


def _extract_day_date(day_start_iso: str, day_end_iso: str) -> str:
    parsed_start = _parse_iso_date(day_start_iso)
    if parsed_start is not None:
        return parsed_start
    parsed_end = _parse_iso_date(day_end_iso)
    if parsed_end is not None:
        return parsed_end
    return datetime.now(tz=JST).date().isoformat()


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
        initial_config: BotConfig | None = None,
        logger: LoggerPort | None = None,
    ):
        self.firestore = firestore
        self.config_repo = config_repo
        self.mode = mode
        self.model_id = model_id
        self.logger = logger
        self.trades_collection_name = "paper_trades" if mode == "PAPER" else "trades"
        self.runs_collection_name = "paper_runs" if mode == "PAPER" else "runs"
        self._trade_storage_cache: dict[str, str] = {}
        self._current_config_cache: BotConfig | None = deepcopy(initial_config) if initial_config is not None else None
        self._trade_snapshot_cache: dict[str, TradeRecord] = {}
        self._open_trade_cache: TradeRecord | None = None
        self._open_trade_cache_initialized = False
        self._open_trade_state_prevents_scan = False
        self._recent_closed_cache: list[TradeRecord] = []
        self._recent_closed_cache_initialized = False
        self._recent_closed_backfill_attempted = False
        self._recent_closed_backfill_complete = False
        self._last_model_metadata_touch_at: datetime | None = None

    def _model_doc(self):
        return self.firestore.collection(MODELS_COLLECTION_ID).document(self.model_id)

    def _touch_model_metadata(self) -> None:
        now = datetime.now(tz=UTC)
        # Throttle heartbeat writes; previously every trade write produced a
        # corresponding model metadata write (millions of writes per month).
        last_touch = self._last_model_metadata_touch_at
        if last_touch is not None and (now - last_touch).total_seconds() < MODEL_METADATA_TOUCH_INTERVAL_SECONDS:
            return
        self._model_doc().set(
            sanitize_firestore_value(
                {
                    "model_id": self.model_id,
                    "mode": self.mode,
                    "updated_at_iso": format_iso_utc(now),
                }
            ),
            merge=True,
        )
        self._last_model_metadata_touch_at = now

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
                    "updated_at_iso": updated_at_iso or format_iso_utc(datetime.now(tz=UTC)),
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

        extracted = _extract_trade_date_from_trade_id(trade_id)
        if extracted is not None:
            return extracted

        fallback = datetime.now(tz=JST).date().isoformat()
        if self.logger is not None:
            self.logger.warn(
                "trade_date fallback to JST today; old trade may split into today's day doc",
                {
                    "trade_id": trade_id,
                    "fallback_date": fallback,
                    "model_id": self.model_id,
                },
            )
        return fallback

    def _daily_balance_collection(self):
        return self._model_doc().collection(DAILY_BALANCE_COLLECTION_NAME)

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
            "updated_at_iso": format_iso_utc(datetime.now(tz=UTC)),
        }
        if isinstance(pair, str):
            payload["pair"] = pair
        self._open_trade_state_doc().set(sanitize_firestore_value(payload))

    def _set_no_open_trade_state(self, pair: str | None = None) -> None:
        payload: dict[str, Any] = {
            "state": NO_OPEN_TRADE_STATE,
            "updated_at_iso": format_iso_utc(datetime.now(tz=UTC)),
        }
        if isinstance(pair, str):
            payload["pair"] = pair
        self._open_trade_state_doc().set(sanitize_firestore_value(payload), merge=True)

    def _clear_open_trade_state(self) -> None:
        try:
            self._set_no_open_trade_state()
        except Exception as error:
            if self.logger is not None:
                # Suppressing this previously meant the next cycle could read a
                # stale CONFIRMED state document and trigger a double-entry.
                self.logger.error(
                    "failed to clear open_trade state document; next cycle may see stale CONFIRMED",
                    {"model_id": self.model_id, "error": str(error)},
                )

    def _set_open_trade_cache(self, trade: TradeRecord | None) -> None:
        self._open_trade_cache = deepcopy(trade) if isinstance(trade, dict) else None
        self._open_trade_cache_initialized = True

    def _merge_open_trade_cache(self, trade_id: str, payload: dict[str, Any]) -> None:
        if not self._open_trade_cache_initialized:
            return
        cached = self._open_trade_cache
        if not isinstance(cached, dict) or cached.get("trade_id") != trade_id:
            return
        merged = deepcopy(cached)
        _deep_merge_dict(merged, payload)
        state = merged.get("state")
        if state == OPEN_TRADE_STATE:
            self._set_open_trade_cache(merged)
        elif state in TERMINAL_TRADE_STATES:
            self._set_open_trade_cache(None)

    def _load_open_trade_from_state(self, pair: Pair) -> TradeRecord | None:
        self._open_trade_state_prevents_scan = False
        state_snapshot = self._open_trade_state_doc().get()
        if not state_snapshot.exists:
            return None

        state_payload = state_snapshot.to_dict()
        if not isinstance(state_payload, dict):
            return None

        raw_state = state_payload.get("state")
        if raw_state == NO_OPEN_TRADE_STATE:
            state_pair = state_payload.get("pair")
            if not isinstance(state_pair, str) or state_pair == pair:
                self._open_trade_state_prevents_scan = True
            return None

        state_pair = state_payload.get("pair")
        if isinstance(state_pair, str) and state_pair != pair:
            return None

        trade_id = state_payload.get("trade_id")
        if not isinstance(trade_id, str) or not trade_id:
            return None

        raw_trade_date = state_payload.get("trade_date")
        trade_date = raw_trade_date if isinstance(raw_trade_date, str) and _is_day_doc_id(raw_trade_date) else None
        trade = self._load_trade_snapshot(trade_id, trade_date)
        if not isinstance(trade, dict):
            return None
        if trade.get("state") != OPEN_TRADE_STATE or trade.get("pair") != pair:
            return None

        resolved_trade_date = trade.get("trade_date")
        if isinstance(resolved_trade_date, str) and _is_day_doc_id(resolved_trade_date):
            self._cache_trade_day(trade_id, resolved_trade_date)
        return trade

    def _scan_open_trade(self, pair: Pair) -> TradeRecord | None:
        candidates_by_trade_id: dict[str, TradeRecord] = {}

        # 6.3: bound the scan to the recent N days. The state document is the
        # primary path; this scan only runs when state is missing/corrupted, so
        # an open trade older than the lookback window is exceptional and
        # better escalated by alerting than auto-discovered here.
        day_snapshots = self._trades_collection().stream()
        all_day_ids = sorted((doc.id for doc in day_snapshots if _is_day_doc_id(doc.id)), reverse=True)
        trade_day_ids = all_day_ids[:OPEN_TRADE_SCAN_LOOKBACK_DAYS]
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
            self._recent_closed_backfill_complete = False
            return []

        payload = state_snapshot.to_dict()
        if not isinstance(payload, dict):
            self._recent_closed_cache = []
            self._recent_closed_backfill_complete = False
            return []

        self._recent_closed_backfill_complete = payload.get("backfill_complete") is True
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
        self._recent_closed_backfill_complete = True
        self._recent_closed_state_doc().set(
            sanitize_firestore_value(
                {
                    "items": trimmed,
                    "backfill_complete": True,
                    "updated_at_iso": format_iso_utc(datetime.now(tz=UTC)),
                }
            ),
            merge=True,
        )

    def _append_recent_closed_trade(self, trade: TradeRecord) -> None:
        trade_id = trade.get("trade_id")
        if not isinstance(trade_id, str):
            return

        doc_ref = self._recent_closed_state_doc()
        new_trade = deepcopy(trade)
        updated_at_iso = format_iso_utc(datetime.now(tz=UTC))

        @transactional
        def _commit(tx: Any) -> list[TradeRecord]:
            snapshot = doc_ref.get(transaction=tx)
            existing_items: list[TradeRecord] = []
            if snapshot.exists:
                payload = snapshot.to_dict()
                if isinstance(payload, dict):
                    raw_items = payload.get("items")
                    if isinstance(raw_items, list):
                        for item in raw_items:
                            if isinstance(item, dict):
                                existing_items.append(deepcopy(item))
            merged: list[TradeRecord] = [deepcopy(new_trade)]
            for item in existing_items:
                existing_id = item.get("trade_id")
                if isinstance(existing_id, str) and existing_id == trade_id:
                    continue
                merged.append(item)
            trimmed = merged[:RECENT_CLOSED_TRADES_MAX_ITEMS]
            tx.set(
                doc_ref,
                sanitize_firestore_value(
                    {
                        "items": trimmed,
                        "backfill_complete": True,
                        "updated_at_iso": updated_at_iso,
                    }
                ),
                merge=True,
            )
            return trimmed

        trimmed = _commit(self.firestore.transaction())
        self._recent_closed_cache = deepcopy(trimmed)
        self._recent_closed_cache_initialized = True
        self._recent_closed_backfill_complete = True

    def _scan_recent_closed_trades(self, pair: Pair, limit: int) -> list[TradeRecord]:
        if limit <= 0:
            return []

        trades_by_id: dict[str, TradeRecord] = {}
        day_snapshots = self._trades_collection().stream()
        all_day_ids = sorted((doc.id for doc in day_snapshots if _is_day_doc_id(doc.id)), reverse=True)
        # 6.3: cap the historical scan window so cost is bounded.
        trade_day_ids = all_day_ids[:RECENT_CLOSED_SCAN_LOOKBACK_DAYS]

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

            # 6.4: Heuristic short-circuit to reduce read cost.
            # The 3x multiplier (vs ``limit`` exactly) gives some slack for
            # subsequent pair/state filters on the merged dict; we'd rather
            # over-fetch a little than miss recent closed trades for a model
            # that has had several pairs/states active on the same day.
            if len(trades_by_id) >= limit * 3:
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
        trade_date, used_today_fallback = _extract_trade_date_from_payload(payload)
        if used_today_fallback and self.logger is not None:
            self.logger.warn(
                "create_trade fell back to JST today for trade_date; payload lacked all date hints",
                {
                    "trade_id": trade.get("trade_id"),
                    "trade_date": trade_date,
                    "model_id": self.model_id,
                },
            )
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
        self._merge_open_trade_cache(trade_id, payload)
        self._refresh_state_from_trade_payload(trade_id, trade_date, payload)

    def find_open_trade(self, pair: Pair) -> TradeRecord | None:
        if self._open_trade_cache_initialized:
            cached = self._open_trade_cache
            if isinstance(cached, dict) and cached.get("pair") == pair and cached.get("state") == OPEN_TRADE_STATE:
                return deepcopy(cached)
            return None

        trade = self._load_open_trade_from_state(pair)
        if not isinstance(trade, dict) and not self._open_trade_state_prevents_scan:
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
            else:
                self._set_no_open_trade_state(pair if isinstance(pair, str) else None)

        self._set_open_trade_cache(trade)
        if isinstance(trade, dict):
            trade_id = trade.get("trade_id")
            if isinstance(trade_id, str):
                self._cache_trade_snapshot(trade_id, trade, merge=True)
        return deepcopy(trade) if isinstance(trade, dict) else None

    def get_trade(self, trade_id: str) -> TradeRecord | None:
        if not isinstance(trade_id, str) or trade_id.strip() == "":
            return None
        trade = self._load_trade_snapshot(trade_id)
        return deepcopy(trade) if isinstance(trade, dict) else None

    def count_trades_for_jst_day(self, pair: Pair, jst_day_start_iso: str, jst_day_end_iso: str) -> int:
        trade_date = _extract_day_date(jst_day_start_iso, jst_day_end_iso)
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

    def count_trades_for_utc_day(self, pair: Pair, day_start_iso: str, day_end_iso: str) -> int:
        """Backward-compatible alias for ``count_trades_for_jst_day``."""

        return self.count_trades_for_jst_day(pair, day_start_iso, day_end_iso)

    def list_recent_closed_trades(self, pair: Pair, limit: int) -> list[TradeRecord]:
        if limit <= 0:
            return []

        cached = self._load_recent_closed_from_state()
        filtered = [
            trade
            for trade in cached
            if isinstance(trade, dict) and trade.get("state") == "CLOSED" and trade.get("pair") == pair
        ]
        if len(filtered) >= limit or self._recent_closed_backfill_complete:
            return deepcopy(filtered[:limit])

        if not self._recent_closed_backfill_attempted:
            self._recent_closed_backfill_attempted = True
            fallback_limit = max(limit * 3, RECENT_CLOSED_TRADES_MAX_ITEMS)
            scanned = self._scan_recent_closed_trades(pair, fallback_limit)
            self._save_recent_closed_state(scanned)
            filtered = [
                trade
                for trade in scanned
                if isinstance(trade, dict)
                and trade.get("state") == "CLOSED"
                and trade.get("pair") == pair
            ]

        return deepcopy(filtered[:limit])

    def save_daily_balance(self, snapshot: DailyBalanceRecord) -> None:
        self._touch_model_metadata()
        payload: DailyBalanceRecord = dict(snapshot)
        snapshot_date_jst = payload.get("snapshot_date_jst")
        if not isinstance(snapshot_date_jst, str) or not _is_day_doc_id(snapshot_date_jst):
            raise ValueError("daily balance snapshot_date_jst must be YYYY-MM-DD")
        payload.setdefault("model_id", self.model_id)
        payload.setdefault(
            "snapshot_at_iso",
            format_iso_utc(datetime.now(tz=UTC)),
        )
        self._daily_balance_collection().document(snapshot_date_jst).set(
            sanitize_firestore_value(payload),
            merge=True,
        )

    def list_recent_daily_balances(self, days: int) -> list[DailyBalanceRecord]:
        if days <= 0:
            return []

        # 6.2: use order_by + limit so cost is O(days) instead of O(all daily docs).
        # Day doc IDs are YYYY-MM-DD which sort lexicographically.
        query = (
            self._daily_balance_collection()
            .order_by("snapshot_date_jst", direction=Query.DESCENDING)
            .limit(days)
        )
        records: list[DailyBalanceRecord] = []
        for snapshot in query.stream():
            payload = snapshot.to_dict()
            if not isinstance(payload, dict):
                continue
            snapshot_date_jst = payload.get("snapshot_date_jst")
            if not isinstance(snapshot_date_jst, str) or not _is_day_doc_id(snapshot_date_jst):
                doc_id = getattr(snapshot, "id", "")
                if not isinstance(doc_id, str) or not _is_day_doc_id(doc_id):
                    continue
                payload["snapshot_date_jst"] = doc_id
            records.append(deepcopy(payload))

        records.sort(key=lambda record: str(record.get("snapshot_date_jst") or ""))
        return records

    def list_trades_in_range(self, from_date_jst: str, to_date_jst: str) -> list[TradeRecord]:
        if not _is_day_doc_id(from_date_jst) or not _is_day_doc_id(to_date_jst):
            raise ValueError("from_date_jst and to_date_jst must be YYYY-MM-DD")

        from_date = datetime.fromisoformat(from_date_jst).date()
        to_date = datetime.fromisoformat(to_date_jst).date()
        if to_date < from_date:
            return []

        trades: list[TradeRecord] = []
        cursor = from_date
        while cursor <= to_date:
            trade_date = cursor.isoformat()
            for doc in self._trade_items_collection_for_date(trade_date).stream():
                payload = doc.to_dict()
                if not isinstance(payload, dict):
                    continue
                payload.setdefault("trade_date", trade_date)
                trades.append(deepcopy(payload))
            cursor += timedelta(days=1)
        return trades

    def list_runs_in_range(self, from_date_jst: str, to_date_jst: str) -> list[RunRecord]:
        if not _is_day_doc_id(from_date_jst) or not _is_day_doc_id(to_date_jst):
            raise ValueError("from_date_jst and to_date_jst must be YYYY-MM-DD")

        from_date = datetime.fromisoformat(from_date_jst).date()
        to_date = datetime.fromisoformat(to_date_jst).date()
        if to_date < from_date:
            return []

        runs: list[RunRecord] = []
        cursor = from_date
        while cursor <= to_date:
            run_date = cursor.isoformat()
            day_ref = self._runs_collection().document(run_date)
            for doc in day_ref.collection("items").stream():
                payload = doc.to_dict()
                if not isinstance(payload, dict):
                    continue
                payload.setdefault("run_date", run_date)
                runs.append(deepcopy(payload))
            cursor += timedelta(days=1)
        return runs

    def list_daily_balances_in_range(
        self, from_date_jst: str, to_date_jst: str
    ) -> list[DailyBalanceRecord]:
        if not _is_day_doc_id(from_date_jst) or not _is_day_doc_id(to_date_jst):
            raise ValueError("from_date_jst and to_date_jst must be YYYY-MM-DD")

        records: list[DailyBalanceRecord] = []
        for snapshot in self._daily_balance_collection().stream():
            payload = snapshot.to_dict()
            if not isinstance(payload, dict):
                continue
            snapshot_date_jst = payload.get("snapshot_date_jst")
            if not isinstance(snapshot_date_jst, str) or not _is_day_doc_id(snapshot_date_jst):
                doc_id = getattr(snapshot, "id", "")
                if not isinstance(doc_id, str) or not _is_day_doc_id(doc_id):
                    continue
                snapshot_date_jst = doc_id
                payload["snapshot_date_jst"] = doc_id
            if from_date_jst <= snapshot_date_jst <= to_date_jst:
                records.append(deepcopy(payload))

        records.sort(key=lambda record: str(record.get("snapshot_date_jst") or ""))
        return records

    def save_run(self, run: RunRecord) -> None:
        runs_collection = self._runs_collection()
        run_date, used_today_fallback = _extract_run_date(run)
        if used_today_fallback and self.logger is not None:
            self.logger.warn(
                "save_run: bar_close_time_iso and executed_at_iso missing/unparseable; "
                "bucketing into today's JST day doc",
                {
                    "model_id": self.model_id,
                    "run_id": run.get("run_id"),
                    "result": run.get("result"),
                    "run_date_fallback": run_date,
                },
            )
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
