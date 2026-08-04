from __future__ import annotations

import csv
import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any, Dict, Iterable, List, Optional

from broker.kite_feed import KiteFeed
from aimios.market.candle_buffer import CandleBuffer
from aimios.market.market_feed import MarketFeed
from aimios.market.market_snapshot import MarketSnapshot, MarketStatus
from app.analysis.pattern_detector import PatternDetector

try:
    from kiteconnect import KiteTicker
except ImportError as exc:  # pragma: no cover
    logger.exception("kiteconnect import failed")
    KiteTicker = None

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_INSTRUMENT_IDS = ["NIFTY", "BANKNIFTY", "SENSEX"]


class KiteLiveFeed:
    def __init__(
        self,
        instrument_ids: Optional[List[str]] = None,
        candle_buffer: Optional[CandleBuffer] = None,
        pattern_detector: Optional[PatternDetector] = None,
    ) -> None:
        self.instrument_ids = instrument_ids or DEFAULT_INSTRUMENT_IDS
        self._broker = KiteFeed()
        self._pattern_detector = pattern_detector or PatternDetector()
        self.candle_buffer = candle_buffer or CandleBuffer(
            pattern_detector=self._pattern_detector
        )
        if candle_buffer is not None:
            self.candle_buffer.set_pattern_detector(self._pattern_detector)

        self.candle_buffer.subscribe_pattern(self._on_pattern_detected)
        self._ticker: Optional[Any] = None
        self._ready = Event()
        self.pattern_log_path = PROJECT_ROOT / "logs" / "patterns.csv"
        self.pattern_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_pattern_log_header()
        self._subscription_symbols = self._build_subscriptions()
        self._subscription_tokens: List[int] = []
        self._instrument_id_by_token: Dict[int, str] = {}
        self._symbol_to_instrument_id: Dict[str, str] = {}

    def _build_subscriptions(self) -> List[str]:
        index_definitions = MarketFeed.load_index_definitions_from_config()
        instrument_map = {
            item["id"]: f"{item['exchange']}:{item['symbol']}"
            for item in index_definitions
        }
        self._symbol_to_instrument_id = {
            f"{item['exchange']}:{item['symbol']}": item["id"]
            for item in index_definitions
        }
        symbols: List[str] = []
        for instrument_id in self.instrument_ids:
            symbol = instrument_map.get(instrument_id)
            if symbol:
                symbols.append(symbol)
            else:
                logger.warning("Unknown instrument for live feed: %s", instrument_id)
        return symbols

    def _ensure_pattern_log_header(self) -> None:
        if not self.pattern_log_path.exists():
            with self.pattern_log_path.open(
                "w", encoding="utf-8", newline=""
            ) as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=[
                        "timestamp",
                        "symbol",
                        "pattern",
                        "confidence",
                        "price",
                    ],
                )
                writer.writeheader()

    def start(self) -> None:
        logger.info("Entering KiteLiveFeed.start()")
        if KiteTicker is None:
            raise RuntimeError(
                "KiteTicker is unavailable. Install kiteconnect to run live feed."
            )

        self._broker.connect()
        self._broker.login()
        if not self._broker.logged_in:
            logger.info(
                "No cached Kite session found; generating session from request token"
            )
            self._broker.generate_session()
        else:
            logger.info("Cached Kite session detected; skipping generate_session()")

        if self._broker.client is None or self._broker.access_token is None:
            raise RuntimeError("Kite broker did not provide a valid access token")

        self._ticker = KiteTicker(self._broker.api_key, self._broker.access_token)
        self._ticker.on_ticks = self._on_ticks
        self._ticker.on_connect = self._on_connect
        self._ticker.on_close = self._on_close
        self._ticker.on_error = self._on_error
        self._resolve_subscription_tokens()
        logger.info(
            "Starting Kite live feed for symbols: %s", self._subscription_symbols
        )
        logger.info("Starting Kite live feed for tokens: %s", self._subscription_tokens)
        logger.info("About to start Kite websocket connection")
        self._ticker.connect(threaded=True)
        self._ready.wait(timeout=30)
        if not self._ready.is_set():
            raise RuntimeError("KiteTicker did not become ready in time")

    def stop(self) -> None:
        logger.info("Stopping Kite live feed")
        if self._ticker is not None:
            try:
                self._ticker.close()
            except Exception as exc:
                logger.exception("Failed to close KiteTicker")
        self._broker.disconnect()

    def _on_connect(self, ws, response):
        print("WebSocket connected")
        print("Subscribing to:", self._subscription_tokens)
        logger.info("KiteTicker connected")
        if self._ticker is None:
            return

        if not self._subscription_tokens:
            logger.error("No subscription tokens available for KiteTicker")
            self._ticker.stop()
            return

        self._ticker.subscribe(self._subscription_tokens)
        self._ticker.set_mode(self._ticker.MODE_LTP, self._subscription_tokens)
        self._ready.set()

    def _resolve_subscription_tokens(self) -> None:
        if self._broker.client is None:
            raise RuntimeError("Kite broker client is not initialized")

        logger.info("Resolving instrument tokens for live subscription")
        try:
            instruments = self._broker.client.instruments()
        except Exception as exc:
            logger.exception("Failed to fetch instruments list")
            raise RuntimeError("Unable to resolve instrument tokens") from exc

        token_map: Dict[str, int] = {}
        for item in instruments:
            instrument_token = item.get("instrument_token")
            tradingsymbol = item.get("tradingsymbol")
            exchange = item.get("exchange")
            if instrument_token is None or not tradingsymbol or not exchange:
                continue
            token_map[f"{exchange}:{tradingsymbol}"] = int(instrument_token)

        self._subscription_tokens = []
        self._instrument_id_by_token = {}
        for symbol in self._subscription_symbols:
            token = token_map.get(symbol)
            if token is None:
                logger.warning("Could not resolve instrument token for %s", symbol)
                continue
            self._subscription_tokens.append(token)
            self._instrument_id_by_token[token] = self._symbol_to_instrument_id.get(
                symbol, symbol
            )

        if not self._subscription_tokens:
            raise RuntimeError(
                "No valid instrument tokens resolved for live feed subscriptions"
            )

    def _on_error(self, ws, code, reason):
        logger.error("KiteTicker error (%s): %s", code, reason)

    def _on_close(self, ws, code, reason):
        logger.info("KiteTicker closed: %s %s", code, reason)

    def _on_ticks(self, ws, ticks: List[Dict[str, Any]]) -> None:
        # print(f"Received {len(ticks)} ticks")
        for tick in ticks:
            instrument_id = self._resolve_instrument_id(tick)
            if instrument_id is None:
                continue
            snapshot = self._snapshot_from_tick(instrument_id, tick)
            self.candle_buffer.update(snapshot)

    def _resolve_instrument_id(self, tick: Dict[str, Any]) -> Optional[str]:
        if self._instrument_id_by_token:
            token = tick.get("instrument_token")
            if token is not None:
                return self._instrument_id_by_token.get(int(token))

        tradingsymbol = tick.get("tradingsymbol")
        if tradingsymbol:
            symbol = str(tradingsymbol)
            mapped_id = self._symbol_to_instrument_id.get(symbol)
            if mapped_id:
                return mapped_id

        return None

    def _snapshot_from_tick(self, symbol: str, tick: Dict[str, Any]) -> MarketSnapshot:
        ltp = float(tick.get("last_price", 0.0))
        ohlc = tick.get("ohlc") or {}
        open_price = float(ohlc.get("open", ltp))
        high_price = float(ohlc.get("high", ltp))
        low_price = float(ohlc.get("low", ltp))
        close_price = float(ohlc.get("close", ltp))
        volume = float(tick.get("volume", 0.0))
        timestamp = self._parse_timestamp(
            tick.get("timestamp") or tick.get("last_trade_time")
        )

        return MarketSnapshot(
            symbol=symbol,
            ltp=ltp,
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=volume,
            timestamp=timestamp,
            market_status=MarketStatus.OPEN,
            session="live",
        )

    def _parse_timestamp(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value).astimezone(timezone.utc)
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    def _on_pattern_detected(self, symbol: str, pattern: Dict[str, object]) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print("--------------------------------------------------")
        print(f"TIME: {timestamp}")
        print(f"SYMBOL: {symbol}")
        print(f"PATTERN: {pattern.get('pattern')}")
        print(f"CONFIDENCE: {pattern.get('confidence')}%")
        print(f"PRICE: {pattern.get('price')}")
        print("--------------------------------------------------")
        self._append_pattern_log(timestamp, symbol, pattern)

    def _append_pattern_log(
        self, timestamp: str, symbol: str, pattern: Dict[str, object]
    ) -> None:
        if not pattern:
            return
        with self.pattern_log_path.open("a", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=["timestamp", "symbol", "pattern", "confidence", "price"],
            )
            writer.writerow(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "pattern": pattern.get("pattern", ""),
                    "confidence": pattern.get("confidence", 0),
                    "price": pattern.get("price", 0),
                }
            )


def main() -> None:
    shutdown_event = Event()

    def handle_shutdown(signum, frame):
        logger.info("Shutdown signal received: %s", signum)
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    live_feed = KiteLiveFeed()
    try:
        live_feed.start()
        print("AIMIOS Live Feed Running... Press Ctrl+C to stop.")
        shutdown_event.wait()
    except Exception as exc:
        logger.exception("KiteLiveFeed failed to start")
    finally:
        live_feed.stop()


if __name__ == "__main__":
    main()
