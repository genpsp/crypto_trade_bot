from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class GroupStats:
    label: str
    count: int
    win_count: int
    loss_count: int
    win_rate_pct: float
    sum_pnl_jpy: float
    avg_pnl_jpy: float


@dataclass(frozen=True)
class LossClusterRow:
    started_at_jst: str
    ended_at_jst: str
    trade_count: int
    total_pnl_jpy: float


@dataclass(frozen=True)
class AttributionResult:
    by_close_reason: list[GroupStats]
    by_direction: list[GroupStats]
    by_hour_of_day: list[GroupStats]
    by_day_of_week: list[GroupStats]
    loss_clusters: list[LossClusterRow]
    signal_compare: dict[str, dict[str, float | None]] = field(default_factory=dict)


def _group_stats(df: pd.DataFrame, group_column: str, label_formatter=None) -> list[GroupStats]:
    if df.empty or group_column not in df.columns:
        return []
    results: list[GroupStats] = []
    for key, sub in df.groupby(group_column, dropna=False):
        count = int(len(sub))
        if count == 0:
            continue
        win = int(sub["is_win"].sum())
        loss = int(sub["is_loss"].sum())
        win_rate = win / count * 100.0 if count > 0 else 0.0
        sum_pnl = float(sub["pnl_jpy"].fillna(0.0).sum())
        avg_pnl = sum_pnl / count if count > 0 else 0.0
        label = label_formatter(key) if label_formatter is not None else (str(key) if key is not None else "UNKNOWN")
        results.append(
            GroupStats(
                label=label,
                count=count,
                win_count=win,
                loss_count=loss,
                win_rate_pct=win_rate,
                sum_pnl_jpy=sum_pnl,
                avg_pnl_jpy=avg_pnl,
            )
        )
    results.sort(key=lambda item: item.label)
    return results


def _signal_compare(closed_df: pd.DataFrame) -> dict[str, dict[str, float | None]]:
    """Compare distribution of entry-time signals between winners and losers."""

    columns = ["ema_fast", "ema_slow", "entry_slippage_bps", "plan_r_multiple"]
    out: dict[str, dict[str, float | None]] = {}
    if closed_df.empty:
        return out
    winners = closed_df[closed_df["is_win"]]
    losers = closed_df[closed_df["is_loss"]]
    for column in columns:
        if column not in closed_df.columns:
            continue
        win_series = winners[column].dropna() if not winners.empty else pd.Series(dtype=float)
        loss_series = losers[column].dropna() if not losers.empty else pd.Series(dtype=float)
        if win_series.empty and loss_series.empty:
            continue
        out[column] = {
            "win_mean": float(win_series.mean()) if not win_series.empty else None,
            "win_median": float(win_series.median()) if not win_series.empty else None,
            "loss_mean": float(loss_series.mean()) if not loss_series.empty else None,
            "loss_median": float(loss_series.median()) if not loss_series.empty else None,
            "n_win": int(len(win_series)),
            "n_loss": int(len(loss_series)),
        }
    return out


def _loss_clusters(closed_df: pd.DataFrame, min_streak: int = 3) -> list[LossClusterRow]:
    if closed_df.empty:
        return []
    clusters: list[LossClusterRow] = []
    streak_start: pd.Timestamp | None = None
    streak_end: pd.Timestamp | None = None
    streak_count = 0
    streak_pnl = 0.0
    for _, row in closed_df.iterrows():
        if bool(row.get("is_loss")):
            if streak_count == 0:
                streak_start = row.get("exit_time_jst")
            streak_end = row.get("exit_time_jst")
            streak_count += 1
            pnl = row.get("pnl_jpy")
            if isinstance(pnl, (int, float)) and not pd.isna(pnl):
                streak_pnl += float(pnl)
        else:
            if streak_count >= min_streak and streak_start is not None and streak_end is not None:
                clusters.append(
                    LossClusterRow(
                        started_at_jst=_format_ts(streak_start),
                        ended_at_jst=_format_ts(streak_end),
                        trade_count=streak_count,
                        total_pnl_jpy=streak_pnl,
                    )
                )
            streak_count = 0
            streak_pnl = 0.0
            streak_start = None
            streak_end = None
    if streak_count >= min_streak and streak_start is not None and streak_end is not None:
        clusters.append(
            LossClusterRow(
                started_at_jst=_format_ts(streak_start),
                ended_at_jst=_format_ts(streak_end),
                trade_count=streak_count,
                total_pnl_jpy=streak_pnl,
            )
        )
    return clusters


def _format_ts(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return ""
        return value.strftime("%Y-%m-%d %H:%M")
    if value is None:
        return ""
    return str(value)


def compute_attribution(closed_df: pd.DataFrame) -> AttributionResult:
    if closed_df.empty:
        return AttributionResult(
            by_close_reason=[],
            by_direction=[],
            by_hour_of_day=[],
            by_day_of_week=[],
            loss_clusters=[],
            signal_compare={},
        )

    enriched = closed_df.copy()
    if "exit_time_jst" in enriched.columns:
        enriched["hour_of_day_jst"] = enriched["exit_time_jst"].dt.hour
        enriched["day_of_week_jst"] = enriched["exit_time_jst"].dt.day_name()

    by_reason = _group_stats(enriched, "close_reason")
    by_direction = _group_stats(enriched, "direction")
    by_hour = _group_stats(enriched, "hour_of_day_jst", label_formatter=lambda v: f"{int(v):02d}:00" if pd.notna(v) else "UNKNOWN")
    by_dow = _group_stats(enriched, "day_of_week_jst")
    clusters = _loss_clusters(enriched)
    signal_compare = _signal_compare(enriched)

    return AttributionResult(
        by_close_reason=by_reason,
        by_direction=by_direction,
        by_hour_of_day=by_hour,
        by_day_of_week=by_dow,
        loss_clusters=clusters,
        signal_compare=signal_compare,
    )
