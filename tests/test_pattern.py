from datetime import datetime, timezone

from aimios.engines.pattern_recognition import PatternRecognitionEngine
from aimios.engines.swing_detection import SwingDetectionEngine
from aimios.market.candle_buffer import Candle


def _make_candle(index: int, price: float) -> Candle:
    return Candle(
        timestamp=datetime.fromtimestamp(1700000000 + index * 60, tz=timezone.utc),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=100.0,
        previous_close=price,
        ticks=1,
        cum_volume=float(index + 1),
        cum_price_volume=float(index + 1) * price,
        vwap=price,
        change_pct=0.0,
        candle_id=index,
        session_id=1,
        color="GREEN",
        body_strength=0.0,
        upper_wick_pct=0.0,
        lower_wick_pct=0.0,
    )


def test_pattern_engine_detects_double_bottom() -> None:
    swing_engine = SwingDetectionEngine(app=None)  # type: ignore[arg-type]
    pattern_engine = PatternRecognitionEngine(app=None, swing_engine=swing_engine)  # type: ignore[arg-type]

    candles = [
        _make_candle(0, 100.0),
        _make_candle(1, 98.0),
        _make_candle(2, 95.0),
        _make_candle(3, 97.0),
        _make_candle(4, 96.0),
    ]
    patterns = pattern_engine.detect_from_candles(candles, symbol="NIFTY")

    assert patterns
    assert patterns[0].pattern in {"DOUBLE_BOTTOM", "W_PATTERN"}
    assert patterns[0].symbol == "NIFTY"
