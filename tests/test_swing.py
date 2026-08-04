from datetime import datetime, timezone

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


def test_swing_detection_identifies_high_and_low_swings() -> None:
    engine = SwingDetectionEngine(app=None)  # type: ignore[arg-type]
    candles = [
        _make_candle(i, 100 + i) for i in range(6)
    ]
    swings = engine.process_candles(candles)

    assert swings
    assert any(swing.kind == "high" for swing in swings)
    assert any(swing.kind == "low" for swing in swings)
