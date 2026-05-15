from __future__ import annotations

import base64
from io import BytesIO
import os
from pathlib import Path
import tempfile

mpl_config_dir = Path(tempfile.gettempdir()) / "crypto_trade_bot_matplotlib"
mpl_config_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(mpl_config_dir))

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter

WIN_COLOR = "#2ca02c"
LOSS_COLOR = "#d62728"
NEUTRAL_COLOR = "#1f77b4"
ACCENT_COLOR = "#ff7f0e"


def _fig_to_base64_png(fig) -> str:
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def render_equity_curve(balances_df: pd.DataFrame, closed_df: pd.DataFrame) -> str | None:
    """Render equity curve. Prefer balance snapshots; fall back to cumulative trade PnL."""

    fig, ax = plt.subplots(figsize=(11, 4.5), dpi=100)
    plotted = False

    if not balances_df.empty:
        balance_col = None
        for column in ("balance_jpy", "balance_total_usdc"):
            if column in balances_df.columns and balances_df[column].notna().any():
                balance_col = column
                break
        if balance_col is not None:
            df = balances_df.dropna(subset=["snapshot_date_jst", balance_col]).copy()
            if not df.empty:
                df["date"] = pd.to_datetime(df["snapshot_date_jst"], errors="coerce")
                df = df.dropna(subset=["date"]).sort_values("date")
                ax.plot(df["date"], df[balance_col], marker="o", linewidth=2, color=NEUTRAL_COLOR, label=balance_col)
                plotted = True

    if not plotted and not closed_df.empty and "exit_time_jst" in closed_df.columns:
        df = closed_df.dropna(subset=["exit_time_jst"]).copy()
        if not df.empty:
            ax.plot(
                df["exit_time_jst"],
                df["cumulative_pnl_jpy"],
                marker="o",
                linewidth=2,
                color=NEUTRAL_COLOR,
                label="Cumulative PnL (JPY)",
            )
            plotted = True

    if not plotted:
        plt.close(fig)
        return None

    ax.set_title("Equity curve")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.6)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:,.0f}"))
    ax.legend(loc="best")
    fig.autofmt_xdate(rotation=0)
    return _fig_to_base64_png(fig)


def render_drawdown(closed_df: pd.DataFrame) -> str | None:
    if closed_df.empty or "cumulative_pnl_jpy" not in closed_df.columns:
        return None
    df = closed_df.dropna(subset=["exit_time_jst"]).copy()
    if df.empty:
        return None

    running_peak = df["cumulative_pnl_jpy"].cummax()
    drawdown = df["cumulative_pnl_jpy"] - running_peak

    fig, ax = plt.subplots(figsize=(11, 3.5), dpi=100)
    ax.fill_between(df["exit_time_jst"], drawdown, 0, color=LOSS_COLOR, alpha=0.45, label="Drawdown")
    ax.plot(df["exit_time_jst"], drawdown, color=LOSS_COLOR, linewidth=1.2)
    ax.set_title("Drawdown (JPY)")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.6)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:,.0f}"))
    fig.autofmt_xdate(rotation=0)
    return _fig_to_base64_png(fig)


def render_daily_pnl_bars(closed_df: pd.DataFrame) -> str | None:
    if closed_df.empty:
        return None
    df = closed_df.dropna(subset=["exit_time_jst"]).copy()
    if df.empty:
        return None
    df["date"] = df["exit_time_jst"].dt.date
    daily = df.groupby("date", as_index=False)["pnl_jpy"].sum()
    if daily.empty:
        return None

    colors = [WIN_COLOR if v >= 0 else LOSS_COLOR for v in daily["pnl_jpy"]]
    fig, ax = plt.subplots(figsize=(11, 3.5), dpi=100)
    ax.bar(daily["date"], daily["pnl_jpy"], color=colors, width=0.8)
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_title("Daily PnL (JPY)")
    ax.grid(True, axis="y", linestyle="--", linewidth=0.5, alpha=0.6)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:,.0f}"))
    fig.autofmt_xdate(rotation=0)
    return _fig_to_base64_png(fig)


def render_pnl_distribution(closed_df: pd.DataFrame) -> str | None:
    if closed_df.empty or closed_df["pnl_jpy"].dropna().empty:
        return None
    pnls = closed_df["pnl_jpy"].dropna()
    fig, ax = plt.subplots(figsize=(8, 4), dpi=100)
    bins = max(10, min(40, int(len(pnls) ** 0.5) * 3))
    ax.hist(pnls[pnls >= 0], bins=bins, color=WIN_COLOR, alpha=0.7, label="Winners")
    ax.hist(pnls[pnls < 0], bins=bins, color=LOSS_COLOR, alpha=0.7, label="Losers")
    ax.axvline(0, color="black", linewidth=0.6)
    ax.set_title("PnL distribution per trade (JPY)")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.6)
    ax.legend()
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:,.0f}"))
    return _fig_to_base64_png(fig)


def render_holding_time_distribution(closed_df: pd.DataFrame) -> str | None:
    if closed_df.empty or "holding_minutes" not in closed_df.columns:
        return None
    df = closed_df.dropna(subset=["holding_minutes"])
    if df.empty:
        return None
    winners = df.loc[df["is_win"], "holding_minutes"]
    losers = df.loc[df["is_loss"], "holding_minutes"]
    if winners.empty and losers.empty:
        return None

    fig, ax = plt.subplots(figsize=(8, 4), dpi=100)
    bins = max(10, min(30, int(len(df) ** 0.5) * 2))
    if not winners.empty:
        ax.hist(winners, bins=bins, color=WIN_COLOR, alpha=0.6, label=f"Winners (n={len(winners)})")
    if not losers.empty:
        ax.hist(losers, bins=bins, color=LOSS_COLOR, alpha=0.6, label=f"Losers (n={len(losers)})")
    ax.set_title("Holding time distribution (minutes)")
    ax.set_xlabel("Holding minutes")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.6)
    ax.legend()
    return _fig_to_base64_png(fig)


def render_hour_dow_heatmap(closed_df: pd.DataFrame) -> str | None:
    if closed_df.empty or "exit_time_jst" not in closed_df.columns:
        return None
    df = closed_df.dropna(subset=["exit_time_jst"]).copy()
    if df.empty:
        return None
    df["hour"] = df["exit_time_jst"].dt.hour
    df["dow"] = df["exit_time_jst"].dt.dayofweek  # 0=Mon
    grouped = df.groupby(["dow", "hour"]).agg(count=("pnl_jpy", "size"), wins=("is_win", "sum")).reset_index()
    grouped["win_rate"] = grouped["wins"] / grouped["count"] * 100.0

    matrix = np.full((7, 24), np.nan, dtype=float)
    counts = np.zeros((7, 24), dtype=int)
    for _, row in grouped.iterrows():
        matrix[int(row["dow"]), int(row["hour"])] = float(row["win_rate"])
        counts[int(row["dow"]), int(row["hour"])] = int(row["count"])

    fig, ax = plt.subplots(figsize=(11, 3.5), dpi=100)
    cmap = plt.get_cmap("RdYlGn")
    im = ax.imshow(matrix, aspect="auto", cmap=cmap, vmin=0, vmax=100, origin="upper")
    ax.set_title("Win-rate by hour x weekday (JST)")
    ax.set_yticks(range(7))
    ax.set_yticklabels(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    ax.set_xticks(range(0, 24, 2))
    ax.set_xticklabels([f"{h:02d}" for h in range(0, 24, 2)])
    ax.set_xlabel("Hour (JST)")
    for r in range(7):
        for c in range(24):
            if counts[r, c] > 0:
                ax.text(c, r, str(counts[r, c]), ha="center", va="center", fontsize=7, color="black")
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("Win-rate (%)")
    return _fig_to_base64_png(fig)


def render_slippage_distribution(closed_df: pd.DataFrame) -> str | None:
    if closed_df.empty:
        return None
    entry = closed_df["entry_slippage_bps"].dropna()
    exit_ = closed_df["exit_slippage_bps"].dropna()
    if entry.empty and exit_.empty:
        return None
    fig, ax = plt.subplots(figsize=(8, 4), dpi=100)
    data = []
    labels = []
    if not entry.empty:
        data.append(entry)
        labels.append(f"Entry (n={len(entry)})")
    if not exit_.empty:
        data.append(exit_)
        labels.append(f"Exit (n={len(exit_)})")
    ax.boxplot(data, labels=labels, vert=True, showmeans=True)
    ax.set_title("Slippage distribution (bps)")
    ax.set_ylabel("bps")
    ax.grid(True, axis="y", linestyle="--", linewidth=0.5, alpha=0.6)
    return _fig_to_base64_png(fig)


def render_signal_boxplot(closed_df: pd.DataFrame, column: str, title: str) -> str | None:
    if closed_df.empty or column not in closed_df.columns:
        return None
    winners = closed_df.loc[closed_df["is_win"], column].dropna()
    losers = closed_df.loc[closed_df["is_loss"], column].dropna()
    if winners.empty and losers.empty:
        return None
    fig, ax = plt.subplots(figsize=(6, 4), dpi=100)
    data = []
    labels = []
    if not winners.empty:
        data.append(winners)
        labels.append(f"Win (n={len(winners)})")
    if not losers.empty:
        data.append(losers)
        labels.append(f"Loss (n={len(losers)})")
    bp = ax.boxplot(data, labels=labels, vert=True, showmeans=True, patch_artist=True)
    for patch, color in zip(bp["boxes"], (WIN_COLOR, LOSS_COLOR)):
        patch.set_facecolor(color)
        patch.set_alpha(0.5)
    ax.set_title(title)
    ax.grid(True, axis="y", linestyle="--", linewidth=0.5, alpha=0.6)
    return _fig_to_base64_png(fig)
