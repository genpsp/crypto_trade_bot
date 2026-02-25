#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from typing import Any

from google.cloud.firestore import Client

MAX_BATCH_OPERATIONS = 450
TRADE_COLLECTIONS = ("trades", "paper_trades")


def _parse_iso_date(value: str) -> str | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.date().isoformat()
    except ValueError:
        return None


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


def _extract_trade_date(payload: dict[str, Any], doc_id: str) -> str:
    for key in ("trade_date", "created_at", "bar_close_time_iso"):
        raw_value = payload.get(key)
        if isinstance(raw_value, str):
            parsed_date = _parse_iso_date(raw_value)
            if parsed_date is not None:
                return parsed_date

    trade_id = payload.get("trade_id")
    if isinstance(trade_id, str):
        parsed_from_trade_id = _parse_iso_date(trade_id.split("_", 1)[0])
        if parsed_from_trade_id is not None:
            return parsed_from_trade_id

    parsed_from_doc_id = _parse_iso_date(doc_id.split("_", 1)[0])
    if parsed_from_doc_id is not None:
        return parsed_from_doc_id

    return datetime.now(tz=UTC).date().isoformat()


@dataclass
class MigrationStats:
    scanned_docs: int = 0
    moved_docs: int = 0
    deleted_legacy_docs: int = 0
    skipped_non_trade_docs: int = 0
    skipped_invalid_payload_docs: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "scanned_docs": self.scanned_docs,
            "moved_docs": self.moved_docs,
            "deleted_legacy_docs": self.deleted_legacy_docs,
            "skipped_non_trade_docs": self.skipped_non_trade_docs,
            "skipped_invalid_payload_docs": self.skipped_invalid_payload_docs,
        }


def _resolve_model_ids(firestore: Client, explicit_model_ids: list[str] | None) -> list[str]:
    if explicit_model_ids:
        return sorted(set(explicit_model_ids))
    model_ids = [doc.id for doc in firestore.collection("models").stream()]
    model_ids.sort()
    return model_ids


def _migrate_collection(
    firestore: Client,
    *,
    model_id: str,
    collection_name: str,
    dry_run: bool,
    delete_legacy: bool,
) -> MigrationStats:
    stats = MigrationStats()
    model_ref = firestore.collection("models").document(model_id)
    collection_ref = model_ref.collection(collection_name)
    snapshots = list(collection_ref.stream())
    if not snapshots:
        return stats

    batch = firestore.batch()
    pending_operations = 0

    def flush_batch() -> None:
        nonlocal batch, pending_operations
        if dry_run or pending_operations == 0:
            return
        batch.commit()
        batch = firestore.batch()
        pending_operations = 0

    for snapshot in snapshots:
        stats.scanned_docs += 1
        payload = snapshot.to_dict()
        if not isinstance(payload, dict):
            stats.skipped_invalid_payload_docs += 1
            continue

        trade_id = payload.get("trade_id")
        if not isinstance(trade_id, str) or trade_id.strip() == "":
            stats.skipped_non_trade_docs += 1
            continue
        trade_id = trade_id.strip()

        trade_date = _extract_trade_date(payload, snapshot.id)
        payload["trade_date"] = trade_date
        payload.setdefault("model_id", model_id)

        day_ref = collection_ref.document(trade_date)
        day_payload = {
            "trade_date": trade_date,
            "updated_at_iso": payload.get("updated_at") or datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        }
        item_ref = day_ref.collection("items").document(trade_id)

        stats.moved_docs += 1
        if dry_run:
            if delete_legacy:
                stats.deleted_legacy_docs += 1
            continue

        batch.set(day_ref, sanitize_firestore_value(day_payload), merge=True)
        pending_operations += 1
        batch.set(item_ref, sanitize_firestore_value(payload), merge=True)
        pending_operations += 1
        if delete_legacy:
            batch.delete(snapshot.reference)
            pending_operations += 1
            stats.deleted_legacy_docs += 1

        if pending_operations >= MAX_BATCH_OPERATIONS:
            flush_batch()

    flush_batch()
    return stats


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate legacy models/{model_id}/{trades|paper_trades}/{trade_id} docs into date-partitioned items."
    )
    parser.add_argument(
        "--credentials",
        default=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
        help="Path to Google service account JSON. Defaults to GOOGLE_APPLICATION_CREDENTIALS.",
    )
    parser.add_argument(
        "--model-id",
        action="append",
        dest="model_ids",
        default=None,
        help="Target model_id (repeatable). If omitted, migrate all models.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report only; do not write or delete.",
    )
    parser.add_argument(
        "--keep-legacy",
        action="store_true",
        help="Keep legacy flat docs after copy.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    credentials_path = str(args.credentials or "").strip()
    if credentials_path == "":
        raise RuntimeError("credentials path is required (set --credentials or GOOGLE_APPLICATION_CREDENTIALS)")

    firestore = Client.from_service_account_json(credentials_path)
    model_ids = _resolve_model_ids(firestore, args.model_ids)
    if not model_ids:
        print("No models found. Nothing to migrate.")
        return 0

    delete_legacy = not args.keep_legacy
    overall = {
        "models": {},
        "total_scanned_docs": 0,
        "total_moved_docs": 0,
        "total_deleted_legacy_docs": 0,
        "total_skipped_non_trade_docs": 0,
        "total_skipped_invalid_payload_docs": 0,
        "dry_run": bool(args.dry_run),
        "delete_legacy": delete_legacy,
    }

    for model_id in model_ids:
        model_stats: dict[str, dict[str, int]] = {}
        for collection_name in TRADE_COLLECTIONS:
            stats = _migrate_collection(
                firestore,
                model_id=model_id,
                collection_name=collection_name,
                dry_run=bool(args.dry_run),
                delete_legacy=delete_legacy,
            )
            model_stats[collection_name] = stats.to_dict()
            overall["total_scanned_docs"] += stats.scanned_docs
            overall["total_moved_docs"] += stats.moved_docs
            overall["total_deleted_legacy_docs"] += stats.deleted_legacy_docs
            overall["total_skipped_non_trade_docs"] += stats.skipped_non_trade_docs
            overall["total_skipped_invalid_payload_docs"] += stats.skipped_invalid_payload_docs
        overall["models"][model_id] = model_stats

    print(overall)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] migrate-trades-by-day failed: {error}")
        raise SystemExit(1) from error
