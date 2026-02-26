from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any

LAMPORTS_PER_SOL = 1_000_000_000
JST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class DailySummaryWindow:
    target_date_jst: str
    start_utc: datetime
    end_utc: datetime


@dataclass(frozen=True)
class ModelDailyTradeSummary:
    model_id: str
    closed_trades: int
    win_trades: int
    loss_trades: int
    realized_pnl_usdc: float
    estimated_fees_usdc: float
    avg_slippage_bps: float
    slippage_samples: int
    failed_runs: int
    skipped_runs: int
    failed_trades: int
    canceled_trades: int

    @property
    def win_rate_pct(self) -> float:
        if self.closed_trades <= 0:
            return 0.0
        return (self.win_trades / self.closed_trades) * 100.0


@dataclass(frozen=True)
class DailyTradeSummaryReport:
    target_date_jst: str
    generated_at_jst: str
    model_summaries: list[ModelDailyTradeSummary]
    total_closed_trades: int
    total_win_trades: int
    total_loss_trades: int
    total_realized_pnl_usdc: float
    total_estimated_fees_usdc: float
    total_avg_slippage_bps: float
    total_failed_runs: int
    total_skipped_runs: int
    total_failed_trades: int
    total_canceled_trades: int

    @property
    def total_win_rate_pct(self) -> float:
        if self.total_closed_trades <= 0:
            return 0.0
        return (self.total_win_trades / self.total_closed_trades) * 100.0


def build_daily_summary_window(target_date_jst: str) -> DailySummaryWindow:
    target_date = date.fromisoformat(target_date_jst)
    start_jst = datetime.combine(target_date, time.min, tzinfo=JST)
    end_jst = start_jst + timedelta(days=1)
    return DailySummaryWindow(
        target_date_jst=target_date_jst,
        start_utc=start_jst.astimezone(UTC),
        end_utc=end_jst.astimezone(UTC),
    )


def iter_utc_day_ids(window: DailySummaryWindow) -> list[str]:
    day_ids: list[str] = []
    start_day = window.start_utc.date()
    end_day = (window.end_utc - timedelta(microseconds=1)).date()
    cursor = start_day
    while cursor <= end_day:
        day_ids.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return day_ids


def build_daily_summary_report(
    *,
    target_date_jst: str,
    generated_at_utc: datetime,
    model_payloads: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]],
) -> DailyTradeSummaryReport:
    window = build_daily_summary_window(target_date_jst)
    model_summaries = [
        build_model_daily_trade_summary(
            model_id=model_id,
            trades=trades,
            runs=runs,
            window=window,
        )
        for model_id, trades, runs in model_payloads
    ]

    total_closed = 0
    total_win = 0
    total_loss = 0
    total_pnl = 0.0
    total_fees = 0.0
    total_failed_runs = 0
    total_skipped_runs = 0
    total_failed_trades = 0
    total_canceled_trades = 0
    total_slippage_weight = 0.0
    total_slippage_samples = 0

    for summary in model_summaries:
        total_closed += summary.closed_trades
        total_win += summary.win_trades
        total_loss += summary.loss_trades
        total_pnl += summary.realized_pnl_usdc
        total_fees += summary.estimated_fees_usdc
        total_failed_runs += summary.failed_runs
        total_skipped_runs += summary.skipped_runs
        total_failed_trades += summary.failed_trades
        total_canceled_trades += summary.canceled_trades
        total_slippage_weight += summary.avg_slippage_bps * summary.slippage_samples
        total_slippage_samples += summary.slippage_samples

    total_avg_slippage = (
        total_slippage_weight / total_slippage_samples if total_slippage_samples > 0 else 0.0
    )
    generated_at_jst = _ensure_aware_utc(generated_at_utc).astimezone(JST).isoformat(timespec="seconds")
    return DailyTradeSummaryReport(
        target_date_jst=target_date_jst,
        generated_at_jst=generated_at_jst,
        model_summaries=model_summaries,
        total_closed_trades=total_closed,
        total_win_trades=total_win,
        total_loss_trades=total_loss,
        total_realized_pnl_usdc=round(total_pnl, 6),
        total_estimated_fees_usdc=round(total_fees, 6),
        total_avg_slippage_bps=round(total_avg_slippage, 4),
        total_failed_runs=total_failed_runs,
        total_skipped_runs=total_skipped_runs,
        total_failed_trades=total_failed_trades,
        total_canceled_trades=total_canceled_trades,
    )


def build_daily_trade_summary_report(
    *,
    target_date_jst: str,
    generated_at_utc: datetime,
    model_payloads: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]],
) -> DailyTradeSummaryReport:
    return build_daily_summary_report(
        target_date_jst=target_date_jst,
        generated_at_utc=generated_at_utc,
        model_payloads=model_payloads,
    )


def build_model_daily_trade_summary(
    *,
    model_id: str,
    trades: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    window: DailySummaryWindow,
) -> ModelDailyTradeSummary:
    closed_trades = 0
    win_trades = 0
    loss_trades = 0
    failed_runs = 0
    skipped_runs = 0
    failed_trades = 0
    canceled_trades = 0
    realized_pnl = 0.0
    estimated_fees = 0.0
    slippage_samples: list[float] = []

    for trade in trades:
        state = str(trade.get("state") or "")
        created_at = _resolve_trade_created_at(trade)
        if _is_in_window(created_at, window):
            if state == "FAILED":
                failed_trades += 1
            elif state == "CANCELED":
                canceled_trades += 1

        if state != "CLOSED":
            continue
        closed_at = _resolve_trade_closed_at(trade)
        if not _is_in_window(closed_at, window):
            continue

        closed_trades += 1
        pnl = _compute_trade_realized_pnl_usdc(trade)
        if pnl is not None:
            realized_pnl += pnl
            if pnl > 0:
                win_trades += 1
            elif pnl < 0:
                loss_trades += 1
        estimated_fees += _estimate_trade_fees_usdc(trade)
        slippage_samples.extend(_collect_trade_slippage_samples_bps(trade))

    for run in runs:
        run_at = _resolve_run_executed_at(run)
        if not _is_in_window(run_at, window):
            continue
        result = str(run.get("result") or "")
        if result == "FAILED":
            failed_runs += 1
        elif result in ("SKIPPED", "SKIPPED_ENTRY"):
            skipped_runs += 1

    avg_slippage_bps = sum(slippage_samples) / len(slippage_samples) if slippage_samples else 0.0
    return ModelDailyTradeSummary(
        model_id=model_id,
        closed_trades=closed_trades,
        win_trades=win_trades,
        loss_trades=loss_trades,
        realized_pnl_usdc=round(realized_pnl, 6),
        estimated_fees_usdc=round(estimated_fees, 6),
        avg_slippage_bps=round(avg_slippage_bps, 4),
        slippage_samples=len(slippage_samples),
        failed_runs=failed_runs,
        skipped_runs=skipped_runs,
        failed_trades=failed_trades,
        canceled_trades=canceled_trades,
    )


def _resolve_trade_created_at(trade: dict[str, Any]) -> datetime | None:
    return _parse_iso_datetime(trade.get("created_at")) or _parse_iso_datetime(trade.get("bar_close_time_iso"))


def _resolve_trade_closed_at(trade: dict[str, Any]) -> datetime | None:
    position = _as_dict(trade.get("position"))
    return (
        _parse_iso_datetime(position.get("exit_time_iso"))
        or _parse_iso_datetime(trade.get("updated_at"))
        or _parse_iso_datetime(trade.get("bar_close_time_iso"))
    )


def _resolve_run_executed_at(run: dict[str, Any]) -> datetime | None:
    return (
        _parse_iso_datetime(run.get("last_executed_at_iso"))
        or _parse_iso_datetime(run.get("executed_at_iso"))
        or _parse_iso_datetime(run.get("bar_close_time_iso"))
    )


def _compute_trade_realized_pnl_usdc(trade: dict[str, Any]) -> float | None:
    position = _as_dict(trade.get("position"))
    execution = _as_dict(trade.get("execution"))
    exit_result = _as_dict(execution.get("exit_result"))

    entry_quote = _to_float(position.get("quote_amount_usdc"))
    if entry_quote is None:
        return None

    exit_quote = _to_float(exit_result.get("spent_quote_usdc"))
    if exit_quote is None:
        quantity = _to_float(position.get("quantity_sol"))
        exit_price = _to_float(position.get("exit_price"))
        if quantity is None or exit_price is None:
            return None
        exit_quote = quantity * exit_price

    direction = str(trade.get("direction") or "LONG")
    if direction == "SHORT":
        return entry_quote - exit_quote
    return exit_quote - entry_quote


def _estimate_trade_fees_usdc(trade: dict[str, Any]) -> float:
    execution = _as_dict(trade.get("execution"))
    position = _as_dict(trade.get("position"))
    entry_fee = _to_float(execution.get("entry_fee_lamports")) or 0.0
    exit_fee = _to_float(execution.get("exit_fee_lamports")) or 0.0
    total_lamports = entry_fee + exit_fee
    if total_lamports <= 0:
        return 0.0

    price_ref = _to_float(position.get("exit_price")) or _to_float(position.get("entry_price"))
    if price_ref is None or price_ref <= 0:
        return 0.0
    return (total_lamports / LAMPORTS_PER_SOL) * price_ref


def _collect_trade_slippage_samples_bps(trade: dict[str, Any]) -> list[float]:
    position = _as_dict(trade.get("position"))
    samples: list[float] = []
    entry_trigger = _to_float(position.get("entry_trigger_price"))
    entry_price = _to_float(position.get("entry_price"))
    if entry_trigger is not None and entry_trigger > 0 and entry_price is not None:
        samples.append(abs(entry_price - entry_trigger) / entry_trigger * 10_000)

    exit_trigger = _to_float(position.get("exit_trigger_price"))
    exit_price = _to_float(position.get("exit_price"))
    if exit_trigger is not None and exit_trigger > 0 and exit_price is not None:
        samples.append(abs(exit_price - exit_trigger) / exit_trigger * 10_000)
    return samples


def _is_in_window(ts: datetime | None, window: DailySummaryWindow) -> bool:
    if ts is None:
        return False
    ts_utc = _ensure_aware_utc(ts)
    return window.start_utc <= ts_utc < window.end_utc


def _ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _ensure_aware_utc(parsed)


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}
