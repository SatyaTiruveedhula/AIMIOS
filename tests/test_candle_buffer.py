from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aimios.market.candle_buffer import CandleBuffer
from aimios.market.market_snapshot import MarketSnapshot, MarketStatus
from app.analysis.pattern_detector import PatternDetector


def make_snapshot(symbol: str, timestamp: datetime, last_price: float, volume: float) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        ltp=last_price,
        open=last_price,
        high=last_price,
        low=last_price,
        close=last_price,
        volume=volume,
        timestamp=timestamp,
        market_status=MarketStatus.OPEN,
        session="regular",
    )


def test_candle_buffer_update_and_get_last():
    buffer = CandleBuffer(max_candles=5)
    base_time = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)

    snapshot1 = make_snapshot("NIFTY", base_time, last_price=18200.0, volume=1000.0)
    snapshot2 = make_snapshot("NIFTY", base_time + timedelta(seconds=30), last_price=18250.0, volume=1200.0)

    buffer.update(snapshot1)
    buffer.update(snapshot2)

    last_candles = buffer.get_last("NIFTY", 1)
    assert len(last_candles) == 1
    candle = last_candles[0]
    assert candle.open == 18200.0
    assert candle.high == 18250.0
    assert candle.low == 18200.0
    assert candle.close == 18250.0
    assert candle.volume == 1200.0

    latest = buffer.get_latest("NIFTY")
    assert latest == candle


def test_candle_buffer_history_and_lookback_metrics():
    buffer = CandleBuffer(max_candles=3)
    base_time = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)

    for i, (price, volume) in enumerate([(18200.0, 1000.0), (18150.0, 1300.0), (18280.0, 1500.0), (18220.0, 1800.0)]):
        timestamp = base_time + timedelta(minutes=i)
        snapshot = make_snapshot("NIFTY", timestamp, last_price=price, volume=volume)
        buffer.update(snapshot)

    last_three = buffer.get_last("NIFTY", 3)
    assert len(last_three) == 3

    high = buffer.get_high("NIFTY", 3)
    low = buffer.get_low("NIFTY", 3)
    close = buffer.get_close("NIFTY", 3)

    assert high == 18280.0
    assert low == 18150.0
    assert close == 18220.0


def test_candle_buffer_clear():
    buffer = CandleBuffer(max_candles=5)
    base_time = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)

    buffer.update(make_snapshot("NIFTY", base_time, last_price=18200.0, volume=1000.0))
    buffer.update(make_snapshot("BANKNIFTY", base_time, last_price=41000.0, volume=500.0))

    assert buffer.get_last("NIFTY", 1)
    assert buffer.get_last("BANKNIFTY", 1)

    buffer.clear("NIFTY")
    assert buffer.get_last("NIFTY", 1) == []
    assert buffer.get_last("BANKNIFTY", 1)

    buffer.clear()
    assert buffer.get_last("BANKNIFTY", 1) == []


def test_completed_candle_triggers_pattern_subscriber():
    buffer = CandleBuffer(max_candles=5, pattern_detector=PatternDetector())
    base_time = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    received: list[dict] = []

    buffer.subscribe_pattern(lambda _instrument_id, pattern: received.append(pattern))

    for minute, price in enumerate([100.0, 90.0, 110.0, 92.0, 105.0, 105.0]):
        snapshot = make_snapshot("NIFTY", base_time + timedelta(minutes=minute), last_price=price, volume=1000.0)
        buffer.update(snapshot)

    assert received
    assert received[-1].get("pattern")


def test_candle_buffer_vwap_and_session_reset():
    buffer = CandleBuffer(max_candles=5)
    base_time = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)

    snapshot1 = make_snapshot("NIFTY", base_time, last_price=18200.0, volume=1000.0)
    snapshot2 = make_snapshot("NIFTY", base_time + timedelta(seconds=30), last_price=18250.0, volume=1200.0)

    buffer.update(snapshot1)
    buffer.update(snapshot2)

    candle = buffer.get_latest("NIFTY")
    assert candle is not None
    assert round(candle.vwap, 2) == 18233.33
    assert candle.color == "GREEN"
    original_session = candle.session_id

    buffer.reset_session("NIFTY")
    buffer.update(make_snapshot("NIFTY", base_time + timedelta(minutes=1), last_price=18200.0, volume=1400.0))
    candle_after_reset = buffer.get_latest("NIFTY")
    assert candle_after_reset is not None
    assert candle_after_reset.session_id == original_session + 1
