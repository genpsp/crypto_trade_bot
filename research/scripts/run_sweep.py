from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research.src.adapters.csv_bar_repository import read_bars_from_csv
from research.src.data.market_dataset import MarketDataset
from research.src.eval.runner import run_trials
from research.src.store.lineage import build_run_id, capture_git_sha, now_utc_iso
from research.src.store.trial_store import TrialStore
from research.src.sweep.plan import build_plan
from research.src.sweep.spec_loader import SweepSpec, load_sweep_spec
from research.src.eval.statistics import percentile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run declarative research sweep")
    parser.add_argument("--spec", required=True, help="sweep YAML path")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--runs-root", default="research/data/runs")
    parser.add_argument("--run-id", default=None, help="override generated run_id")
    parser.add_argument("--output", default=None, help="optional legacy JSON mirror path")
    parser.add_argument("--max-trials", type=int, default=None, help="optional trial cap for smoke tests")
    parser.add_argument(
        "--keep-trades",
        choices=["none", "on-error", "all"],
        default="none",
        help="trade detail retention mode; writes trades/{trial_id}.parquet when retained",
    )
    return parser.parse_args()


def _resolve_path(raw: str | Path, *, spec: SweepSpec) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.exists():
        return candidate
    relative = spec.source_path.parent / candidate
    if relative.exists():
        return relative
    return candidate


def _load_dataset(spec: SweepSpec) -> MarketDataset:
    dataset_spec = spec.dataset
    broker = str(dataset_spec["broker"])
    pair = str(dataset_spec["pair"])
    timeframe = str(dataset_spec["timeframe"])
    bars_path = dataset_spec.get("bars_path")
    if bars_path:
        bars = read_bars_from_csv(_resolve_path(str(bars_path), spec=spec))
        return MarketDataset.from_bars(broker=broker, pair=pair, timeframe=timeframe, bars=bars)
    return MarketDataset.load(
        broker=broker,
        pair=pair,
        timeframe=timeframe,
        cache_root=str(dataset_spec.get("cache_root", "research/data/cache")),
        last_n_days=dataset_spec.get("last_n_days"),
    )


def _series_stats(values: list[float]) -> dict[str, float]:
    return {
        "mean": sum(values) / len(values),
        "p05": percentile(values, 0.05),
        "p95": percentile(values, 0.95),
    }


def _seed_group_key(row: dict[str, Any]) -> tuple[Any, ...]:
    tags = row.get("tags") if isinstance(row.get("tags"), dict) else {}
    window = row.get("window") if isinstance(row.get("window"), dict) else {}
    return (
        tags.get("spec_name"),
        tags.get("case_index"),
        tags.get("case_name"),
        window.get("window_id"),
        window.get("role"),
        tags.get("execution_model_id"),
    )


def _add_seed_aggregates(rows: list[dict[str, Any]]) -> None:
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("error") is None:
            buckets[_seed_group_key(row)].append(row)
    metric_names = [
        "total_scaled_pnl_pct",
        "total_scaled_pnl_pct_ci_low",
        "return_to_dd",
        "return_to_dd_ci_low",
        "average_r_multiple",
        "deflated_sharpe",
        "dsr_p_value",
    ]
    for group_rows in buckets.values():
        for metric in metric_names:
            values = []
            for row in group_rows:
                summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
                value = summary.get(metric)
                if isinstance(value, (int, float)):
                    values.append(float(value))
            if not values:
                continue
            stats = _series_stats(values)
            for row in group_rows:
                summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
                summary["seed_count"] = len(group_rows)
                for stat_name, stat_value in stats.items():
                    summary[f"{metric}_seed_{stat_name}"] = round(stat_value, 6)


def _wf_group_key(row: dict[str, Any]) -> tuple[Any, ...]:
    tags = row.get("tags") if isinstance(row.get("tags"), dict) else {}
    return (tags.get("spec_name"), tags.get("case_index"), tags.get("seed"), tags.get("execution_model_id"))


def _add_walk_forward_stability(rows: list[dict[str, Any]]) -> None:
    ratios: dict[tuple[Any, ...], float] = {}
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        window = row.get("window") if isinstance(row.get("window"), dict) else {}
        if window.get("type") == "walk_forward" and window.get("role") == "test" and row.get("error") is None:
            grouped[_wf_group_key(row)].append(row)
    for key, group_rows in grouped.items():
        if not group_rows:
            continue
        positives = 0
        for row in group_rows:
            summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
            positives += 1 if float(summary.get("total_scaled_pnl_pct") or 0.0) > 0 else 0
        ratios[key] = positives / len(group_rows)
    for row in rows:
        key = _wf_group_key(row)
        if key in ratios and isinstance(row.get("summary"), dict):
            row["summary"]["walk_forward_positive_ratio"] = round(ratios[key], 6)


def _ranked_trial_ids(rows: list[dict[str, Any]]) -> list[str]:
    candidates = [row for row in rows if row.get("error") is None]
    if any(((row.get("window") or {}).get("role") == "holdout") for row in candidates if isinstance(row.get("window"), dict)):
        candidates = [row for row in candidates if isinstance(row.get("window"), dict) and row["window"].get("role") == "holdout"]
    ranked = sorted(
        candidates,
        key=lambda row: (
            row.get("summary", {}).get("return_to_dd")
            if row.get("summary", {}).get("return_to_dd") is not None
            else float("-inf"),
            row.get("summary", {}).get("total_scaled_pnl_pct", float("-inf")),
        ),
        reverse=True,
    )
    return [str(row["trial_id"]) for row in ranked]


def main() -> None:
    args = parse_args()
    spec = load_sweep_spec(args.spec)
    dataset = _load_dataset(spec)
    trials = build_plan(spec, dataset)
    if args.max_trials is not None:
        trials = trials[: args.max_trials]

    started_at = now_utc_iso()
    results = list(
        run_trials(
            trials,
            {dataset.key.stable_key(): dataset},
            workers=args.workers,
            keep_trades=args.keep_trades,
        )
    )
    finished_at = now_utc_iso()
    result_by_id = {result.trial_id: result for result in results}
    rows: list[dict[str, Any]] = []
    for trial in trials:
        result = result_by_id[trial.trial_id]
        rows.append({**trial.to_dict(), **result.to_dict()})
    _add_seed_aggregates(rows)
    _add_walk_forward_stability(rows)

    trades_by_trial_id: dict[str, list[dict[str, Any]]] | None = None
    if args.keep_trades == "all":
        trades_by_trial_id = {result.trial_id: result.trades or [] for result in results}
    elif args.keep_trades == "on-error":
        trades_by_trial_id = {
            result.trial_id: result.trades or []
            for result in results
            if result.error is not None
        }
    trade_files_count = len(trades_by_trial_id or {})

    git_sha = capture_git_sha()
    run_id = args.run_id or build_run_id(spec.name, git_sha=git_sha)
    ranked_ids = _ranked_trial_ids(rows)
    manifest = {
        "run_id": run_id,
        "spec": {
            "name": spec.name,
            "model_id": spec.model_id,
            "source_path": str(spec.source_path),
            "base_config": str(spec.base_config),
            "dataset": spec.dataset,
            "windows": spec.windows,
            "holdout": spec.holdout,
            "min_trades": spec.min_trades,
            "execution_model": spec.execution_model,
            "axes": spec.axes,
            "combinations": spec.combinations,
        },
        "dataset": {
            **dataset.key.to_dict(),
            "start": dataset.start.isoformat().replace("+00:00", "Z"),
            "end": dataset.end.isoformat().replace("+00:00", "Z"),
            "bars": len(dataset.bars),
            "data_hash": dataset.data_hash,
        },
        "git_sha": git_sha,
        "started_at": started_at,
        "finished_at": finished_at,
        "python_utc_now": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "trial_count": len(rows),
        "error_count": sum(1 for row in rows if row.get("error") is not None),
        "keep_trades": args.keep_trades,
        "trade_files_count": trade_files_count,
        "ranked_by_holdout_return_to_dd": ranked_ids,
    }
    store = TrialStore(args.runs_root)
    run_dir = store.write_run(
        run_id=run_id,
        manifest=manifest,
        rows=rows,
        legacy_output=args.output,
        trades_by_trial_id=trades_by_trial_id,
    )
    print(
        "[research] sweep completed",
        {
            "run_id": run_id,
            "spec": spec.name,
            "trials": len(rows),
            "errors": manifest["error_count"],
            "top_trial": ranked_ids[0] if ranked_ids else None,
            "trade_files": trade_files_count,
            "run_dir": str(run_dir),
        },
    )


if __name__ == "__main__":
    main()
