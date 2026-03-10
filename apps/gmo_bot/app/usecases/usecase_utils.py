from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


SUMMARY_ERROR_MAX_LENGTH = 220


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def to_error_message(error: Exception | BaseException | object) -> str:
    if isinstance(error, BaseException):
        return str(error)
    return str(error)


def strip_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: entry for key, entry in value.items() if entry is not None}


def summarize_error_for_log(message: str, max_length: int = SUMMARY_ERROR_MAX_LENGTH) -> str:
    normalized = " ".join(message.strip().split())
    if len(normalized) > max_length:
        return f"{normalized[: max_length - 3]}..."
    return normalized
