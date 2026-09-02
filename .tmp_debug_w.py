from tests.test_mw_pattern_engine import make_candle
from aimios.engines.mw_pattern_engine import MWPatternEngine


def make_candles(prices):
    return [make_candle(i, price) for i, price in enumerate(prices)]


prices_w = [
    24500,
    24510,
    24500,
    24515,
    24530,
    24540,
    24530,
    24520,
    24508,
    24506,
    24500,
    24495,
]
engine = MWPatternEngine()
print("min_pivot_distance=", engine.min_pivot_distance)

candles = make_candles(prices_w)

# compute day_low and valley1_index
from datetime import datetime

day_low_candle = min(candles, key=lambda item: float(item.low))
day_low = float(day_low_candle.low)
valley1_index = engine._candle_index(candles, day_low_candle)
print("day_low=", day_low, "valley1_index=", valley1_index)

valley2_candidates = []
for index in range(valley1_index + engine.min_pivot_distance, len(candles)):
    valley2_candidates.append((index, candles[index]))
print("valley2_candidate_indices=", [i for i, _ in valley2_candidates])

for valley2_index, valley2 in reversed(valley2_candidates):
    valley2_value = float(valley2.low)
    swing_distance_pct = engine._difference_pct(day_low, valley2_value)
    print(
        "candidate",
        valley2_index,
        "valley2_value",
        valley2_value,
        "swing_distance_pct",
        swing_distance_pct,
    )
    if swing_distance_pct > engine.max_outer_difference_pct:
        print("rejected by outer diff")
        continue
    highs_between = []
    for idx in range(valley1_index + 1, valley2_index):
        if engine._is_high_pivot(candles, idx):
            highs_between.append((idx, candles[idx]))
    print("highs_between indices", [i for i, _ in highs_between])
    if not highs_between:
        print("no highs_between")
        continue
    high_index, high = max(highs_between, key=lambda item: float(item[1].high))
    high_value = float(high.high)
    reversal_pct = (high_value - day_low) / abs(day_low) * 100.0
    print("high_value", high_value, "reversal_pct", reversal_pct)
    if reversal_pct < engine.min_reversal_pct:
        print("rejected by reversal")
        continue
    candle_distance = valley2_index - valley1_index
    print(
        "accepted W with valley2_index",
        valley2_index,
        "candle_distance",
        candle_distance,
    )
    break
else:
    print("no W detected")
