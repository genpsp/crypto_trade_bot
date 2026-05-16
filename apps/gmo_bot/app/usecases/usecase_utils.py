from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from apps.gmo_bot.domain.utils.time import format_iso_utc

SUMMARY_ERROR_MAX_LENGTH = 220

# 11.3: a single default ``now`` provider so usecases can inject a frozen
# clock in tests rather than reaching for ``datetime.now`` directly.
NowProvider = Callable[[], datetime]


def default_now_utc() -> datetime:
    return datetime.now(tz=UTC)


def now_iso(now_provider: NowProvider | None = None) -> str:
    provider = now_provider or default_now_utc
    return format_iso_utc(provider())


# 9.7/9.8: collapsed duplicated branches; ``Exception`` ⊂ ``BaseException`` ⊂
# ``object`` so the isinstance check was a no-op.
def to_error_message(error: object) -> str:
    return str(error)


def strip_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: entry for key, entry in value.items() if entry is not None}


def summarize_error_for_log(message: str, max_length: int = SUMMARY_ERROR_MAX_LENGTH) -> str:
    normalized = " ".join(message.strip().split())
    if len(normalized) > max_length:
        return f"{normalized[: max_length - 3]}..."
    return normalized
