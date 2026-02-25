from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any

from pybot.app.usecases.execution_error_classifier import classify_execution_error

NON_RETRIABLE_ERROR_MARKERS = (
    "invalid params",
    "invalid argument",
    "unsupported pair",
    "must be > 0",
    "must be object",
    "account not found",
    "owner mismatch",
    "signature verification failed",
    "simulation failed: error processing instruction 0",
)

SLIPPAGE_ERROR_MARKERS = (
    "custom program error: 0x1771",
    "custom program error: 0x1781",
    "custom 6001",
    "custom 6017",
    "slippage",
    "exact out amount not matched",
)
SUMMARY_ERROR_MAX_LENGTH = 220


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def to_error_message(error: Exception | BaseException | object) -> str:
    if isinstance(error, BaseException):
        return str(error)
    return str(error)


def strip_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: entry for key, entry in value.items() if entry is not None}


def is_non_retriable_error_message(message: str) -> bool:
    classified = classify_execution_error(message)
    if classified.action != "RETRY":
        return True

    normalized = message.strip().lower()
    return any(marker in normalized for marker in NON_RETRIABLE_ERROR_MARKERS)


def is_slippage_error_message(message: str) -> bool:
    classified = classify_execution_error(message)
    if classified.kind == "SLIPPAGE":
        return True

    normalized = message.strip().lower()
    return any(marker in normalized for marker in SLIPPAGE_ERROR_MARKERS)


def is_market_condition_error_message(message: str) -> bool:
    return classify_execution_error(message).kind == "MARKET_CONDITION"


def is_insufficient_funds_error_message(message: str) -> bool:
    return classify_execution_error(message).kind == "INSUFFICIENT_FUNDS"


def summarize_error_for_log(message: str, max_length: int = SUMMARY_ERROR_MAX_LENGTH) -> str:
    normalized = " ".join(message.strip().split())
    for pattern in (r"'message':\s*'([^']+)'", r'"message"\s*:\s*"([^"]+)"'):
        matched = re.search(pattern, normalized)
        if matched:
            normalized = matched.group(1).strip()
            break
    if len(normalized) > max_length:
        return f"{normalized[: max_length - 3]}..."
    return normalized


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
