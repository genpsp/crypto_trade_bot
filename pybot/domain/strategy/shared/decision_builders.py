from __future__ import annotations

from typing import Any

from pybot.domain.model.types import EntrySignalDecision, NoSignalDecision


def build_no_signal(
    summary: str,
    reason: str,
    ema_fast: float | None = None,
    ema_slow: float | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> NoSignalDecision:
    return NoSignalDecision(
        type="NO_SIGNAL",
        summary=summary,
        reason=reason,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        diagnostics=diagnostics,
    )


def build_entry_signal(
    summary: str,
    ema_fast: float,
    ema_slow: float,
    entry_price: float,
    stop_price: float,
    take_profit_price: float,
    diagnostics: dict[str, Any] | None = None,
) -> EntrySignalDecision:
    return EntrySignalDecision(
        type="ENTER",
        summary=summary,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        diagnostics=diagnostics,
    )

