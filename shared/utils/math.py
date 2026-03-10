from decimal import Decimal, ROUND_DOWN


def round_to(value: float, decimals: int) -> float:
    factor = 10**decimals
    return round(value * factor) / factor


def percent_of(value: float, pct: float) -> float:
    return (value * pct) / 100


def to_atomic_amount_down(value: float, atomic_multiplier: int) -> int:
    if value <= 0:
        return 0
    if atomic_multiplier <= 0:
        raise ValueError("atomic_multiplier must be > 0")
    scaled = Decimal(str(value)) * Decimal(atomic_multiplier)
    return int(scaled.to_integral_value(rounding=ROUND_DOWN))


def scale_atomic_amount_down(amount_atomic: int, multiplier: float) -> int:
    if amount_atomic <= 0 or multiplier <= 0:
        return 0
    scaled = Decimal(amount_atomic) * Decimal(str(multiplier))
    return int(scaled.to_integral_value(rounding=ROUND_DOWN))
