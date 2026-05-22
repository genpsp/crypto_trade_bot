from __future__ import annotations

from copy import deepcopy
from typing import Any

from research.src.data.market_dataset import MarketDataset
from research.src.eval.trial import TrialSpec
from research.src.eval.window import ConcreteWindow, build_windows, parse_utc_datetime
from research.src.infra.research_config import load_bot_config
from research.src.sweep.grid import expand_cases, format_case_name
from research.src.sweep.overrides import apply_overrides
from research.src.sweep.spec_loader import SweepSpec


def _build_holdout_windows(holdout: dict[str, Any], dataset: MarketDataset) -> list[ConcreteWindow]:
    train_end = parse_utc_datetime(str(holdout["train_end"]))
    test_start = parse_utc_datetime(str(holdout["test_start"]))
    if train_end >= test_start:
        raise ValueError("holdout train_end must be earlier than test_start")
    return [
        ConcreteWindow(
            window_id="holdout_train",
            type="holdout",
            start=dataset.start,
            end=train_end,
            role="train",
            metadata={"holdout": True},
        ),
        ConcreteWindow(
            window_id="holdout_test",
            type="holdout",
            start=test_start,
            end=dataset.end,
            role="holdout",
            metadata={"holdout": True},
        ),
    ]


def _apply_execution_model(config: dict[str, Any], execution_model: dict[str, Any] | None, seed: int | None) -> dict[str, Any]:
    updated = deepcopy(config)
    execution = dict(updated.get("execution", {}))
    if execution_model:
        execution["model_id"] = str(execution_model.get("id", execution_model.get("model_id", "ideal_v1")))
        for key, value in execution_model.items():
            if key in {"id", "model_id", "seeds"}:
                continue
            execution[key] = value
    if seed is not None:
        execution["seed"] = int(seed)
    updated["execution"] = execution
    return updated


def build_plan(spec: SweepSpec, dataset: MarketDataset) -> list[TrialSpec]:
    base_config = load_bot_config(spec.base_config)
    cases = expand_cases(spec.axes, spec.combinations, spec.cases)
    windows: list[ConcreteWindow] = []
    if spec.holdout:
        windows.extend(_build_holdout_windows(spec.holdout, dataset))
    if spec.windows:
        windows.extend(build_windows(spec.windows, dataset))
    if not windows:
        windows = build_windows([{"type": "last_n_days", "days": 365}], dataset)
    seeds_raw = (spec.execution_model or {}).get("seeds") if spec.execution_model else None
    seeds: list[int | None] = [int(seed) for seed in seeds_raw] if isinstance(seeds_raw, list) and seeds_raw else [None]
    trials: list[TrialSpec] = []
    for case_index, case in enumerate(cases):
        case_overrides = case.overrides
        case_config = apply_overrides(base_config, case_overrides)
        case_name = format_case_name(case)
        for seed in seeds:
            config = _apply_execution_model(case_config, spec.execution_model, seed)
            for window in windows:
                trials.append(
                    TrialSpec.create(
                        model_id=spec.model_id,
                        config=config,
                        dataset_key=dataset.key,
                        window=window,
                        tags={
                            "spec_name": spec.name,
                            "case_index": case_index,
                            "case_name": case_name,
                            "axis_values": case_overrides,
                            "window_role": window.role,
                            "min_trades": spec.min_trades,
                            "execution_model_id": config.get("execution", {}).get("model_id", "ideal_v1"),
                            "seed": seed,
                        },
                    )
                )
    return trials
