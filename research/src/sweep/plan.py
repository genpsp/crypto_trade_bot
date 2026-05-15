from __future__ import annotations

from typing import Any

from research.src.data.market_dataset import MarketDataset
from research.src.eval.trial import TrialSpec
from research.src.eval.window import build_windows
from research.src.infra.research_config import load_bot_config
from research.src.sweep.grid import expand_cases, format_case_name
from research.src.sweep.overrides import apply_overrides
from research.src.sweep.spec_loader import SweepSpec


def build_plan(spec: SweepSpec, dataset: MarketDataset) -> list[TrialSpec]:
    base_config = load_bot_config(spec.base_config)
    cases = expand_cases(spec.axes, spec.combinations, spec.cases)
    windows = build_windows(spec.windows, dataset)
    trials: list[TrialSpec] = []
    for case_index, case in enumerate(cases):
        config = apply_overrides(base_config, case)
        case_name = format_case_name(case)
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
                        "axis_values": case,
                        "window_role": window.role,
                    },
                )
            )
    return trials
