from __future__ import annotations

import abc
import logging
from typing import Any

logger = logging.getLogger(__name__)


class BaseEngine(abc.ABC):
    """Abstract base class for AIMIOS engines."""

    name: str = "base"

    def __init__(self, app: Any = None, **_: Any) -> None:
        self.app = app
        self.active = False

    @abc.abstractmethod
    def start(self) -> None:
        self.active = True
        logger.debug("Engine %s started", self.name)

    @abc.abstractmethod
    def stop(self) -> None:
        self.active = False
        logger.debug("Engine %s stopped", self.name)

    def status(self) -> str:
        return "running" if self.active else "stopped"
