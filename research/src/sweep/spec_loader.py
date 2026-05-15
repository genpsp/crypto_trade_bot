from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SweepSpec:
    name: str
    model_id: str
    base_config: Path
    dataset: dict[str, Any]
    windows: list[dict[str, Any]]
    axes: list[dict[str, Any]]
    combinations: str
    cases: list[dict[str, Any]]
    source_path: Path
    holdout: dict[str, Any] | None = None
    min_trades: int = 30
    execution_model: dict[str, Any] | None = None


def _resolve_path(raw: str | Path, *, spec_path: Path) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.exists():
        return candidate
    relative_to_spec = spec_path.parent / candidate
    if relative_to_spec.exists():
        return relative_to_spec
    return candidate


def load_sweep_spec(path: str | Path) -> SweepSpec:
    source = Path(path)
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"sweep spec must be object: {source}")
    dataset = payload.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError("sweep spec dataset must be object")
    holdout = payload.get("holdout")
    if not isinstance(holdout, dict):
        raise ValueError("sweep spec holdout is required for validity-aware sweeps")
    if holdout.get("type") != "time_split":
        raise ValueError("sweep spec holdout.type must be time_split")
    windows = payload.get("windows") or []
    if not isinstance(windows, list):
        raise ValueError("sweep spec windows must be list")
    axes = payload.get("axes") or []
    if not isinstance(axes, list):
        raise ValueError("sweep spec axes must be list")
    cases = payload.get("cases") or []
    if not isinstance(cases, list):
        raise ValueError("sweep spec cases must be list")
    min_trades = int(payload.get("min_trades", 30))
    if min_trades <= 0:
        raise ValueError("sweep spec min_trades must be positive")
    execution_model = payload.get("execution_model") or {"id": "ideal_v1"}
    if not isinstance(execution_model, dict):
        raise ValueError("sweep spec execution_model must be object")
    return SweepSpec(
        name=str(payload["name"]),
        model_id=str(payload["model_id"]),
        base_config=_resolve_path(payload["base_config"], spec_path=source),
        dataset=dict(dataset),
        windows=[dict(item) for item in windows],
        axes=[dict(item) for item in axes],
        combinations=str(payload.get("combinations", "full_grid")),
        cases=[dict(item) for item in cases],
        source_path=source,
        holdout=dict(holdout),
        min_trades=min_trades,
        execution_model=dict(execution_model),
    )
