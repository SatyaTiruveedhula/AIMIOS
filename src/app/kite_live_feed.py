from __future__ import annotations

import csv
import json
import logging
import signal
import time

from datetime import datetime
from pathlib import Path
from threading import Event, Lock
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from broker.kite_feed import KiteFeed

from aimios.market.candle_buffer import CandleBuffer
from aimios.market.market_feed import MarketFeed
from aimios.market.market_snapshot import (
    MarketSnapshot,
    MarketStatus,
)

from aimios.engines.day_extreme_pattern_sentinel import (
    DayExtremePatternSentinel,
)

try:
    from kiteconnect import KiteTicker
except ImportError:
    KiteTicker = None


logger = logging.getLogger(__name__)


# ============================================================
# PROJECT
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

IST = ZoneInfo("Asia/Kolkata")


# ============================================================
# DEFAULT INSTRUMENTS
# ============================================================

DEFAULT_INSTRUMENT_IDS = [
    "NIFTY",
    "BANKNIFTY",
    "SENSEX",
]


# ============================================================
# OUTPUT CONTROL
# ============================================================
#
# IMPORTANT:
#
# Health output was previously printed every 5 seconds.
# This created continuous output such as:
#
#   [AIMIOS HEALTH]
#   NIFTY: LTP=...
#   BANKNIFTY: LTP=...
#   SENSEX: LTP=...
#
# That output is now disabled.
#
# AIMIOS will print only important operational messages
# and actual pattern alerts.
#
# ============================================================

ENABLE_HEALTH_PRINT = False

ENABLE_RAW_TICK_PRINT = False

ENABLE_FIRST_TICK_PRINT = False

ENABLE_BROKER_SYNC_PRINT = False


# ============================================================
# KITE LIVE FEED
# ============================================================


class KiteLiveFeed:

    @staticmethod
    def _is_active_alert_symbol(symbol: Optional[str]) -> bool:
        return str(symbol or "").upper() in {"NIFTY", "BANKNIFTY", "SENSEX"}

    def __init__(
        self,
        instrument_ids: Optional[List[str]] = None,
        candle_buffer: Optional[CandleBuffer] = None,
    ) -> None:

        requested = instrument_ids or DEFAULT_INSTRUMENT_IDS
        self.instrument_ids = [
            symbol for symbol in requested if self._is_active_alert_symbol(symbol)
        ] or ["NIFTY", "BANKNIFTY", "SENSEX"]

        # ====================================================
        # BROKER
        # ====================================================

        self._broker = KiteFeed()

        # ====================================================
        # CANDLE BUFFER
        # ====================================================

        self.candle_buffer = candle_buffer or CandleBuffer()

        # ----------------------------------------------------
        # EXISTING M/W PATTERN
        #
        # DO NOT REMOVE
        # ----------------------------------------------------

        self.candle_buffer.subscribe_pattern(self._on_pattern_detected)

        # ====================================================
        # DAY EXTREME SENTINEL
        # ====================================================

        self.day_extreme_sentinel = DayExtremePatternSentinel()

        # ====================================================
        # THREAD / CONTROL
        # ====================================================

        self._ticker: Optional[Any] = None

        self._ready = Event()

        self._stop_event = Event()

        self._tick_lock = Lock()

        # ====================================================
        # EXISTING PATTERN LOG
        # ====================================================

        self.pattern_log_path = PROJECT_ROOT / "logs" / "patterns.csv"

        self.pattern_log_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._ensure_pattern_log_header()

        # ====================================================
        # DAY EXTREME LOG
        # ====================================================

        self.day_extreme_log_path = PROJECT_ROOT / "logs" / "day_extremes.csv"

        self.day_extreme_log_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._ensure_day_extreme_log_header()

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
        # HEALTH / TICK STATE
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

        self._last_snapshots: Dict[
            str,
            MarketSnapshot,
        ] = {}

        self._last_health_print = 0.0

        self._snapshot_printed_initial = False

        self._last_15min_snapshot_epoch = 0.0

        self._runtime_state_path = PROJECT_ROOT / "logs" / "runtime_state.json"

        self._reconnect_attempts = 0

        self._max_reconnect_delay = 30

        self._last_signal_direction: Dict[str, str] = {}

        self._signal_cooling_notified: Dict[str, str] = {}

        self._reconnect_attempts = 0

        self._max_reconnect_delay = 30

        # ====================================================
        # BROKER DAY OHLC
        # ====================================================

        self._broker_ohlc_seen: Dict[
            str,
            bool,
        ] = {}

        # ====================================================
        # DAY EXTREME STATE
        #
        # Only completed candles are sent to the
        # DayExtremePatternSentinel.
        # ====================================================

        self._last_processed_candle_id: Dict[
            str,
            int,
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
    # EXISTING PATTERN CSV HEADER
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
    # DAY EXTREME CSV HEADER
    # ========================================================

    def _ensure_day_extreme_log_header(
        self,
    ) -> None:

        if self.day_extreme_log_path.exists():
            return

        with self.day_extreme_log_path.open(
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
                    "direction",
                    "confidence",
                    "price",
                    "day_high",
                    "day_low",
                    "high1",
                    "valley",
                    "high2",
                    "low1",
                    "peak",
                    "low2",
                ],
            )

            writer.writeheader()

    def _get_runtime_state_path(self):
        path = self._runtime_state_path
        if callable(path):
            path = path()
        return Path(str(path))

    def _save_runtime_state(self) -> None:
        state_path = self._get_runtime_state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "instrument_ids": list(self.instrument_ids),
            "snapshots": [],
            "m_setup": [],
            "w_setup": [],
            "day_extremes": {},
        }

        for instrument_id, snapshot in self._last_snapshots.items():
            payload["snapshots"].append(
                {
                    "symbol": instrument_id,
                    "ltp": float(snapshot.ltp),
                    "open": float(snapshot.open),
                    "high": float(snapshot.high),
                    "low": float(snapshot.low),
                    "close": float(snapshot.close),
                    "volume": float(snapshot.volume),
                    "timestamp": snapshot.timestamp.isoformat(),
                    "market_status": (
                        snapshot.market_status.value
                        if hasattr(snapshot.market_status, "value")
                        else str(snapshot.market_status)
                    ),
                    "session": getattr(snapshot, "session", "regular"),
                }
            )

        if self.day_extreme_sentinel is not None:
            for symbol, setup in getattr(
                self.day_extreme_sentinel, "_m_setup", {}
            ).items():
                payload["m_setup"].append(
                    {
                        "symbol": symbol,
                        "high1": float(setup.high1),
                        "high1_timestamp": (
                            setup.high1_timestamp.isoformat()
                            if getattr(setup, "high1_timestamp", None) is not None
                            else None
                        ),
                        "high1_candle_id": int(getattr(setup, "high1_candle_id", 0)),
                        "valley": (
                            float(setup.valley)
                            if getattr(setup, "valley", None) is not None
                            else None
                        ),
                        "valley_timestamp": (
                            setup.valley_timestamp.isoformat()
                            if getattr(setup, "valley_timestamp", None) is not None
                            else None
                        ),
                        "valley_candle_id": getattr(setup, "valley_candle_id", None),
                        "waiting_printed": bool(
                            getattr(setup, "waiting_printed", False)
                        ),
                        "active": bool(getattr(setup, "active", True)),
                    }
                )

            for symbol, setup in getattr(
                self.day_extreme_sentinel, "_w_setup", {}
            ).items():
                payload["w_setup"].append(
                    {
                        "symbol": symbol,
                        "low1": float(setup.low1),
                        "low1_timestamp": (
                            setup.low1_timestamp.isoformat()
                            if getattr(setup, "low1_timestamp", None) is not None
                            else None
                        ),
                        "low1_candle_id": int(getattr(setup, "low1_candle_id", 0)),
                        "peak": (
                            float(setup.peak)
                            if getattr(setup, "peak", None) is not None
                            else None
                        ),
                        "peak_timestamp": (
                            setup.peak_timestamp.isoformat()
                            if getattr(setup, "peak_timestamp", None) is not None
                            else None
                        ),
                        "peak_candle_id": getattr(setup, "peak_candle_id", None),
                        "waiting_printed": bool(
                            getattr(setup, "waiting_printed", False)
                        ),
                        "active": bool(getattr(setup, "active", True)),
                    }
                )

            payload["day_extremes"] = {
                "day_high": {
                    str(symbol): float(value)
                    for symbol, value in getattr(
                        self.day_extreme_sentinel, "_last_day_high", {}
                    ).items()
                },
                "day_low": {
                    str(symbol): float(value)
                    for symbol, value in getattr(
                        self.day_extreme_sentinel, "_last_day_low", {}
                    ).items()
                },
            }

        with state_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)

    def _restore_runtime_state(self) -> None:
        state_path = self._get_runtime_state_path()
        if not state_path.exists():
            return

        try:
            with state_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError, OSError:
            return

        if not isinstance(payload, dict):
            return

        for item in payload.get("snapshots", []):
            symbol = str(item.get("symbol", ""))
            if not symbol:
                continue
            snapshot = MarketSnapshot(
                symbol=symbol,
                ltp=float(item.get("ltp", 0.0)),
                open=float(item.get("open", item.get("ltp", 0.0))),
                high=float(item.get("high", item.get("ltp", 0.0))),
                low=float(item.get("low", item.get("ltp", 0.0))),
                close=float(item.get("close", item.get("ltp", 0.0))),
                volume=float(item.get("volume", 0.0)),
                timestamp=(
                    datetime.fromisoformat(item["timestamp"])
                    if item.get("timestamp")
                    else datetime.now(IST)
                ),
                market_status=(
                    MarketStatus(item.get("market_status"))
                    if item.get("market_status")
                    in {status.value for status in MarketStatus}
                    else MarketStatus.OPEN
                ),
                session=item.get("session", "regular"),
            )
            self._last_snapshots[symbol] = snapshot

        if self.day_extreme_sentinel is not None:
            for item in payload.get("m_setup", []):
                symbol = str(item.get("symbol", ""))
                if not symbol:
                    continue
                setup_type = type(
                    "RestoredMSetup",
                    (),
                    {
                        "high1": float(item.get("high1", 0.0)),
                        "high1_timestamp": (
                            datetime.fromisoformat(item["high1_timestamp"])
                            if item.get("high1_timestamp")
                            else datetime.now(IST)
                        ),
                        "high1_candle_id": int(item.get("high1_candle_id", 0)),
                        "valley": (
                            float(item["valley"])
                            if item.get("valley") is not None
                            else None
                        ),
                        "valley_timestamp": (
                            datetime.fromisoformat(item["valley_timestamp"])
                            if item.get("valley_timestamp")
                            else None
                        ),
                        "valley_candle_id": item.get("valley_candle_id"),
                        "waiting_printed": bool(item.get("waiting_printed", False)),
                        "active": bool(item.get("active", True)),
                    },
                )
                self.day_extreme_sentinel._m_setup[symbol] = setup_type()

            for item in payload.get("w_setup", []):
                symbol = str(item.get("symbol", ""))
                if not symbol:
                    continue
                setup_type = type(
                    "RestoredWSetup",
                    (),
                    {
                        "low1": float(item.get("low1", 0.0)),
                        "low1_timestamp": (
                            datetime.fromisoformat(item["low1_timestamp"])
                            if item.get("low1_timestamp")
                            else datetime.now(IST)
                        ),
                        "low1_candle_id": int(item.get("low1_candle_id", 0)),
                        "peak": (
                            float(item["peak"])
                            if item.get("peak") is not None
                            else None
                        ),
                        "peak_timestamp": (
                            datetime.fromisoformat(item["peak_timestamp"])
                            if item.get("peak_timestamp")
                            else None
                        ),
                        "peak_candle_id": item.get("peak_candle_id"),
                        "waiting_printed": bool(item.get("waiting_printed", False)),
                        "active": bool(item.get("active", True)),
                    },
                )
                self.day_extreme_sentinel._w_setup[symbol] = setup_type()

            for symbol, value in (
                payload.get("day_extremes", {}).get("day_high", {}).items()
            ):
                self.day_extreme_sentinel._last_day_high[str(symbol)] = float(value)
            for symbol, value in (
                payload.get("day_extremes", {}).get("day_low", {}).items()
            ):
                self.day_extreme_sentinel._last_day_low[str(symbol)] = float(value)

    # ========================================================
    # START
    # ========================================================

    def _connect_kite_ticker(self) -> None:

        if KiteTicker is None:
            raise RuntimeError("kiteconnect is not installed")

        print("")
        print("=" * 60)
        print("AIMIOS LIVE FEED STARTING")
        print("=" * 60)

        print("Connecting to Kite broker...")

        self._broker.connect()

        self._broker.login()

        if not self._broker.logged_in:
            print("No cached Kite session found; generating session...")
            self._broker.generate_session()
        else:
            print("Cached Kite session detected.")

        if self._broker.client is None or self._broker.access_token is None:
            raise RuntimeError("Kite broker did not provide a valid access token")

        self._ticker = KiteTicker(
            self._broker.api_key,
            self._broker.access_token,
        )

        self._ticker.on_ticks = self._on_ticks
        self._ticker.on_connect = self._on_connect
        self._ticker.on_close = self._on_close
        self._ticker.on_error = self._on_error

        self._resolve_subscription_tokens()

        print("Subscription symbols:", self._subscription_symbols)
        print("Subscription tokens:", self._subscription_tokens)
        print("Token mapping:", self._instrument_id_by_token)

        self._ticker.connect(threaded=True)

        if not self._ready.wait(timeout=30):
            self._ticker = None
            raise RuntimeError("KiteTicker did not become ready")

        print("")
        print("=" * 60)
        print("AIMIOS LIVE FEED RUNNING")
        print("=" * 60)
        print("Broker Day OHLC synchronization enabled.")
        print("Existing M/W pattern detection enabled.")
        print("DAY HIGH / DAY LOW detection enabled.")
        print("DAY EXTREME M/W detection enabled.")
        print("Continuous health output disabled.")
        print("Waiting for pattern alerts...")
        print("=" * 60)
        print("")

    def start(
        self,
    ) -> None:

        self._stop_event.clear()
        self._ready.clear()
        self._restore_runtime_state()

        reconnect_delay = 5

        while not self._stop_event.is_set():
            try:
                self._connect_kite_ticker()
                self._reconnect_attempts = 0
                reconnect_delay = 5

                while not self._stop_event.wait(timeout=1):
                    if not self._ready.is_set():
                        break
            except Exception:
                logger.exception("Kite live feed connection failed")

            if self._stop_event.is_set():
                break

            self._reconnect_attempts += 1
            reconnect_delay = min(
                5 * self._reconnect_attempts, self._max_reconnect_delay
            )
            print(f"Kite connection lost. Reconnecting in {reconnect_delay}s...")
            self._ready.clear()
            self._stop_event.wait(timeout=reconnect_delay)

        print("")
        print("Live feed loop exited.")

    def _print_complete_market_snapshot(
        self,
    ) -> None:

        if not self._last_snapshots:
            print("[MARKET SNAPSHOT] No market data yet.")
            return

        print("")
        print("=" * 80)
        print("COMPLETE MARKET SNAPSHOT")
        print("=" * 80)

        for instrument_id in sorted(self.instrument_ids):
            snapshot = self._last_snapshots.get(instrument_id)

            if snapshot is None:
                continue

            print(
                f"{instrument_id:>12} | "
                f"LTP={snapshot.ltp:,.2f} | "
                f"OPEN={snapshot.open:,.2f} | "
                f"HIGH={snapshot.high:,.2f} | "
                f"LOW={snapshot.low:,.2f} | "
                f"CLOSE={snapshot.close:,.2f} | "
                f"VOL={snapshot.volume:,.0f} | "
                f"TIME={snapshot.timestamp.astimezone(IST).strftime('%Y-%m-%d %H:%M:%S')}"
            )

        print("=" * 80)
        print("")

    def _build_instrument_watch_status(
        self,
        instrument_id: str,
    ) -> str:

        status_parts: List[str] = []

        sell_setup = self.day_extreme_sentinel.get_m_setup(instrument_id)
        buy_setup = self.day_extreme_sentinel.get_w_setup(instrument_id)

        snapshot = (
            self._last_snapshots.get(instrument_id)
            if hasattr(self, "_last_snapshots")
            else None
        )
        current_price = None

        if snapshot is not None:
            try:
                current_price = float(snapshot.ltp)
            except AttributeError, TypeError, ValueError:
                current_price = None

        def _near_reversal(
            reference: float, current: float, threshold_pct: float = 0.05
        ) -> bool:
            if reference <= 0 or current <= 0:
                return False
            gap_pct = abs(current - reference) / reference * 100.0
            return gap_pct <= threshold_pct

        if sell_setup:
            high1 = sell_setup.get("high1")
            valley = sell_setup.get("valley")
            if (
                high1 is not None
                and current_price is not None
                and _near_reversal(high1, current_price)
            ):
                valley_text = (
                    f"VALLEY={valley:.2f}" if valley is not None else "VALLEY=NA"
                )
                status_parts.append(
                    "SELL WATCH | "
                    f"H1={high1:.2f} | "
                    f"{valley_text} | "
                    "WAITING FOR SELL REVERSAL"
                )

        if buy_setup and not sell_setup:
            low1 = buy_setup.get("low1")
            peak = buy_setup.get("peak")
            if (
                low1 is not None
                and current_price is not None
                and _near_reversal(low1, current_price)
            ):
                peak_text = f"PEAK={peak:.2f}" if peak is not None else "PEAK=NA"
                status_parts.append(
                    "BUY WATCH | "
                    f"L1={low1:.2f} | "
                    f"{peak_text} | "
                    "WAITING FOR BUY REVERSAL"
                )

        if not status_parts:
            return "WATCH | NONE"

        return " | ".join(status_parts)

    def _print_minimal_market_snapshot(
        self,
    ) -> None:

        if not self._last_snapshots:
            return

        print("")
        print("=" * 60)
        print("15 MIN MARKET SNAPSHOT")
        print("=" * 60)

        for instrument_id in sorted(self.instrument_ids):
            snapshot = self._last_snapshots.get(instrument_id)

            if snapshot is None:
                continue

            print(
                f"{instrument_id:>12} | "
                f"LTP={snapshot.ltp:,.2f} | "
                f"HIGH={snapshot.high:,.2f} | "
                f"LOW={snapshot.low:,.2f} | "
                f"VOL={snapshot.volume:,.0f}"
            )

            watch_status = self._build_instrument_watch_status(instrument_id)
            print(f"{instrument_id:>12} | {watch_status}")

        print("=" * 60)
        print("")

    def _print_complete_market_snapshot(
        self,
    ) -> None:

        if not self._last_snapshots:
            print("[MARKET SNAPSHOT] No market data yet.")
            return

        print("")
        print("=" * 80)
        print("COMPLETE MARKET SNAPSHOT")
        print("=" * 80)

        for instrument_id in sorted(self.instrument_ids):
            snapshot = self._last_snapshots.get(instrument_id)

            if snapshot is None:
                continue

            print(
                f"{instrument_id:>12} | "
                f"LTP={snapshot.ltp:,.2f} | "
                f"OPEN={snapshot.open:,.2f} | "
                f"HIGH={snapshot.high:,.2f} | "
                f"LOW={snapshot.low:,.2f} | "
                f"CLOSE={snapshot.close:,.2f} | "
                f"VOL={snapshot.volume:,.0f} | "
                f"TIME={snapshot.timestamp.astimezone(IST).strftime('%Y-%m-%d %H:%M:%S')}"
            )

            watch_status = self._build_instrument_watch_status(instrument_id)
            print(f"{instrument_id:>12} | {watch_status}")

        print("=" * 80)
        print("")

    def _maybe_print_snapshots(
        self,
    ) -> None:

        if not self._snapshot_printed_initial and self._last_snapshots:
            self._snapshot_printed_initial = True
            self._print_complete_market_snapshot()
            self._last_15min_snapshot_epoch = time.time()
            return

        # TEMPORARILY disabled: no periodic 15-minute snapshot prints.
        return

    # ========================================================
    # STOP
    # ========================================================

    def stop(
        self,
    ) -> None:

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

            self._save_runtime_state()

        except Exception:

            logger.exception("Failed to save runtime state")

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
        # FALLBACK
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

        if not self._stop_event.is_set():
            self._ticker = None

        if not self._stop_event.is_set():
            self._ticker = None

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
        # DISABLED BY DEFAULT
        # ----------------------------------------------------

        if ENABLE_RAW_TICK_PRINT and not self._raw_tick_printed:

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

            self._last_tick_time = datetime.now(IST)

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

                if ENABLE_FIRST_TICK_PRINT:

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
                self._last_snapshots[instrument_id] = snapshot
                self._save_runtime_state()
                self._maybe_print_snapshots()

                # --------------------------------------------
                # EXISTING CANDLE ENGINE
                # --------------------------------------------

                self.candle_buffer.update(snapshot)

                # --------------------------------------------
                # DAY EXTREME ENGINE
                # --------------------------------------------

                self._process_day_extreme_engine(instrument_id)

            except Exception:

                logger.exception(
                    "Failed processing tick for %s",
                    instrument_id,
                )

    # ========================================================
    # DAY EXTREME ENGINE
    # ========================================================

    def _process_day_extreme_engine(
        self,
        instrument_id: str,
    ) -> None:
        """
        Run DayExtremePatternSentinel only when a NEW
        COMPLETED candle is available.

        This does NOT run the pattern engine on every tick.
        """

        try:

            # ------------------------------------------------
            # DAY EXTREMES
            # ------------------------------------------------

            day_high = self.candle_buffer.get_day_high(instrument_id)

            day_low = self.candle_buffer.get_day_low(instrument_id)

            if day_high is None or day_low is None:

                return

            # ------------------------------------------------
            # GET CANDLE HISTORY
            # ------------------------------------------------

            candles = None

            if hasattr(
                self.candle_buffer,
                "get_candles",
            ):

                try:

                    candles = self.candle_buffer.get_candles(instrument_id)

                except TypeError:

                    candles = self.candle_buffer.get_candles()

            elif hasattr(
                self.candle_buffer,
                "get_history",
            ):

                try:

                    candles = self.candle_buffer.get_history(instrument_id)

                except TypeError:

                    candles = self.candle_buffer.get_history()

            elif hasattr(
                self.candle_buffer,
                "history",
            ):

                history = self.candle_buffer.history

                if isinstance(
                    history,
                    dict,
                ):

                    candles = history.get(
                        instrument_id,
                        [],
                    )

                else:

                    candles = history

            # ------------------------------------------------
            # LAST RESORT
            # ------------------------------------------------

            if candles is None:

                history = getattr(
                    self.candle_buffer,
                    "_history",
                    None,
                )

                if isinstance(
                    history,
                    dict,
                ):

                    candles = history.get(
                        instrument_id,
                        [],
                    )

                elif history is not None:

                    candles = history

            if not candles:

                return

            candles = list(candles)

            if not candles:

                return

            # ------------------------------------------------
            # LAST COMPLETED CANDLE
            # ------------------------------------------------

            candle = candles[-1]

            try:

                candle_id = int(
                    getattr(
                        candle,
                        "candle_id",
                        0,
                    )
                )

            except (
                TypeError,
                ValueError,
            ):

                return

            if candle_id <= 0:

                return

            # ------------------------------------------------
            # DO NOT PROCESS SAME CANDLE AGAIN
            # ------------------------------------------------

            previous_id = self._last_processed_candle_id.get(instrument_id)

            if previous_id is not None and candle_id <= previous_id:

                return

            # ------------------------------------------------
            # MARK CANDLE PROCESSED
            # ------------------------------------------------

            self._last_processed_candle_id[instrument_id] = candle_id

            # ------------------------------------------------
            # PROCESS
            # ------------------------------------------------

            alerts = self.day_extreme_sentinel.process_candle(
                symbol=instrument_id,
                candle=candle,
                candles=candles,
                day_high=day_high,
                day_low=day_low,
            )

            # ------------------------------------------------
            # HANDLE ONLY ACTUAL ALERTS
            # ------------------------------------------------

            for alert in alerts:

                self._on_day_extreme_alert(
                    instrument_id,
                    alert,
                )

            self._maybe_emit_cooled_signal_alert(instrument_id)

        except Exception:

            logger.exception(
                "Day extreme processing failed for %s",
                instrument_id,
            )

    def _maybe_emit_cooled_signal_alert(
        self,
        symbol: str,
    ) -> None:

        if not self._is_active_alert_symbol(symbol):
            return

        direction = self._last_signal_direction.get(symbol)

        if not direction:
            return

        direction = direction.upper()

        if direction == "BUY":
            has_active_setup = self.day_extreme_sentinel.get_w_setup(symbol) is not None
        elif direction == "SELL":
            has_active_setup = self.day_extreme_sentinel.get_m_setup(symbol) is not None
        else:
            has_active_setup = False

        if has_active_setup:
            return

        if self._signal_cooling_notified.get(symbol) == direction:
            return

        snapshot = self._last_snapshots.get(symbol)
        price = snapshot.ltp if snapshot is not None else 0.0

        print("")
        print("=" * 60)
        print(f"{direction} SIGNAL LOST STRENGTH")
        print(f"SYMBOL     : {symbol}")
        print(f"PRICE      : {price}")
        print("STATUS     : setup lost strength; waiting for a fresh signal")
        print("=" * 60)
        print("")

        self._signal_cooling_notified[symbol] = direction
        self._last_signal_direction.pop(symbol, None)

    # ========================================================
    # DAY EXTREME ALERT
    # ========================================================

    def _on_day_extreme_alert(
        self,
        symbol: str,
        alert: Dict[str, object],
    ) -> None:

        if not alert:
            return

        if not self._is_active_alert_symbol(symbol):
            return

        timestamp_value = alert.get("timestamp")

        if isinstance(
            timestamp_value,
            datetime,
        ):

            if timestamp_value.tzinfo is None:

                timestamp_value = timestamp_value.replace(tzinfo=IST)

            timestamp = timestamp_value.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")

        else:

            timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

        pattern = alert.get(
            "pattern",
            "",
        )

        direction = alert.get(
            "direction",
            "",
        )

        confidence = alert.get(
            "confidence",
            0,
        )

        price = alert.get(
            "price",
            0,
        )

        # ----------------------------------------------------
        # ACTUAL DAY EXTREME ALERT
        # ----------------------------------------------------

        print("")
        print("=" * 60)
        print("AIMIOS DAY EXTREME ALERT")
        print("=" * 60)

        print(f"TIME       : {timestamp}")

        print(f"SYMBOL     : {symbol}")

        print(f"PATTERN    : {pattern}")

        print(f"DIRECTION  : {direction}")

        print(f"CONFIDENCE : {confidence}%")

        print(f"PRICE      : {price}")

        print(f"DAY HIGH   : " f"{alert.get('day_high', '')}")

        print(f"DAY LOW    : " f"{alert.get('day_low', '')}")

        # ----------------------------------------------------
        # M
        # ----------------------------------------------------

        if pattern == "M":

            print(f"HIGH1      : " f"{alert.get('high1', '')}")

            print(f"VALLEY     : " f"{alert.get('valley', '')}")

            print(f"HIGH2      : " f"{alert.get('high2', '')}")

        # ----------------------------------------------------
        # W
        # ----------------------------------------------------

        if pattern == "W":

            print(f"LOW1       : " f"{alert.get('low1', '')}")

            print(f"PEAK       : " f"{alert.get('peak', '')}")

            print(f"LOW2       : " f"{alert.get('low2', '')}")

        print("=" * 60)
        print("")

        self._last_signal_direction[symbol] = str(direction).upper()
        self._signal_cooling_notified.pop(symbol, None)

        # ----------------------------------------------------
        # CSV
        # ----------------------------------------------------

        self._append_day_extreme_log(
            timestamp,
            symbol,
            alert,
        )

    # ========================================================
    # DAY EXTREME CSV
    # ========================================================

    def _append_day_extreme_log(
        self,
        timestamp: str,
        symbol: str,
        alert: Dict[str, object],
    ) -> None:

        with self.day_extreme_log_path.open(
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
                    "direction",
                    "confidence",
                    "price",
                    "day_high",
                    "day_low",
                    "high1",
                    "valley",
                    "high2",
                    "low1",
                    "peak",
                    "low2",
                ],
            )

            writer.writerow(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "pattern": alert.get(
                        "pattern",
                        "",
                    ),
                    "direction": alert.get(
                        "direction",
                        "",
                    ),
                    "confidence": alert.get(
                        "confidence",
                        0,
                    ),
                    "price": alert.get(
                        "price",
                        0,
                    ),
                    "day_high": alert.get(
                        "day_high",
                        0,
                    ),
                    "day_low": alert.get(
                        "day_low",
                        0,
                    ),
                    "high1": alert.get(
                        "high1",
                        "",
                    ),
                    "valley": alert.get(
                        "valley",
                        "",
                    ),
                    "high2": alert.get(
                        "high2",
                        "",
                    ),
                    "low1": alert.get(
                        "low1",
                        "",
                    ),
                    "peak": alert.get(
                        "peak",
                        "",
                    ),
                    "low2": alert.get(
                        "low2",
                        "",
                    ),
                }
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
        # FALLBACK
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

        # ----------------------------------------------------
        # BROKER SYNC MESSAGE
        #
        # Disabled by default.
        # ----------------------------------------------------

        if not self._broker_ohlc_seen.get(
            instrument_id,
            False,
        ):

            self._broker_ohlc_seen[instrument_id] = True

            if ENABLE_BROKER_SYNC_PRINT:

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

                return value.replace(tzinfo=IST)

            return value.astimezone(IST)

        if isinstance(
            value,
            str,
        ):

            try:

                parsed = datetime.fromisoformat(value)

                if parsed.tzinfo is None:

                    parsed = parsed.replace(tzinfo=IST)

                return parsed.astimezone(IST)

            except ValueError:

                pass

        return datetime.now(IST)

    # ========================================================
    # HEALTH
    #
    # Kept for optional diagnostics.
    #
    # ENABLE_HEALTH_PRINT = False means it is not called
    # from the main live-feed loop.
    # ========================================================

    def _print_health(
        self,
    ) -> None:

        if not ENABLE_HEALTH_PRINT:
            return

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
            datetime.now(IST).strftime("%H:%M:%S"),
        )

        print(
            "Ticks received:",
            count,
        )

        if last_tick is not None:

            age = (datetime.now(IST) - last_tick).total_seconds()

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
    # EXISTING M/W PATTERN ALERT
    # ========================================================

    def _on_pattern_detected(
        self,
        symbol: str,
        pattern: Dict[str, object],
    ) -> None:

        timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

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
    # EXISTING PATTERN CSV
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
