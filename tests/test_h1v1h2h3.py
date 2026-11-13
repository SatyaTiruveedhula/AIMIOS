from datetime import datetime
from types import SimpleNamespace

from aimios.engines.h1v1h2h3 import H1V1H2H3Detector
from app.kite_live_feed import KiteLiveFeed


def _candle(ts: str, high: float, low: float) -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=datetime.strptime(ts, "%H:%M").time(),
        high=high,
        low=low,
        close=(high + low) / 2,
    )


def test_h1_v1_h2_h3_emits_ce_and_pe_alerts_once_per_day() -> None:
    detector = H1V1H2H3Detector(min_wait_minutes=0)
    candles = [
        _candle("09:15", 82020.0, 81980.0),
        _candle("09:20", 82310.0, 82200.0),
        _candle("09:25", 82460.0, 82390.0),
        _candle("09:30", 82490.0, 82420.0),
        _candle("09:35", 82400.0, 82310.0),
        _candle("09:40", 82455.0, 82390.0),
        _candle("09:45", 82475.0, 82410.0),
        _candle("09:50", 82440.0, 82400.0),
        _candle("09:55", 82430.0, 82390.0),
    ]

    alerts: list[dict] = []
    for index, candle in enumerate(candles):
        alerts.extend(detector.process_candle("SENSEX", candle, candles[: index + 1]))

    directions = {alert["direction"] for alert in alerts}
    assert directions.issubset({"CE", "PE"})
    assert len([alert for alert in alerts if alert["direction"] == "CE"]) <= 1
    assert len([alert for alert in alerts if alert["direction"] == "PE"]) <= 1
    assert all(alert["pattern"] == "H1V1H2H3" for alert in alerts)
    assert any(float(alert["h3"]) < float(alert["h2"]) for alert in alerts)


def test_h1_v1_h2_h3_supports_reverse_direction_after_0_5_percent_pullback() -> None:
    detector = H1V1H2H3Detector(min_wait_minutes=0)
    candles = [
        _candle("09:15", 82020.0, 81980.0),
        _candle("09:20", 81920.0, 81560.0),
        _candle("09:25", 81800.0, 81590.0),
        _candle("09:30", 81850.0, 81680.0),
        _candle("09:35", 81970.0, 81740.0),
        _candle("09:40", 82030.0, 81570.0),
        _candle("09:45", 81910.0, 81670.0),
        _candle("09:50", 82080.0, 81880.0),
    ]

    alerts: list[dict] = []
    for index, candle in enumerate(candles):
        alerts.extend(detector.process_candle("SENSEX", candle, candles[: index + 1]))

    assert alerts
    assert {alert["direction"] for alert in alerts}.issubset({"CE", "PE"})
    assert any(float(alert["h3"]) > float(alert["h2"]) for alert in alerts)


def test_h1_v1_h2_h3_allows_reversal_without_second_0_5_trigger() -> None:
    detector = H1V1H2H3Detector(min_wait_minutes=0)
    candles = [
        _candle("09:15", 10050.0, 9950.0),
        _candle("09:20", 10080.0, 9955.0),
        _candle("09:25", 10030.0, 9948.0),
        _candle("09:30", 9975.0, 9940.0),
        _candle("09:35", 9958.0, 9944.0),
        _candle("09:40", 10070.0, 9954.0),
    ]

    alerts: list[dict] = []
    for index, candle in enumerate(candles):
        alerts.extend(detector.process_candle("SENSEX", candle, candles[: index + 1]))

    assert any(
        alert["direction"] == "PE" and float(alert["h3"]) > float(alert["h2"])
        for alert in alerts
    )


def test_live_feed_prints_snapshot_when_day_high_or_low_changes(capsys) -> None:
    feed = KiteLiveFeed.__new__(KiteLiveFeed)
    feed._last_snapshots = {
        "SENSEX": SimpleNamespace(
            ltp=82050.0,
            open=81760.0,
            high=82060.0,
            low=81710.0,
            close=81900.0,
            volume=100,
            timestamp=datetime(2026, 11, 12, 11, 30),
        )
    }
    feed.instrument_ids = ["SENSEX"]
    feed._last_high_low_print = {}
    feed._snapshot_printed_initial = True

    feed._last_snapshots["SENSEX"] = SimpleNamespace(
        ltp=82080.0,
        open=81760.0,
        high=82100.0,
        low=81710.0,
        close=82090.0,
        volume=150,
        timestamp=datetime(2026, 11, 12, 11, 35),
    )

    feed._maybe_print_snapshots()

    captured = capsys.readouterr().out
    assert "COMPLETE MARKET SNAPSHOT" in captured
    assert "HIGH=82,100.00" in captured
    assert "LOW=81,710.00" in captured
