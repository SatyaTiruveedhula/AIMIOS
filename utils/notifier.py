from typing import Optional

class Notifier:
    """Simple notifier abstraction. Engines decide when to alert; Notifier decides how."""

    def __init__(self, channel: Optional[str] = None):
        self.channel = channel or "console"

    def send(self, title: str, message: str) -> None:
        if self.channel == "console":
            print(f"ALERT: {title} - {message}")
        else:
            # Integrations (Telegram, email, desktop) to be implemented
            raise NotImplementedError
