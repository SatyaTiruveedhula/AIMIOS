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
