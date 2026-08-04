from typing import List


def sma(values: List[float], period: int) -> float:
    if not values or period <= 0:
        return 0.0
    return sum(values[-period:]) / min(len(values), period)
