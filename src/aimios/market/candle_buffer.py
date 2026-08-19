from __future__ import annotations

import csv
import io
import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Deque, Dict, List, Optional

from .market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)


# ============================================================
# CANDLE
# ============================================================


@dataclass(frozen=True)
class Candle:
    timestamp: datetime

    open: float
    high: float
    low: float
    close: float

    volume: float
    previous_close: float

    ticks: int

    cum_volume: float
    cum_price_volume: float

    change_pct: float = 0.0
    candle_id: int = 0

    body_strength: float = 0.0
    upper_wick_pct: float = 0.0
    lower_wick_pct: float = 0.0

    vwap: float = 0.0

    session_id: int = 1
    color: str = "DOJI"

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return max(
            self.high - max(self.open, self.close),
            0.0,
        )

    @property
    def lower_wick(self) -> float:
        return max(
            min(self.open, self.close) - self.low,
            0.0,
        )


# ============================================================
# CANDLE BUFFER
# ============================================================


class CandleBuffer:

    def __init__(
        self,
        max_candles: int = 500,
        timeframe: int = 60,
        pattern_detector=None,
    ) -> None:

        if max_candles <= 0:
            raise ValueError("max_candles must be positive")

        if timeframe <= 0:
            raise ValueError("timeframe must be positive")

        self.max_candles = max_candles
        self.timeframe = timeframe

        self._lock = RLock()

        # ----------------------------------------------------
        # COMPLETED CANDLES
        # ----------------------------------------------------

        self._history: Dict[str, Deque[Candle]] = defaultdict(
            lambda: deque(maxlen=self.max_candles)
        )

        # ----------------------------------------------------
        # CURRENT / UNFINISHED CANDLE
        # ----------------------------------------------------

        self._current: Dict[str, Candle] = {}

        # ----------------------------------------------------
        # VOLUME
        # ----------------------------------------------------

        self._last_volume: Dict[str, float] = {}

        # ----------------------------------------------------
        # HIGH / LOW CACHE
        # ----------------------------------------------------

        self._high_cache: Dict[str, Deque[float]] = defaultdict(
            lambda: deque(maxlen=self.max_candles)
        )

        self._low_cache: Dict[str, Deque[float]] = defaultdict(
            lambda: deque(maxlen=self.max_candles)
        )

        # ----------------------------------------------------
        # BROKER DAY OHLC
        # ----------------------------------------------------

        self._day_key: Dict[str, str] = {}
        self._day_high: Dict[str, float] = {}
        self._day_low: Dict[str, float] = {}
        self._broker_day_synced: Dict[str, bool] = {}

        # ----------------------------------------------------
        # CANDLE IDS
        # ----------------------------------------------------

        self._next_candle_id: Dict[str, int] = defaultdict(int)

        # ----------------------------------------------------
        # SESSION
        # ----------------------------------------------------

        self._session_id: Dict[str, int] = defaultdict(lambda: 1)

        # ----------------------------------------------------
        # CANDLE SUBSCRIBERS
        # ----------------------------------------------------

        self._subscribers: List[Callable[[str, Candle], None]] = []

        # ----------------------------------------------------
        # PATTERN SUBSCRIBERS
        # ----------------------------------------------------

        self._pattern_subscribers: List[Callable[[str, Dict[str, object]], None]] = []

        # ----------------------------------------------------
        # DAY EXTREME PATTERN SENTINEL
        #
        # This is the NEW M/W detector.
        #
        # It is deliberately separate from the old
        # PatternSentinel.
        #
        # DAY HIGH / DAY LOW / M / W are generated from
        # completed candles only.
        # ----------------------------------------------------

        self._day_extreme_sentinel = None

        try:
            from aimios.engines.day_extreme_pattern_sentinel import (
                DayExtremePatternSentinel,
            )

            self._day_extreme_sentinel = DayExtremePatternSentinel()

            logger.info("DayExtremePatternSentinel initialized")

        except Exception:
            logger.exception("Failed to initialize " "DayExtremePatternSentinel")

            self._day_extreme_sentinel = None

        # ----------------------------------------------------
        # LEGACY PATTERN DETECTOR
        #
        # Kept for compatibility with existing AIMIOS code.
        #
        # IMPORTANT:
        # The new DayExtremePatternSentinel is independent.
        # ----------------------------------------------------

        self._pattern_detector = pattern_detector

        logger.info(
            "Initialized CandleBuffer(" "max_candles=%d, timeframe=%ds)",
            self.max_candles,
            self.timeframe,
        )

    # ========================================================
    # SUBSCRIPTIONS
    # ========================================================

    def subscribe(
        self,
        callback: Callable[[str, Candle], None],
    ) -> None:

        with self._lock:

            if callback not in self._subscribers:
                self._subscribers.append(callback)

    # --------------------------------------------------------

    def unsubscribe(
        self,
        callback: Callable[[str, Candle], None],
    ) -> None:

        with self._lock:

            if callback in self._subscribers:
                self._subscribers.remove(callback)

    # --------------------------------------------------------

    def subscribe_pattern(
        self,
        callback: Callable[
            [str, Dict[str, object]],
            None,
        ],
    ) -> None:

        with self._lock:

            if callback not in self._pattern_subscribers:
                self._pattern_subscribers.append(callback)

    # --------------------------------------------------------

    def unsubscribe_pattern(
        self,
        callback: Callable[
            [str, Dict[str, object]],
            None,
        ],
    ) -> None:

        with self._lock:

            if callback in self._pattern_subscribers:
                self._pattern_subscribers.remove(callback)

    # --------------------------------------------------------

    def set_pattern_detector(
        self,
        pattern_detector,
    ) -> None:

        self._pattern_detector = pattern_detector

    # ========================================================
    # BROKER DAY OHLC SYNCHRONIZATION
    # ========================================================

    def sync_broker_day_ohlc(
        self,
        instrument_id: str,
        timestamp: datetime,
        open_price: float,
        high_price: float,
        low_price: float,
        previous_close: float = 0.0,
    ) -> None:

        try:
            high_price = float(high_price)
            low_price = float(low_price)

        except TypeError, ValueError:
            return

        if high_price <= 0 or low_price <= 0:
            return

        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        day_key = self._india_day_key(timestamp)

        with self._lock:

            previous_day = self._day_key.get(instrument_id)

            # ------------------------------------------------
            # NEW DAY
            # ------------------------------------------------

            if previous_day is not None and previous_day != day_key:

                self._day_high.pop(
                    instrument_id,
                    None,
                )

                self._day_low.pop(
                    instrument_id,
                    None,
                )

                self._broker_day_synced[instrument_id] = False

                # Reset Day Extreme Sentinel for new day.
                if self._day_extreme_sentinel is not None:
                    try:
                        self._day_extreme_sentinel.clear_symbol(instrument_id)
                    except Exception:
                        logger.exception(
                            "Failed to reset DayExtremePatternSentinel "
                            "for new day | %s",
                            instrument_id,
                        )

            self._day_key[instrument_id] = day_key

            # ------------------------------------------------
            # BROKER VALUES ARE AUTHORITATIVE
            # ------------------------------------------------

            self._day_high[instrument_id] = high_price
            self._day_low[instrument_id] = low_price

            self._broker_day_synced[instrument_id] = True

        logger.info(
            "Broker Day OHLC synchronized | " "%s | date=%s | high=%s | low=%s",
            instrument_id,
            day_key,
            high_price,
            low_price,
        )

    # ========================================================
    # BROKER SYNC STATUS
    # ========================================================

    def is_broker_day_synced(
        self,
        instrument_id: str,
    ) -> bool:

        with self._lock:

            return self._broker_day_synced.get(
                instrument_id,
                False,
            )

    # ========================================================
    # PUBLISH COMPLETED CANDLE
    # ========================================================

    def _publish(
        self,
        instrument_id: str,
        candle: Candle,
    ) -> None:

        subscribers = list(self._subscribers)

        for subscriber in subscribers:

            try:
                subscriber(
                    instrument_id,
                    candle,
                )

            except Exception:
                logger.exception("CandleBuffer subscriber failed")

    # ========================================================
    # PUBLISH PATTERN
    # ========================================================

    def _publish_pattern(
        self,
        instrument_id: str,
        patterns: Dict[str, object],
    ) -> None:

        if not patterns:
            return

        subscribers = list(self._pattern_subscribers)

        for subscriber in subscribers:

            try:
                subscriber(
                    instrument_id,
                    patterns,
                )

            except Exception:
                logger.exception("Pattern subscriber failed")

    # ========================================================
    # UPDATE
    # ========================================================

    def update(
        self,
        snapshot: MarketSnapshot,
    ) -> None:

        if snapshot is None:
            return

        instrument_id = str(snapshot.symbol)

        if not instrument_id:
            return

        try:
            ltp = float(snapshot.ltp)
        except TypeError, ValueError:
            return

        if ltp <= 0:
            return

        bucket_time = self._align_timestamp(snapshot.timestamp)

        with self._lock:

            # ------------------------------------------------
            # ENSURE DAY RANGE IS INITIALIZED
            # ------------------------------------------------

            self._ensure_day(
                instrument_id,
                bucket_time,
            )

            volume_delta = self._calculate_volume_delta(
                instrument_id,
                snapshot.volume,
            )

            current = self._current.get(instrument_id)

            # ------------------------------------------------
            # TIMEFRAME CHANGED
            #
            # Current candle becomes completed.
            # ------------------------------------------------

            if current is not None and bucket_time != current.timestamp:

                completed = current

                self._current.pop(
                    instrument_id,
                    None,
                )

                self._append_candle(
                    instrument_id,
                    completed,
                )

                current = None

            # ------------------------------------------------
            # CREATE NEW CURRENT CANDLE
            # ------------------------------------------------

            if current is None:

                new_candle = self._create_candle(
                    instrument_id=instrument_id,
                    timestamp=bucket_time,
                    ltp=ltp,
                    volume_delta=volume_delta,
                )

                self._current[instrument_id] = new_candle

                # ------------------------------------------------
                # LIVE PRICE CAN EXTEND DAY RANGE
                # ------------------------------------------------

                self._update_day_extremes(
                    instrument_id,
                    bucket_time,
                    ltp,
                )

                return

            # ------------------------------------------------
            # UPDATE CURRENT CANDLE
            # ------------------------------------------------

            updated = self._update_candle(
                current=current,
                ltp=ltp,
                volume_delta=volume_delta,
            )

            self._current[instrument_id] = updated

            # ------------------------------------------------
            # LIVE CANDLE CAN EXTEND DAY RANGE
            # ------------------------------------------------

            self._update_day_extremes(
                instrument_id,
                bucket_time,
                updated.high,
            )

            self._update_day_extremes(
                instrument_id,
                bucket_time,
                updated.low,
            )

    # ========================================================
    # APPEND COMPLETED CANDLE
    # ========================================================

    def _append_candle(
        self,
        instrument_id: str,
        candle: Candle,
    ) -> None:

        # ----------------------------------------------------
        # STORE COMPLETED CANDLE
        # ----------------------------------------------------

        self._history[instrument_id].append(candle)

        # ----------------------------------------------------
        # UPDATE HIGH/LOW CACHES
        # ----------------------------------------------------

        self._high_cache[instrument_id].append(candle.high)

        self._low_cache[instrument_id].append(candle.low)

        # ----------------------------------------------------
        # UPDATE DAY RANGE
        # ----------------------------------------------------

        self._update_day_extremes(
            instrument_id,
            candle.timestamp,
            candle.high,
        )

        self._update_day_extremes(
            instrument_id,
            candle.timestamp,
            candle.low,
        )

        # ----------------------------------------------------
        # PUBLISH COMPLETED CANDLE
        # ----------------------------------------------------

        self._publish(
            instrument_id,
            candle,
        )

        # ----------------------------------------------------
        # NEW DAY EXTREME M/W SENTINEL
        #
        # IMPORTANT:
        # Only completed candles are passed.
        # ----------------------------------------------------

        self._run_day_extreme_sentinel(
            instrument_id,
            candle,
        )

    # ========================================================
    # DAY EXTREME PATTERN SENTINEL
    # ========================================================

    def _run_day_extreme_sentinel(
        self,
        instrument_id: str,
        candle: Candle,
    ) -> None:

        sentinel = self._day_extreme_sentinel

        if sentinel is None:
            return

        try:

            completed_candles = list(self._history[instrument_id])

            # ------------------------------------------------
            # Need enough completed candles for:
            #
            # HIGH1/LOW1
            # valley/peak
            # HIGH2/LOW2
            #
            # Minimum outer separation = 7 candles.
            # ------------------------------------------------

            if len(completed_candles) < 8:
                return

            day_high = self._day_high.get(instrument_id)

            day_low = self._day_low.get(instrument_id)

            if day_high is None or day_low is None:
                return

            # ------------------------------------------------
            # PROCESS COMPLETED CANDLE
            # ------------------------------------------------

            results = sentinel.process_candle(
                symbol=instrument_id,
                candle=candle,
                candles=completed_candles,
                day_high=day_high,
                day_low=day_low,
            )

            if not results:
                return

            # ------------------------------------------------
            # Sentinel returns a LIST of alerts.
            # ------------------------------------------------

            if isinstance(results, dict):
                results = [results]

            for result in results:

                if not isinstance(result, dict):
                    continue

                alert = dict(result)

                # ------------------------------------------------
                # ATTACH REAL DAY EXTREMES
                # ------------------------------------------------

                alert["day_high"] = self._day_high.get(instrument_id)

                alert["day_low"] = self._day_low.get(instrument_id)

                # ------------------------------------------------
                # NORMALIZE TIMESTAMP
                # ------------------------------------------------

                alert.setdefault(
                    "timestamp",
                    candle.timestamp,
                )

                # ------------------------------------------------
                # NORMALIZE PRICE
                #
                # DayExtremePatternSentinel already provides
                # "price".
                #
                # Do NOT replace it with "entry".
                # ------------------------------------------------

                if alert.get("price") is None:
                    alert["price"] = candle.close

                # ------------------------------------------------
                # NORMALIZE CONFIDENCE
                # ------------------------------------------------

                try:
                    confidence = float(
                        alert.get(
                            "confidence",
                            0.0,
                        )
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    confidence = 0.0

                alert["confidence"] = round(
                    confidence,
                    1,
                )

                pattern = alert.get(
                    "pattern",
                    "UNKNOWN",
                )

                direction = alert.get(
                    "direction",
                    "INFO",
                )

                # ------------------------------------------------
                # LOG
                # ------------------------------------------------

                logger.warning(
                    "PATTERN ALERT | "
                    "%s | pattern=%s | direction=%s | "
                    "confidence=%.1f | price=%s | "
                    "day_high=%s | day_low=%s",
                    instrument_id,
                    pattern,
                    direction,
                    alert["confidence"],
                    alert.get("price"),
                    alert.get("day_high"),
                    alert.get("day_low"),
                )

                # ------------------------------------------------
                # PUBLISH
                # ------------------------------------------------

                self._publish_pattern(
                    instrument_id,
                    alert,
                )

        except Exception:

            logger.exception(
                "DayExtremePatternSentinel failed for %s",
                instrument_id,
            )

    # ========================================================
    # INDIA DAY KEY
    # ========================================================

    @staticmethod
    def _india_day_key(
        timestamp: datetime,
    ) -> str:

        try:

            from zoneinfo import ZoneInfo

            india_tz = ZoneInfo("Asia/Kolkata")

            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

            return timestamp.astimezone(india_tz).strftime("%Y-%m-%d")

        except Exception:

            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

            return timestamp.strftime("%Y-%m-%d")

    # ========================================================
    # ENSURE DAY
    # ========================================================

    def _ensure_day(
        self,
        instrument_id: str,
        timestamp: datetime,
    ) -> None:

        day_key = self._india_day_key(timestamp)

        previous_day = self._day_key.get(instrument_id)

        if previous_day is not None and previous_day != day_key:

            self._day_high.pop(
                instrument_id,
                None,
            )

            self._day_low.pop(
                instrument_id,
                None,
            )

            self._broker_day_synced[instrument_id] = False

            if self._day_extreme_sentinel is not None:

                try:
                    self._day_extreme_sentinel.clear_symbol(instrument_id)

                except Exception:
                    logger.exception(
                        "Failed to reset DayExtremePatternSentinel " "for new day | %s",
                        instrument_id,
                    )

        self._day_key[instrument_id] = day_key

    # ========================================================
    # UPDATE DAY EXTREMES
    # ========================================================

    def _update_day_extremes(
        self,
        instrument_id: str,
        timestamp: datetime,
        price: float,
    ) -> None:

        try:
            price = float(price)
        except (
            TypeError,
            ValueError,
        ):
            return

        if price <= 0:
            return

        self._ensure_day(
            instrument_id,
            timestamp,
        )

        # ----------------------------------------------------
        # HIGH
        # ----------------------------------------------------

        current_high = self._day_high.get(instrument_id)

        if current_high is None or price > current_high:

            self._day_high[instrument_id] = price

        # ----------------------------------------------------
        # LOW
        # ----------------------------------------------------

        current_low = self._day_low.get(instrument_id)

        if current_low is None or price < current_low:

            self._day_low[instrument_id] = price

    # ========================================================
    # DAY HIGH
    # ========================================================

    def get_day_high(
        self,
        instrument_id: str,
    ) -> Optional[float]:

        with self._lock:

            return self._day_high.get(instrument_id)

    # ========================================================
    # DAY LOW
    # ========================================================

    def get_day_low(
        self,
        instrument_id: str,
    ) -> Optional[float]:

        with self._lock:

            return self._day_low.get(instrument_id)

    # ========================================================
    # DAY RANGE
    # ========================================================

    def get_day_range(
        self,
        instrument_id: str,
    ) -> Optional[float]:

        with self._lock:

            high = self._day_high.get(instrument_id)

            low = self._day_low.get(instrument_id)

            if high is None or low is None:
                return None

            return high - low

    # ========================================================
    # DAY STATUS
    # ========================================================

    def get_day_status(
        self,
        instrument_id: str,
    ) -> dict:

        with self._lock:

            high = self._day_high.get(instrument_id)

            low = self._day_low.get(instrument_id)

            return {
                "symbol": instrument_id,
                "date": self._day_key.get(instrument_id),
                "day_high": high,
                "day_low": low,
                "day_range": (
                    high - low if (high is not None and low is not None) else None
                ),
                "broker_synced": (
                    self._broker_day_synced.get(
                        instrument_id,
                        False,
                    )
                ),
            }

    # ========================================================
    # FINALIZE
    # ========================================================

    def finalize(
        self,
        instrument_id: Optional[str] = None,
    ) -> None:

        with self._lock:

            if instrument_id is None:

                instrument_ids = list(self._current.keys())

                for symbol in instrument_ids:

                    current = self._current.pop(
                        symbol,
                        None,
                    )

                    if current is None:
                        continue

                    self._append_candle(
                        symbol,
                        current,
                    )

                return

            current = self._current.pop(
                instrument_id,
                None,
            )

            if current is None:
                return

            self._append_candle(
                instrument_id,
                current,
            )

    # ========================================================
    # CREATE CANDLE
    # ========================================================

    def _create_candle(
        self,
        instrument_id: str,
        timestamp: datetime,
        ltp: float,
        volume_delta: float,
    ) -> Candle:

        previous_close = self._get_previous_close(instrument_id)

        candle_id = self._next_candle_id[instrument_id] + 1

        self._next_candle_id[instrument_id] = candle_id

        volume_delta = max(
            float(volume_delta),
            0.0,
        )

        cum_volume = volume_delta

        cum_price_volume = volume_delta * ltp

        if previous_close == 0.0:

            change_pct = 0.0

        else:

            change_pct = ((ltp - previous_close) / previous_close) * 100.0

        if ltp > previous_close and previous_close != 0.0:

            color = "GREEN"

        elif ltp < previous_close and previous_close != 0.0:

            color = "RED"

        else:

            color = "DOJI"

        return Candle(
            timestamp=timestamp,
            open=ltp,
            high=ltp,
            low=ltp,
            close=ltp,
            volume=volume_delta,
            previous_close=previous_close,
            ticks=1,
            cum_volume=cum_volume,
            cum_price_volume=cum_price_volume,
            vwap=ltp,
            change_pct=change_pct,
            candle_id=candle_id,
            session_id=self._session_id[instrument_id],
            color=color,
        )

    # ========================================================
    # UPDATE CANDLE
    # ========================================================

    def _update_candle(
        self,
        current: Candle,
        ltp: float,
        volume_delta: float,
    ) -> Candle:

        volume_delta = max(
            float(volume_delta),
            0.0,
        )

        high = max(
            current.high,
            ltp,
        )

        low = min(
            current.low,
            ltp,
        )

        cum_volume = current.cum_volume + volume_delta

        cum_price_volume = current.cum_price_volume + volume_delta * ltp

        if current.previous_close == 0.0:

            change_pct = 0.0

        else:

            change_pct = (
                (ltp - current.previous_close) / current.previous_close
            ) * 100.0

        (
            body_strength,
            upper_wick_pct,
            lower_wick_pct,
        ) = self._compute_strengths(
            current.open,
            high,
            low,
            ltp,
        )

        if cum_volume > 0.0:

            vwap = cum_price_volume / cum_volume

        else:

            vwap = (high + low + ltp) / 3.0

        if ltp > current.open:

            color = "GREEN"

        elif ltp < current.open:

            color = "RED"

        else:

            color = "DOJI"

        return Candle(
            timestamp=current.timestamp,
            open=current.open,
            high=high,
            low=low,
            close=ltp,
            volume=(current.volume + volume_delta),
            previous_close=current.previous_close,
            ticks=current.ticks + 1,
            cum_volume=cum_volume,
            cum_price_volume=cum_price_volume,
            vwap=vwap,
            change_pct=change_pct,
            candle_id=current.candle_id,
            session_id=current.session_id,
            color=color,
            body_strength=body_strength,
            upper_wick_pct=upper_wick_pct,
            lower_wick_pct=lower_wick_pct,
        )

    # ========================================================
    # PREVIOUS CLOSE
    # ========================================================

    def _get_previous_close(
        self,
        instrument_id: str,
    ) -> float:

        history = self._history[instrument_id]

        if not history:
            return 0.0

        return history[-1].close

    # ========================================================
    # CANDLE STRENGTH
    # ========================================================

    @staticmethod
    def _compute_strengths(
        open_price: float,
        high: float,
        low: float,
        close: float,
    ) -> tuple[float, float, float]:

        range_value = high - low

        if range_value <= 0.0:

            return (
                0.0,
                0.0,
                0.0,
            )

        body_strength = abs(close - open_price) / range_value

        upper_wick_pct = (
            max(
                high
                - max(
                    open_price,
                    close,
                ),
                0.0,
            )
            / range_value
        )

        lower_wick_pct = (
            max(
                min(
                    open_price,
                    close,
                )
                - low,
                0.0,
            )
            / range_value
        )

        return (
            body_strength,
            upper_wick_pct,
            lower_wick_pct,
        )

    # ========================================================
    # LAST CANDLES
    # ========================================================

    def get_last(
        self,
        instrument_id: str,
        count: int,
    ) -> List[Candle]:

        with self._lock:

            if count <= 0:
                return []

            history = list(self._history[instrument_id])

            current = self._current.get(instrument_id)

            if current is not None:
                history.append(current)

            return history[-count:]

    # ========================================================
    # COMPLETED CANDLES
    # ========================================================

    def get_completed(
        self,
        instrument_id: str,
        count: int,
    ) -> List[Candle]:

        with self._lock:

            if count <= 0:
                return []

            history = list(self._history[instrument_id])

            return history[-count:]

    # ========================================================
    # LATEST
    # ========================================================

    def get_latest(
        self,
        instrument_id: str,
    ) -> Optional[Candle]:

        with self._lock:

            current = self._current.get(instrument_id)

            if current is not None:
                return current

            history = self._history[instrument_id]

            return history[-1] if history else None

    # ========================================================
    # LATEST COMPLETED
    # ========================================================

    def get_latest_completed(
        self,
        instrument_id: str,
    ) -> Optional[Candle]:

        with self._lock:

            history = self._history[instrument_id]

            return history[-1] if history else None

    # ========================================================
    # HIGH
    # ========================================================

    def get_high(
        self,
        instrument_id: str,
        lookback: int,
    ) -> Optional[float]:

        candles = self.get_last(
            instrument_id,
            lookback,
        )

        if not candles:
            return None

        return max(candle.high for candle in candles)

    # ========================================================
    # LOW
    # ========================================================

    def get_low(
        self,
        instrument_id: str,
        lookback: int,
    ) -> Optional[float]:

        candles = self.get_last(
            instrument_id,
            lookback,
        )

        if not candles:
            return None

        return min(candle.low for candle in candles)

    # ========================================================
    # CLOSE
    # ========================================================

    def get_close(
        self,
        instrument_id: str,
        lookback: int,
    ) -> Optional[float]:

        candles = self.get_last(
            instrument_id,
            lookback,
        )

        if not candles:
            return None

        return candles[-1].close

    # ========================================================
    # ATR
    # ========================================================

    def get_atr(
        self,
        instrument_id: str,
        period: int = 14,
    ) -> Optional[float]:

        if period <= 0:
            raise ValueError("ATR period must be positive")

        candles = self.get_completed(
            instrument_id,
            period + 1,
        )

        if len(candles) < period + 1:
            return None

        true_ranges: List[float] = []

        for previous, current in zip(
            candles,
            candles[1:],
        ):

            true_range = max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )

            true_ranges.append(true_range)

        return sum(true_ranges[-period:]) / period

    # ========================================================
    # DATAFRAME-LIKE OUTPUT
    # ========================================================

    def get_dataframe(
        self,
        instrument_id: str,
    ) -> List[dict]:

        with self._lock:

            history = list(self._history[instrument_id])

            current = self._current.get(instrument_id)

            if current is not None:
                history.append(current)

            day_high = self._day_high.get(instrument_id)

            day_low = self._day_low.get(instrument_id)

        rows = []

        for candle in history:

            rows.append(
                {
                    "timestamp": (candle.timestamp.isoformat()),
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                    "previous_close": (candle.previous_close),
                    "ticks": candle.ticks,
                    "cum_volume": (candle.cum_volume),
                    "cum_price_volume": (candle.cum_price_volume),
                    "vwap": candle.vwap,
                    "change_pct": (candle.change_pct),
                    "candle_id": (candle.candle_id),
                    "session_id": (candle.session_id),
                    "color": candle.color,
                    "body_strength": (candle.body_strength),
                    "upper_wick_pct": (candle.upper_wick_pct),
                    "lower_wick_pct": (candle.lower_wick_pct),
                    "bullish": candle.bullish,
                    "bearish": candle.bearish,
                    "body": candle.body,
                    "upper_wick": (candle.upper_wick),
                    "lower_wick": (candle.lower_wick),
                    "day_high": day_high,
                    "day_low": day_low,
                }
            )

        return rows

    # ========================================================
    # CSV
    # ========================================================

    def to_csv(
        self,
        instrument_id: str,
        file_path: Optional[str] = None,
    ) -> str:

        rows = self.get_dataframe(instrument_id)

        if not rows:
            return ""

        output = io.StringIO()

        writer = csv.DictWriter(
            output,
            fieldnames=list(rows[0].keys()),
        )

        writer.writeheader()
        writer.writerows(rows)

        csv_text = output.getvalue()

        if file_path:

            with open(
                file_path,
                "w",
                encoding="utf-8",
                newline="",
            ) as csv_file:

                csv_file.write(csv_text)

        return csv_text

    # ========================================================
    # JSON
    # ========================================================

    def to_json(
        self,
        instrument_id: str,
        file_path: Optional[str] = None,
    ) -> str:

        rows = self.get_dataframe(instrument_id)

        json_text = json.dumps(
            rows,
            indent=2,
        )

        if file_path:

            with open(
                file_path,
                "w",
                encoding="utf-8",
            ) as json_file:

                json_file.write(json_text)

        return json_text

    # ========================================================
    # CACHED HIGHS
    # ========================================================

    def get_cached_highs(
        self,
        instrument_id: str,
        count: int,
    ) -> List[float]:

        with self._lock:

            if count <= 0:
                return []

            highs = list(self._high_cache[instrument_id])

            return highs[-count:]

    # ========================================================
    # CACHED LOWS
    # ========================================================

    def get_cached_lows(
        self,
        instrument_id: str,
        count: int,
    ) -> List[float]:

        with self._lock:

            if count <= 0:
                return []

            lows = list(self._low_cache[instrument_id])

            return lows[-count:]

    # ========================================================
    # VOLUME DELTA
    # ========================================================

    def _calculate_volume_delta(
        self,
        instrument_id: str,
        volume: float,
    ) -> float:

        try:
            volume = float(volume)

        except (
            TypeError,
            ValueError,
        ):

            volume = 0.0

        if volume < 0.0:
            volume = 0.0

        previous = self._last_volume.get(instrument_id)

        self._last_volume[instrument_id] = volume

        if previous is None:
            return volume

        delta = volume - previous

        if delta >= 0.0:
            return delta

        # ----------------------------------------------------
        # BROKER VOLUME RESET
        # ----------------------------------------------------

        return volume

    # ========================================================
    # TIMESTAMP ALIGNMENT
    # ========================================================

    def _align_timestamp(
        self,
        timestamp: datetime,
    ) -> datetime:

        if timestamp is None:

            timestamp = datetime.now(timezone.utc)

        if timestamp.tzinfo is not None:

            epoch_seconds = int(timestamp.timestamp())

        else:

            epoch_seconds = int(timestamp.replace(tzinfo=timezone.utc).timestamp())

        bucket_seconds = epoch_seconds - (epoch_seconds % self.timeframe)

        return datetime.fromtimestamp(
            bucket_seconds,
            tz=timezone.utc,
        )

    # ========================================================
    # CLEAR
    # ========================================================

    def clear(
        self,
        instrument_id: Optional[str] = None,
    ) -> None:

        with self._lock:

            if instrument_id is None:

                self._history.clear()
                self._current.clear()
                self._last_volume.clear()
                self._high_cache.clear()
                self._low_cache.clear()
                self._day_key.clear()
                self._day_high.clear()
                self._day_low.clear()
                self._broker_day_synced.clear()
                self._next_candle_id.clear()
                self._session_id.clear()

                if self._day_extreme_sentinel is not None:

                    try:
                        self._day_extreme_sentinel.clear()

                    except Exception:

                        logger.exception("Failed to reset " "DayExtremePatternSentinel")

                return

            self._history.pop(
                instrument_id,
                None,
            )

            self._current.pop(
                instrument_id,
                None,
            )

            self._last_volume.pop(
                instrument_id,
                None,
            )

            self._high_cache.pop(
                instrument_id,
                None,
            )

            self._low_cache.pop(
                instrument_id,
                None,
            )

            self._day_key.pop(
                instrument_id,
                None,
            )

            self._day_high.pop(
                instrument_id,
                None,
            )

            self._day_low.pop(
                instrument_id,
                None,
            )

            self._broker_day_synced.pop(
                instrument_id,
                None,
            )

            self._next_candle_id.pop(
                instrument_id,
                None,
            )

            self._session_id.pop(
                instrument_id,
                None,
            )

            if self._day_extreme_sentinel is not None:

                try:
                    self._day_extreme_sentinel.clear_symbol(instrument_id)

                except Exception:

                    logger.exception(
                        "Failed to reset " "DayExtremePatternSentinel | %s",
                        instrument_id,
                    )

    # ========================================================
    # RESET SESSION
    # ========================================================

    def reset_session(
        self,
        instrument_id: Optional[str] = None,
    ) -> None:

        with self._lock:

            if instrument_id is None:

                instrument_ids = set(self._history.keys())

                instrument_ids.update(self._current.keys())

                instrument_ids.update(self._session_id.keys())

                for symbol in instrument_ids:

                    self._session_id[symbol] += 1

                    self._day_key.pop(
                        symbol,
                        None,
                    )

                    self._day_high.pop(
                        symbol,
                        None,
                    )

                    self._day_low.pop(
                        symbol,
                        None,
                    )

                    self._broker_day_synced.pop(
                        symbol,
                        None,
                    )

                    if self._day_extreme_sentinel is not None:

                        try:
                            self._day_extreme_sentinel.clear_symbol(symbol)

                        except Exception:

                            logger.exception(
                                "Failed to reset " "DayExtremePatternSentinel | %s",
                                symbol,
                            )

                self._current.clear()

                return

            self._session_id[instrument_id] += 1

            self._current.pop(
                instrument_id,
                None,
            )

            self._day_key.pop(
                instrument_id,
                None,
            )

            self._day_high.pop(
                instrument_id,
                None,
            )

            self._day_low.pop(
                instrument_id,
                None,
            )

            self._broker_day_synced.pop(
                instrument_id,
                None,
            )

            if self._day_extreme_sentinel is not None:

                try:
                    self._day_extreme_sentinel.clear_symbol(instrument_id)

                except Exception:

                    logger.exception(
                        "Failed to reset " "DayExtremePatternSentinel | %s",
                        instrument_id,
                    )

    # ========================================================
    # SESSION ID
    # ========================================================

    def get_session_id(
        self,
        instrument_id: str,
    ) -> int:

        with self._lock:

            return self._session_id[instrument_id]
