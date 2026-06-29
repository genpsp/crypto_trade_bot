from __future__ import annotations

from typing import Any, Protocol

from apps.gmo_bot.domain.model.types import TradeRecord
from apps.gmo_bot.domain.utils.coercion import as_dict as _as_dict, to_float as _to_float


class TradeCloseNotifier(Protocol):
    def notify_trade_closed(
        self,
        *,
        model_id: str,
        trade_id: str,
        pair: str,
        direction: str,
        close_reason: str,
        entry_price: float | None,
        exit_price: float | None,
        gross_pnl: float | None,
        fee: float | None,
        net_pnl: float | None,
        quote_ccy: str,
        cumulative_gross_pnl: float | None = None,
        cumulative_net_pnl: float | None = None,
    ) -> None:
        ...


def compute_gmo_close_metrics(trade: TradeRecord) -> tuple[float | None, float, float | None]:
    position = _as_dict(trade.get("position"))
    execution = _as_dict(trade.get("execution"))
    exit_result = _as_dict(execution.get("exit_result"))

    realized_pnl = _to_float(execution.get("exit_leg_realized_pnl_jpy"))
    if realized_pnl is None:
        realized_pnl = _to_float(exit_result.get("realized_pnl_jpy"))
    event_fee = _to_float(exit_result.get("fee_jpy"))

    entry_quote = _to_float(position.get("quote_amount_jpy"))
    exit_quote = _to_float(exit_result.get("filled_quote_jpy"))
    quantity = _to_float(position.get("quantity_sol"))
    filled_base = _to_float(exit_result.get("filled_base_sol"))
    has_partial_size_mismatch = (
        realized_pnl is None
        and quantity is not None
        and filled_base is not None
        and abs(filled_base - quantity) > 1e-9
    )
    if has_partial_size_mismatch:
        exit_quote = None
    if exit_quote is None and not has_partial_size_mismatch:
        exit_price = _to_float(position.get("exit_price"))
        if quantity is not None and exit_price is not None:
            exit_quote = quantity * exit_price

    gross_pnl: float | None = realized_pnl
    direction = str(trade.get("direction") or "LONG")
    if gross_pnl is None and entry_quote is not None and exit_quote is not None:
        gross_pnl = entry_quote - exit_quote if direction == "SHORT" else exit_quote - entry_quote

    fee = event_fee
    if fee is None:
        fee = _to_float(execution.get("exit_fee_jpy"))
    if fee is None and gross_pnl is None:
        fee = (_to_float(execution.get("entry_fee_jpy")) or 0.0) + (_to_float(execution.get("exit_fee_jpy")) or 0.0)
    fee = fee or 0.0
    net_pnl = gross_pnl - fee if gross_pnl is not None else None
    return gross_pnl, fee, net_pnl


def compute_gmo_cumulative_close_metrics(trade: TradeRecord) -> tuple[float | None, float | None]:
    execution = _as_dict(trade.get("execution"))
    cumulative_gross_pnl = _to_float(execution.get("total_realized_pnl_jpy"))
    if cumulative_gross_pnl is None:
        cumulative_gross_pnl = _to_float(execution.get("realized_pnl_jpy"))
    if cumulative_gross_pnl is None:
        return None, None
    cumulative_fee = (_to_float(execution.get("entry_fee_jpy")) or 0.0) + (
        _to_float(execution.get("exit_fee_jpy")) or 0.0
    )
    return cumulative_gross_pnl, cumulative_gross_pnl - cumulative_fee


def notify_gmo_trade_closed(
    notifier: TradeCloseNotifier,
    *,
    model_id: str,
    default_pair: str,
    trade: TradeRecord,
) -> bool:
    trade_id = trade.get("trade_id")
    if not isinstance(trade_id, str) or trade_id.strip() == "":
        return False

    close_reason = str(trade.get("close_reason") or "")
    if close_reason not in ("TAKE_PROFIT", "STOP_LOSS"):
        return False

    position = _as_dict(trade.get("position"))
    gross_pnl, fee, net_pnl = compute_gmo_close_metrics(trade)
    cumulative_gross_pnl, cumulative_net_pnl = compute_gmo_cumulative_close_metrics(trade)
    notifier.notify_trade_closed(
        model_id=model_id,
        trade_id=trade_id,
        pair=str(trade.get("pair") or default_pair),
        direction=str(trade.get("direction") or ""),
        close_reason=close_reason,
        entry_price=_to_float(position.get("entry_price")),
        exit_price=_to_float(position.get("exit_price")),
        gross_pnl=gross_pnl,
        fee=fee,
        net_pnl=net_pnl,
        quote_ccy="JPY",
        cumulative_gross_pnl=cumulative_gross_pnl,
        cumulative_net_pnl=cumulative_net_pnl,
    )
    return True
