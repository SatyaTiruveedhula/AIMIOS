from datetime import datetime, timezone

from aimios.engines.day_extreme_pattern_sentinel import DayExtremePatternSentinel
from app.kite_live_feed import KiteLiveFeed


def test_day_extreme_signal_defaults_match_user_requirements() -> None:
    sentinel = DayExtremePatternSentinel()

    assert sentinel.min_reversal_pct == 0.11
    assert sentinel.min_second_swing_difference_pct == 0.05
    assert sentinel.min_candles_between_extremes == 5
    assert sentinel.max_buy_per_day == 2
    assert sentinel.max_sell_per_day == 2
    assert sentinel.min_signal_confidence == 80.0
    assert sentinel._passes_confidence_gate(80.0)
    assert not sentinel._passes_confidence_gate(79.9)


def test_snapshot_watch_status_reports_active_sell_and_buy_setup() -> None:
    feed = object.__new__(KiteLiveFeed)
    sentinel = DayExtremePatternSentinel()
    feed.day_extreme_sentinel = sentinel
    feed._last_snapshots = {
        "NIFTY": type(
            "Snapshot",
            (),
            {"ltp": 99.95},
        )()
    }

    sentinel._m_setup["NIFTY"] = type(
        "Setup",
        (),
        {
            "high1": 100.0,
            "high1_timestamp": None,
            "high1_candle_id": 1,
            "valley": 99.0,
            "valley_timestamp": None,
            "valley_candle_id": 2,
            "waiting_printed": True,
            "active": True,
        },
    )()
    sentinel._w_setup["NIFTY"] = type(
        "Setup",
        (),
        {
            "low1": 98.0,
            "low1_timestamp": None,
            "low1_candle_id": 3,
            "peak": 99.5,
            "peak_timestamp": None,
            "peak_candle_id": 4,
            "waiting_printed": True,
            "active": True,
        },
    )()

    status = feed._build_instrument_watch_status("NIFTY")

    assert "SELL WATCH" in status
    assert "H1=100.00" in status
    assert "VALLEY=99.00" in status
    assert "WAITING FOR SELL REVERSAL" in status
    assert "BUY WATCH" not in status


def test_merge_signal_alerts_keeps_only_best_buy_and_sell() -> None:
    sentinel = DayExtremePatternSentinel()

    buy_ai = sentinel._build_alert(
        symbol="NIFTY",
        pattern="W",
        alert_type="AI_LOGIC",
        direction="BUY",
        price=100.0,
        candle=None,
        confidence=82.0,
        logic="AI",
    )
    buy_my = sentinel._build_alert(
        symbol="NIFTY",
        pattern="MY_LOGIC_BUY",
        alert_type="MY_LOGIC",
        direction="BUY",
        price=100.0,
        candle=None,
        confidence=90.0,
        logic="MY_LOGIC",
    )
    sell_ai = sentinel._build_alert(
        symbol="NIFTY",
        pattern="M",
        alert_type="AI_LOGIC",
        direction="SELL",
        price=99.0,
        candle=None,
        confidence=91.0,
        logic="AI",
    )
    sell_my = sentinel._build_alert(
        symbol="NIFTY",
        pattern="MY_LOGIC_SELL",
        alert_type="MY_LOGIC",
        direction="SELL",
        price=99.0,
        candle=None,
        confidence=87.0,
        logic="MY_LOGIC",
    )

    merged = sentinel._merge_signal_alerts([buy_ai, buy_my, sell_ai, sell_my])

    assert len(merged) == 2
    directions = {alert["direction"] for alert in merged}
    assert directions == {"BUY", "SELL"}
    assert (
        max(alert["confidence"] for alert in merged if alert["direction"] == "BUY")
        == 90.0
    )
    assert (
        max(alert["confidence"] for alert in merged if alert["direction"] == "SELL")
        == 91.0
    )


def test_watch_status_only_prints_near_reversal() -> None:
    feed = object.__new__(KiteLiveFeed)
    sentinel = DayExtremePatternSentinel()
    feed.day_extreme_sentinel = sentinel
    feed._last_snapshots = {
        "NIFTY": type(
            "Snapshot",
            (),
            {"ltp": 99.95},
        )()
    }

    sentinel._m_setup["NIFTY"] = type(
        "Setup",
        (),
        {
            "high1": 100.0,
            "high1_timestamp": None,
            "high1_candle_id": 1,
            "valley": 99.0,
            "valley_timestamp": None,
            "valley_candle_id": 2,
            "waiting_printed": True,
            "active": True,
        },
    )()

    status = feed._build_instrument_watch_status("NIFTY")

    assert "SELL WATCH" in status
    assert "WAITING FOR SELL REVERSAL" in status

    feed._last_snapshots = {
        "NIFTY": type(
            "Snapshot",
            (),
            {"ltp": 90.0},
        )()
    }
    status_far = feed._build_instrument_watch_status("NIFTY")
    assert status_far == "WATCH | NONE"


def test_eod_signal_evaluation_scores_buy_and_sell_outcomes() -> None:
    sentinel = DayExtremePatternSentinel()
    symbol = "NIFTY"
    day_key = "2026-09-01"

    sentinel._signal_history[symbol] = [
        {
            "symbol": symbol,
            "direction": "BUY",
            "entry_price": 98.0,
            "timestamp": datetime(2026, 9, 1, 9, 15, tzinfo=timezone.utc),
            "confidence": 90.0,
            "pattern": "W",
            "day_key": day_key,
        },
        {
            "symbol": symbol,
            "direction": "SELL",
            "entry_price": 105.0,
            "timestamp": datetime(2026, 9, 1, 10, 15, tzinfo=timezone.utc),
            "confidence": 88.0,
            "pattern": "M",
            "day_key": day_key,
        },
    ]

    summary = sentinel.evaluate_eod_signal_quality(
        symbol,
        day_close_price=101.0,
    )

    assert summary["total_signals"] == 2
    assert summary["buy_good"] == 1
    assert summary["sell_good"] == 1
    assert summary["win_rate"] == 100.0
    assert summary["status"] == "GOOD"


def test_runtime_state_can_be_saved_and_restored_for_restart_recovery(tmp_path) -> None:
    feed = object.__new__(KiteLiveFeed)
    feed.instrument_ids = ["NIFTY"]
    feed.day_extreme_sentinel = DayExtremePatternSentinel()
    feed._last_snapshots = {
        "NIFTY": type(
            "Snapshot",
            (),
            {
                "symbol": "NIFTY",
                "ltp": 101.25,
                "open": 100.0,
                "high": 102.0,
                "low": 99.5,
                "close": 101.25,
                "volume": 5000,
                "timestamp": datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc),
                "market_status": "OPEN",
                "session": "regular",
            },
        )()
    }
    feed.day_extreme_sentinel._m_setup["NIFTY"] = type(
        "Setup",
        (),
        {
            "high1": 102.0,
            "high1_timestamp": datetime(2026, 9, 1, 9, 20, tzinfo=timezone.utc),
            "high1_candle_id": 12,
            "valley": 100.0,
            "valley_timestamp": datetime(2026, 9, 1, 9, 25, tzinfo=timezone.utc),
            "valley_candle_id": 13,
            "waiting_printed": True,
            "active": True,
        },
    )()

    save_path = tmp_path / "runtime_state.json"
    feed._runtime_state_path = lambda: save_path

    feed._save_runtime_state()

    restored = object.__new__(KiteLiveFeed)
    restored.instrument_ids = ["NIFTY"]
    restored.day_extreme_sentinel = DayExtremePatternSentinel()
    restored._last_snapshots = {}
    restored._runtime_state_path = lambda: save_path

    restored._restore_runtime_state()

    assert restored._last_snapshots["NIFTY"].ltp == 101.25
    assert restored.day_extreme_sentinel.get_m_setup("NIFTY")["high1"] == 102.0
    assert restored.day_extreme_sentinel.get_m_setup("NIFTY")["valley"] == 100.0


def test_signal_cooling_alert_emits_once_per_cycle(capsys) -> None:
    feed = object.__new__(KiteLiveFeed)
    feed.day_extreme_sentinel = DayExtremePatternSentinel()
    feed._last_snapshots = {
        "NIFTY": type(
            "Snapshot",
            (),
            {"ltp": 101.0},
        )()
    }
    feed._last_signal_direction = {"NIFTY": "BUY"}
    feed._signal_cooling_notified = {}

    feed._maybe_emit_cooled_signal_alert("NIFTY")
    first = capsys.readouterr().out
    assert "BUY SIGNAL COOLED" in first

    feed._maybe_emit_cooled_signal_alert("NIFTY")
    second = capsys.readouterr().out
    assert "BUY SIGNAL COOLED" not in second
