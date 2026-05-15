from __future__ import annotations

from copy import deepcopy
from typing import Any


def apply_path(config: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    if not path or path.startswith(".") or path.endswith("."):
        raise ValueError(f"invalid override path: {path!r}")
    merged = deepcopy(config)
    target: Any = merged
    parts = path.split(".")
    for part in parts[:-1]:
        if not isinstance(target, dict):
            raise TypeError(f"override path crosses non-object at {part}: {path}")
        if part not in target:
            target[part] = {}
        target = target[part]
    if not isinstance(target, dict):
        raise TypeError(f"override target parent is not object: {path}")
    target[parts[-1]] = value
    return merged


def apply_overrides(config: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(config)
    for path, value in overrides.items():
        merged = apply_path(merged, path, value)
    return merged
