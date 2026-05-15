from __future__ import annotations

import argparse
from typing import Any

from research.src.eval.gates import evaluate_gate_a
from research.src.store.trial_store import TrialStore
from research.src.store.views import diff, format_table, marginal_by_axis, rank


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare or rank stored research sweep runs")
    parser.add_argument("--run", default="latest", help="single run id or latest")
    parser.add_argument("--runs", default=None, help="comma-separated two run ids for diff")
    parser.add_argument("--runs-root", default="research/data/runs")
    parser.add_argument("--metric", default="return_to_dd")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--marginal", action="store_true", help="show axis marginal table for single run")
    parser.add_argument("--diff-key", default="case_name", help="tag key used to align diff rows")
    parser.add_argument("--role", default="auto", help="row role to rank: auto, all, holdout, test, train")
    parser.add_argument("--gate-a", action="store_true", help="print Gate A pass/fail table")
    return parser.parse_args()


def _resolve_role(rows: list[dict[str, Any]], requested: str) -> str | None:
    if requested == "all":
        return None
    if requested != "auto":
        return requested
    for row in rows:
        window = row.get("window") if isinstance(row.get("window"), dict) else {}
        if window.get("role") == "holdout":
            return "holdout"
    return None


def _rank_rows(rows: list[dict[str, Any]], *, metric: str, top: int, role: str | None) -> list[dict[str, Any]]:
    ranked = rank(rows, by=metric, top_k=top, role=role)
    output: list[dict[str, Any]] = []
    for index, row in enumerate(ranked, start=1):
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        tags = row.get("tags") if isinstance(row.get("tags"), dict) else {}
        output.append(
            {
                "rank": index,
                "trial_id": row.get("trial_id"),
                "case_name": tags.get("case_name"),
                "window": (row.get("window") or {}).get("window_id") if isinstance(row.get("window"), dict) else None,
                "role": (row.get("window") or {}).get("role") if isinstance(row.get("window"), dict) else None,
                "seed": tags.get("seed"),
                metric: summary.get(metric),
                "total_scaled_pnl_pct": summary.get("total_scaled_pnl_pct"),
                "ci_low": summary.get("total_scaled_pnl_pct_ci_low"),
                "dsr_p_value": summary.get("dsr_p_value"),
                "closed_trades": summary.get("closed_trades"),
                "win_rate_pct": summary.get("win_rate_pct"),
            }
        )
    return output


def _gate_rows(rows: list[dict[str, Any]], *, top: int, role: str | None) -> list[dict[str, Any]]:
    candidates = rank(rows, by="return_to_dd", top_k=top, role=role)
    output: list[dict[str, Any]] = []
    for row in candidates:
        tags = row.get("tags") if isinstance(row.get("tags"), dict) else {}
        gate = evaluate_gate_a(row)
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        output.append(
            {
                "trial_id": row.get("trial_id"),
                "case_name": tags.get("case_name"),
                "gate_a": "PASS" if gate["passed"] else "FAIL",
                "failed_checks": ",".join(gate["failed_checks"]),
                "closed_trades": summary.get("closed_trades"),
                "ci_low": summary.get("total_scaled_pnl_pct_ci_low"),
                "dsr_p_value": summary.get("dsr_p_value"),
                "wf_positive": summary.get("walk_forward_positive_ratio"),
            }
        )
    return output


def main() -> None:
    args = parse_args()
    store = TrialStore(args.runs_root)
    if args.runs:
        run_ids = [token.strip() for token in args.runs.split(",") if token.strip()]
        if len(run_ids) != 2:
            raise ValueError("--runs must contain exactly two run ids")
        rows_a = store.load_trials(run_ids[0])
        rows_b = store.load_trials(run_ids[1])
        diff_rows = diff(rows_a, rows_b, metric=args.metric, key=args.diff_key)
        print(format_table(diff_rows[: args.top], ["key", "a", "b", "delta"]))
        return

    resolved_run_id = store.resolve_run_id(args.run)
    manifest = store.load_manifest(resolved_run_id)
    rows = store.load_trials(resolved_run_id)
    role = _resolve_role(rows, args.role)
    print(
        "[research] run",
        {
            "run_id": resolved_run_id,
            "spec": manifest.get("spec", {}).get("name") if isinstance(manifest.get("spec"), dict) else None,
            "trials": manifest.get("trial_count"),
            "errors": manifest.get("error_count"),
            "metric": args.metric,
            "role": role or "all",
        },
    )
    if args.gate_a:
        table_rows = _gate_rows(rows, top=args.top, role=role)
        print(format_table(table_rows, ["trial_id", "case_name", "gate_a", "failed_checks", "closed_trades", "ci_low", "dsr_p_value", "wf_positive"]))
        return
    if args.marginal:
        table_rows = marginal_by_axis(rows, metric=args.metric)
        print(format_table(table_rows, ["axis", "value", "count", f"mean_{args.metric}", f"min_{args.metric}", f"max_{args.metric}"]))
        return
    table_rows = _rank_rows(rows, metric=args.metric, top=args.top, role=role)
    print(format_table(table_rows, ["rank", "trial_id", "case_name", "window", "role", "seed", args.metric, "total_scaled_pnl_pct", "ci_low", "dsr_p_value", "closed_trades", "win_rate_pct"]))


if __name__ == "__main__":
    main()
