import json
from pathlib import Path

from app.kite_live_feed import KiteLiveFeed
from aimios.engines.user_alerts import UserInitiatedPriceAlertManager


def test_user_price_alert_triggers_at_custom_level(capsys) -> None:
    manager = UserInitiatedPriceAlertManager(
        [
            {
                "symbol": "SENSEX",
                "trigger_price": 80500.0,
                "direction": "down",
                "label": "manual target",
            }
        ]
    )

    manager.process_price("SENSEX", 81000.0)
    manager.process_price("SENSEX", 80500.0)

    output = capsys.readouterr().out
    assert "USER INITIATED ALERT" in output
    assert "SENSEX" in output
    assert "80500.00" in output
    assert "manual target" in output


def test_kite_live_feed_marks_index_symbols_as_active() -> None:
    assert KiteLiveFeed._is_active_alert_symbol("NIFTY") is True
    assert KiteLiveFeed._is_active_alert_symbol("BANKNIFTY") is True
    assert KiteLiveFeed._is_active_alert_symbol("SENSEX") is True


def test_user_alert_manager_loads_json_file_and_reloads(tmp_path: Path) -> None:
    config_path = tmp_path / "user_alerts.json"
    config_path.write_text(
        json.dumps(
            [
                {
                    "symbol": "SENSEX",
                    "trigger_price": 80500.0,
                    "direction": "down",
                    "label": "manual target",
                }
            ]
        ),
        encoding="utf-8",
    )

    manager = UserInitiatedPriceAlertManager(config_path=config_path)
    assert len(manager._alerts) == 1

    config_path.write_text(
        json.dumps(
            [
                {
                    "symbol": "SENSEX",
                    "trigger_price": 80750.0,
                    "direction": "up",
                    "label": "reloaded target",
                }
            ]
        ),
        encoding="utf-8",
    )

    count = manager.reload_from_file(config_path)
    assert count == 1
    assert "reloaded target" in next(iter(manager._alerts.values())).label
    assert next(iter(manager._alerts.values())).trigger_price == 80750.0


def test_user_alert_manager_fires_if_price_is_already_above_trigger_on_reload(
    capsys,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "user_alerts.json"
    config_path.write_text(
        json.dumps(
            [
                {
                    "symbol": "NIFTY",
                    "trigger_price": 25250.0,
                    "direction": "up",
                    "label": "manual target",
                    "enabled": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    manager = UserInitiatedPriceAlertManager(config_path=config_path)
    manager.process_snapshot(
        "NIFTY",
        type(
            "Snapshot",
            (),
            {"ltp": 25254.20},
        )(),
    )

    captured = capsys.readouterr().out
    assert "USER INITIATED ALERT" in captured
    assert "NIFTY" in captured
    assert "25254.20" in captured


def test_user_alert_manager_emits_a_light_beep_when_triggered(monkeypatch) -> None:
    calls = []

    def fake_beep(freq: int = 900, duration_ms: int = 180) -> None:
        calls.append((freq, duration_ms))

    import aimios.engines.user_alerts as user_alerts

    monkeypatch.setattr(user_alerts, "play_alert_beep", fake_beep)

    manager = UserInitiatedPriceAlertManager(
        [
            {
                "symbol": "NIFTY",
                "trigger_price": 25250.0,
                "direction": "up",
                "label": "manual target",
            }
        ]
    )

    manager.process_price("NIFTY", 25260.0)

    assert calls
    assert calls[0][0] > 0
    assert calls[0][1] > 0
