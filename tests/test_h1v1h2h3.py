from datetime import datetime
from types import SimpleNamespace

from aimios.engines.h1v1h2h3 import H1V1H2H3Detector


def _candle(ts: str, high: float, low: float) -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=datetime.strptime(ts, "%H:%M").time(),
        high=high,
        low=low,
        close=(high + low) / 2,
    )


def test_h1_v1_h2_h3_emits_ce_and_pe_alerts_once_per_day() -> None:
    detector = H1V1H2H3Detector()
    candles = [
        _candle("09:15", 95.0, 94.0),
        _candle("09:20", 96.0, 95.0),
        _candle("09:25", 98.0, 97.0),
        _candle("09:30", 100.0, 99.0),
        _candle("09:35", 99.0, 82.0),
        _candle("09:40", 88.0, 81.0),
        _candle("09:45", 84.0, 80.0),
        _candle("09:50", 89.0, 84.0),
        _candle("09:55", 92.5, 88.5),
        _candle("10:00", 93.0, 90.0),
        _candle("10:05", 92.0, 91.0),
    ]

    alerts: list[dict] = []
    for index, candle in enumerate(candles):
        alerts.extend(detector.process_candle("NIFTY", candle, candles[: index + 1]))

    directions = {alert["direction"] for alert in alerts}
    assert {"CE", "PE"}.issubset(directions)
    assert len([alert for alert in alerts if alert["direction"] == "CE"]) == 1
    assert len([alert for alert in alerts if alert["direction"] == "PE"]) == 1
    assert all(alert["pattern"] == "H1V1H2H3" for alert in alerts)
