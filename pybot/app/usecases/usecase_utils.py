from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

NON_RETRIABLE_ERROR_MARKERS = (
    "insufficient funds",
    "insufficient lamports",
    "invalid params",
    "invalid argument",
    "unsupported pair",
    "must be > 0",
    "must be object",
    "simulation failed: error processing instruction",
    "custom program error: 0x1",
    "account not found",
    "owner mismatch",
    "signature verification failed",
)


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def to_error_message(error: Exception | BaseException | object) -> str:
    if isinstance(error, BaseException):
        return str(error)
    return str(error)


def strip_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: entry for key, entry in value.items() if entry is not None}


def is_non_retriable_error_message(message: str) -> bool:
    normalized = message.strip().lower()
    return any(marker in normalized for marker in NON_RETRIABLE_ERROR_MARKERS)


def should_retry_error(*, attempt: int, max_attempts: int, error_message: str) -> bool:
    return attempt < max_attempts and not is_non_retriable_error_message(error_message)
