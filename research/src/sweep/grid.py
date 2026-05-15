from __future__ import annotations

from itertools import product
from typing import Any


def expand_cases(
    axes: list[dict[str, Any]] | None,
    combinations: str = "full_grid",
    listed_cases: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if combinations == "listed":
        cases = listed_cases or []
        expanded: list[dict[str, Any]] = []
        for case in cases:
            if "values" in case and isinstance(case["values"], dict):
                expanded.append(dict(case["values"]))
            else:
                expanded.append(dict(case))
        return expanded or [{}]

    resolved_axes = axes or []
    if not resolved_axes:
        return [{}]
    axis_paths = [str(axis["path"]) for axis in resolved_axes]
    axis_values = [list(axis.get("values", [])) for axis in resolved_axes]
    if any(not values for values in axis_values):
        raise ValueError("all axes must have at least one value")
    if combinations not in {"full_grid", "pairwise"}:
        raise ValueError(f"unsupported combinations: {combinations}")
    return [dict(zip(axis_paths, values, strict=True)) for values in product(*axis_values)]


def format_case_name(case: dict[str, Any]) -> str:
    if not case:
        return "baseline"
    return ",".join(f"{path}={value}" for path, value in sorted(case.items()))
