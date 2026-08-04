from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aimios.market.candle_buffer import CandleBuffer
from aimios.market.market_snapshot import MarketSnapshot, MarketStatus
from app.analysis.pattern_detector import PatternDetector


def make_snapshot(symbol: str, timestamp: datetime, price: float, volume: float) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        ltp=price,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=volume,
        timestamp=timestamp,
        market_status=MarketStatus.OPEN,
        session="historical",
    )


def _run_history(symbol: str, prices: list[float]) -> dict:
    buffer = CandleBuffer(pattern_detector=PatternDetector())
    results: list[dict] = []

    def capture(symbol_id: str, pattern: dict) -> None:
        if pattern:
            results.append(pattern)

    buffer.subscribe_pattern(capture)
    base_time = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)

    for minute, price in enumerate(prices):
        timestamp = base_time + timedelta(minutes=minute)
        snapshot = make_snapshot(symbol, timestamp, price, 1000.0)
        buffer.update(snapshot)

    return results[-1] if results else {}


def test_detect_double_top():
    prices = [100.0, 90.0, 110.0, 92.0, 105.0, 105.0]
    pattern = _run_history("NIFTY", prices)
    assert pattern["pattern"] == "DOUBLE_TOP"
    assert pattern["confidence"] > 0


def test_detect_double_bottom():
    prices = [100.0, 110.0, 90.0, 108.0, 92.0, 92.0]
    pattern = _run_history("NIFTY", prices)
    assert pattern["pattern"] == "DOUBLE_BOTTOM"
    assert pattern["confidence"] > 0


def test_detect_v_reversal():
    prices = [100.0, 90.0, 105.0, 105.0]
    pattern = _run_history("NIFTY", prices)
    assert pattern["pattern"] == "V_REVERSAL"
    assert pattern["confidence"] > 0


def test_detect_w_pattern():
    prices = [100.0, 110.0, 95.0, 108.0, 98.0, 98.0]
    pattern = _run_history("NIFTY", prices)
    assert pattern["pattern"] == "W"
    assert pattern["confidence"] > 0


def test_detect_m_pattern():
    prices = [100.0, 90.0, 110.0, 92.0, 105.0, 105.0]
    pattern = _run_history("NIFTY", prices)
    assert pattern["pattern"] in {"M", "DOUBLE_TOP"}
    assert pattern["confidence"] > 0


def test_detect_breakout():
    prices = [100.0, 100.0, 105.0, 105.0]
    pattern = _run_history("NIFTY", prices)
    assert pattern["pattern"] == "BREAKOUT"
    assert pattern["confidence"] > 0


def test_detect_fake_breakout():
    prices = [100.0, 110.0, 107.0, 107.0]
    pattern = _run_history("NIFTY", prices)
    assert pattern["pattern"] == "FAKE_BREAKOUT"
    assert pattern["confidence"] > 0


def test_detect_exhaustion():
    prices = [100.0, 108.0, 108.5, 108.5]
    pattern = _run_history("NIFTY", prices)
    assert pattern["pattern"] == "EXHAUSTION"
    assert pattern["confidence"] > 0
