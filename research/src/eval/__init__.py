from __future__ import annotations

__all__ = ["ConcreteWindow", "TrialResult", "TrialSpec", "run_trials"]


def __getattr__(name: str):
    if name == "run_trials":
        from research.src.eval.runner import run_trials

        return run_trials
    if name in {"TrialResult", "TrialSpec"}:
        from research.src.eval.trial import TrialResult, TrialSpec

        return {"TrialResult": TrialResult, "TrialSpec": TrialSpec}[name]
    if name == "ConcreteWindow":
        from research.src.eval.window import ConcreteWindow

        return ConcreteWindow
    raise AttributeError(name)
