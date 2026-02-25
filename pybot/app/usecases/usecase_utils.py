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


def resolve_tx_fee_lamports(
    execution: object,
    tx_signature: str,
    *,
    logger: Any,
    log_context: dict[str, Any] | None = None,
) -> int | None:
    fee_getter = getattr(execution, "get_transaction_fee_lamports", None)
    if not callable(fee_getter):
        return None

    context = dict(log_context or {})
    context["tx_signature"] = tx_signature
    try:
        fee_value = fee_getter(tx_signature)
    except Exception as error:
        logger.warn(
            "failed to fetch transaction fee",
            {**context, "error": to_error_message(error)},
        )
        return None

    if fee_value is None:
        return None
    if isinstance(fee_value, int) and fee_value >= 0:
        return fee_value

    logger.warn(
        "invalid transaction fee payload",
        {**context, "fee_value": fee_value},
    )
    return None
