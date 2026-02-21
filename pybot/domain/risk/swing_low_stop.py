from __future__ import annotations


def calculate_swing_low(lows: list[float], lookback_bars: int) -> float:
    if lookback_bars <= 0:
        raise ValueError("lookback_bars must be greater than 0")
    if len(lows) < lookback_bars:
        raise ValueError(f"Not enough lows for swing low: required={lookback_bars}, actual={len(lows)}")

    recent_lows = lows[-lookback_bars:]
    return min(recent_lows)


def calculate_max_loss_stop_price(entry_price: float, max_loss_pct: float) -> float:
    if entry_price <= 0:
        raise ValueError("entry_price must be greater than 0")
    if max_loss_pct <= 0:
        raise ValueError("max_loss_pct must be greater than 0")

    max_loss_ratio = max_loss_pct / 100
    return entry_price * (1 - max_loss_ratio)


def tighten_stop_for_long(entry_price: float, swing_low_stop: float, max_loss_pct: float) -> float:
    pct_stop = calculate_max_loss_stop_price(entry_price, max_loss_pct)
    return max(swing_low_stop, pct_stop)


def calculate_take_profit_price(entry_price: float, stop_price: float, r_multiple: float) -> float:
    if r_multiple <= 0:
        raise ValueError("r_multiple must be greater than 0")
    if entry_price <= stop_price:
        raise ValueError("entry_price must be greater than stop_price")

    one_r = entry_price - stop_price
    return entry_price + one_r * r_multiple

