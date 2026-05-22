"""Post-mortem analysis: which features at entry time discriminate winners
from losers?

Workflow:
1. Run v0 (or any) strategy backtest on the full bars CSV
2. For each closed trade, compute features observable at entry time:
   - JST hour, day-of-week
   - ATR% / ADX / volume z-score / BTC recent return / etc.
3. Bin each feature, compute win rate + sample count per bin
4. Identify features whose top vs bottom bins have the largest WR spread,
   and emit suggested entry filters.

Output: Markdown report + CSV of per-trade features for further analysis.

Usage:

    python -m research.scripts.postmortem_trade_features \\
        --bars research/data/raw/soljpy_15m_to_2026_05.csv \\
        --btc-bars research/data/raw/btcjpy_15m_1y.csv \\
        --output-dir research/data/runs/postmortem_v0
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from apps.dex_bot.domain.model.types import OhlcvBar
from research.src.adapters.csv_bar_repository import read_bars_from_csv
from research.src.data.regime_tagger import attach_regime_tags
from research.src.domain.backtest_engine import run_backtest
from research.src.infra.research_config import load_bot_config

JST = timezone(timedelta(hours=9))


def _wilder_smooth(values: list[float], period: int) -> list[float]:
    n = len(values)
    if n == 0 or period <= 0:
        return [0.0] * n
    smoothed = [0.0] * n
    running = 0.0
    for i in range(n):
        if i < period - 1:
            running += values[i]
        elif i == period - 1:
            running += values[i]
            smoothed[i] = running / period
        else:
            prev = smoothed[i - 1]
            smoothed[i] = prev - (prev / period) + (values[i] / period)
    return smoothed


def _compute_adx_series(bars: list[OhlcvBar], period: int = 14) -> list[float]:
    n = len(bars)
    if n == 0:
        return []
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    tr = [bars[0].high - bars[0].low]
    for i in range(1, n):
        up = bars[i].high - bars[i - 1].high
        dn = bars[i - 1].low - bars[i].low
        plus_dm[i] = up if up > dn and up > 0 else 0.0
        minus_dm[i] = dn if dn > up and dn > 0 else 0.0
        tr.append(
            max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - bars[i - 1].close),
                abs(bars[i].low - bars[i - 1].close),
            )
        )
    tr_s = _wilder_smooth(tr, period)
    plus_s = _wilder_smooth(plus_dm, period)
    minus_s = _wilder_smooth(minus_dm, period)
    dx = [0.0] * n
    for i in range(n):
        if tr_s[i] <= 0:
            continue
        p = 100 * plus_s[i] / tr_s[i]
        m = 100 * minus_s[i] / tr_s[i]
        denom = p + m
        dx[i] = 100 * abs(p - m) / denom if denom > 0 else 0.0
    return _wilder_smooth(dx, period)


def _compute_atr_series(bars: list[OhlcvBar], period: int = 14) -> list[float]:
    n = len(bars)
    if n == 0:
        return []
    tr = [bars[0].high - bars[0].low]
    for i in range(1, n):
        tr.append(
            max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - bars[i - 1].close),
                abs(bars[i].low - bars[i - 1].close),
            )
        )
    out = [0.0] * n
    running = 0.0
    for i in range(n):
        if i < period:
            running += tr[i]
            if i == period - 1:
                out[i] = running / period
        else:
            running = running - tr[i - period] + tr[i]
            out[i] = running / period
    return out


def _build_bar_index(bars: list[OhlcvBar]) -> dict[datetime, int]:
    return {bar.open_time.astimezone(UTC): i for i, bar in enumerate(bars)}


def _parse_entry_time(entry_time_iso: str) -> datetime:
    return datetime.fromisoformat(entry_time_iso.replace("Z", "+00:00")).astimezone(UTC)


def _btc_recent_return_pct(
    btc_bars: list[OhlcvBar],
    btc_index_by_open: dict[datetime, int],
    entry_time_utc: datetime,
    lookback_bars: int,
) -> float | None:
    """SOL entry at T → BTC return between (T - lookback*15m) and T close.

    Uses BTC bar that opens at the same UTC time as the SOL entry bar (or the
    closest BTC bar at or before that time)."""
    # Find the BTC bar with the largest open_time <= entry_time_utc
    sorted_keys = sorted(btc_index_by_open.keys())
    # Linear scan would be slow; binary search via bisect
    import bisect

    idx = bisect.bisect_right(sorted_keys, entry_time_utc) - 1
    if idx < 0:
        return None
    btc_idx = btc_index_by_open[sorted_keys[idx]]
    if btc_idx < lookback_bars:
        return None
    prior_close = btc_bars[btc_idx - lookback_bars].close
    current_close = btc_bars[btc_idx].close
    if prior_close <= 0:
        return None
    return (current_close - prior_close) / prior_close * 100


def _features_for_trade(
    *,
    trade: dict,
    bars: list[OhlcvBar],
    sol_index_by_open: dict[datetime, int],
    adx_series: list[float],
    atr_series: list[float],
    btc_bars: list[OhlcvBar] | None,
    btc_index_by_open: dict[datetime, int] | None,
) -> dict[str, Any]:
    entry_utc = _parse_entry_time(trade["entry_time"])
    sol_idx = sol_index_by_open.get(entry_utc)
    if sol_idx is None or sol_idx < 1:
        return {}
    entry_bar = bars[sol_idx]
    prev_bar = bars[sol_idx - 1]

    entry_jst = entry_utc.astimezone(JST)
    atr_pct = (atr_series[sol_idx] / entry_bar.close * 100) if entry_bar.close > 0 else 0.0
    adx_value = adx_series[sol_idx]

    # 20-bar avg volume preceding entry
    vol_lookback = bars[max(0, sol_idx - 20) : sol_idx]
    avg_vol = (sum(b.volume for b in vol_lookback) / len(vol_lookback)) if vol_lookback else 0.0
    vol_ratio = (entry_bar.volume / avg_vol) if avg_vol > 0 else 0.0

    # last-N-bar return (sub-strategy momentum proxy)
    def _ret(nbar: int) -> float | None:
        if sol_idx < nbar:
            return None
        ref = bars[sol_idx - nbar].close
        return (entry_bar.close - ref) / ref * 100 if ref > 0 else None

    btc_ret_4bar = None
    btc_ret_16bar = None
    if btc_bars and btc_index_by_open is not None:
        btc_ret_4bar = _btc_recent_return_pct(
            btc_bars, btc_index_by_open, entry_utc, lookback_bars=4
        )
        btc_ret_16bar = _btc_recent_return_pct(
            btc_bars, btc_index_by_open, entry_utc, lookback_bars=16
        )

    direction = trade.get("entry_regime", {}).get("entry_direction") if trade.get(
        "entry_regime"
    ) else None
    # entry_regime may not carry direction; fall back to price-vs-stop sign
    if direction is None:
        direction = "LONG" if (trade.get("stop_price") or 0) < (trade.get("entry_price") or 0) else "SHORT"

    pnl = trade.get("pnl_pct") or 0.0
    return {
        "entry_time_utc": entry_utc.isoformat().replace("+00:00", "Z"),
        "jst_hour": entry_jst.hour,
        "jst_dow": entry_jst.weekday(),  # 0=Mon, 6=Sun
        "atr_pct": round(atr_pct, 4),
        "adx": round(adx_value, 2),
        "volume_ratio_20": round(vol_ratio, 3),
        "ret_4bar_pct": round(_ret(4) or 0.0, 3),
        "ret_16bar_pct": round(_ret(16) or 0.0, 3),
        "btc_ret_4bar_pct": round(btc_ret_4bar, 3) if btc_ret_4bar is not None else None,
        "btc_ret_16bar_pct": round(btc_ret_16bar, 3) if btc_ret_16bar is not None else None,
        "direction": direction,
        "exit_reason": trade.get("exit_reason"),
        "pnl_pct": round(pnl, 4),
        "scaled_pnl_pct": round(trade.get("scaled_pnl_pct") or 0.0, 4),
        "win": 1 if pnl > 0 else 0,
        "holding_bars": trade.get("holding_bars") or 0,
    }


def _bin_label(feature: str, value: Any) -> str | None:
    if value is None:
        return None
    if feature == "jst_hour":
        if 0 <= value < 6:
            return "deep-night 00-06"
        if 6 <= value < 12:
            return "morning 06-12"
        if 12 <= value < 18:
            return "afternoon 12-18"
        return "evening 18-24"
    if feature == "jst_dow":
        return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][value]
    return None


def _quantile_bin(values: list[float], n_bins: int) -> tuple[list[float], callable]:
    series = pd.Series(values).dropna()
    if len(series) < n_bins:
        return [], lambda v: None
    quantiles = [series.quantile(i / n_bins) for i in range(n_bins + 1)]

    def assign(v: float) -> str | None:
        if v is None:
            return None
        for i in range(n_bins):
            if v <= quantiles[i + 1] or i == n_bins - 1:
                return f"q{i + 1} [{quantiles[i]:.3f},{quantiles[i + 1]:.3f}]"
        return None

    return quantiles, assign


def _summarize_bins(
    df: pd.DataFrame, feature: str, bin_assign: callable
) -> pd.DataFrame:
    df = df.copy()
    df["bin"] = df[feature].apply(bin_assign)
    grouped = (
        df.dropna(subset=["bin"])
        .groupby("bin")
        .agg(
            n=("win", "size"),
            wr_pct=("win", lambda s: 100 * s.mean()),
            avg_pnl=("pnl_pct", "mean"),
            sum_scaled=("scaled_pnl_pct", "sum"),
        )
        .round(2)
        .reset_index()
    )
    return grouped


def _df_to_md(df: pd.DataFrame) -> str:
    if df.empty:
        return "(empty)"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, row in df.iterrows():
        cells = []
        for col in cols:
            val = row[col]
            if isinstance(val, float):
                cells.append(f"{val:.2f}")
            else:
                cells.append(str(val))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bars", default="research/data/raw/soljpy_15m_to_2026_05.csv"
    )
    parser.add_argument("--btc-bars", default="research/data/raw/btcjpy_15m_1y.csv")
    parser.add_argument(
        "--base-config",
        default="research/models/gmo_ema_pullback_15m_both_v0/config/current.json",
    )
    parser.add_argument(
        "--output-dir", default="research/data/runs/postmortem_v0"
    )
    parser.add_argument(
        "--strategy-name",
        default="ema_trend_pullback_15m_v0",
        help="Strategy to backtest (v0 or v2)",
    )
    args = parser.parse_args()

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    print("[postmortem] loading bars...")
    bars = read_bars_from_csv(args.bars)
    attach_regime_tags(bars)
    btc_bars = None
    btc_index_by_open = None
    if Path(args.btc_bars).exists():
        btc_bars = read_bars_from_csv(args.btc_bars)
        btc_index_by_open = _build_bar_index(btc_bars)
        print(f"[postmortem] loaded {len(btc_bars)} BTC bars")
    else:
        print(f"[postmortem] btc bars not found at {args.btc_bars}, skipping BTC features")

    base_config = load_bot_config(args.base_config)
    config = json.loads(json.dumps(base_config))
    config["execution"]["model_id"] = "ideal_v1"
    config["strategy"]["name"] = args.strategy_name

    print(f"[postmortem] running {args.strategy_name} backtest on {len(bars)} bars...")
    report = run_backtest(bars=bars, config=config)
    closed_trades = [t for t in report.trades if t.exit_reason != "OPEN"]
    print(f"[postmortem] closed trades: {len(closed_trades)}")

    print("[postmortem] computing ADX/ATR series...")
    adx_series = _compute_adx_series(bars, period=14)
    atr_series = _compute_atr_series(bars, period=14)
    sol_index_by_open = _build_bar_index(bars)

    print("[postmortem] extracting per-trade features...")
    rows: list[dict[str, Any]] = []
    for trade in closed_trades:
        trade_dict = trade.to_dict()
        feat = _features_for_trade(
            trade=trade_dict,
            bars=bars,
            sol_index_by_open=sol_index_by_open,
            adx_series=adx_series,
            atr_series=atr_series,
            btc_bars=btc_bars,
            btc_index_by_open=btc_index_by_open,
        )
        if feat:
            rows.append(feat)

    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("no per-trade features extracted")

    df.to_csv(output / "trade_features.csv", index=False)
    print(f"[postmortem] wrote {output}/trade_features.csv ({len(df)} rows)")

    # Overall baseline
    overall_wr = 100 * df["win"].mean()
    overall_n = len(df)
    print(f"\n## Overall: n={overall_n} WR={overall_wr:.2f}%")

    report_lines: list[str] = []
    report_lines.append(
        f"# Post-mortem feature analysis\n\n"
        f"- Strategy: `{args.strategy_name}`\n"
        f"- Bars: `{args.bars}` ({len(bars)} bars)\n"
        f"- Total closed trades: **{overall_n}**\n"
        f"- Overall WR: **{overall_wr:.2f}%**\n"
        f"- Overall mean PnL: **{df['pnl_pct'].mean():.3f}%**\n"
        f"- Overall sum scaled_pnl: **{df['scaled_pnl_pct'].sum():.2f}**\n"
    )

    # Group by categorical features
    for feature, label_fn in [
        ("jst_hour", lambda v: _bin_label("jst_hour", v)),
        ("jst_dow", lambda v: _bin_label("jst_dow", v)),
        ("direction", lambda v: v),
        ("exit_reason", lambda v: v),
    ]:
        df_cat = df.copy()
        df_cat["bin"] = df_cat[feature].apply(label_fn)
        grp = (
            df_cat.dropna(subset=["bin"])
            .groupby("bin")
            .agg(
                n=("win", "size"),
                wr_pct=("win", lambda s: 100 * s.mean()),
                avg_pnl=("pnl_pct", "mean"),
                sum_scaled=("scaled_pnl_pct", "sum"),
            )
            .round(2)
            .reset_index()
            .sort_values("wr_pct", ascending=False)
        )
        report_lines.append(f"\n## By `{feature}`\n\n{_df_to_md(grp)}\n")

    # Group by quantile bins for numeric features
    for feature in [
        "atr_pct",
        "adx",
        "volume_ratio_20",
        "ret_4bar_pct",
        "ret_16bar_pct",
        "btc_ret_4bar_pct",
        "btc_ret_16bar_pct",
        "holding_bars",
    ]:
        if feature not in df.columns or df[feature].dropna().empty:
            continue
        _, assigner = _quantile_bin(df[feature].dropna().tolist(), n_bins=5)
        grp = _summarize_bins(df, feature, assigner)
        if grp.empty:
            continue
        report_lines.append(f"\n## By `{feature}` (quintile)\n\n{_df_to_md(grp)}\n")

    # Cross: direction × jst_hour bucket
    df["jst_hour_bin"] = df["jst_hour"].apply(lambda v: _bin_label("jst_hour", v))
    cross = (
        df.groupby(["direction", "jst_hour_bin"])
        .agg(
            n=("win", "size"),
            wr_pct=("win", lambda s: 100 * s.mean()),
            avg_pnl=("pnl_pct", "mean"),
        )
        .round(2)
        .reset_index()
        .sort_values("wr_pct", ascending=False)
    )
    report_lines.append(
        f"\n## Cross `direction × jst_hour_bucket`\n\n{_df_to_md(cross)}\n"
    )

    (output / "report.md").write_text("\n".join(report_lines), encoding="utf-8")
    print(f"[postmortem] wrote {output}/report.md")


if __name__ == "__main__":
    main()
