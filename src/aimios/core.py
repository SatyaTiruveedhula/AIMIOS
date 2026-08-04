import logging
from pathlib import Path
from typing import Dict, Type

from .data.storage import SQLiteStorage
from .engines.engine import BaseEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)

class AIMIOS:
    """Core AIMIOS controller managing engines and data storage."""

    def __init__(self, db_path: str = "aimios.db") -> None:
        self.storage = SQLiteStorage(db_path)
        self.engines: Dict[str, BaseEngine] = {}

    def register_engine(self, engine_class: Type[BaseEngine], **kwargs) -> BaseEngine:
        engine = engine_class(self, **kwargs)
        self.engines[engine.name] = engine
        logger.info("Registered engine: %s", engine.name)
        return engine

    def start_all(self) -> None:
        for engine in self.engines.values():
            logger.info("Starting engine: %s", engine.name)
            engine.start()

    def stop_all(self) -> None:
        for engine in self.engines.values():
            logger.info("Stopping engine: %s", engine.name)
            engine.stop()

    def get_engine(self, name: str) -> BaseEngine | None:
        return self.engines.get(name)
