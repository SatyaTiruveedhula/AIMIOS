from .engine import BaseEngine

class AlertsEngine(BaseEngine):
    name = "Alerts"

    def start(self) -> None:
        super().start()
        # TODO: connect alerts to storage and notify user

    def stop(self) -> None:
        super().stop()
        # TODO: stop alert generation
