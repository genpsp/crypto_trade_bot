from __future__ import annotations

from dataclasses import dataclass
import math
import random
from statistics import mean, pstdev
from typing import Any, Callable, Iterable


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _normal_ppf(p: float) -> float:
    """Acklam's inverse-normal approximation; enough for gate thresholds."""

    if p <= 0.0 or p >= 1.0:
        raise ValueError("p must be in (0, 1)")
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def percentile(values: Iterable[float], pct: float) -> float:
    sorted_values = sorted(float(value) for value in values if isinstance(value, (int, float)) and math.isfinite(float(value)))
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * min(1.0, max(0.0, pct))
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return sorted_values[low]
    weight = rank - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def block_bootstrap_trades(trades: list[Any], n_resamples: int = 1000, block_size: int = 10, seed: int = 1337) -> list[list[Any]]:
    if not trades:
        return []
    resolved_block_size = max(1, int(block_size))
    blocks = [trades[index : index + resolved_block_size] for index in range(0, len(trades), resolved_block_size)]
    rng = random.Random(seed)
    resamples: list[list[Any]] = []
    for _ in range(max(1, int(n_resamples))):
        sample: list[Any] = []
        while len(sample) < len(trades):
            sample.extend(rng.choice(blocks))
        resamples.append(sample[: len(trades)])
    return resamples


def bootstrap_ci(
    trades: list[Any],
    metric: Callable[[list[Any]], float | None],
    *,
    n_resamples: int = 1000,
    block_size: int = 10,
    seed: int = 1337,
) -> tuple[float, float]:
    values = []
    for sample in block_bootstrap_trades(trades, n_resamples=n_resamples, block_size=block_size, seed=seed):
        value = metric(sample)
        if value is not None and math.isfinite(float(value)):
            values.append(float(value))
    if not values:
        return 0.0, 0.0
    return percentile(values, 0.025), percentile(values, 0.975)


def _skew(values: list[float]) -> float:
    if len(values) < 3:
        return 0.0
    mu = mean(values)
    sd = pstdev(values)
    if sd <= 0:
        return 0.0
    return sum(((value - mu) / sd) ** 3 for value in values) / len(values)


def _kurtosis(values: list[float]) -> float:
    if len(values) < 4:
        return 3.0
    mu = mean(values)
    sd = pstdev(values)
    if sd <= 0:
        return 3.0
    return sum(((value - mu) / sd) ** 4 for value in values) / len(values)


def deflated_sharpe(returns: list[float], n_trials: int, skew: float | None = None, kurt: float | None = None) -> tuple[float, float]:
    values = [float(value) for value in returns if isinstance(value, (int, float)) and math.isfinite(float(value))]
    if len(values) < 2:
        return 0.0, 1.0
    sd = pstdev(values)
    if sd <= 0:
        return 0.0, 1.0
    sharpe = mean(values) / sd
    resolved_skew = _skew(values) if skew is None else float(skew)
    resolved_kurt = _kurtosis(values) if kurt is None else float(kurt)
    n = len(values)
    sr_variance = max(1e-12, (1 - resolved_skew * sharpe + ((resolved_kurt - 1) / 4) * sharpe * sharpe) / max(1, n - 1))
    sr_std = math.sqrt(sr_variance)
    # Expected best Sharpe under multiple trials. This is intentionally conservative
    # and avoids SciPy so it can run inside the lightweight unit-test environment.
    trials = max(1, int(n_trials))
    expected_max_sr = 0.0 if trials <= 1 else _normal_ppf(1 - 1 / max(2, trials)) * sr_std
    dsr = (sharpe - expected_max_sr) / sr_std
    p_value = 1 - _normal_cdf(dsr)
    return dsr, min(1.0, max(0.0, p_value))


def power_analysis(win_rate: float, r: float, alpha: float = 0.05, power: float = 0.8) -> int:
    p = min(0.999, max(0.001, float(win_rate)))
    reward_r = max(0.001, float(r))
    outcomes = [reward_r, -1.0]
    expected = p * outcomes[0] + (1 - p) * outcomes[1]
    variance = p * (outcomes[0] - expected) ** 2 + (1 - p) * (outcomes[1] - expected) ** 2
    if expected <= 0:
        return 10_000
    z_alpha = _normal_ppf(1 - alpha)
    z_power = _normal_ppf(power)
    n = ((z_alpha + z_power) * math.sqrt(max(variance, 1e-12)) / expected) ** 2
    return max(1, int(math.ceil(n)))
