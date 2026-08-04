"""Broker base connector abstract class."""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Iterable, List

class BrokerBase(ABC):
    """Abstract base class for broker feed connectors."""

    connected: bool = False
    logged_in: bool = False

    @abstractmethod
    def connect(self) -> None:
        pass

    @abstractmethod
    def login(self) -> Optional[str]:
        pass

    @abstractmethod
    def generate_session(self) -> str:
        pass

    @abstractmethod
    def disconnect(self) -> None:
        pass

    @abstractmethod
    def get_quote(self, instrument: str) -> Any:
        pass

    @abstractmethod
    def get_quotes(self, instruments: List[str]) -> Any:
        pass

    @abstractmethod
    def get_historical(
        self, instrument: str, start_date: datetime, end_date: datetime, interval: str
    ) -> Any:
        pass

    @abstractmethod
    def start_websocket(self) -> None:
        pass
