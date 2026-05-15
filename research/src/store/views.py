from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def _metric_value(row: dict[str, Any], metric: str) -> Any:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    if metric in row:
        return row[metric]
    return summary.get(metric)


def _role(row: dict[str, Any]) -> str | None:
    window = row.get("window") if isinstance(row.get("window"), dict) else {}
    tags = row.get("tags") if isinstance(row.get("tags"), dict) else {}
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    return window.get("role") or tags.get("window_role") or summary.get("window_role")


def rank(
    rows: Iterable[dict[str, Any]],
    *,
    by: str = "return_to_dd",
    desc: bool = True,
    top_k: int = 10,
    role: str | None = None,
    eligible_only: bool = False,
) -> list[dict[str, Any]]:
    valid = []
    for row in rows:
        if row.get("error") not in (None, ""):
            continue
        if role is not None and _role(row) != role:
            continue
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        if eligible_only and summary.get("rank_eligible") is False:
            continue
        valid.append(row)
    return sorted(
        valid,
        key=lambda row: _metric_value(row, by) if _metric_value(row, by) is not None else float("-inf"),
        reverse=desc,
    )[:top_k]


def marginal_by_axis(rows: Iterable[dict[str, Any]], *, metric: str = "return_to_dd") -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        if row.get("error") not in (None, ""):
            continue
        value = _metric_value(row, metric)
        if not isinstance(value, (int, float)):
            continue
        tags = row.get("tags") if isinstance(row.get("tags"), dict) else {}
        axis_values = tags.get("axis_values") if isinstance(tags.get("axis_values"), dict) else {}
        for axis, axis_value in axis_values.items():
            buckets[(axis, str(axis_value))].append(float(value))
    result: list[dict[str, Any]] = []
    for (axis, axis_value), values in sorted(buckets.items()):
        result.append(
            {
                "axis": axis,
                "value": axis_value,
                "count": len(values),
                f"mean_{metric}": sum(values) / len(values),
                f"min_{metric}": min(values),
                f"max_{metric}": max(values),
            }
        )
    return result


def diff(
    rows_a: Iterable[dict[str, Any]],
    rows_b: Iterable[dict[str, Any]],
    *,
    metric: str = "return_to_dd",
    key: str = "case_name",
) -> list[dict[str, Any]]:
    def build_index(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for row in rows:
            tags = row.get("tags") if isinstance(row.get("tags"), dict) else {}
            key_value = tags.get(key) or row.get(key) or row.get("trial_id")
            if key_value is not None:
                indexed[str(key_value)] = row
        return indexed

    left = build_index(rows_a)
    right = build_index(rows_b)
    output: list[dict[str, Any]] = []
    for key_value in sorted(set(left.keys()) | set(right.keys())):
        value_a = _metric_value(left[key_value], metric) if key_value in left else None
        value_b = _metric_value(right[key_value], metric) if key_value in right else None
        delta = None
        if isinstance(value_a, (int, float)) and isinstance(value_b, (int, float)):
            delta = value_b - value_a
        output.append({"key": key_value, "a": value_a, "b": value_b, "delta": delta})
    return output


def format_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "(no rows)"
    table_rows = [{column: _format_cell(row.get(column)) for column in columns} for row in rows]
    widths = {column: max(len(column), *(len(row[column]) for row in table_rows)) for column in columns}
    header = " | ".join(column.ljust(widths[column]) for column in columns)
    sep = "-+-".join("-" * widths[column] for column in columns)
    body = [" | ".join(row[column].ljust(widths[column]) for column in columns) for row in table_rows]
    return "\n".join([header, sep, *body])


def _format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)
