import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

class SQLiteStorage:
    """Simple SQLite storage manager for AIMIOS."""

    def __init__(self, database_path: str | Path = "aimios.db") -> None:
        self.database_path = Path(database_path)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                engine TEXT NOT NULL,
                symbol TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                engine TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.commit()
        logger.info("Initialized storage schema at %s", self.database_path)

    def insert_alert(self, engine: str, symbol: str, message: str) -> int:
        cursor = self.connection.cursor()
        cursor.execute(
            "INSERT INTO alerts (engine, symbol, message) VALUES (?, ?, ?)",
            (engine, symbol, message),
        )
        self.connection.commit()
        return cursor.lastrowid

    def insert_feed(self, engine: str, payload: str) -> int:
        cursor = self.connection.cursor()
        cursor.execute(
            "INSERT INTO feeds (engine, payload) VALUES (?, ?)",
            (engine, payload),
        )
        self.connection.commit()
        return cursor.lastrowid

    def close(self) -> None:
        self.connection.close()
