"""Market session and lifecycle management."""

from datetime import datetime, time
from enum import Enum


class MarketStatus(Enum):
    PREOPEN = "PREOPEN"
    OPEN = "OPEN"
    POSTCLOSE = "POSTCLOSE"
    CLOSED = "CLOSED"


class MarketSession:
    """Represents a market session (intraday/overnight)."""

    def __init__(self, session_name: str = "regular"):
        self.session_name = session_name
        self.active = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def is_weekend(self) -> bool:
        return datetime.now().weekday() >= 5

    def get_poll_interval(self) -> float | None:
        if self.is_weekend():
            return None

        now = datetime.now().time()
        market_open = time(hour=9, minute=15)
        market_close = time(hour=15, minute=30)

        if market_open <= now <= market_close:
            return 1.0

        return 30.0

    def get_market_status(self) -> MarketStatus:
        if self.is_weekend():
            return MarketStatus.CLOSED

        now = datetime.now().time()
        market_open = time(hour=9, minute=15)
        market_close = time(hour=15, minute=30)
        postclose_end = time(hour=20, minute=0)

        if now < market_open:
            return MarketStatus.PREOPEN
        if market_open <= now <= market_close:
            return MarketStatus.OPEN
        if market_close < now <= postclose_end:
            return MarketStatus.POSTCLOSE
        return MarketStatus.CLOSED
