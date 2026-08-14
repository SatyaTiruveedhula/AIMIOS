from __future__ import annotations

import csv
import io
import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
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
    """
    Maintain rolling OHLC candle history for market instruments.

    Pattern detection is performed only on completed candles.

    A candle normally becomes completed when the next timeframe
    bucket arrives. For historical replay/tests, finalize() can be
    called explicitly to complete the final unfinished candle.
    """

    def __init__(
        self,
        max_candles: int = 300,
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
        # Completed candles
        # ----------------------------------------------------

        self._history: Dict[
            str,
            Deque[Candle],
        ] = defaultdict(lambda: deque(maxlen=self.max_candles))

        # ----------------------------------------------------
        # Current unfinished candle
        # ----------------------------------------------------

        self._current: Dict[str, Candle] = {}

        # ----------------------------------------------------
        # Previous cumulative market volume
        # ----------------------------------------------------

        self._last_volume: Dict[str, float] = {}

        # ----------------------------------------------------
        # Cached highs/lows
        # ----------------------------------------------------

        self._high_cache: Dict[
            str,
            Deque[float],
        ] = defaultdict(lambda: deque(maxlen=self.max_candles))

        self._low_cache: Dict[
            str,
            Deque[float],
        ] = defaultdict(lambda: deque(maxlen=self.max_candles))

        # ----------------------------------------------------
        # Sequential candle ID
        # ----------------------------------------------------

        self._next_candle_id: Dict[str, int] = defaultdict(int)

        # ----------------------------------------------------
        # Session ID
        # ----------------------------------------------------

        self._session_id: Dict[str, int] = defaultdict(lambda: 1)

        # ----------------------------------------------------
        # Candle subscribers
        # ----------------------------------------------------

        self._subscribers: List[Callable[[str, Candle], None]] = []

        # ----------------------------------------------------
        # M/W Pattern Sentinel
        # ----------------------------------------------------

        self._pattern_sentinel = None

        try:
            from aimios.engines.pattern_sentinel import PatternSentinel

            self._pattern_sentinel = PatternSentinel()
            self._pattern_sentinel.start()

            logger.info("M/W PatternSentinel initialized")

        except Exception:
            logger.exception("Failed to initialize PatternSentinel")

            self._pattern_sentinel = None

        # ----------------------------------------------------
        # Existing PatternDetector compatibility
        # ----------------------------------------------------

        self._pattern_detector = pattern_detector

        # ----------------------------------------------------
        # Pattern subscribers
        # ----------------------------------------------------

        self._pattern_subscribers: List[Callable[[str, Dict[str, object]], None]] = []

        logger.info(
            "Initialized CandleBuffer(" "max_candles=%d, timeframe=%ds" ")",
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

        if callback not in self._subscribers:
            self._subscribers.append(callback)

        logger.debug(
            "Subscriber added to CandleBuffer: %s",
            callback,
        )

    def unsubscribe(
        self,
        callback: Callable[[str, Candle], None],
    ) -> None:

        if callback in self._subscribers:
            self._subscribers.remove(callback)

        logger.debug(
            "Subscriber removed from CandleBuffer: %s",
            callback,
        )

    def subscribe_pattern(
        self,
        callback: Callable[
            [str, Dict[str, object]],
            None,
        ],
    ) -> None:

        if callback not in self._pattern_subscribers:
            self._pattern_subscribers.append(callback)

        logger.debug(
            "Pattern subscriber added: %s",
            callback,
        )

    def unsubscribe_pattern(
        self,
        callback: Callable[
            [str, Dict[str, object]],
            None,
        ],
    ) -> None:

        if callback in self._pattern_subscribers:
            self._pattern_subscribers.remove(callback)

        logger.debug(
            "Pattern subscriber removed: %s",
            callback,
        )

    def set_pattern_detector(
        self,
        pattern_detector,
    ) -> None:

        self._pattern_detector = pattern_detector

        logger.debug(
            "Pattern detector set: %s",
            pattern_detector,
        )

    # ========================================================
    # PUBLISH CANDLE
    # ========================================================

    def _publish(
        self,
        instrument_id: str,
        candle: Candle,
    ) -> None:

        for subscriber in list(self._subscribers):

            try:
                subscriber(
                    instrument_id,
                    candle,
                )

            except Exception as exc:
                logger.exception(
                    "CandleBuffer subscriber failed: %s",
                    exc,
                )

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

        for subscriber in list(self._pattern_subscribers):

            try:
                subscriber(
                    instrument_id,
                    patterns,
                )

            except Exception as exc:
                logger.exception(
                    "Pattern subscriber failed: %s",
                    exc,
                )

    # ========================================================
    # UPDATE
    # ========================================================

    def update(
        self,
        snapshot: MarketSnapshot,
    ) -> None:

        instrument_id = snapshot.symbol

        bucket_time = self._align_timestamp(snapshot.timestamp)

        volume_delta = self._calculate_volume_delta(
            instrument_id,
            snapshot.volume,
        )

        with self._lock:

            current = self._current.get(instrument_id)

            # ------------------------------------------------
            # NEW TIMEFRAME / NEW CANDLE
            # ------------------------------------------------

            if current is not None and bucket_time != current.timestamp:

                completed = current

                self._append_candle(
                    instrument_id,
                    completed,
                )

                logger.debug(
                    "Completed candle "
                    "instrument=%s "
                    "timestamp=%s "
                    "candle_id=%s "
                    "session_id=%s",
                    instrument_id,
                    completed.timestamp,
                    completed.candle_id,
                    completed.session_id,
                )

                current = None

            # ------------------------------------------------
            # CREATE NEW CANDLE
            # ------------------------------------------------

            if current is None:

                self._current[instrument_id] = self._create_candle(
                    instrument_id=instrument_id,
                    timestamp=bucket_time,
                    ltp=snapshot.ltp,
                    volume_delta=volume_delta,
                )

                logger.debug(
                    "Started candle "
                    "instrument=%s "
                    "timestamp=%s "
                    "candle_id=%s "
                    "session_id=%s",
                    instrument_id,
                    bucket_time,
                    self._current[instrument_id].candle_id,
                    self._current[instrument_id].session_id,
                )

                return

            # ------------------------------------------------
            # UPDATE CURRENT CANDLE
            # ------------------------------------------------

            self._current[instrument_id] = self._update_candle(
                current=current,
                ltp=snapshot.ltp,
                volume_delta=volume_delta,
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

        self._high_cache[instrument_id].append(candle.high)

        self._low_cache[instrument_id].append(candle.low)

        # ----------------------------------------------------
        # PUBLISH COMPLETED CANDLE
        # ----------------------------------------------------

        self._publish(
            instrument_id,
            candle,
        )

        # ----------------------------------------------------
        # M/W PATTERN SENTINEL
        # ----------------------------------------------------

        if self._pattern_sentinel is not None:

            try:

                completed_candles = list(self._history[instrument_id])

                sentinel_result = self._pattern_sentinel.process_candle(
                    candle=candle,
                    candles=completed_candles,
                    symbol=instrument_id,
                )

                if sentinel_result:

                    logger.info(
                        "M/W SENTINEL ALERT | " "symbol=%s | result=%s",
                        instrument_id,
                        sentinel_result,
                    )

                    self._publish_pattern(
                        instrument_id,
                        sentinel_result,
                    )

            except Exception:

                logger.exception(
                    "M/W Pattern Sentinel failed " "for %s",
                    instrument_id,
                )

        # ----------------------------------------------------
        # EXISTING PATTERN DETECTION
        # ----------------------------------------------------

        self._run_pattern_detection(
            instrument_id,
        )

    # ========================================================
    # EXISTING PATTERN DETECTION
    # ========================================================

    def _run_pattern_detection(
        self,
        instrument_id: str,
    ) -> None:

        # Only completed candles are passed to detectors.

        recent_candles = list(self._history[instrument_id])[-100:]

        if not recent_candles:
            return

        # ----------------------------------------------------
        # EXISTING PatternDetector
        # ----------------------------------------------------

        if self._pattern_detector is not None:

            try:

                patterns = self._pattern_detector.detect(recent_candles)

                logger.debug(
                    "PatternDetector result " "for %s: %s",
                    instrument_id,
                    patterns,
                )

                if patterns:

                    self._publish_pattern(
                        instrument_id,
                        patterns,
                    )

                    return

            except Exception as exc:

                logger.exception(
                    "PatternDetector failed " "for %s: %s",
                    instrument_id,
                    exc,
                )

        # ----------------------------------------------------
        # PATTERN RECOGNITION ENGINE
        # ----------------------------------------------------

        try:

            from ..engines.pattern_recognition import (
                PatternRecognitionEngine,
            )

        except Exception:

            PatternRecognitionEngine = None

        if PatternRecognitionEngine is None:
            return

        try:

            engine = PatternRecognitionEngine(app=None)

            signals = engine.detect_from_candles(
                recent_candles,
                symbol=instrument_id,
            )

            if not signals:
                return

            signal = signals[0]

            payload: Dict[str, object] = {
                "pattern": getattr(
                    signal,
                    "pattern",
                    None,
                ),
                "direction": getattr(
                    signal,
                    "direction",
                    None,
                ),
                "confidence": getattr(
                    signal,
                    "confidence",
                    0.0,
                ),
                "entry": getattr(
                    signal,
                    "entry",
                    None,
                ),
                "stoploss": getattr(
                    signal,
                    "stoploss",
                    None,
                ),
                "target": getattr(
                    signal,
                    "target",
                    None,
                ),
                "timestamp": getattr(
                    signal,
                    "timestamp",
                    None,
                ),
                "symbol": getattr(
                    signal,
                    "symbol",
                    instrument_id,
                ),
            }

            logger.debug(
                "PatternRecognitionEngine " "result for %s: %s",
                instrument_id,
                payload,
            )

            self._publish_pattern(
                instrument_id,
                payload,
            )

        except Exception as exc:

            logger.exception(
                "PatternRecognitionEngine failed " "for %s: %s",
                instrument_id,
                exc,
            )

    # ========================================================
    # FINALIZE CURRENT CANDLE
    # ========================================================

    def finalize(
        self,
        instrument_id: Optional[str] = None,
    ) -> None:
        """
        Finalize the currently unfinished candle.

        Used for:
        - historical replay
        - unit tests
        - end-of-session processing
        - graceful shutdown

        The finalized candle follows the same processing path
        as a candle completed by arrival of the next timeframe.
        """

        with self._lock:

            # ------------------------------------------------
            # FINALIZE ALL INSTRUMENTS
            # ------------------------------------------------

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

            # ------------------------------------------------
            # FINALIZE ONE INSTRUMENT
            # ------------------------------------------------

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

            logger.debug(
                "Finalized current candle "
                "instrument=%s "
                "timestamp=%s "
                "candle_id=%s",
                instrument_id,
                current.timestamp,
                current.candle_id,
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
            volume_delta,
            0.0,
        )

        cum_volume = volume_delta

        cum_price_volume = volume_delta * ltp

        change_pct = (
            0.0
            if previous_close == 0.0
            else ((ltp - previous_close) / previous_close) * 100.0
        )

        vwap = ltp

        color = "GREEN"

        (
            body_strength,
            upper_wick_pct,
            lower_wick_pct,
        ) = self._compute_strengths(
            ltp,
            ltp,
            ltp,
            ltp,
        )

        session_id = self._session_id[instrument_id]

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
            vwap=vwap,
            change_pct=change_pct,
            candle_id=candle_id,
            session_id=session_id,
            color=color,
            body_strength=body_strength,
            upper_wick_pct=upper_wick_pct,
            lower_wick_pct=lower_wick_pct,
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
            volume_delta,
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

        change_pct = (
            0.0
            if current.previous_close == 0.0
            else ((ltp - current.previous_close) / current.previous_close) * 100.0
        )

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

        # ----------------------------------------------------
        # VWAP
        # ----------------------------------------------------

        vwap = (high + low + ltp) / 3.0

        # ----------------------------------------------------
        # CANDLE COLOR
        # ----------------------------------------------------

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
            volume=current.volume + volume_delta,
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
    ) -> tuple[
        float,
        float,
        float,
    ]:

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
    # COMPLETED CANDLES ONLY
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

        return [
            {
                "timestamp": candle.timestamp.isoformat(),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "previous_close": candle.previous_close,
                "ticks": candle.ticks,
                "cum_volume": candle.cum_volume,
                "cum_price_volume": candle.cum_price_volume,
                "vwap": candle.vwap,
                "change_pct": candle.change_pct,
                "candle_id": candle.candle_id,
                "session_id": candle.session_id,
                "color": candle.color,
                "body_strength": candle.body_strength,
                "upper_wick_pct": candle.upper_wick_pct,
                "lower_wick_pct": candle.lower_wick_pct,
                "bullish": candle.bullish,
                "bearish": candle.bearish,
                "body": candle.body,
                "upper_wick": candle.upper_wick,
                "lower_wick": candle.lower_wick,
            }
            for candle in history
        ]

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

            logger.info(
                "Exported candle CSV " "for %s to %s",
                instrument_id,
                file_path,
            )

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

            logger.info(
                "Exported candle JSON " "for %s to %s",
                instrument_id,
                file_path,
            )

        return json_text

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
                self._next_candle_id.clear()
                self._session_id.clear()

                if self._pattern_sentinel is not None:

                    try:
                        self._pattern_sentinel.clear()

                    except Exception:
                        logger.exception("Failed to reset " "M/W PatternSentinel")

                logger.info("Cleared candle history " "for all instruments")

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

            self._next_candle_id.pop(
                instrument_id,
                None,
            )

            self._session_id.pop(
                instrument_id,
                None,
            )

            logger.info(
                "Cleared candle history " "for %s",
                instrument_id,
            )

    # ========================================================
    # RESET SESSION
    # ========================================================

    def reset_session(
        self,
        instrument_id: Optional[str] = None,
    ) -> None:
        """
        Discard the unfinished candle and start
        a new logical session.

        Completed candle history is preserved.

        Candle IDs continue increasing.

        Session IDs increment:

            initial session = 1
            reset_session() = 2
            reset_session() = 3
            ...
        """

        with self._lock:

            if instrument_id is None:

                instrument_ids = set(self._history.keys())

                instrument_ids.update(self._current.keys())

                instrument_ids.update(self._session_id.keys())

                for symbol in instrument_ids:
                    self._session_id[symbol] += 1

                self._current.clear()

                logger.info("Reset session for all instruments")

                return

            self._session_id[instrument_id] += 1

            self._current.pop(
                instrument_id,
                None,
            )

            logger.info(
                "Reset session " "for %s -> session_id=%d",
                instrument_id,
                self._session_id[instrument_id],
            )

    # ========================================================
    # CURRENT SESSION ID
    # ========================================================

    def get_session_id(
        self,
        instrument_id: str,
    ) -> int:

        with self._lock:
            return self._session_id[instrument_id]

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

        previous = self._last_volume.get(instrument_id)

        self._last_volume[instrument_id] = volume

        if previous is None:
            return max(
                volume,
                0.0,
            )

        delta = volume - previous

        return max(
            delta,
            0.0,
        )

    # ========================================================
    # TIMESTAMP ALIGNMENT
    # ========================================================

    def _align_timestamp(
        self,
        timestamp: datetime,
    ) -> datetime:
        """
        Align incoming market timestamp to the
        configured candle timeframe.

        The timestamp's timezone is preserved.

        If the broker/feed supplies Asia/Kolkata
        timestamps, candle timestamps remain
        Asia/Kolkata.

        We do NOT convert timestamps to UTC here.
        """

        if timestamp.tzinfo is not None:

            epoch_seconds = timestamp.timestamp()

            bucket_seconds = int(epoch_seconds) - (int(epoch_seconds) % self.timeframe)

            return datetime.fromtimestamp(
                bucket_seconds,
                tz=timestamp.tzinfo,
            )

        # If feed gives a naive timestamp,
        # preserve it as naive instead of
        # silently applying the machine timezone.

        seconds = int(timestamp.timestamp())

        bucket = seconds - (seconds % self.timeframe)

        return datetime.fromtimestamp(bucket)
