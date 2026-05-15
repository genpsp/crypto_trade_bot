from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class GateCheck:
    name: str
    passed: bool
    value: Any
    threshold: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _summary(row_or_summary: dict[str, Any]) -> dict[str, Any]:
    summary = row_or_summary.get("summary") if isinstance(row_or_summary.get("summary"), dict) else row_or_summary
    return summary if isinstance(summary, dict) else {}


def _by_regime_positive(summary: dict[str, Any]) -> bool:
    by_regime = summary.get("by_regime") if isinstance(summary.get("by_regime"), dict) else {}
    trend = by_regime.get("trend") if isinstance(by_regime.get("trend"), dict) else {}
    required = ("BULL", "BEAR", "CHOPPY")
    for label in required:
        metrics = trend.get(label)
        if not isinstance(metrics, dict):
            return False
        if float(metrics.get("total_pnl_pct", metrics.get("total_scaled_pnl_pct", 0.0)) or 0.0) <= 0:
            return False
    return True


def _regime_representative(summary: dict[str, Any]) -> bool:
    by_regime = summary.get("by_regime") if isinstance(summary.get("by_regime"), dict) else {}
    trend = by_regime.get("trend") if isinstance(by_regime.get("trend"), dict) else {}
    for label in ("BULL", "BEAR", "CHOPPY"):
        metrics = trend.get(label)
        if not isinstance(metrics, dict) or int(metrics.get("trades") or 0) < 5:
            return False
    return True


def evaluate_gate_a(row_or_summary: dict[str, Any], *, min_trades: int | None = None) -> dict[str, Any]:
    summary = _summary(row_or_summary)
    resolved_min_trades = int(min_trades or summary.get("min_trades") or 30)
    stochastic_ci_p05 = summary.get("total_scaled_pnl_pct_ci_low_seed_p05")
    if stochastic_ci_p05 is None:
        stochastic_ci_p05 = summary.get("total_scaled_pnl_pct_seed_p05")
    checks = [
        GateCheck("min_trades", int(summary.get("closed_trades") or 0) >= resolved_min_trades, summary.get("closed_trades"), f">= {resolved_min_trades}"),
        GateCheck("holdout_pnl_ci_positive", float(summary.get("total_scaled_pnl_pct_ci_low") or 0.0) > 0, summary.get("total_scaled_pnl_pct_ci_low"), "> 0"),
        GateCheck("return_to_dd_ci_positive", float(summary.get("return_to_dd_ci_low") or 0.0) > 0, summary.get("return_to_dd_ci_low"), "> 0"),
        GateCheck("walk_forward_positive_ratio", float(summary.get("walk_forward_positive_ratio") or 0.0) >= 0.7, summary.get("walk_forward_positive_ratio"), ">= 0.7"),
        GateCheck("all_trend_regimes_positive", _by_regime_positive(summary), summary.get("by_regime"), "BULL/BEAR/CHOPPY total_pnl_pct > 0"),
        GateCheck("regime_representative", _regime_representative(summary), summary.get("by_regime"), "each trend regime trades >= 5"),
        GateCheck("deflated_sharpe_p_value", float(summary.get("dsr_p_value", summary.get("deflated_sharpe_p_value", 1.0)) or 1.0) < 0.05, summary.get("dsr_p_value", summary.get("deflated_sharpe_p_value")), "< 0.05"),
        GateCheck("stochastic_seed_p05_ci_positive", stochastic_ci_p05 is not None and float(stochastic_ci_p05) > 0, stochastic_ci_p05, "> 0"),
    ]
    passed = all(check.passed for check in checks)
    return {
        "passed": passed,
        "checks": [check.to_dict() for check in checks],
        "failed_checks": [check.name for check in checks if not check.passed],
    }
