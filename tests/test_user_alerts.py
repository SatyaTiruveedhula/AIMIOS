import json
from pathlib import Path

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
