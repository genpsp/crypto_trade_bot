"""Apply candidate entry filters to the trade_features.csv and measure their
out-of-sample effect via temporal split.

Splits the trade CSV into in-sample (first half) and out-of-sample (second
half) and reports baseline + per-filter WR/PnL on each. Helps detect
overfitting: filters that work in-sample but not out-of-sample are likely
spurious.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def _metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0, "wr": 0.0, "mean_pnl": 0.0, "sum_scaled": 0.0}
    return {
        "n": len(df),
        "wr": round(100 * df["win"].mean(), 2),
        "mean_pnl": round(df["pnl_pct"].mean(), 3),
        "sum_scaled": round(df["scaled_pnl_pct"].sum(), 2),
    }


def _apply(df: pd.DataFrame, expr: str) -> pd.DataFrame:
    if not expr:
        return df
    try:
        return df.query(expr)
    except Exception as error:
        print(f"  filter error '{expr}': {error}")
        return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--features-csv",
        default="research/data/runs/postmortem_v0/trade_features.csv",
    )
    parser.add_argument("--output", default="research/data/runs/postmortem_v0/filter_sweep.md")
    args = parser.parse_args()

    df = pd.read_csv(args.features_csv).sort_values("entry_time_utc").reset_index(drop=True)
    n = len(df)
    midpoint = n // 2
    in_sample = df.iloc[:midpoint].copy()
    out_sample = df.iloc[midpoint:].copy()
    print(f"Total trades: {n}  IS: {len(in_sample)}  OOS: {len(out_sample)}")

    filters: list[tuple[str, str]] = [
        ("baseline", ""),
        ("F-hour-not-evening", "jst_hour < 18"),
        ("F-hour-deep-night-only", "jst_hour < 6"),
        ("F-vol-ratio>=0.4", "volume_ratio_20 >= 0.4"),
        ("F-vol-ratio>=0.36", "volume_ratio_20 >= 0.36"),
        ("F-atr>=0.36", "atr_pct >= 0.36"),
        ("F-atr>=0.46", "atr_pct >= 0.46"),
        ("F-btc-4bar-abs>=0.3", "abs(btc_ret_4bar_pct) >= 0.3"),
        ("F-adx-16-32", "16 <= adx <= 32"),
        # Combined filters
        (
            "F-combo-1",
            "jst_hour < 18 and volume_ratio_20 >= 0.4 and atr_pct >= 0.36",
        ),
        (
            "F-combo-2",
            "jst_hour < 18 and volume_ratio_20 >= 0.4 and atr_pct >= 0.36 and abs(btc_ret_4bar_pct) >= 0.3",
        ),
        (
            "F-combo-3",
            "jst_hour < 18 and atr_pct >= 0.36 and 16 <= adx <= 32",
        ),
        # Direction-aware time-of-day filter
        (
            "F-dir-tod",
            "(direction == 'LONG' and jst_hour < 18) or (direction == 'SHORT' and jst_hour < 6)",
        ),
        (
            "F-dir-tod-loose",
            "(direction == 'LONG' and jst_hour < 18) or (direction == 'SHORT' and (jst_hour < 6 or 12 <= jst_hour < 18))",
        ),
        # Direction-aware combo with momentum + volume
        (
            "F-dir-tod+vol+atr",
            (
                "((direction == 'LONG' and jst_hour < 18) or "
                "(direction == 'SHORT' and jst_hour < 6)) "
                "and volume_ratio_20 >= 0.4 and atr_pct >= 0.36"
            ),
        ),
    ]

    rows: list[dict] = []
    for name, expr in filters:
        all_m = _metrics(_apply(df, expr))
        is_m = _metrics(_apply(in_sample, expr))
        oos_m = _metrics(_apply(out_sample, expr))
        rows.append(
            {
                "filter": name,
                "expr": expr or "(none)",
                "all_n": all_m["n"],
                "all_wr": all_m["wr"],
                "all_mean": all_m["mean_pnl"],
                "all_sum": all_m["sum_scaled"],
                "IS_n": is_m["n"],
                "IS_wr": is_m["wr"],
                "IS_mean": is_m["mean_pnl"],
                "IS_sum": is_m["sum_scaled"],
                "OOS_n": oos_m["n"],
                "OOS_wr": oos_m["wr"],
                "OOS_mean": oos_m["mean_pnl"],
                "OOS_sum": oos_m["sum_scaled"],
            }
        )

    out_df = pd.DataFrame(rows)
    cols = [
        "filter",
        "expr",
        "all_n",
        "all_wr",
        "all_mean",
        "all_sum",
        "IS_n",
        "IS_wr",
        "IS_mean",
        "IS_sum",
        "OOS_n",
        "OOS_wr",
        "OOS_mean",
        "OOS_sum",
    ]
    out_df = out_df[cols]

    # Render Markdown
    lines = [
        "# Filter sweep — temporal IS/OOS",
        "",
        f"- Total trades: **{n}** (IS = first {len(in_sample)}, OOS = last {len(out_sample)})",
        f"- Source: `{args.features_csv}`",
        "",
        "| " + " | ".join(cols) + " |",
        "|" + "|".join(["---"] * len(cols)) + "|",
    ]
    for _, r in out_df.iterrows():
        cells = []
        for col in cols:
            val = r[col]
            if isinstance(val, float):
                cells.append(f"{val:.2f}")
            else:
                cells.append(str(val))
        lines.append("| " + " | ".join(cells) + " |")

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote: {args.output}")
    print(out_df.to_string(index=False))


if __name__ == "__main__":
    main()
