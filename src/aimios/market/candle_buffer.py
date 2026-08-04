from __future__ import annotations

import csv
import io
import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from typing import Callable, Deque, Dict, List, Optional

from .market_snapshot import MarketSnapshot
from app.analysis.pattern_detector import PatternDetector


try:
    from ..engines.pattern_recognition import PatternRecognitionEngine
except Exception:  # pragma: no cover - optional import for runtime stability
    PatternRecognitionEngine = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


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
    vwap: float
    change_pct: float
    candle_id: int
    session_id: int
    color: str
    body_strength: float
    upper_wick_pct: float
    lower_wick_pct: float

    @property
    def bullish(self) -> bool:
        return self.color == "GREEN"

    @property
    def bearish(self) -> bool:
        return self.color == "RED"

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return max(self.high - max(self.open, self.close), 0.0)

    @property
    def lower_wick(self) -> float:
        return max(min(self.open, self.close) - self.low, 0.0)


class CandleBuffer:
    """Maintain rolling OHLC candle history for market instruments."""

    def __init__(
        self,
        max_candles: int = 300,
        timeframe: int = 60,
        pattern_detector: Optional[PatternDetector] = None,
    ) -> None:
        self.max_candles = max_candles
        self.timeframe = timeframe
        self._lock = RLock()
        self._history: Dict[str, Deque[Candle]] = defaultdict(lambda: deque(maxlen=self.max_candles))
        self._current: Dict[str, Candle] = {}
        self._last_volume: Dict[str, float] = {}
        self._high_cache: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=self.max_candles))
        self._low_cache: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=self.max_candles))
        self._next_candle_id: Dict[str, int] = defaultdict(int)
        self._session_id: Dict[str, int] = defaultdict(int)
        self._subscribers: List[Callable[[str, Candle], None]] = []
        self._pattern_detector: Optional[PatternDetector] = pattern_detector
        self._pattern_subscribers: List[Callable[[str, Dict[str, object]], None]] = []
        logger.info(
            "Initialized CandleBuffer(max_candles=%d, timeframe=%ds)",
            self.max_candles,
            self.timeframe,
        )

    def subscribe(self, callback: Callable[[str, Candle], None]) -> None:
        self._subscribers.append(callback)
        logger.debug("Subscriber added to CandleBuffer: %s", callback)

    def unsubscribe(self, callback: Callable[[str, Candle], None]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)
            logger.debug("Subscriber removed from CandleBuffer: %s", callback)

    def subscribe_pattern(self, callback: Callable[[str, Dict[str, float]], None]) -> None:
        self._pattern_subscribers.append(callback)
        logger.debug("Pattern subscriber added to CandleBuffer: %s", callback)

    def unsubscribe_pattern(self, callback: Callable[[str, Dict[str, float]], None]) -> None:
        if callback in self._pattern_subscribers:
            self._pattern_subscribers.remove(callback)
            logger.debug("Pattern subscriber removed from CandleBuffer: %s", callback)

    def set_pattern_detector(self, pattern_detector: PatternDetector) -> None:
        self._pattern_detector = pattern_detector
        logger.debug("Pattern detector set for CandleBuffer: %s", pattern_detector)

    def _publish(self, instrument_id: str, candle: Candle) -> None:
        for subscriber in list(self._subscribers):
            try:
                subscriber(instrument_id, candle)
            except Exception as exc:
                logger.exception("CandleBuffer subscriber failed: %s", exc)

    def _publish_pattern(self, instrument_id: str, patterns: Dict[str, object]) -> None:
        if not patterns:
            return
        for subscriber in list(self._pattern_subscribers):
            try:
                subscriber(instrument_id, patterns)
            except Exception as exc:
                logger.exception("CandleBuffer pattern subscriber failed: %s", exc)

    def update(self, snapshot: MarketSnapshot) -> None:
        instrument_id = snapshot.symbol
        bucket_time = self._align_timestamp(snapshot.timestamp)
        volume_delta = self._calculate_volume_delta(instrument_id, snapshot.volume)

        with self._lock:
            current = self._current.get(instrument_id)

            if current is not None and bucket_time != current.timestamp:
                self._append_candle(instrument_id, current)
                logger.debug(
                    "Completed candle for %s at %s: %s",
                    instrument_id,
                    current.timestamp,
                    current,
                )
                current = None

            if current is None:
                self._current[instrument_id] = self._create_candle(
                    instrument_id=instrument_id,
                    timestamp=bucket_time,
                    ltp=snapshot.ltp,
                    volume_delta=volume_delta,
                )
                logger.debug(
                    "Started new candle for %s at %s: %s",
                    instrument_id,
                    bucket_time,
                    self._current[instrument_id],
                )
                return

            self._current[instrument_id] = self._update_candle(
                current=current,
                ltp=snapshot.ltp,
                volume_delta=volume_delta,
            )
            logger.debug("Updated candle for %s: %s", instrument_id, self._current[instrument_id])

    def _append_candle(self, instrument_id: str, candle: Candle) -> None:
        self._history[instrument_id].append(candle)
        self._high_cache[instrument_id].append(candle.high)
        self._low_cache[instrument_id].append(candle.low)
        self._publish(instrument_id, candle)
        self._run_pattern_detection(instrument_id)

    def _run_pattern_detection(self, instrument_id: str) -> None:
        recent_candles = list(self._history[instrument_id])[-100:]
        current = self._current.get(instrument_id)
        if current is not None:
            recent_candles.append(current)

        if self._pattern_detector is not None:
            patterns = self._pattern_detector.detect(recent_candles)
            logger.debug("PatternDetector result for %s: %s", instrument_id, patterns)
            self._publish_pattern(instrument_id, patterns)

            if patterns and self._pattern_detector is not None:
                return

        if PatternRecognitionEngine is not None:
            try:
                engine = PatternRecognitionEngine(app=None)
                signals = engine.detect_from_candles(recent_candles, symbol=instrument_id)
                if signals:
                    payload = {
                        "pattern": signals[0].pattern,
                        "direction": signals[0].direction,
                        "confidence": signals[0].confidence,
                        "entry": signals[0].entry,
                        "stoploss": signals[0].stoploss,
                        "target": signals[0].target,
                        "timestamp": signals[0].timestamp,
                        "symbol": signals[0].symbol,
                    }
                    logger.debug("PatternRecognitionEngine result for %s: %s", instrument_id, payload)
                    self._publish_pattern(instrument_id, payload)
            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.exception("PatternRecognitionEngine failed for %s: %s", instrument_id, exc)

    def _create_candle(self, instrument_id: str, timestamp: datetime, ltp: float, volume_delta: float) -> Candle:
        previous_close = self._get_previous_close(instrument_id)
        candle_id = self._next_candle_id[instrument_id] + 1
        self._next_candle_id[instrument_id] = candle_id
        cum_volume = volume_delta
        cum_price_volume = volume_delta * ltp
        vwap = (previous_close + (2.0 * ltp)) / 3.0 if previous_close != 0.0 else ltp
        session_id = self._session_id[instrument_id]
        change_pct = 0.0 if previous_close == 0.0 else ((ltp - previous_close) / previous_close) * 100.0
        body_strength, upper_wick_pct, lower_wick_pct = self._compute_strengths(ltp, ltp, ltp, ltp)
        color = self._candle_color(ltp, ltp)
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

    def _update_candle(self, current: Candle, ltp: float, volume_delta: float) -> Candle:
        high = max(current.high, ltp)
        low = min(current.low, ltp)
        cum_volume = current.cum_volume + volume_delta
        cum_price_volume = current.cum_price_volume + volume_delta * ltp
        vwap = (current.open + (2.0 * ltp)) / 3.0 if current.open != 0.0 else ltp
        change_pct = 0.0 if current.previous_close == 0.0 else ((ltp - current.previous_close) / current.previous_close) * 100.0
        color = self._candle_color(current.open, ltp)
        body_strength, upper_wick_pct, lower_wick_pct = self._compute_strengths(
            current.open,
            high,
            low,
            ltp,
        )
        return Candle(
            timestamp=current.timestamp,
            open=current.open,
            high=high,
            low=low,
            close=ltp,
            volume=volume_delta,
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

    def _get_previous_close(self, instrument_id: str) -> float:
        history = self._history[instrument_id]
        if not history:
            return 0.0
        return history[-1].close

    def _candle_color(self, open_price: float, close_price: float) -> str:
        if close_price > open_price:
            return "GREEN"
        if close_price < open_price:
            return "RED"
        return "DOJI"

    def _compute_strengths(self, open_price: float, high: float, low: float, close: float) -> tuple[float, float, float]:
        range_value = high - low
        if range_value <= 0.0:
            return 0.0, 0.0, 0.0

        body_strength = abs(close - open_price) / range_value
        upper_wick_pct = max(high - max(open_price, close), 0.0) / range_value
        lower_wick_pct = max(min(open_price, close) - low, 0.0) / range_value
        return body_strength, upper_wick_pct, lower_wick_pct

    def get_last(self, instrument_id: str, count: int) -> List[Candle]:
        with self._lock:
            history = list(self._history[instrument_id])
            current = self._current.get(instrument_id)
            if current is not None:
                history.append(current)
            result = history[-count:] if count > 0 else []
        logger.debug("get_last(%s, %d) -> %d candles", instrument_id, count, len(result))
        return result

    def get_latest(self, instrument_id: str) -> Optional[Candle]:
        with self._lock:
            current = self._current.get(instrument_id)
            if current is not None:
                logger.debug("get_latest(%s) -> current candle", instrument_id)
                return current
            history = self._history[instrument_id]
            latest = history[-1] if history else None
        logger.debug("get_latest(%s) -> history candle: %s", instrument_id, latest)
        return latest

    def get_high(self, instrument_id: str, lookback: int) -> Optional[float]:
        candles = self.get_last(instrument_id, lookback)
        if not candles:
            logger.debug("get_high(%s, %d) -> None (no candles)", instrument_id, lookback)
            return None
        high = max(c.high for c in candles)
        logger.debug("get_high(%s, %d) -> %s", instrument_id, lookback, high)
        return high

    def get_low(self, instrument_id: str, lookback: int) -> Optional[float]:
        candles = self.get_last(instrument_id, lookback)
        if not candles:
            logger.debug("get_low(%s, %d) -> None (no candles)", instrument_id, lookback)
            return None
        low = min(c.low for c in candles)
        logger.debug("get_low(%s, %d) -> %s", instrument_id, lookback, low)
        return low

    def get_close(self, instrument_id: str, lookback: int) -> Optional[float]:
        candles = self.get_last(instrument_id, lookback)
        if not candles:
            logger.debug("get_close(%s, %d) -> None (no candles)", instrument_id, lookback)
            return None
        close = candles[-1].close
        logger.debug("get_close(%s, %d) -> %s", instrument_id, lookback, close)
        return close

    def get_dataframe(self, instrument_id: str) -> List[dict]:
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

    def to_csv(self, instrument_id: str, file_path: Optional[str] = None) -> str:
        rows = self.get_dataframe(instrument_id)
        if not rows:
            return ""

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        csv_text = output.getvalue()
        if file_path:
            with open(file_path, "w", encoding="utf-8", newline="") as csv_file:
                csv_file.write(csv_text)
            logger.info("Exported candle CSV for %s to %s", instrument_id, file_path)
        return csv_text

    def to_json(self, instrument_id: str, file_path: Optional[str] = None) -> str:
        rows = self.get_dataframe(instrument_id)
        json_text = json.dumps(rows, indent=2)
        if file_path:
            with open(file_path, "w", encoding="utf-8") as json_file:
                json_file.write(json_text)
            logger.info("Exported candle JSON for %s to %s", instrument_id, file_path)
        return json_text

    def clear(self, instrument_id: Optional[str] = None) -> None:
        with self._lock:
            if instrument_id is None:
                self._history.clear()
                self._current.clear()
                self._last_volume.clear()
                self._high_cache.clear()
                self._low_cache.clear()
                self._next_candle_id.clear()
                self._session_id.clear()
                logger.info("Cleared candle history for all instruments")
                return
            self._history.pop(instrument_id, None)
            self._current.pop(instrument_id, None)
            self._last_volume.pop(instrument_id, None)
            self._high_cache.pop(instrument_id, None)
            self._low_cache.pop(instrument_id, None)
            self._next_candle_id.pop(instrument_id, None)
            self._session_id.pop(instrument_id, None)
            logger.info("Cleared candle history for %s", instrument_id)

    def reset_session(self, instrument_id: Optional[str] = None) -> None:
        """Clear the current unfinished candle(s) at market session start.

        Keeps completed history intact while discarding the in-progress candle.
        """
        with self._lock:
            if instrument_id is None:
                self._current.clear()
                for key in list(self._session_id.keys()):
                    self._session_id[key] += 1
                logger.info("Reset session state for all instruments")
                return
            if instrument_id in self._current:
                self._current.pop(instrument_id, None)
            self._session_id[instrument_id] += 1
            logger.info("Reset session state for %s", instrument_id)

    def get_cached_highs(self, instrument_id: str, count: int) -> List[float]:
        with self._lock:
            highs = list(self._high_cache[instrument_id])[-count:] if count > 0 else []
        logger.debug("get_cached_highs(%s, %d) -> %d values", instrument_id, count, len(highs))
        return highs

    def get_cached_lows(self, instrument_id: str, count: int) -> List[float]:
        with self._lock:
            lows = list(self._low_cache[instrument_id])[-count:] if count > 0 else []
        logger.debug("get_cached_lows(%s, %d) -> %d values", instrument_id, count, len(lows))
        return lows

    def _calculate_volume_delta(self, instrument_id: str, volume: float) -> float:
        self._last_volume[instrument_id] = volume
        return volume

    def _align_timestamp(self, timestamp: datetime) -> datetime:
        seconds = int(timestamp.timestamp())
        bucket = seconds - (seconds % self.timeframe)
        return datetime.fromtimestamp(bucket, tz=timestamp.tzinfo or None)
