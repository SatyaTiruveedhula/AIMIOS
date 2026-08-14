from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.analysis.pattern_detector import PatternDetector


def _make_candle(price: float, index: int = 0):
    return SimpleNamespace(
        timestamp=datetime(
            2026,
            1,
            1,
            9,
            15 + index,
            tzinfo=timezone.utc,
        ),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1000.0,
        previous_close=price,
        ticks=1,
        cum_volume=1000.0,
        cum_price_volume=price * 1000.0,
    )


def _make_candles(prices: list[float]):
    return [_make_candle(price, index) for index, price in enumerate(prices)]


def _run_history(symbol: str, prices: list[float]):
    detector = PatternDetector()
    candles = _make_candles(prices)
    return detector.detect(candles)


def test_detect_double_top():
    detector = PatternDetector()

    prices = [
        100.0,
        90.0,
        110.0,
        92.0,
        105.0,
        105.0,
    ]

    candles = _make_candles(prices)

    pattern = detector._detect_double_top(candles)

    assert pattern is not None
    assert pattern["pattern"] == "DOUBLE_TOP"
    assert pattern["price"] == 105.0
    assert pattern["confidence"] == 88


def test_detect_double_bottom():
    detector = PatternDetector()

    prices = [
        100.0,
        110.0,
        90.0,
        108.0,
        92.0,
        92.0,
    ]

    candles = _make_candles(prices)

    pattern = detector._detect_double_bottom(candles)

    assert pattern is not None
    assert pattern["pattern"] == "DOUBLE_BOTTOM"
    assert pattern["price"] == 92.0
    assert pattern["confidence"] == 88


def test_detect_v_reversal():
    detector = PatternDetector()

    prices = [
        100.0,
        90.0,
        105.0,
        105.0,
    ]

    candles = _make_candles(prices)

    pattern = detector._detect_v(candles)

    assert pattern is not None
    assert pattern["pattern"] == "V_REVERSAL"
    assert pattern["price"] == 105.0
    assert pattern["confidence"] == 85


def test_detect_w_pattern():
    detector = PatternDetector()

    prices = [
        100.0,
        110.0,
        95.0,
        108.0,
        98.0,
        98.0,
    ]

    candles = _make_candles(prices)

    pattern = detector._detect_w(candles)

    assert pattern is not None
    assert pattern["pattern"] == "W"
    assert pattern["price"] == 98.0
    assert pattern["confidence"] == 85


def test_detect_m_pattern():
    detector = PatternDetector()

    prices = [
        100.0,
        90.0,
        110.0,
        92.0,
        105.0,
        100.0,
    ]

    candles = _make_candles(prices)

    pattern = detector._detect_m(candles)

    assert pattern is not None
    assert pattern["pattern"] == "M"
    assert pattern["price"] == 100.0
    assert pattern["confidence"] == 85


def test_detect_breakout():
    detector = PatternDetector()

    prices = [
        100.0,
        100.0,
        105.0,
        105.0,
    ]

    candles = _make_candles(prices)

    pattern = detector._detect_breakout(candles)

    assert pattern is not None
    assert pattern["pattern"] == "BREAKOUT"
    assert pattern["price"] == 105.0
    assert pattern["confidence"] == 80


def test_detect_fake_breakout():
    detector = PatternDetector()

    prices = [
        100.0,
        110.0,
        107.0,
        107.0,
    ]

    candles = _make_candles(prices)

    pattern = detector._detect_fake_breakout(candles)

    assert pattern is not None
    assert pattern["pattern"] == "FAKE_BREAKOUT"
    assert pattern["price"] == 107.0
    assert pattern["confidence"] == 75


def test_detect_exhaustion():
    detector = PatternDetector()

    prices = [
        100.0,
        108.0,
        108.5,
        108.5,
    ]

    candles = _make_candles(prices)

    pattern = detector._detect_exhaustion(candles)

    assert pattern is not None
    assert pattern["pattern"] == "EXHAUSTION"
    assert pattern["price"] == 108.5
    assert pattern["confidence"] == 75


def test_public_detector_returns_pattern():
    detector = PatternDetector()

    prices = [
        100.0,
        90.0,
        110.0,
        92.0,
        105.0,
        105.0,
    ]

    candles = _make_candles(prices)

    pattern = detector.detect(candles)

    assert pattern
    assert "pattern" in pattern
    assert "confidence" in pattern
    assert "price" in pattern
    assert "time" in pattern
