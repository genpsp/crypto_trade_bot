from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
from statistics import mean, median
from typing import Any

from research.src.adapters.csv_bar_repository import write_json
from research.src.domain.backtest_engine import run_backtest
from research.src.infra.research_config import load_bot_config
from research.src.adapters.csv_bar_repository import read_bars_from_csv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run walk-forward backtest (train/test rolling windows)")
    parser.add_argument("--config", required=True, help="JSON config file path")
    parser.add_argument("--bars", required=True, help="OHLCV CSV file path")
    parser.add_argument("--train-days", type=float, default=180.0, help="train window days (default: 180)")
    parser.add_argument("--test-days", type=float, default=90.0, help="test window days (default: 90)")
    parser.add_argument(
        "--step-days",
        type=float,
        default=None,
        help="window step days (default: same as --test-days)",
    )
    parser.add_argument(
        "--max-windows",
        type=int,
        default=None,
        help="optional max number of windows to evaluate",
    )
    parser.add_argument(
        "--output",
        default="research/data/processed/walk_forward_latest.json",
        help="output report JSON path",
    )
    return parser.parse_args()


def _to_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _infer_bar_minutes(close_times: list[datetime]) -> int:
    if len(close_times) < 2:
        raise ValueError("walk-forward requires at least 2 OHLCV bars")
    deltas: list[int] = []
    for index in range(1, len(close_times)):
        delta_minutes = int((close_times[index] - close_times[index - 1]).total_seconds() / 60)
        if delta_minutes > 0:
            deltas.append(delta_minutes)
    if not deltas:
        raise ValueError("failed to infer bar interval")
    inferred = Counter(deltas).most_common(1)[0][0]
    if inferred <= 0 or 1440 % inferred != 0:
        raise ValueError(f"unsupported bar interval minutes: {inferred}")
    return inferred


def _split_contiguous_segments(close_times: list[datetime], expected_minutes: int) -> list[tuple[int, int]]:
    if not close_times:
        return []
    segments: list[tuple[int, int]] = []
    segment_start = 0
    for index in range(1, len(close_times)):
        delta_minutes = int((close_times[index] - close_times[index - 1]).total_seconds() / 60)
        if delta_minutes != expected_minutes:
            segments.append((segment_start, index - 1))
            segment_start = index
    segments.append((segment_start, len(close_times) - 1))
    return segments


def _aggregate_test_summaries(windows: list[dict[str, Any]]) -> dict[str, Any]:
    if not windows:
        return {
            "count": 0,
            "positive_ratio_pct": 0.0,
            "mean_total_scaled_pnl_pct": 0.0,
            "median_total_scaled_pnl_pct": 0.0,
            "min_total_scaled_pnl_pct": 0.0,
            "max_total_scaled_pnl_pct": 0.0,
            "mean_win_rate_pct": 0.0,
            "mean_closed_trades": 0.0,
            "mean_average_r_multiple": 0.0,
            "best_window_id": None,
            "worst_window_id": None,
        }

    test_pnls = [window["test"]["summary"]["total_scaled_pnl_pct"] for window in windows]
    win_rates = [window["test"]["summary"]["win_rate_pct"] for window in windows]
    closed_trades = [window["test"]["summary"]["closed_trades"] for window in windows]
    average_r_values = [window["test"]["summary"]["average_r_multiple"] for window in windows]
    positive_count = sum(1 for pnl in test_pnls if pnl > 0)

    best_window = max(windows, key=lambda item: item["test"]["summary"]["total_scaled_pnl_pct"])
    worst_window = min(windows, key=lambda item: item["test"]["summary"]["total_scaled_pnl_pct"])

    return {
        "count": len(windows),
        "positive_ratio_pct": (positive_count / len(windows)) * 100,
        "mean_total_scaled_pnl_pct": mean(test_pnls),
        "median_total_scaled_pnl_pct": median(test_pnls),
        "min_total_scaled_pnl_pct": min(test_pnls),
        "max_total_scaled_pnl_pct": max(test_pnls),
        "mean_win_rate_pct": mean(win_rates),
        "mean_closed_trades": mean(closed_trades),
        "mean_average_r_multiple": mean(average_r_values),
        "best_window_id": best_window["window_id"],
        "worst_window_id": worst_window["window_id"],
    }


def main() -> None:
    args = parse_args()
    config = load_bot_config(args.config)
    bars = read_bars_from_csv(args.bars)
    close_times = [bar.close_time for bar in bars]

    bar_minutes = _infer_bar_minutes(close_times)
    bars_per_day = int(1440 / bar_minutes)

    step_days = args.test_days if args.step_days is None else args.step_days
    train_bars = int(round(args.train_days * bars_per_day))
    test_bars = int(round(args.test_days * bars_per_day))
    step_bars = int(round(step_days * bars_per_day))

    if train_bars <= 0 or test_bars <= 0 or step_bars <= 0:
        raise ValueError(
            f"invalid windows: train_bars={train_bars}, test_bars={test_bars}, step_bars={step_bars}"
        )

    window_bars = train_bars + test_bars
    segments = _split_contiguous_segments(close_times, bar_minutes)

    window_results: list[dict[str, Any]] = []
    segment_summaries: list[dict[str, Any]] = []

    for segment_index, (segment_start, segment_end) in enumerate(segments):
        segment_length = segment_end - segment_start + 1
        windows_in_segment = 0

        if segment_length >= window_bars:
            cursor = segment_start
            while cursor + window_bars - 1 <= segment_end:
                train_start = cursor
                train_end = cursor + train_bars - 1
                test_start = train_end + 1
                test_end = test_start + test_bars - 1

                train_slice = bars[train_start : train_end + 1]
                test_slice = bars[test_start : test_end + 1]

                train_report = run_backtest(train_slice, config)
                test_report = run_backtest(test_slice, config)

                window_id = f"seg{segment_index}_w{windows_in_segment}"
                window_results.append(
                    {
                        "window_id": window_id,
                        "segment_index": segment_index,
                        "train": {
                            "start_close_time": _to_iso(train_slice[0].close_time),
                            "end_close_time": _to_iso(train_slice[-1].close_time),
                            "summary": train_report.summary.to_dict(),
                        },
                        "test": {
                            "start_close_time": _to_iso(test_slice[0].close_time),
                            "end_close_time": _to_iso(test_slice[-1].close_time),
                            "summary": test_report.summary.to_dict(),
                        },
                    }
                )

                windows_in_segment += 1
                cursor += step_bars

                if args.max_windows is not None and len(window_results) >= args.max_windows:
                    break

        segment_summaries.append(
            {
                "segment_index": segment_index,
                "start_close_time": _to_iso(close_times[segment_start]),
                "end_close_time": _to_iso(close_times[segment_end]),
                "bars": segment_length,
                "windows": windows_in_segment,
            }
        )

        if args.max_windows is not None and len(window_results) >= args.max_windows:
            break

    report = {
        "config_path": args.config,
        "bars_path": args.bars,
        "params": {
            "train_days": args.train_days,
            "test_days": args.test_days,
            "step_days": step_days,
            "max_windows": args.max_windows,
            "bar_minutes": bar_minutes,
            "bars_per_day": bars_per_day,
            "train_bars": train_bars,
            "test_bars": test_bars,
            "step_bars": step_bars,
            "window_bars": window_bars,
        },
        "segments": segment_summaries,
        "aggregate_test": _aggregate_test_summaries(window_results),
        "windows": window_results,
    }

    write_json(args.output, report)
    print("[research] walk-forward completed", report["aggregate_test"])
    print("[research] windows", len(window_results))
    print("[research] report", args.output)


if __name__ == "__main__":
    main()
