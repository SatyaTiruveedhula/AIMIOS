from datetime import datetime, timedelta, timezone

from aimios.engines.mw_pattern_engine import MWPatternEngine
from aimios.market.candle_buffer import Candle


def make_candle(
    candle_id: int,
    price: float,
) -> Candle:

    timestamp = datetime(
        2026,
        8,
        10,
        9,
        15,
        tzinfo=timezone.utc,
    ) + timedelta(minutes=candle_id)

    return Candle(
        timestamp=timestamp,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1000.0,
        previous_close=price,
        ticks=1,
        cum_volume=1000.0,
        cum_price_volume=price * 1000.0,
        change_pct=0.0,
        candle_id=candle_id,
        body_strength=0.0,
        upper_wick_pct=0.0,
        lower_wick_pct=0.0,
    )


def build_candles(prices):

    return [make_candle(i, price) for i, price in enumerate(prices)]


# ============================================================
# M VALID
# ============================================================


def test_valid_m():

    engine = MWPatternEngine()

    candles = build_candles(
        [
            24500,  # 0
            24490,  # 1
            24500,  # 2 HIGH1
            24490,  # 3
            24470,  # 4
            24465,  # 5 VALLEY
            24475,  # 6
            24485,  # 7
            24492,  # 8
            24494,  # 9 HIGH2 area
            24490,  # 10
            24485,  # 11
        ]
    )

    signal = engine.detect(
        candles,
        symbol="NIFTY",
    )

    assert signal is not None
    assert signal["pattern"] == "M"
    assert signal["direction"] == "SELL"

    assert signal["reversal_pct"] >= 0.13
    assert signal["swing_distance_pct"] <= 0.03
    assert signal["candle_distance"] >= 7


# ============================================================
# W VALID
# ============================================================


def test_valid_w():

    engine = MWPatternEngine()

    candles = build_candles(
        [
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
    )

    signal = engine.detect(
        candles,
        symbol="NIFTY",
    )

    assert signal is not None
    assert signal["pattern"] == "W"
    assert signal["direction"] == "BUY"

    assert signal["reversal_pct"] >= 0.13
    assert signal["swing_distance_pct"] <= 0.03
    assert signal["candle_distance"] >= 7


# ============================================================
# M FAIL - HIGH2 TOO FAR
# ============================================================


def test_m_rejected_when_high2_too_far():

    engine = MWPatternEngine()

    candles = build_candles(
        [
            24500,
            24490,
            24500,  # HIGH1
            24480,
            24465,  # VALLEY
            24470,
            24480,
            24490,
            24500,
            24510,  # HIGH2 too high
            24490,
            24480,
        ]
    )

    signal = engine.detect(
        candles,
        symbol="NIFTY",
    )

    assert signal is None


# ============================================================
# M FAIL - REVERSAL TOO SMALL
# ============================================================


def test_m_rejected_when_reversal_too_small():

    engine = MWPatternEngine()

    candles = build_candles(
        [
            24500,
            24490,
            24500,  # HIGH1
            24495,
            24480,  # valley = ~0.0816%
            24490,
            24500,
            24499,
            24499,
            24499,
            24490,
        ]
    )

    signal = engine.detect(
        candles,
        symbol="NIFTY",
    )

    assert signal is None


# ============================================================
# M FAIL - LESS THAN 7 CANDLES
# ============================================================


def test_m_rejected_when_too_close():

    engine = MWPatternEngine()

    candles = build_candles(
        [
            24500,
            24490,
            24500,  # HIGH1
            24465,  # VALLEY
            24480,
            24494,  # HIGH2 only 3 candles later
            24490,
        ]
    )

    signal = engine.detect(
        candles,
        symbol="NIFTY",
    )

    assert signal is None


# ============================================================
# W FAIL - VALLEY2 TOO FAR
# ============================================================


def test_w_rejected_when_valley2_too_far():

    engine = MWPatternEngine()

    candles = build_candles(
        [
            24500,  # VALLEY1
            24510,
            24540,  # HIGH
            24520,
            24510,
            24500,
            24490,
            24480,  # VALLEY2 too low
            24490,
            24500,
        ]
    )

    signal = engine.detect(
        candles,
        symbol="NIFTY",
    )

    assert signal is None
