from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from apps.gmo_bot.domain.model.types import DailyBalanceRecord, RunRecord, TradeRecord
from apps.gmo_bot.domain.utils.coercion import to_float as _to_float

JST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class ReportContext:
    model_id: str
    mode: str  # "LIVE" or "PAPER"
    from_date_jst: str
    to_date_jst: str
    generated_at_jst: str


@dataclass(frozen=True)
class PerformanceDataset:
    context: ReportContext
    trades: list[TradeRecord]
    runs: list[RunRecord]
    balances: list[DailyBalanceRecord]
    trades_df: pd.DataFrame
    runs_df: pd.DataFrame
    balances_df: pd.DataFrame


def _to_jst_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(JST)


def _nested_get(record: dict[str, Any], *path: str) -> Any:
    cursor: Any = record
    for key in path:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
    return cursor


# §9.2: ``_to_float`` is now imported from ``apps.gmo_bot.domain.utils.coercion``.


def build_trades_dataframe(trades: list[TradeRecord]) -> pd.DataFrame:
    """Flatten relevant trade fields into a pandas DataFrame for analysis.

    Includes both closed and non-closed trades; downstream filters on `state`.
    """

    rows: list[dict[str, Any]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        position = trade.get("position") if isinstance(trade.get("position"), dict) else {}
        execution = trade.get("execution") if isinstance(trade.get("execution"), dict) else {}
        exit_result = execution.get("exit_result") if isinstance(execution.get("exit_result"), dict) else {}
        signal = trade.get("signal") if isinstance(trade.get("signal"), dict) else {}
        plan = trade.get("plan") if isinstance(trade.get("plan"), dict) else {}

        entry_time = _to_jst_datetime(position.get("entry_time_iso") or trade.get("created_at"))
        exit_time = _to_jst_datetime(position.get("exit_time_iso") or trade.get("updated_at"))

        entry_price = _to_float(position.get("entry_price"))
        exit_price = _to_float(position.get("exit_price"))
        entry_reference = _to_float(execution.get("entry_reference_price"))
        exit_reference = _to_float(execution.get("exit_reference_price"))

        entry_slippage_bps: float | None = None
        if entry_reference is not None and entry_reference > 0 and entry_price is not None:
            entry_slippage_bps = abs(entry_price - entry_reference) / entry_reference * 10_000

        exit_slippage_bps: float | None = None
        if exit_reference is not None and exit_reference > 0 and exit_price is not None:
            exit_slippage_bps = abs(exit_price - exit_reference) / exit_reference * 10_000

        rows.append(
            {
                "trade_id": trade.get("trade_id"),
                "model_id": trade.get("model_id"),
                "pair": trade.get("pair"),
                "direction": trade.get("direction"),
                "state": trade.get("state"),
                "close_reason": trade.get("close_reason"),
                "config_version": trade.get("config_version"),
                "trade_date": trade.get("trade_date"),
                "entry_time_jst": entry_time,
                "exit_time_jst": exit_time,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "entry_reference_price": entry_reference,
                "exit_reference_price": exit_reference,
                "entry_slippage_bps": entry_slippage_bps,
                "exit_slippage_bps": exit_slippage_bps,
                "quantity_sol": _to_float(position.get("quantity_sol")),
                "quote_amount_jpy": _to_float(position.get("quote_amount_jpy")),
                "stop_price": _to_float(position.get("stop_price")),
                "take_profit_price": _to_float(position.get("take_profit_price")),
                "entry_fee_jpy": _to_float(execution.get("entry_fee_jpy")),
                "exit_fee_jpy": _to_float(execution.get("exit_fee_jpy")),
                "total_realized_pnl_jpy": _to_float(execution.get("total_realized_pnl_jpy")),
                "exit_leg_realized_pnl_jpy": _to_float(execution.get("exit_leg_realized_pnl_jpy")),
                "exit_result_realized_pnl_jpy": _to_float(exit_result.get("realized_pnl_jpy")),
                "ema_fast": _to_float(signal.get("ema_fast")),
                "ema_slow": _to_float(signal.get("ema_slow")),
                "signal_summary": signal.get("summary"),
                "plan_r_multiple": _to_float(plan.get("r_multiple")),
                "bar_close_time_iso": trade.get("bar_close_time_iso"),
                "created_at_jst": _to_jst_datetime(trade.get("created_at")),
                "updated_at_jst": _to_jst_datetime(trade.get("updated_at")),
            }
        )

    df = pd.DataFrame(rows)
    return df


def build_runs_dataframe(runs: list[RunRecord]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        executed_at = _to_jst_datetime(
            run.get("last_executed_at_iso") or run.get("executed_at_iso") or run.get("bar_close_time_iso")
        )
        occurrence = run.get("occurrence_count")
        if isinstance(occurrence, bool) or not isinstance(occurrence, (int, float)):
            occurrence_count = 1
        else:
            occurrence_count = max(int(occurrence), 1)
        rows.append(
            {
                "run_id": run.get("run_id"),
                "model_id": run.get("model_id"),
                "result": run.get("result"),
                "summary": run.get("summary"),
                "reason": run.get("reason"),
                "trade_id": run.get("trade_id"),
                "config_version": run.get("config_version"),
                "occurrence_count": occurrence_count,
                "executed_at_jst": executed_at,
                "run_date": run.get("run_date"),
            }
        )
    return pd.DataFrame(rows)


def build_balances_dataframe(balances: list[DailyBalanceRecord]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in balances:
        if not isinstance(record, dict):
            continue
        rows.append(
            {
                "snapshot_date_jst": record.get("snapshot_date_jst"),
                "snapshot_at_iso": record.get("snapshot_at_iso"),
                "equity_jpy": _to_float(record.get("equity_jpy")),
                "balance_jpy": _to_float(record.get("balance_jpy")),
                "balance_total_usdc": _to_float(record.get("balance_total_usdc")),
                "cumulative_realized_pnl_jpy": _to_float(record.get("cumulative_realized_pnl_jpy")),
                "cumulative_realized_pnl_usdc": _to_float(record.get("cumulative_realized_pnl_usdc")),
                "source": record.get("source"),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty and "snapshot_date_jst" in df.columns:
        df = df.sort_values("snapshot_date_jst").reset_index(drop=True)
    return df
