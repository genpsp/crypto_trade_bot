from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

ErrorAction = Literal["SKIP", "FAIL", "RETRY"]
ErrorKind = Literal["SLIPPAGE", "MARKET_CONDITION", "INSUFFICIENT_FUNDS", "FATAL", "UNKNOWN"]

SLIPPAGE_CUSTOM_CODES = frozenset({6001, 6017})
FATAL_CUSTOM_CODES = frozenset({6008, 6014, 6025})
INSUFFICIENT_FUNDS_CUSTOM_CODES = frozenset({6024})

SLIPPAGE_MARKERS = (
    "slippage tolerance exceeded",
    "slippage exceeded",
    "exact out amount not matched",
)

MARKET_CONDITION_MARKERS = (
    "no routes found",
    "no_routes_found",
    "could not find any route",
    "could_not_find_any_route",
    "route plan does not consume all the amount",
    "route_plan_does_not_consume_all_the_amount",
    "token not tradable",
    "token_not_tradable",
    "insufficient liquidity",
    "price impact too high",
)

INSUFFICIENT_FUNDS_MARKERS = (
    "insufficient funds",
    "insufficient lamports",
)

FATAL_MARKERS = (
    "invalid params",
    "invalid argument",
    "unsupported pair",
    "must be > 0",
    "must be object",
    "account not found",
    "owner mismatch",
    "signature verification failed",
    "not enough account keys",
    "incorrect token program id",
    "invalid token account",
)

_CUSTOM_HEX_PATTERN = re.compile(r"custom program error:\s*0x([0-9a-f]+)", re.IGNORECASE)
_CUSTOM_DECIMAL_PATTERN = re.compile(r"\bcustom\s+(\d{3,5})\b", re.IGNORECASE)
_CUSTOM_DICT_PATTERN = re.compile(r"['\"]custom['\"]\s*:\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class ExecutionErrorClassification:
    kind: ErrorKind
    action: ErrorAction
    custom_code: int | None = None


def normalize_error_message(message: str) -> str:
    return message.strip().lower()


def extract_custom_program_error_code(message: str) -> int | None:
    normalized = normalize_error_message(message)
    matched_hex = _CUSTOM_HEX_PATTERN.search(normalized)
    if matched_hex:
        try:
            return int(matched_hex.group(1), 16)
        except ValueError:
            pass

    matched_decimal = _CUSTOM_DECIMAL_PATTERN.search(normalized)
    if matched_decimal:
        try:
            return int(matched_decimal.group(1))
        except ValueError:
            pass

    matched_dict = _CUSTOM_DICT_PATTERN.search(normalized)
    if matched_dict:
        try:
            return int(matched_dict.group(1))
        except ValueError:
            pass

    return None


def classify_execution_error(message: str) -> ExecutionErrorClassification:
    normalized = normalize_error_message(message)
    custom_code = extract_custom_program_error_code(normalized)

    if custom_code in SLIPPAGE_CUSTOM_CODES:
        return ExecutionErrorClassification(kind="SLIPPAGE", action="SKIP", custom_code=custom_code)
    if custom_code in INSUFFICIENT_FUNDS_CUSTOM_CODES:
        return ExecutionErrorClassification(kind="INSUFFICIENT_FUNDS", action="SKIP", custom_code=custom_code)
    if custom_code in FATAL_CUSTOM_CODES:
        return ExecutionErrorClassification(kind="FATAL", action="FAIL", custom_code=custom_code)

    if any(marker in normalized for marker in SLIPPAGE_MARKERS):
        return ExecutionErrorClassification(kind="SLIPPAGE", action="SKIP", custom_code=custom_code)
    if any(marker in normalized for marker in MARKET_CONDITION_MARKERS):
        return ExecutionErrorClassification(kind="MARKET_CONDITION", action="SKIP", custom_code=custom_code)
    if any(marker in normalized for marker in INSUFFICIENT_FUNDS_MARKERS):
        return ExecutionErrorClassification(kind="INSUFFICIENT_FUNDS", action="SKIP", custom_code=custom_code)
    if any(marker in normalized for marker in FATAL_MARKERS):
        return ExecutionErrorClassification(kind="FATAL", action="FAIL", custom_code=custom_code)

    return ExecutionErrorClassification(kind="UNKNOWN", action="RETRY", custom_code=custom_code)
