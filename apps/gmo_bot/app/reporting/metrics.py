from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class ReportMetrics:
    total_trades: int
    closed_trades: int
    win_trades: int
    loss_trades: int
    win_rate_pct: float
    gross_pnl_jpy: float
    fees_jpy: float
    net_pnl_jpy: float
    profit_factor: float | None
    avg_win_jpy: float | None
    avg_loss_jpy: float | None
    avg_rr: float | None
    avg_holding_minutes: float | None
    max_drawdown_jpy: float | None
    max_drawdown_pct: float | None
    longest_loss_streak: int
    sharpe_daily: float | None
    start_balance: float | None
    end_balance: float | None
    cumulative_return_pct: float | None
    avg_entry_slippage_bps: float | None
    avg_exit_slippage_bps: float | None


def compute_trade_pnl_jpy(row: pd.Series) -> float | None:
    """Cumulative realized PnL for a closed trade, matching the daily summary logic.

    Priority:
      1) execution.total_realized_pnl_jpy
      2) execution.exit_result.realized_pnl_jpy
      3) derived from entry_quote / exit price / direction
    """

    total = row.get("total_realized_pnl_jpy")
    if isinstance(total, (int, float)) and not pd.isna(total):
        return float(total)

    exit_result_pnl = row.get("exit_result_realized_pnl_jpy")
    if isinstance(exit_result_pnl, (int, float)) and not pd.isna(exit_result_pnl):
        return float(exit_result_pnl)

    quote_amount = row.get("quote_amount_jpy")
    quantity = row.get("quantity_sol")
    exit_price = row.get("exit_price")
    if (
        not isinstance(quote_amount, (int, float))
        or pd.isna(quote_amount)
        or not isinstance(quantity, (int, float))
        or pd.isna(quantity)
        or not isinstance(exit_price, (int, float))
        or pd.isna(exit_price)
    ):
        return None
    exit_quote = float(quantity) * float(exit_price)
    direction = str(row.get("direction") or "LONG")
    if direction == "SHORT":
        return float(quote_amount) - exit_quote
    return exit_quote - float(quote_amount)


def annotate_closed_trades(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame restricted to CLOSED trades with computed PnL and helpers."""

    if trades_df.empty:
        return trades_df.copy()

    df = trades_df[trades_df["state"] == "CLOSED"].copy()
    if df.empty:
        return df

    df["pnl_jpy"] = df.apply(compute_trade_pnl_jpy, axis=1)
    df["fee_total_jpy"] = (
        df["entry_fee_jpy"].fillna(0.0) + df["exit_fee_jpy"].fillna(0.0)
    )
    df["net_pnl_jpy"] = df["pnl_jpy"] - df["fee_total_jpy"]
    df["is_win"] = df["pnl_jpy"].fillna(0.0) > 0
    df["is_loss"] = df["pnl_jpy"].fillna(0.0) < 0

    holding = df["exit_time_jst"] - df["entry_time_jst"]
    df["holding_minutes"] = holding.dt.total_seconds() / 60.0

    df = df.sort_values("exit_time_jst").reset_index(drop=True)
    df["cumulative_pnl_jpy"] = df["pnl_jpy"].fillna(0.0).cumsum()
    return df


def _longest_loss_streak(closed_df: pd.DataFrame) -> int:
    longest = 0
    current = 0
    for is_loss in closed_df["is_loss"]:
        if bool(is_loss):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _max_drawdown_from_cum(cum: pd.Series) -> tuple[float | None, float | None]:
    """Return (drawdown_jpy, drawdown_pct) using running peak of cumulative PnL."""

    if cum.empty:
        return None, None
    running_peak = cum.cummax()
    drawdown = cum - running_peak
    max_dd = float(drawdown.min())
    peak_at_min = float(running_peak.loc[drawdown.idxmin()]) if not drawdown.empty else 0.0
    if peak_at_min > 0:
        dd_pct = max_dd / peak_at_min * 100.0
    else:
        dd_pct = None
    return max_dd, dd_pct


def _sharpe_daily(closed_df: pd.DataFrame) -> float | None:
    """Naive Sharpe-like ratio based on daily PnL (mean / std * sqrt(252))."""

    if closed_df.empty or "exit_time_jst" not in closed_df.columns:
        return None
    daily = (
        closed_df.dropna(subset=["exit_time_jst"])
        .assign(_d=lambda d: d["exit_time_jst"].dt.date)
        .groupby("_d")["pnl_jpy"]
        .sum()
    )
    if len(daily) < 2:
        return None
    std = float(daily.std(ddof=1))
    if std == 0.0 or pd.isna(std):
        return None
    return float(daily.mean()) / std * sqrt(252.0)


def compute_metrics(
    closed_df: pd.DataFrame,
    balances_df: pd.DataFrame,
    total_trades_count: int,
) -> ReportMetrics:
    win_trades = int(closed_df["is_win"].sum()) if not closed_df.empty else 0
    loss_trades = int(closed_df["is_loss"].sum()) if not closed_df.empty else 0
    closed = int(len(closed_df))
    win_rate = (win_trades / closed * 100.0) if closed > 0 else 0.0

    gross_pnl = float(closed_df["pnl_jpy"].fillna(0.0).sum()) if not closed_df.empty else 0.0
    fees = float(closed_df["fee_total_jpy"].fillna(0.0).sum()) if not closed_df.empty else 0.0
    net_pnl = gross_pnl - fees

    avg_win: float | None = None
    avg_loss: float | None = None
    profit_factor: float | None = None
    avg_rr: float | None = None
    if not closed_df.empty:
        wins = closed_df.loc[closed_df["is_win"], "pnl_jpy"].dropna()
        losses = closed_df.loc[closed_df["is_loss"], "pnl_jpy"].dropna()
        if not wins.empty:
            avg_win = float(wins.mean())
        if not losses.empty:
            avg_loss = float(losses.mean())
        loss_abs_sum = float(losses.abs().sum())
        win_sum = float(wins.sum())
        if loss_abs_sum > 0:
            profit_factor = win_sum / loss_abs_sum
        if avg_win is not None and avg_loss is not None and avg_loss != 0:
            avg_rr = abs(avg_win / avg_loss)

    avg_holding: float | None = None
    if not closed_df.empty and "holding_minutes" in closed_df.columns:
        holding = closed_df["holding_minutes"].dropna()
        if not holding.empty:
            avg_holding = float(holding.mean())

    max_dd, max_dd_pct = (None, None)
    if not closed_df.empty:
        max_dd, max_dd_pct = _max_drawdown_from_cum(closed_df["cumulative_pnl_jpy"])

    longest_streak = _longest_loss_streak(closed_df) if not closed_df.empty else 0
    sharpe = _sharpe_daily(closed_df) if not closed_df.empty else None

    start_balance: float | None = None
    end_balance: float | None = None
    cumulative_return: float | None = None
    if not balances_df.empty:
        balance_col = _pick_balance_column(balances_df)
        if balance_col is not None:
            values = balances_df[balance_col].dropna()
            if not values.empty:
                start_balance = float(values.iloc[0])
                end_balance = float(values.iloc[-1])
                if start_balance and start_balance != 0:
                    cumulative_return = (end_balance - start_balance) / start_balance * 100.0

    avg_entry_slip: float | None = None
    avg_exit_slip: float | None = None
    if not closed_df.empty:
        entry_slip = closed_df["entry_slippage_bps"].dropna()
        if not entry_slip.empty:
            avg_entry_slip = float(entry_slip.mean())
        exit_slip = closed_df["exit_slippage_bps"].dropna()
        if not exit_slip.empty:
            avg_exit_slip = float(exit_slip.mean())

    return ReportMetrics(
        total_trades=total_trades_count,
        closed_trades=closed,
        win_trades=win_trades,
        loss_trades=loss_trades,
        win_rate_pct=win_rate,
        gross_pnl_jpy=gross_pnl,
        fees_jpy=fees,
        net_pnl_jpy=net_pnl,
        profit_factor=profit_factor,
        avg_win_jpy=avg_win,
        avg_loss_jpy=avg_loss,
        avg_rr=avg_rr,
        avg_holding_minutes=avg_holding,
        max_drawdown_jpy=max_dd,
        max_drawdown_pct=max_dd_pct,
        longest_loss_streak=longest_streak,
        sharpe_daily=sharpe,
        start_balance=start_balance,
        end_balance=end_balance,
        cumulative_return_pct=cumulative_return,
        avg_entry_slippage_bps=avg_entry_slip,
        avg_exit_slippage_bps=avg_exit_slip,
    )


def _pick_balance_column(balances_df: pd.DataFrame) -> str | None:
    # equity_jpy を最優先: actualProfitLoss（評価損益込み時価総額）で正しい equity を使う
    # balance_jpy は availableAmount（取引余力のみ）なので SHORT/LONG 中に歪む
    for column in ("equity_jpy", "balance_jpy", "balance_total_usdc"):
        if column in balances_df.columns and balances_df[column].notna().any():
            return column
    return None
