def round_to(value: float, decimals: int) -> float:
    factor = 10**decimals
    return round(value * factor) / factor


def percent_of(value: float, pct: float) -> float:
    return (value * pct) / 100

