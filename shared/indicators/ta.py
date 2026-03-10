from __future__ import annotations

import math


def _is_valid_period(period: int) -> bool:
    return isinstance(period, int) and period > 0


def _is_finite_series(values: list[float]) -> bool:
    return all(math.isfinite(value) for value in values)


def ema_series(closes: list[float], period: int) -> list[float]:
    if not _is_valid_period(period) or len(closes) == 0 or not _is_finite_series(closes):
        return []
    if len(closes) < period:
        return []

    k = 2 / (period + 1)
    seed = sum(closes[:period]) / period
    values: list[float] = [seed]

    ema = seed
    for close in closes[period:]:
        ema = close * k + ema * (1 - k)
        values.append(ema)

    return values


def rsi_series(closes: list[float], period: int) -> list[float]:
    if not _is_valid_period(period) or len(closes) == 0 or not _is_finite_series(closes):
        return []
    if len(closes) <= period:
        return []

    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, len(closes)):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _to_rsi(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    values: list[float] = [_to_rsi(avg_gain, avg_loss)]

    for index in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[index]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[index]) / period
        values.append(_to_rsi(avg_gain, avg_loss))

    return values


def atr_series(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float]:
    if not _is_valid_period(period):
        return []
    if len(highs) == 0 or len(lows) == 0 or len(closes) == 0:
        return []
    if len(highs) != len(lows) or len(highs) != len(closes):
        return []
    if not _is_finite_series(highs) or not _is_finite_series(lows) or not _is_finite_series(closes):
        return []
    if len(closes) <= period:
        return []

    tr_values: list[float] = []
    for index in range(len(closes)):
        if index == 0:
            tr = highs[index] - lows[index]
        else:
            tr = max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        tr_values.append(tr)

    seed = sum(tr_values[1 : period + 1]) / period
    values: list[float] = [seed]
    atr = seed

    for tr in tr_values[period + 1 :]:
        atr = ((atr * (period - 1)) + tr) / period
        values.append(atr)

    return values

