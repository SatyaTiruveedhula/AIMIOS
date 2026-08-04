import json
import logging
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, Optional

from broker.broker_base import BrokerBase
from .market_session import MarketSession
from .market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)
DEFAULT_INDEX_DEFINITIONS = [
    {"id": "NIFTY", "exchange": "NSE", "symbol": "NIFTY 50"},
    {"id": "BANKNIFTY", "exchange": "NSE", "symbol": "NIFTY BANK"},
    {"id": "SENSEX", "exchange": "BSE", "symbol": "SENSEX"},
]

class FeedStatus(Enum):
    STOPPED = "STOPPED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    AUTH_FAILED = "AUTH_FAILED"

class MarketFeed:
    CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "indices.json"

    @staticmethod
    def load_index_definitions_from_config() -> list[dict]:
        try:
            with MarketFeed.CONFIG_PATH.open("r", encoding="utf-8") as config_file:
                config = json.load(config_file)
                indices = config.get("indices", [])
                return [
                    {
                        "id": item["id"],
                        "exchange": item["exchange"],
                        "symbol": item["symbol"],
                    }
                    for item in indices
                    if item.get("id") and item.get("exchange") and item.get("symbol")
                ]
        except FileNotFoundError:
            logger.warning("Indices config not found at %s; using default instruments", MarketFeed.CONFIG_PATH)
        except json.JSONDecodeError as exc:
            logger.warning("Invalid indices config JSON: %s; using default instruments", exc)
        return DEFAULT_INDEX_DEFINITIONS

    def __init__(self, broker: BrokerBase, instruments: Optional[list[str]] = None) -> None:
        self.broker = broker
        self.index_definitions = self.load_index_definitions_from_config()
        self._instrument_map = {
            item["id"]: f"{item['exchange']}:{item['symbol']}"
            for item in self.index_definitions
        }
        self.instruments = instruments or list(self._instrument_map)
        invalid_ids = [instrument for instrument in self.instruments if instrument not in self._instrument_map]
        if invalid_ids:
            raise ValueError(f"Unknown instrument IDs: {invalid_ids}")

        self._snapshot: Optional[Dict[str, MarketSnapshot]] = None
        self._lock = threading.Lock()
        self._loop_thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._reconnect_attempts = 0
        self.feed_status = FeedStatus.STOPPED
        self.subscribers: list[Callable[[Dict[str, MarketSnapshot]], None]] = []
        self.market_session = MarketSession()

    def start(self) -> None:
        logger.info("Starting market feed")
        self.feed_status = FeedStatus.CONNECTING

        if not self.broker.connected:
            self.broker.connect()

        if not self.broker.logged_in:
            try:
                self.broker.login()
                self.broker.generate_session()
            except Exception as exc:
                self.feed_status = FeedStatus.AUTH_FAILED
                logger.error("Market feed authentication failed: %s", exc)
                self.broker.disconnect()
                raise
        else:
            logger.info("Reusing existing broker access token")

        if self.market_session.is_weekend():
            self.feed_status = FeedStatus.STOPPED
            logger.info("Market session is weekend; feed will not start")
            return

        self.feed_status = FeedStatus.CONNECTED
        self.market_session.start()
        self._running.set()
        self._loop_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._loop_thread.start()
        logger.info("Market feed started")

    def stop(self) -> None:
        logger.info("Stopping market feed")
        self.feed_status = FeedStatus.STOPPED
        self._running.clear()
        if self._loop_thread and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=5)
        self.broker.disconnect()
        self.market_session.stop()
        logger.info("Market feed stopped")

    def get_snapshot(self) -> Optional[Dict[str, MarketSnapshot]]:
        with self._lock:
            return self._snapshot.copy() if self._snapshot is not None else None

    def subscribe(self, callback: Callable[[Dict[str, MarketSnapshot]], None]) -> None:
        self.subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[Dict[str, MarketSnapshot]], None]) -> None:
        if callback in self.subscribers:
            self.subscribers.remove(callback)

    def _publish(self, snapshots: Dict[str, MarketSnapshot]) -> None:
        for subscriber in list(self.subscribers):
            try:
                subscriber(snapshots)
            except Exception as exc:
                logger.exception("Subscriber callback failed: %s", exc)

    def _poll_loop(self) -> None:
        while self._running.is_set():
            if self.market_session.is_weekend():
                self.feed_status = FeedStatus.STOPPED
                logger.info("Market session is weekend; stopping polling")
                break

            try:
                self._refresh_snapshot()
                if self._reconnect_attempts > 0:
                    logger.info("Connection restored after %d retry attempts", self._reconnect_attempts)
                self._reconnect_attempts = 0
                self.feed_status = FeedStatus.CONNECTED
            except Exception as exc:
                self._reconnect_attempts += 1
                self.feed_status = FeedStatus.RECONNECTING
                logger.warning(
                    "Market feed polling failed (attempt %d): %s",
                    self._reconnect_attempts,
                    exc,
                )
                time.sleep(min(5 * self._reconnect_attempts, 30))
                continue

            interval = self.market_session.get_poll_interval()
            if interval is None:
                self.feed_status = FeedStatus.STOPPED
                logger.info("Market session indicates weekend/stop state")
                break

            time.sleep(interval)

    def _refresh_snapshot(self) -> None:
        if not self.broker.connected:
            raise RuntimeError("Broker is not connected")

        broker_symbols = [self._instrument_map[instrument_id] for instrument_id in self.instruments]
        logger.debug("Fetching quotes for %s", broker_symbols)
        quote_response = self.broker.get_quotes(broker_symbols)
        snapshots: Dict[str, MarketSnapshot] = {}

        for instrument_id in self.instruments:
            broker_symbol = self._instrument_map[instrument_id]
            quote = quote_response.get(broker_symbol)
            if not quote:
                logger.warning("No quote data returned for %s", instrument)
                continue

            ohlc = quote.get("ohlc", {})
            timestamp_value = quote.get("timestamp") or quote.get("last_trade_time")
            timestamp = datetime.now(timezone.utc)
            if isinstance(timestamp_value, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp_value)
                except ValueError:
                    logger.debug("Unable to parse broker timestamp %s", timestamp_value)
            elif isinstance(timestamp_value, datetime):
                timestamp = timestamp_value

            snapshots[instrument_id] = MarketSnapshot(
                symbol=instrument_id,
                ltp=float(quote.get("last_price", 0.0)),
                open=float(ohlc.get("open", 0.0)),
                high=float(ohlc.get("high", 0.0)),
                low=float(ohlc.get("low", 0.0)),
                close=float(ohlc.get("close", 0.0)),
                volume=float(quote.get("volume", 0.0)),
                timestamp=timestamp,
                market_status=self.market_session.get_market_status(),
                session=self.market_session.session_name,
            )

        if not snapshots:
            raise RuntimeError("No valid quotes were returned for configured instruments")

        should_publish = self._snapshot is None or snapshots != self._snapshot
        if should_publish:
            with self._lock:
                self._snapshot = snapshots

            logger.debug("Updated market snapshots: %s", snapshots)
            self._publish(snapshots)
        else:
            logger.debug("Market snapshots unchanged; publishing skipped")
