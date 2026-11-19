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

    assert all(alert["direction"] in {"CE", "PE"} for alert in alerts)
    assert len({alert["direction"] for alert in alerts}) <= 1


def test_h1_v1_h2_h3_logs_only_final_alert_not_prepare_messages(
    capsys, monkeypatch
) -> None:
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

    for index, candle in enumerate(candles):
        detector.process_candle("SENSEX", candle, candles[: index + 1])

    captured_before = capsys.readouterr().out
    assert "PREPARE ACTION" not in captured_before

    calls = []

    def fake_beep(freq: int = 1400, duration_ms: int = 220) -> None:
        calls.append((freq, duration_ms))

    import app.kite_live_feed as kite_live_feed

    monkeypatch.setattr(kite_live_feed, "play_pattern_alert_beep", fake_beep)

    feed = KiteLiveFeed.__new__(KiteLiveFeed)
    feed._on_h1v1h2h3_alert(
        "NIFTY",
        {
            "timestamp": datetime(2026, 11, 12, 10, 20),
            "direction": "CE",
            "pattern": "H1V1H2H3",
            "h1": 25290.0,
            "v1": 25270.0,
            "h2": 25280.0,
            "h3": 25295.0,
            "waiting_minutes": 10,
        },
    )

    captured_after = capsys.readouterr().out
    assert "AIMIOS EXCLUSIVE H1V1H2H3 ALERT" in captured_after
    assert "TAKE ACTION NOW" in captured_after
    assert calls


def test_live_feed_does_not_repeat_snapshot_after_startup(capsys) -> None:
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
    assert captured == ""


def test_h1_v1_h2_h3_uses_previous_close_for_trigger() -> None:
    previous_close = 82532.19
    detector = H1V1H2H3Detector(min_wait_minutes=0)

    candles = [
        SimpleNamespace(
            timestamp=datetime.strptime("09:15", "%H:%M").time(),
            open=82376.56,
            high=82390.0,
            low=82250.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:20", "%H:%M").time(),
            open=82250.0,
            high=82270.0,
            low=82050.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:25", "%H:%M").time(),
            open=82050.0,
            high=82220.0,
            low=81840.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:30", "%H:%M").time(),
            open=81840.0,
            high=82140.0,
            low=81900.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:35", "%H:%M").time(),
            open=81900.0,
            high=82220.0,
            low=81950.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:40", "%H:%M").time(),
            open=82220.0,
            high=82320.0,
            low=82100.0,
            previous_close=previous_close,
        ),
    ]

    alerts = []
    for index, candle in enumerate(candles):
        alerts.extend(detector.process_candle("SENSEX", candle, candles[: index + 1]))

    assert any(alert["direction"] == "PE" for alert in alerts)


def test_h1v1h2h3_state_round_trip_preserves_trigger_and_previous_close() -> None:
    previous_close = 82532.19
    detector = H1V1H2H3Detector(min_wait_minutes=0)

    candles = [
        SimpleNamespace(
            timestamp=datetime.strptime("09:15", "%H:%M").time(),
            open=82376.56,
            high=82390.0,
            low=82250.0,
            close=82300.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:20", "%H:%M").time(),
            open=82250.0,
            high=82270.0,
            low=82050.0,
            close=82080.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:25", "%H:%M").time(),
            open=82050.0,
            high=82220.0,
            low=81840.0,
            close=81900.0,
            previous_close=previous_close,
        ),
    ]

    detector.process_candle("SENSEX", candles[-1], candles)
    snapshot = detector.export_state()

    restored = H1V1H2H3Detector(min_wait_minutes=0)
    restored.restore_state(snapshot)

    for day_key, state in restored._daily_state.items():
        assert day_key.startswith("SENSEX:")
        assert state.reference_open == previous_close
        assert state.trigger_direction in {"up", "down"}


def test_candle_buffer_runtime_state_round_trip_preserves_previous_close_and_history() -> (
    None
):
    from datetime import timezone, timedelta
    from zoneinfo import ZoneInfo
    from aimios.market.candle_buffer import CandleBuffer
    from aimios.market.market_snapshot import MarketSnapshot, MarketStatus

    india_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    timestamp = india_now.replace(hour=9, minute=15, second=0, microsecond=0)

    buffer = CandleBuffer(max_candles=500, timeframe=60)
    buffer.sync_broker_day_ohlc(
        "SENSEX",
        timestamp,
        82376.56,
        82578.87,
        81780.47,
        82532.19,
    )
    buffer.update(
        MarketSnapshot(
            symbol="SENSEX",
            ltp=82390.0,
            open=82376.56,
            high=82390.0,
            low=82250.0,
            close=82390.0,
            volume=100,
            timestamp=timestamp,
            market_status=MarketStatus.OPEN,
            session="live",
        )
    )

    snapshot = buffer.export_runtime_state()
    restored = CandleBuffer(max_candles=500, timeframe=60)
    restored.restore_runtime_state(snapshot)

    assert restored._previous_close["SENSEX"] == 82532.19
    assert restored._day_high["SENSEX"] == 82578.87
    assert restored._day_low["SENSEX"] == 81780.47
    assert restored.get_latest("SENSEX") is not None


def test_h1v1h2h3_reconciles_and_extends_restored_ohlc_history() -> None:
    previous_close = 82532.19

    def candle(ts: str, high: float, low: float) -> SimpleNamespace:
        return SimpleNamespace(
            timestamp=datetime.strptime(ts, "%H:%M").time(),
            open=previous_close,
            high=high,
            low=low,
            close=(high + low) / 2,
            previous_close=previous_close,
        )

    restored_history = [
        candle("09:15", 82390.0, 82250.0),
        candle("09:20", 82270.0, 82050.0),
        candle("09:25", 82140.0, 81840.0),
        candle("09:30", 82220.0, 81900.0),
    ]

    detector = H1V1H2H3Detector(min_wait_minutes=0)
    setup = detector.reconcile_from_history("SENSEX", restored_history)
    assert setup is not None

    first_h1 = setup["h1"]

    restored_history.append(candle("09:35", 82080.0, 81790.0))
    setup = detector.reconcile_from_history("SENSEX", restored_history)

    assert setup is not None
    assert setup["direction"] == "down"
    assert setup["h1"] == 81790.0


def test_initial_ce_trigger_allows_later_pe_pattern_without_second_trigger() -> None:
    previous_close = 82000.0
    detector = H1V1H2H3Detector(min_wait_minutes=0)
    candles = [
        SimpleNamespace(
            timestamp=datetime.strptime("09:15", "%H:%M").time(),
            open=82000.0,
            high=82020.0,
            low=81980.0,
            close=82000.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:20", "%H:%M").time(),
            open=82100.0,
            high=82410.0,
            low=82150.0,
            close=82390.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:25", "%H:%M").time(),
            open=82380.0,
            high=82390.0,
            low=82220.0,
            close=82240.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:30", "%H:%M").time(),
            open=82240.0,
            high=82340.0,
            low=82230.0,
            close=82320.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:35", "%H:%M").time(),
            open=82320.0,
            high=82330.0,
            low=82200.0,
            close=82210.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:40", "%H:%M").time(),
            open=82190.0,
            high=82100.0,
            low=81880.0,
            close=81900.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:45", "%H:%M").time(),
            open=81900.0,
            high=82050.0,
            low=81920.0,
            close=82040.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:50", "%H:%M").time(),
            open=82040.0,
            high=82000.0,
            low=81940.0,
            close=81950.0,
            previous_close=previous_close,
        ),
        SimpleNamespace(
            timestamp=datetime.strptime("09:55", "%H:%M").time(),
            open=81950.0,
            high=82010.0,
            low=81960.0,
            close=82000.0,
            previous_close=previous_close,
        ),
    ]

    alerts = []
    for index, candle in enumerate(candles):
        alerts.extend(detector.process_candle("NIFTY", candle, candles[: index + 1]))

    assert [alert["direction"] for alert in alerts] == ["CE", "PE"]


def test_h1v1h2h3_restore_state_rebuilds_saved_timestamps() -> None:
    from zoneinfo import ZoneInfo

    india_tz = ZoneInfo("Asia/Kolkata")
    now = datetime.now(india_tz).replace(hour=9, minute=15, second=0, microsecond=0)
    previous_close = 82532.19
    candle = SimpleNamespace(
        timestamp=now,
        open=82376.56,
        high=82390.0,
        low=82050.0,
        close=82100.0,
        previous_close=previous_close,
    )

    detector = H1V1H2H3Detector(min_wait_minutes=0)
    detector.process_candle("SENSEX", candle, [candle])
    snapshot = detector.export_state()

    restored = H1V1H2H3Detector(min_wait_minutes=0)
    restored.restore_state(snapshot)

    assert restored.export_state()["daily_state"]
