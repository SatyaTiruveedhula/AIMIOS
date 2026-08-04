"""Market snapshot models and utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class MarketStatus(Enum):
    PREOPEN = "PREOPEN"
    OPEN = "OPEN"
    POSTCLOSE = "POSTCLOSE"
    CLOSED = "CLOSED"


@dataclass
class MarketSnapshot:
    symbol: str
    ltp: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: datetime
    market_status: MarketStatus
    session: str

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if len(args) == 4 and not kwargs:
            self.symbol = str(args[1])
            self.ltp = float(args[2])
            self.open = float(args[2])
            self.high = float(args[2])
            self.low = float(args[2])
            self.close = float(args[2])
            self.volume = float(args[3])
            self.timestamp = datetime.now()
            self.market_status = MarketStatus.OPEN
            self.session = "legacy"
            return

        if len(args) == 10 and not kwargs:
            self.symbol = str(args[0])
            self.ltp = float(args[1])
            self.open = float(args[2])
            self.high = float(args[3])
            self.low = float(args[4])
            self.close = float(args[5])
            self.volume = float(args[6])
            self.timestamp = args[7]
            self.market_status = args[8]
            self.session = args[9]
            return

        if kwargs:
            self.symbol = kwargs.get("symbol", "")
            self.ltp = float(kwargs.get("ltp", 0.0))
            self.open = float(kwargs.get("open", self.ltp))
            self.high = float(kwargs.get("high", self.ltp))
            self.low = float(kwargs.get("low", self.ltp))
            self.close = float(kwargs.get("close", self.ltp))
            self.volume = float(kwargs.get("volume", 0.0))
            self.timestamp = kwargs.get("timestamp", datetime.now())
            self.market_status = kwargs.get("market_status", MarketStatus.OPEN)
            self.session = kwargs.get("session", "regular")
            return

        raise TypeError("Unsupported MarketSnapshot initialization signature")

    @property
    def price(self) -> float:
        return self.ltp
