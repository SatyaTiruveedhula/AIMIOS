from datetime import datetime
from zoneinfo import ZoneInfo

from aimios.engines.macro_alerts import MacroNewsManager


def test_macro_news_manager_filters_macro_headlines(monkeypatch) -> None:
    import aimios.engines.macro_alerts as macro_alerts

    class FakeResponse:
        ok = True
        text = """
        <rss>
          <channel>
            <item><title>RBI policy review and repo rate decision</title></item>
            <item><title>Fed FOMC rate decision expected</title></item>
            <item><title>Market buzz about cricket result</title></item>
          </channel>
        </rss>
        """

    def fake_get(url: str, timeout: int, headers=None):
        return FakeResponse()

    monkeypatch.setattr(macro_alerts.requests, "get", fake_get)

    manager = MacroNewsManager()
    events = manager.fetch_today_events()

    assert any("RBI" in event.title.upper() for event in events)
    assert any(
        "FED" in event.title.upper() or "FOMC" in event.title.upper()
        for event in events
    )
    assert all("CRICKET" not in event.title.upper() for event in events)


def test_macro_news_manager_marks_1530_ist_as_due() -> None:
    manager = MacroNewsManager()
    ist = ZoneInfo("Asia/Kolkata")

    assert (
        manager.should_emit_summary(datetime(2026, 11, 17, 15, 29, tzinfo=ist)) is False
    )
    assert (
        manager.should_emit_summary(datetime(2026, 11, 17, 15, 30, tzinfo=ist)) is True
    )
