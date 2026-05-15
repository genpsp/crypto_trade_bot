from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pandas as pd

from apps.gmo_bot.adapters.persistence.firestore_repo import FirestoreRepository
from apps.gmo_bot.app.reporting.attribution import compute_attribution
from apps.gmo_bot.app.reporting.dataset import (
    PerformanceDataset,
    ReportContext,
    build_balances_dataframe,
    build_runs_dataframe,
    build_trades_dataframe,
)
from apps.gmo_bot.app.reporting.metrics import annotate_closed_trades, compute_metrics
from apps.gmo_bot.infra.reporting.chart_renderer import (
    render_daily_pnl_bars,
    render_drawdown,
    render_equity_curve,
    render_holding_time_distribution,
    render_hour_dow_heatmap,
    render_pnl_distribution,
    render_signal_boxplot,
    render_slippage_distribution,
)
from apps.gmo_bot.infra.reporting.html_renderer import render_report_html

JST = timezone(timedelta(hours=9))
MAX_FAILED_RUNS_DISPLAYED = 30
MAX_NO_SIGNAL_REASONS_DISPLAYED = 10
MAX_SKIPPED_REASONS_DISPLAYED = 20


@dataclass(frozen=True)
class GenerateReportRequest:
    model_id: str
    mode: str
    from_date_jst: str
    to_date_jst: str


@dataclass(frozen=True)
class GenerateReportResult:
    html: str
    context: ReportContext
    headline: str  # short text summary suitable for Slack/log


def _load_dataset(repo: FirestoreRepository, request: GenerateReportRequest) -> PerformanceDataset:
    trades = repo.list_trades_in_range(request.from_date_jst, request.to_date_jst)
    runs = repo.list_runs_in_range(request.from_date_jst, request.to_date_jst)
    balances = repo.list_daily_balances_in_range(request.from_date_jst, request.to_date_jst)

    context = ReportContext(
        model_id=request.model_id,
        mode=request.mode,
        from_date_jst=request.from_date_jst,
        to_date_jst=request.to_date_jst,
        generated_at_jst=datetime.now(tz=UTC).astimezone(JST).isoformat(timespec="seconds"),
    )

    return PerformanceDataset(
        context=context,
        trades=trades,
        runs=runs,
        balances=balances,
        trades_df=build_trades_dataframe(trades),
        runs_df=build_runs_dataframe(runs),
        balances_df=build_balances_dataframe(balances),
    )


def _build_decision_log(runs_df: pd.DataFrame) -> dict[str, Any]:
    if runs_df.empty:
        return {"no_signal_reasons": [], "skipped_reasons": [], "failed_runs": []}

    no_signal = runs_df[runs_df["result"] == "NO_SIGNAL"].copy()
    no_signal_counts: list[tuple[str, int]] = []
    if not no_signal.empty:
        no_signal["__label"] = no_signal["reason"].fillna(no_signal["summary"]).fillna("UNKNOWN")
        counts = no_signal.groupby("__label")["occurrence_count"].sum().sort_values(ascending=False)
        no_signal_counts = [(str(label), int(count)) for label, count in counts.head(MAX_NO_SIGNAL_REASONS_DISPLAYED).items()]

    skipped = runs_df[runs_df["result"].isin(["SKIPPED", "SKIPPED_ENTRY"])].copy()
    skipped_rows: list[tuple[str, str, int]] = []
    if not skipped.empty:
        skipped["__label"] = skipped["reason"].fillna(skipped["summary"]).fillna("UNKNOWN")
        grouped = (
            skipped.groupby(["result", "__label"])["occurrence_count"].sum().sort_values(ascending=False)
        )
        for (result, label), count in grouped.head(MAX_SKIPPED_REASONS_DISPLAYED).items():
            skipped_rows.append((str(result), str(label), int(count)))

    failed = runs_df[runs_df["result"] == "FAILED"].copy()
    failed_rows: list[dict[str, str]] = []
    if not failed.empty:
        failed = failed.sort_values("executed_at_jst", ascending=False).head(MAX_FAILED_RUNS_DISPLAYED)
        for _, row in failed.iterrows():
            executed_at = row.get("executed_at_jst")
            failed_rows.append(
                {
                    "executed_at_jst": executed_at.strftime("%Y-%m-%d %H:%M:%S") if isinstance(executed_at, pd.Timestamp) and not pd.isna(executed_at) else "",
                    "summary": str(row.get("summary") or "")[:200],
                    "reason": str(row.get("reason") or "")[:200],
                }
            )

    return {
        "no_signal_reasons": no_signal_counts,
        "skipped_reasons": skipped_rows,
        "failed_runs": failed_rows,
    }


def _build_trade_table(closed_df: pd.DataFrame) -> list[dict[str, Any]]:
    if closed_df.empty:
        return []
    rows: list[dict[str, Any]] = []
    sorted_df = closed_df.sort_values("exit_time_jst", ascending=False)
    for _, row in sorted_df.iterrows():
        entry_ts = row.get("entry_time_jst")
        exit_ts = row.get("exit_time_jst")
        rows.append(
            {
                "entry_time": entry_ts.strftime("%Y-%m-%d %H:%M") if isinstance(entry_ts, pd.Timestamp) and not pd.isna(entry_ts) else "",
                "exit_time": exit_ts.strftime("%Y-%m-%d %H:%M") if isinstance(exit_ts, pd.Timestamp) and not pd.isna(exit_ts) else "",
                "direction": row.get("direction") or "",
                "quantity_sol": row.get("quantity_sol"),
                "entry_price": row.get("entry_price"),
                "exit_price": row.get("exit_price"),
                "fee_total_jpy": row.get("fee_total_jpy"),
                "pnl_jpy": row.get("pnl_jpy"),
                "close_reason": row.get("close_reason") or "",
                "entry_slippage_bps": row.get("entry_slippage_bps"),
                "exit_slippage_bps": row.get("exit_slippage_bps"),
            }
        )
    return rows


def _build_charts(closed_df: pd.DataFrame, balances_df: pd.DataFrame) -> dict[str, str | None]:
    return {
        "equity_curve": render_equity_curve(balances_df, closed_df),
        "drawdown": render_drawdown(closed_df),
        "daily_pnl": render_daily_pnl_bars(closed_df),
        "pnl_distribution": render_pnl_distribution(closed_df),
        "holding_time": render_holding_time_distribution(closed_df),
        "hour_dow_heatmap": render_hour_dow_heatmap(closed_df),
        "slippage": render_slippage_distribution(closed_df),
        "signal_ema_fast": render_signal_boxplot(closed_df, "ema_fast", "EMA fast at entry"),
        "signal_r_multiple": render_signal_boxplot(closed_df, "plan_r_multiple", "Plan R-multiple"),
    }


def _headline(metrics, context: ReportContext) -> str:
    pnl_sign = "+" if (metrics.net_pnl_jpy or 0) >= 0 else ""
    return (
        f"[{context.model_id}] {context.from_date_jst}–{context.to_date_jst} "
        f"({context.mode}) "
        f"trades={metrics.closed_trades} win_rate={metrics.win_rate_pct:.1f}% "
        f"net_pnl={pnl_sign}{(metrics.net_pnl_jpy or 0):,.0f} JPY"
    )


def generate_report(repo: FirestoreRepository, request: GenerateReportRequest) -> GenerateReportResult:
    dataset = _load_dataset(repo, request)
    closed_df = annotate_closed_trades(dataset.trades_df)
    metrics = compute_metrics(closed_df, dataset.balances_df, total_trades_count=len(dataset.trades))
    attribution = compute_attribution(closed_df)
    charts = _build_charts(closed_df, dataset.balances_df)
    decision_log = _build_decision_log(dataset.runs_df)
    trade_table = _build_trade_table(closed_df)

    html = render_report_html(
        {
            "context": dataset.context,
            "metrics": metrics,
            "attribution": attribution,
            "charts": charts,
            "decision_log": decision_log,
            "trade_table": trade_table,
        }
    )

    return GenerateReportResult(html=html, context=dataset.context, headline=_headline(metrics, dataset.context))
