from __future__ import annotations

import csv
import logging
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock
from typing import Any, Dict, List, Optional

from broker.kite_feed import KiteFeed
from aimios.market.candle_buffer import CandleBuffer
from aimios.market.market_feed import MarketFeed
from aimios.market.market_snapshot import (
    MarketSnapshot,
    MarketStatus,
)

try:
    from kiteconnect import KiteTicker
except ImportError:
    KiteTicker = None


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_INSTRUMENT_IDS = [
    "NIFTY",
    "BANKNIFTY",
    "SENSEX",
]


class KiteLiveFeed:

    def __init__(
        self,
        instrument_ids: Optional[List[str]] = None,
        candle_buffer: Optional[CandleBuffer] = None,
    ) -> None:

        self.instrument_ids = instrument_ids or DEFAULT_INSTRUMENT_IDS

        # ====================================================
        # BROKER
        # ====================================================

        self._broker = KiteFeed()

        # ====================================================
        # CANDLE BUFFER
        # ====================================================

        self.candle_buffer = candle_buffer or CandleBuffer()

        self.candle_buffer.subscribe_pattern(self._on_pattern_detected)

        # ====================================================
        # THREAD / CONTROL
        # ====================================================

        self._ticker: Optional[Any] = None

        self._ready = Event()

        self._stop_event = Event()

        self._tick_lock = Lock()

        # ====================================================
        # PATTERN LOG
        # ====================================================

        self.pattern_log_path = PROJECT_ROOT / "logs" / "patterns.csv"

        self.pattern_log_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._ensure_pattern_log_header()

        # ====================================================
        # SUBSCRIPTIONS
        # ====================================================

        self._subscription_tokens: List[int] = []

        self._instrument_id_by_token: Dict[
            int,
            str,
        ] = {}

        self._symbol_to_instrument_id: Dict[
            str,
            str,
        ] = {}

        self._subscription_symbols = self._build_subscriptions()

        # ====================================================
        # HEALTH
        # ====================================================

        self._tick_count = 0

        self._last_tick_time: Optional[datetime] = None

        self._first_tick_seen: Dict[
            str,
            bool,
        ] = {}

        self._last_prices: Dict[
            str,
            float,
        ] = {}

        self._last_health_print = 0.0

        # ====================================================
        # BROKER DAY OHLC
        # ====================================================

        self._broker_ohlc_seen: Dict[
            str,
            bool,
        ] = {}

        # ====================================================
        # DIAGNOSTIC
        # ====================================================

        self._raw_tick_printed = False

    # ========================================================
    # BUILD SUBSCRIPTIONS
    # ========================================================

    def _build_subscriptions(
        self,
    ) -> List[str]:

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

                logger.warning(
                    "Unknown instrument: %s",
                    instrument_id,
                )

        return symbols

    # ========================================================
    # CSV HEADER
    # ========================================================

    def _ensure_pattern_log_header(
        self,
    ) -> None:

        if self.pattern_log_path.exists():
            return

        with self.pattern_log_path.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as csv_file:

            writer = csv.DictWriter(
                csv_file,
                fieldnames=[
                    "timestamp",
                    "symbol",
                    "pattern",
                    "confidence",
                    "price",
                    "day_high",
                    "day_low",
                ],
            )

            writer.writeheader()

    # ========================================================
    # START
    # ========================================================

    def start(self) -> None:

        if KiteTicker is None:

            raise RuntimeError("kiteconnect is not installed")

        self._stop_event.clear()
        self._ready.clear()

        print("")
        print("=" * 60)
        print("AIMIOS LIVE FEED STARTING")
        print("=" * 60)

        # ----------------------------------------------------
        # BROKER
        # ----------------------------------------------------

        print("Connecting to Kite broker...")

        self._broker.connect()

        self._broker.login()

        if not self._broker.logged_in:

            print("No cached Kite session found; " "generating session...")

            self._broker.generate_session()

        else:

            print("Cached Kite session detected.")

        if self._broker.client is None or self._broker.access_token is None:

            raise RuntimeError("Kite broker did not provide " "a valid access token")

        # ----------------------------------------------------
        # KITE TICKER
        # ----------------------------------------------------

        self._ticker = KiteTicker(
            self._broker.api_key,
            self._broker.access_token,
        )

        self._ticker.on_ticks = self._on_ticks
        self._ticker.on_connect = self._on_connect
        self._ticker.on_close = self._on_close
        self._ticker.on_error = self._on_error

        # ----------------------------------------------------
        # TOKENS
        # ----------------------------------------------------

        self._resolve_subscription_tokens()

        print(
            "Subscription symbols:",
            self._subscription_symbols,
        )

        print(
            "Subscription tokens:",
            self._subscription_tokens,
        )

        print(
            "Token mapping:",
            self._instrument_id_by_token,
        )

        # ----------------------------------------------------
        # CONNECT
        # ----------------------------------------------------

        self._ticker.connect(threaded=True)

        if not self._ready.wait(timeout=30):

            self._stop_event.set()

            raise RuntimeError("KiteTicker did not become ready")

        print("")
        print("=" * 60)
        print("AIMIOS Live Feed Running...")
        print("Broker Day OHLC synchronization enabled.")
        print("M/W pattern detection enabled.")
        print("Waiting for live ticks...")
        print("=" * 60)
        print("")

        # ----------------------------------------------------
        # HEALTH LOOP
        # ----------------------------------------------------

        while not self._stop_event.wait(timeout=5):

            self._print_health()

        print("")
        print("Live feed loop exited.")

    # ========================================================
    # STOP
    # ========================================================

    def stop(self) -> None:

        self._stop_event.set()

        self._ready.clear()

        ticker = self._ticker

        self._ticker = None

        if ticker is not None:

            try:

                ticker.close()

            except Exception:

                logger.exception("Failed to close KiteTicker")

        try:

            self._broker.disconnect()

        except Exception:

            logger.exception("Failed to disconnect broker")

    # ========================================================
    # CONNECT
    # ========================================================

    def _on_connect(
        self,
        ws,
        response,
    ) -> None:

        if self._stop_event.is_set():
            return

        print("")
        print("WebSocket connected")

        print(
            "Subscribing to:",
            self._subscription_tokens,
        )

        if self._ticker is None:
            return

        if not self._subscription_tokens:

            print("ERROR: No subscription tokens!")

            self._stop_event.set()

            return

        self._ticker.subscribe(self._subscription_tokens)

        self._ticker.set_mode(
            self._ticker.MODE_FULL,
            self._subscription_tokens,
        )

        print("Subscription successful.")

        print("FULL market-data mode enabled.")

        self._ready.set()

    # ========================================================
    # RESOLVE TOKENS
    # ========================================================

    def _resolve_subscription_tokens(
        self,
    ) -> None:

        if self._broker.client is None:

            raise RuntimeError("Kite broker client is not initialized")

        print("Resolving instrument tokens...")

        instruments = self._broker.client.instruments()

        token_map: Dict[
            str,
            int,
        ] = {}

        for item in instruments:

            token = item.get("instrument_token")

            tradingsymbol = item.get("tradingsymbol")

            exchange = item.get("exchange")

            if token is None or not tradingsymbol or not exchange:

                continue

            token_map[f"{exchange}:{tradingsymbol}"] = int(token)

        self._subscription_tokens = []

        self._instrument_id_by_token = {}

        for symbol in self._subscription_symbols:

            token = token_map.get(symbol)

            if token is None:

                print(
                    "WARNING: Could not resolve:",
                    symbol,
                )

                continue

            self._subscription_tokens.append(token)

            instrument_id = self._symbol_to_instrument_id.get(symbol)

            if instrument_id:

                self._instrument_id_by_token[token] = instrument_id

        # ----------------------------------------------------
        # FALLBACK TOKEN MAPPING
        #
        # These are the confirmed tokens currently being
        # subscribed by AIMIOS.
        # ----------------------------------------------------

        fallback_token_map = {
            256265: "NIFTY",
            260105: "BANKNIFTY",
            265: "SENSEX",
        }

        for token in self._subscription_tokens:

            if token in fallback_token_map:

                if token not in self._instrument_id_by_token:

                    self._instrument_id_by_token[token] = fallback_token_map[token]

        if not self._subscription_tokens:

            raise RuntimeError("No valid instrument tokens resolved")

        print(
            "Resolved token mapping:",
            self._instrument_id_by_token,
        )

    # ========================================================
    # ERROR
    # ========================================================

    def _on_error(
        self,
        ws,
        code,
        reason,
    ) -> None:

        print(f"KiteTicker ERROR: {code} {reason}")

    # ========================================================
    # CLOSE
    # ========================================================

    def _on_close(
        self,
        ws,
        code,
        reason,
    ) -> None:

        print(f"KiteTicker CLOSED: {code} {reason}")

        self._ready.clear()

    # ========================================================
    # TICKS
    # ========================================================

    def _on_ticks(
        self,
        ws,
        ticks: List[Dict[str, Any]],
    ) -> None:

        if not ticks:
            return

        # ----------------------------------------------------
        # RAW TICK DIAGNOSTIC
        #
        # Print only once so the console is not flooded.
        # ----------------------------------------------------

        if not self._raw_tick_printed:

            self._raw_tick_printed = True

            print("")
            print("=" * 60)
            print("RAW KITE TICK RECEIVED")
            print("=" * 60)
            print(ticks[0])
            print("=" * 60)
            print("")

        with self._tick_lock:

            self._tick_count += len(ticks)

            self._last_tick_time = datetime.now(timezone.utc)

        for tick in ticks:

            instrument_id = self._resolve_instrument_id(tick)

            if instrument_id is None:

                continue

            # ------------------------------------------------
            # BROKER DAY OHLC
            # ------------------------------------------------

            self._sync_broker_ohlc(
                instrument_id,
                tick,
            )

            # ------------------------------------------------
            # FIRST TICK
            # ------------------------------------------------

            if not self._first_tick_seen.get(
                instrument_id,
                False,
            ):

                self._first_tick_seen[instrument_id] = True

                self._print_first_tick(
                    instrument_id,
                    tick,
                )

            # ------------------------------------------------
            # SNAPSHOT
            # ------------------------------------------------

            try:

                snapshot = self._snapshot_from_tick(
                    instrument_id,
                    tick,
                )

                self._last_prices[instrument_id] = snapshot.ltp

                self.candle_buffer.update(snapshot)

            except Exception:

                logger.exception(
                    "Failed processing tick for %s",
                    instrument_id,
                )

    # ========================================================
    # RESOLVE INSTRUMENT
    # ========================================================

    def _resolve_instrument_id(
        self,
        tick: Dict[str, Any],
    ) -> Optional[str]:

        token = tick.get("instrument_token")

        if token is None:
            return None

        try:

            token = int(token)

        except (
            TypeError,
            ValueError,
        ):

            return None

        # ----------------------------------------------------
        # NORMAL MAPPING
        # ----------------------------------------------------

        instrument_id = self._instrument_id_by_token.get(token)

        if instrument_id:

            return instrument_id

        # ----------------------------------------------------
        # FALLBACK MAPPING
        # ----------------------------------------------------

        fallback_map = {
            256265: "NIFTY",
            260105: "BANKNIFTY",
            265: "SENSEX",
        }

        instrument_id = fallback_map.get(token)

        if instrument_id:

            self._instrument_id_by_token[token] = instrument_id

            logger.warning(
                "Using fallback token mapping | " "token=%s | instrument=%s",
                token,
                instrument_id,
            )

            return instrument_id

        # ----------------------------------------------------
        # UNKNOWN TOKEN
        # ----------------------------------------------------

        logger.warning(
            "Unknown Kite instrument token: %s",
            token,
        )

        return None

    # ========================================================
    # BROKER OHLC
    # ========================================================

    def _sync_broker_ohlc(
        self,
        instrument_id: str,
        tick: Dict[str, Any],
    ) -> None:

        ohlc = tick.get("ohlc") or {}

        try:

            open_price = float(
                ohlc.get(
                    "open",
                    0.0,
                )
            )

            high_price = float(
                ohlc.get(
                    "high",
                    0.0,
                )
            )

            low_price = float(
                ohlc.get(
                    "low",
                    0.0,
                )
            )

            previous_close = float(
                ohlc.get(
                    "close",
                    0.0,
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            return

        if high_price <= 0 or low_price <= 0:

            return

        timestamp = self._parse_timestamp(
            tick.get("timestamp") or tick.get("last_trade_time")
        )

        self.candle_buffer.sync_broker_day_ohlc(
            instrument_id=instrument_id,
            timestamp=timestamp,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            previous_close=previous_close,
        )

        if not self._broker_ohlc_seen.get(
            instrument_id,
            False,
        ):

            self._broker_ohlc_seen[instrument_id] = True

            print("")
            print("[BROKER DAY OHLC SYNC]")

            print(f"{instrument_id}")

            print(f"Open     : {open_price}")

            print(f"Day High : {high_price}")

            print(f"Day Low  : {low_price}")

            print(f"PrevClose: {previous_close}")

            print("Broker day range synchronized.")

            print("")

    # ========================================================
    # FIRST TICK
    # ========================================================

    def _print_first_tick(
        self,
        instrument_id: str,
        tick: Dict[str, Any],
    ) -> None:

        ohlc = tick.get("ohlc") or {}

        print("")
        print("=" * 60)
        print("FIRST LIVE TICK RECEIVED")
        print("=" * 60)

        print(
            "Instrument :",
            instrument_id,
        )

        print(
            "Token      :",
            tick.get("instrument_token"),
        )

        print(
            "LTP        :",
            tick.get("last_price"),
        )

        print(
            "Open       :",
            ohlc.get("open"),
        )

        print(
            "Day High   :",
            ohlc.get("high"),
        )

        print(
            "Day Low    :",
            ohlc.get("low"),
        )

        print(
            "Prev Close :",
            ohlc.get("close"),
        )

        print(
            "Volume     :",
            tick.get("volume"),
        )

        print("=" * 60)
        print("")

    # ========================================================
    # SNAPSHOT
    # ========================================================

    def _snapshot_from_tick(
        self,
        symbol: str,
        tick: Dict[str, Any],
    ) -> MarketSnapshot:

        ltp = float(
            tick.get(
                "last_price",
                0.0,
            )
        )

        ohlc = tick.get("ohlc") or {}

        open_price = float(
            ohlc.get(
                "open",
                ltp,
            )
        )

        high_price = float(
            ohlc.get(
                "high",
                ltp,
            )
        )

        low_price = float(
            ohlc.get(
                "low",
                ltp,
            )
        )

        close_price = float(
            ohlc.get(
                "close",
                ltp,
            )
        )

        volume_value = tick.get(
            "volume",
            0.0,
        )

        try:

            volume = float(volume_value or 0.0)

        except (
            TypeError,
            ValueError,
        ):

            volume = 0.0

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

    # ========================================================
    # TIMESTAMP
    # ========================================================

    def _parse_timestamp(
        self,
        value: Any,
    ) -> datetime:

        if isinstance(
            value,
            datetime,
        ):

            if value.tzinfo is None:

                return value.replace(tzinfo=timezone.utc)

            return value.astimezone(timezone.utc)

        if isinstance(
            value,
            str,
        ):

            try:

                parsed = datetime.fromisoformat(value)

                if parsed.tzinfo is None:

                    parsed = parsed.replace(tzinfo=timezone.utc)

                return parsed.astimezone(timezone.utc)

            except ValueError:

                pass

        return datetime.now(timezone.utc)

    # ========================================================
    # HEALTH
    # ========================================================

    def _print_health(
        self,
    ) -> None:

        now = time.time()

        if now - self._last_health_print < 4:

            return

        self._last_health_print = now

        with self._tick_lock:

            count = self._tick_count

            last_tick = self._last_tick_time

        print("")
        print(
            "[AIMIOS HEALTH]",
            datetime.now().strftime("%H:%M:%S"),
        )

        print(
            "Ticks received:",
            count,
        )

        if last_tick is not None:

            age = (datetime.now(timezone.utc) - last_tick).total_seconds()

            print(
                "Last tick age:",
                f"{age:.1f}s",
            )

        else:

            print("Last tick age: NO TICKS")

        print("")

        for instrument_id in self.instrument_ids:

            price = self._last_prices.get(instrument_id)

            day_high = self.candle_buffer.get_day_high(instrument_id)

            day_low = self.candle_buffer.get_day_low(instrument_id)

            synced = self.candle_buffer.is_broker_day_synced(instrument_id)

            if price is None:

                print(
                    f"{instrument_id}: "
                    f"WAITING FOR PRICE | "
                    f"DAY_HIGH={day_high} | "
                    f"DAY_LOW={day_low} | "
                    f"BROKER_SYNC={synced}"
                )

            else:

                print(
                    f"{instrument_id}: "
                    f"LTP={price} | "
                    f"DAY_HIGH={day_high} | "
                    f"DAY_LOW={day_low} | "
                    f"BROKER_SYNC={synced}"
                )

        print("")

    # ========================================================
    # PATTERN ALERT
    # ========================================================

    def _on_pattern_detected(
        self,
        symbol: str,
        pattern: Dict[str, object],
    ) -> None:

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        print("")
        print("-" * 60)

        print(f"TIME       : {timestamp}")

        print(f"SYMBOL     : {symbol}")

        print(f"PATTERN    : " f"{pattern.get('pattern')}")

        print(f"DIRECTION  : " f"{pattern.get('direction')}")

        print(f"CONFIDENCE : " f"{pattern.get('confidence')}%")

        print(f"PRICE      : " f"{pattern.get('price')}")

        print(f"DAY HIGH   : " f"{pattern.get('day_high')}")

        print(f"DAY LOW    : " f"{pattern.get('day_low')}")

        print("-" * 60)
        print("")

        self._append_pattern_log(
            timestamp,
            symbol,
            pattern,
        )

    # ========================================================
    # PATTERN CSV
    # ========================================================

    def _append_pattern_log(
        self,
        timestamp: str,
        symbol: str,
        pattern: Dict[str, object],
    ) -> None:

        if not pattern:
            return

        with self.pattern_log_path.open(
            "a",
            encoding="utf-8",
            newline="",
        ) as csv_file:

            writer = csv.DictWriter(
                csv_file,
                fieldnames=[
                    "timestamp",
                    "symbol",
                    "pattern",
                    "confidence",
                    "price",
                    "day_high",
                    "day_low",
                ],
            )

            writer.writerow(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "pattern": pattern.get(
                        "pattern",
                        "",
                    ),
                    "confidence": pattern.get(
                        "confidence",
                        0,
                    ),
                    "price": pattern.get(
                        "price",
                        0,
                    ),
                    "day_high": pattern.get(
                        "day_high",
                        0,
                    ),
                    "day_low": pattern.get(
                        "day_low",
                        0,
                    ),
                }
            )


# ============================================================
# MAIN
# ============================================================


def main() -> None:

    live_feed: Optional[KiteLiveFeed] = None

    def handle_shutdown(
        signum,
        frame,
    ) -> None:

        print("")
        print("Shutdown signal received.")

        if live_feed is not None:

            live_feed.stop()

    signal.signal(
        signal.SIGINT,
        handle_shutdown,
    )

    signal.signal(
        signal.SIGTERM,
        handle_shutdown,
    )

    live_feed = KiteLiveFeed()

    try:

        live_feed.start()

    except KeyboardInterrupt:

        print("Keyboard interrupt received.")

    except Exception:

        logger.exception("KiteLiveFeed failed")

    finally:

        if live_feed is not None:

            live_feed.stop()


if __name__ == "__main__":

    main()
