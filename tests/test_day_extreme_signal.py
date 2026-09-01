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
