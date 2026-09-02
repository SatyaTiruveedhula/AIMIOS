from datetime import datetime, timedelta, timezone
from aimios.engines.mw_pattern_engine import MWPatternEngine
from tests.test_mw_pattern_engine import make_candle


def make_candles(prices):
    return [make_candle(i, price) for i, price in enumerate(prices)]


# Case 1: test_valid_w
prices_w = [
    24500,  # 0
    24510,  # 1
    24500,  # 2 VALLEY1
    24515,  # 3
    24530,  # 4
    24540,  # 5 HIGH
    24530,  # 6
    24520,  # 7
    24508,  # 8 VALLEY2
    24506,  # 9
    24500,  # 10
    24495,  # 11
]
engine = MWPatternEngine()
print("min_pivot_distance=", engine.min_pivot_distance)
candles_w = make_candles(prices_w)
signal_w = engine.detect(candles_w, symbol="NIFTY")
print("W signal:", signal_w)

# Case 2: test_m_rejected_when_too_close
prices_m = [
    24500,
    24490,
    24500,  # HIGH1
    24465,  # VALLEY
    24480,
    24494,  # HIGH2 only 3 candles later
    24490,
]
engine2 = MWPatternEngine()
candles_m = make_candles(prices_m)
signal_m = engine2.detect(candles_m, symbol="NIFTY")
print("M signal for too close case:", signal_m)
