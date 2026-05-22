from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Any


@dataclass(frozen=True)
class ExpandedCase:
    overrides: dict[str, Any]
    name: str | None = None
    tags: dict[str, Any] = field(default_factory=dict)

    # Backwards-compatibility helpers so callers can keep treating an ExpandedCase
    # like the dict-of-overrides that expand_cases used to return.
    def __iter__(self):
        return iter(self.overrides)

    def __getitem__(self, key: str) -> Any:
        return self.overrides[key]

    def __contains__(self, key: object) -> bool:
        return key in self.overrides

    def __len__(self) -> int:
        return len(self.overrides)

    def items(self):
        return self.overrides.items()

    def keys(self):
        return self.overrides.keys()

    def values(self):
        return self.overrides.values()

    def get(self, key: str, default: Any = None) -> Any:
        return self.overrides.get(key, default)


def expand_cases(
    axes: list[dict[str, Any]] | None,
    combinations: str = "full_grid",
    listed_cases: list[dict[str, Any]] | None = None,
) -> list[ExpandedCase]:
    if combinations == "listed":
        cases = listed_cases or []
        expanded: list[ExpandedCase] = []
        for case in cases:
            explicit_name = case.get("name") if isinstance(case, dict) else None
            tags = dict(case.get("tags") or {}) if isinstance(case, dict) else {}
            if isinstance(case, dict) and "values" in case and isinstance(case["values"], dict):
                overrides = dict(case["values"])
            elif isinstance(case, dict):
                overrides = {k: v for k, v in case.items() if k not in {"name", "tags"}}
            else:
                overrides = {}
            expanded.append(ExpandedCase(overrides=overrides, name=explicit_name, tags=tags))
        return expanded or [ExpandedCase(overrides={})]

    resolved_axes = axes or []
    if not resolved_axes:
        return [ExpandedCase(overrides={})]
    axis_paths = [str(axis["path"]) for axis in resolved_axes]
    axis_values = [list(axis.get("values", [])) for axis in resolved_axes]
    if any(not values for values in axis_values):
        raise ValueError("all axes must have at least one value")
    if combinations not in {"full_grid", "pairwise"}:
        raise ValueError(f"unsupported combinations: {combinations}")
    return [
        ExpandedCase(overrides=dict(zip(axis_paths, values, strict=True)))
        for values in product(*axis_values)
    ]


def format_case_name(case: dict[str, Any] | ExpandedCase) -> str:
    if isinstance(case, ExpandedCase):
        if case.name:
            return case.name
        overrides = case.overrides
    else:
        overrides = case
    if not overrides:
        return "baseline"
    return ",".join(f"{path}={value}" for path, value in sorted(overrides.items()))
