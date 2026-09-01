from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

INDIA_TZ = ZoneInfo("Asia/Kolkata")


# ============================================================
# CONFIGURATION
# ============================================================

MIN_REVERSAL_PCT = 0.13
MIN_OUTER_DIFFERENCE_PCT = 0.03
MIN_OUTER_CANDLE_SEPARATION = 7

PIVOT_LEFT = 1
PIVOT_RIGHT = 1

MAX_PATTERN_CANDLES = 100
MAX_ALERT_HISTORY = 1000

# ------------------------------------------------------------
# DAY HIGH / LOW tolerance
#
# HIGH1 must be effectively the day's high.
# LOW1 must be effectively the day's low.
#
# We use percentage tolerance rather than exact equality
# because tick/candle OHLC values can differ slightly.
# ------------------------------------------------------------

DAY_EXTREME_TOLERANCE_PCT = 0.03

# ------------------------------------------------------------
# 5-MINUTE candle size
# ------------------------------------------------------------

FIVE_MINUTES = 5

# ============================================================
# DATA CLASS
# ============================================================


@dataclass(frozen=True)
class Pivot:
    index: int
    candle_id: int
    timestamp: datetime
    price: float
    kind: str


# ============================================================
# PATTERN SENTINEL
# ============================================================


class PatternSentinel:
    """
    AIMIOS M/W detector.

    ONLY FOUR ALERT TYPES ARE ALLOWED:

        DAY_HIGH_M
        DAY_LOW_W
        5MIN_M
        5MIN_W

    ----------------------------------------------------------
    M:

        HIGH1
           /\
          /  \
         /    \
        /      \
              HIGH2
              /\

        HIGH1 -> VALLEY >= 0.13%

        HIGH1 > HIGH2 by >= 0.03%

        HIGH1 -> HIGH2 >= 7 candles

        SELL

    ----------------------------------------------------------
    W:

        LOW1
          \    /
           \  /
            \/
            PEAK
              \
               \
               LOW2

        LOW1 -> PEAK >= 0.13%

        LOW2 > LOW1 by >= 0.03%

        LOW1 -> LOW2 >= 7 candles

        BUY

    ----------------------------------------------------------
    DAY_HIGH_M:

        Same M structure, BUT HIGH1 must be the
        day's high.

    DAY_LOW_W:

        Same W structure, BUT LOW1 must be the
        day's low.

    ----------------------------------------------------------
    5MIN_M / 5MIN_W:

        Detection is performed on internally constructed
        5-minute candles.

    ----------------------------------------------------------
    Duplicate protection:

        The same completed pattern can generate only
        ONE alert.
    """

    def __init__(self) -> None:

        self._alert_history: Dict[
            str,
            List[Tuple[str, int, int]],
        ] = {}

        # Last processed 5-minute bucket for each symbol.
        self._last_5m_bucket: Dict[str, datetime] = {}

        # Aggregated 5-minute candles.
        self._five_min_candles: Dict[str, List[object]] = {}

        logger.info(
            "PatternSentinel initialized | "
            "ONLY DAY_HIGH_M / DAY_LOW_W / 5MIN_M / 5MIN_W"
        )

    # ========================================================
    # START
    # ========================================================

    def start(self) -> None:

        logger.info("PatternSentinel started | " "4 alert modes enabled")

    # ========================================================
    # CLEAR
    # ========================================================

    def clear(self) -> None:

        self._alert_history.clear()
        self._last_5m_bucket.clear()
        self._five_min_candles.clear()

        logger.info("PatternSentinel cleared")

    # ========================================================
    # PROCESS CANDLE
    # ========================================================

    def process_candle(
        self,
        candle,
        candles,
        symbol: str,
    ) -> Optional[Dict[str, object]]:

        if candle is None:
            return None

        if not candles:
            return None

        if not symbol:
            return None

        completed = list(candles)

        if len(completed) < 3:
            return None

        # ====================================================
        # 1. DAY HIGH M / DAY LOW W
        #
        # These use the supplied completed candles.
        # ====================================================

        day_high = max(float(c.high) for c in completed if c.high is not None)

        day_low = min(float(c.low) for c in completed if c.low is not None)

        day_high_m = self._detect_day_high_m(
            completed=completed,
            symbol=symbol,
            day_high=day_high,
            latest=candle,
        )

        if day_high_m is not None:
            return day_high_m

        day_low_w = self._detect_day_low_w(
            completed=completed,
            symbol=symbol,
            day_low=day_low,
            latest=candle,
        )

        if day_low_w is not None:
            return day_low_w

        # ====================================================
        # 2. BUILD 5-MINUTE CANDLES
        # ====================================================

        five_min_ready = self._update_5m_candles(
            candle=candle,
            symbol=symbol,
        )

        if five_min_ready is None:
            return None

        five_min_completed = self._five_min_candles.get(
            symbol,
            [],
        )

        if len(five_min_completed) < 3:
            return None

        if len(five_min_completed) > MAX_PATTERN_CANDLES:
            five_min_completed = five_min_completed[-MAX_PATTERN_CANDLES:]

        # ====================================================
        # 3. PROPER 5-MINUTE M
        # ====================================================

        m_alert = self._detect_5m_m(
            completed=five_min_completed,
            symbol=symbol,
            latest=five_min_completed[-1],
        )

        if m_alert is not None:
            return m_alert

        # ====================================================
        # 4. PROPER 5-MINUTE W
        # ====================================================

        w_alert = self._detect_5m_w(
            completed=five_min_completed,
            symbol=symbol,
            latest=five_min_completed[-1],
        )

        if w_alert is not None:
            return w_alert

        return None

    # ========================================================
    # DAY HIGH M
    # ========================================================

    def _detect_day_high_m(
        self,
        completed,
        symbol: str,
        day_high: float,
        latest,
    ) -> Optional[Dict[str, object]]:

        if len(completed) < MIN_OUTER_CANDLE_SEPARATION + 1:
            return None

        high_pivots = self._find_high_pivots(completed)
        low_pivots = self._find_low_pivots(completed)

        if len(high_pivots) < 2:
            return None

        # ----------------------------------------------------
        # Newest HIGH2 first.
        # ----------------------------------------------------

        for high2 in reversed(high_pivots):

            if high2.index >= len(completed) - PIVOT_RIGHT:
                continue

            previous_highs = [h for h in high_pivots if h.index < high2.index]

            for high1 in reversed(previous_highs):

                # ------------------------------------------------
                # HIGH1 MUST BE DAY HIGH
                # ------------------------------------------------

                if not self._is_day_high(
                    high1.price,
                    day_high,
                ):
                    continue

                separation = high2.candle_id - high1.candle_id

                if separation < MIN_OUTER_CANDLE_SEPARATION:
                    continue

                valleys = [
                    low for low in low_pivots if high1.index < low.index < high2.index
                ]

                if not valleys:
                    continue

                valley = min(
                    valleys,
                    key=lambda x: x.price,
                )

                reversal_pct = self._down_pct(
                    high1.price,
                    valley.price,
                )

                if reversal_pct < MIN_REVERSAL_PCT:
                    continue

                if high1.price <= high2.price:
                    continue

                difference_pct = self._difference_pct(
                    high1.price,
                    high2.price,
                )

                if difference_pct < MIN_OUTER_DIFFERENCE_PCT:
                    continue

                if not (high1.index < valley.index < high2.index):
                    continue

                if self._already_alerted(
                    symbol,
                    "DAY_HIGH_M",
                    high1.candle_id,
                    high2.candle_id,
                ):
                    continue

                return self._build_alert(
                    pattern="DAY_HIGH_M",
                    direction="SELL",
                    symbol=symbol,
                    reversal_pct=reversal_pct,
                    outer_difference_pct=difference_pct,
                    separation=separation,
                    entry=float(latest.close),
                    high1=high1,
                    valley=valley,
                    high2=high2,
                )

        return None

    # ========================================================
    # DAY LOW W
    # ========================================================

    def _detect_day_low_w(
        self,
        completed,
        symbol: str,
        day_low: float,
        latest,
    ) -> Optional[Dict[str, object]]:

        if len(completed) < MIN_OUTER_CANDLE_SEPARATION + 1:
            return None

        low_pivots = self._find_low_pivots(completed)
        high_pivots = self._find_high_pivots(completed)

        if len(low_pivots) < 2:
            return None

        for low2 in reversed(low_pivots):

            if low2.index >= len(completed) - PIVOT_RIGHT:
                continue

            previous_lows = [low for low in low_pivots if low.index < low2.index]

            for low1 in reversed(previous_lows):

                # ------------------------------------------------
                # LOW1 MUST BE DAY LOW
                # ------------------------------------------------

                if not self._is_day_low(
                    low1.price,
                    day_low,
                ):
                    continue

                separation = low2.candle_id - low1.candle_id

                if separation < MIN_OUTER_CANDLE_SEPARATION:
                    continue

                peaks = [
                    high for high in high_pivots if low1.index < high.index < low2.index
                ]

                if not peaks:
                    continue

                peak = max(
                    peaks,
                    key=lambda x: x.price,
                )

                reversal_pct = self._up_pct(
                    low1.price,
                    peak.price,
                )

                if reversal_pct < MIN_REVERSAL_PCT:
                    continue

                if low2.price <= low1.price:
                    continue

                difference_pct = self._difference_pct(
                    low2.price,
                    low1.price,
                )

                if difference_pct < MIN_OUTER_DIFFERENCE_PCT:
                    continue

                if not (low1.index < peak.index < low2.index):
                    continue

                if self._already_alerted(
                    symbol,
                    "DAY_LOW_W",
                    low1.candle_id,
                    low2.candle_id,
                ):
                    continue

                return self._build_alert(
                    pattern="DAY_LOW_W",
                    direction="BUY",
                    symbol=symbol,
                    reversal_pct=reversal_pct,
                    outer_difference_pct=difference_pct,
                    separation=separation,
                    entry=float(latest.close),
                    low1=low1,
                    peak=peak,
                    low2=low2,
                )

        return None

    # ========================================================
    # 5-MINUTE CANDLE AGGREGATION
    # ========================================================

    def _update_5m_candles(
        self,
        candle,
        symbol: str,
    ):

        timestamp = candle.timestamp

        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=INDIA_TZ)
        else:
            timestamp = timestamp.astimezone(INDIA_TZ)

        # ----------------------------------------------------
        # 5-minute bucket.
        #
        # Example:
        #
        # 09:15 -> 09:20
        # 09:20 -> 09:25
        # 09:25 -> 09:30
        # ----------------------------------------------------

        minute = timestamp.minute - timestamp.minute % FIVE_MINUTES

        bucket = timestamp.replace(
            minute=minute,
            second=0,
            microsecond=0,
        )

        candles_5m = self._five_min_candles.setdefault(
            symbol,
            [],
        )

        # ----------------------------------------------------
        # New 5-minute candle.
        # ----------------------------------------------------

        if not candles_5m or candles_5m[-1].timestamp != bucket:

            five = self._create_5m_candle(
                candle,
                bucket,
            )

            candles_5m.append(five)

            if len(candles_5m) > MAX_PATTERN_CANDLES + 10:
                del candles_5m[:-MAX_PATTERN_CANDLES]

            self._last_5m_bucket[symbol] = bucket

            # The newly started candle is NOT completed yet.
            #
            # Return the previous candle as completed.
            if len(candles_5m) >= 2:
                return candles_5m[-2]

            return None

        # ----------------------------------------------------
        # Update current 5-minute candle.
        # ----------------------------------------------------

        current = candles_5m[-1]

        current.high = max(
            float(current.high),
            float(candle.high),
        )

        current.low = min(
            float(current.low),
            float(candle.low),
        )

        current.close = float(candle.close)

        if hasattr(candle, "volume"):
            current.volume = getattr(current, "volume", 0) + (float(candle.volume or 0))

        return None

    # ========================================================
    # CREATE 5M CANDLE
    # ========================================================

    @staticmethod
    def _create_5m_candle(
        candle,
        timestamp,
    ):

        # ----------------------------------------------------
        # Dynamic lightweight candle object.
        #
        # We deliberately don't modify CandleBuffer's
        # original Candle class.
        # ----------------------------------------------------

        class FiveMinuteCandle:
            pass

        result = FiveMinuteCandle()

        result.timestamp = timestamp
        result.open = float(candle.open)
        result.high = float(candle.high)
        result.low = float(candle.low)
        result.close = float(candle.close)
        result.volume = float(getattr(candle, "volume", 0) or 0)

        # Use original candle ID as a stable base.
        result.candle_id = int(getattr(candle, "candle_id", 0))

        return result

    # ========================================================
    # 5-MIN M
    # ========================================================

    def _detect_5m_m(
        self,
        completed,
        symbol,
        latest,
    ):

        if len(completed) < MIN_OUTER_CANDLE_SEPARATION + 1:
            return None

        highs = self._find_high_pivots(completed)
        lows = self._find_low_pivots(completed)

        if len(highs) < 2:
            return None

        for high2 in reversed(highs):

            if high2.index != len(completed) - 2:
                continue

            for high1 in reversed([h for h in highs if h.index < high2.index]):

                separation = high2.index - high1.index

                if separation < MIN_OUTER_CANDLE_SEPARATION:
                    continue

                valleys = [low for low in lows if high1.index < low.index < high2.index]

                if not valleys:
                    continue

                valley = min(
                    valleys,
                    key=lambda x: x.price,
                )

                reversal_pct = self._down_pct(
                    high1.price,
                    valley.price,
                )

                if reversal_pct < MIN_REVERSAL_PCT:
                    continue

                if high1.price <= high2.price:
                    continue

                difference_pct = self._difference_pct(
                    high1.price,
                    high2.price,
                )

                if difference_pct < MIN_OUTER_DIFFERENCE_PCT:
                    continue

                if self._already_alerted(
                    symbol,
                    "5MIN_M",
                    high1.candle_id,
                    high2.candle_id,
                ):
                    continue

                return self._build_alert(
                    pattern="5MIN_M",
                    direction="SELL",
                    symbol=symbol,
                    reversal_pct=reversal_pct,
                    outer_difference_pct=difference_pct,
                    separation=separation,
                    entry=float(latest.close),
                    high1=high1,
                    valley=valley,
                    high2=high2,
                )

        return None

    # ========================================================
    # 5-MIN W
    # ========================================================

    def _detect_5m_w(
        self,
        completed,
        symbol,
        latest,
    ):

        if len(completed) < MIN_OUTER_CANDLE_SEPARATION + 1:
            return None

        lows = self._find_low_pivots(completed)
        highs = self._find_high_pivots(completed)

        if len(lows) < 2:
            return None

        for low2 in reversed(lows):

            if low2.index != len(completed) - 2:
                continue

            for low1 in reversed([l for l in lows if l.index < low2.index]):

                separation = low2.index - low1.index

                if separation < MIN_OUTER_CANDLE_SEPARATION:
                    continue

                peaks = [high for high in highs if low1.index < high.index < low2.index]

                if not peaks:
                    continue

                peak = max(
                    peaks,
                    key=lambda x: x.price,
                )

                reversal_pct = self._up_pct(
                    low1.price,
                    peak.price,
                )

                if reversal_pct < MIN_REVERSAL_PCT:
                    continue

                if low2.price <= low1.price:
                    continue

                difference_pct = self._difference_pct(
                    low2.price,
                    low1.price,
                )

                if difference_pct < MIN_OUTER_DIFFERENCE_PCT:
                    continue

                if self._already_alerted(
                    symbol,
                    "5MIN_W",
                    low1.candle_id,
                    low2.candle_id,
                ):
                    continue

                return self._build_alert(
                    pattern="5MIN_W",
                    direction="BUY",
                    symbol=symbol,
                    reversal_pct=reversal_pct,
                    outer_difference_pct=difference_pct,
                    separation=separation,
                    entry=float(latest.close),
                    low1=low1,
                    peak=peak,
                    low2=low2,
                )

        return None

    # ========================================================
    # DAY EXTREME CHECKS
    # ========================================================

    @staticmethod
    def _is_day_high(
        price: float,
        day_high: float,
    ) -> bool:

        if day_high <= 0:
            return False

        difference = ((day_high - price) / day_high) * 100.0

        return difference <= DAY_EXTREME_TOLERANCE_PCT

    # --------------------------------------------------------

    @staticmethod
    def _is_day_low(
        price: float,
        day_low: float,
    ) -> bool:

        if day_low <= 0:
            return False

        difference = ((price - day_low) / day_low) * 100.0

        return difference <= DAY_EXTREME_TOLERANCE_PCT

    # ========================================================
    # HIGH PIVOTS
    # ========================================================

    @staticmethod
    def _find_high_pivots(candles):

        pivots = []

        total = len(candles)

        for i in range(
            PIVOT_LEFT,
            total - PIVOT_RIGHT,
        ):

            candle = candles[i]

            is_high = True

            for j in range(
                i - PIVOT_LEFT,
                i,
            ):

                if candles[j].high > candle.high:
                    is_high = False
                    break

            if not is_high:
                continue

            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1,
            ):

                if candles[j].high > candle.high:
                    is_high = False
                    break

            if not is_high:
                continue

            pivots.append(
                Pivot(
                    index=i,
                    candle_id=int(candle.candle_id),
                    timestamp=candle.timestamp,
                    price=float(candle.high),
                    kind="HIGH",
                )
            )

        return pivots

    # ========================================================
    # LOW PIVOTS
    # ========================================================

    @staticmethod
    def _find_low_pivots(candles):

        pivots = []

        total = len(candles)

        for i in range(
            PIVOT_LEFT,
            total - PIVOT_RIGHT,
        ):

            candle = candles[i]

            is_low = True

            for j in range(
                i - PIVOT_LEFT,
                i,
            ):

                if candles[j].low < candle.low:
                    is_low = False
                    break

            if not is_low:
                continue

            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1,
            ):

                if candles[j].low < candle.low:
                    is_low = False
                    break

            if not is_low:
                continue

            pivots.append(
                Pivot(
                    index=i,
                    candle_id=int(candle.candle_id),
                    timestamp=candle.timestamp,
                    price=float(candle.low),
                    kind="LOW",
                )
            )

        return pivots

    # ========================================================
    # PERCENTAGE HELPERS
    # ========================================================

    @staticmethod
    def _down_pct(start, end):

        if start <= 0:
            return 0.0

        return ((start - end) / start) * 100.0

    # --------------------------------------------------------

    @staticmethod
    def _up_pct(start, end):

        if start <= 0:
            return 0.0

        return ((end - start) / start) * 100.0

    # --------------------------------------------------------

    @staticmethod
    def _difference_pct(first, second):

        if first <= 0:
            return 0.0

        return (abs(first - second) / first) * 100.0

    # ========================================================
    # DUPLICATE PROTECTION
    # ========================================================

    def _already_alerted(
        self,
        symbol,
        pattern,
        outer1,
        outer2,
    ):

        key = (
            pattern,
            outer1,
            outer2,
        )

        history = self._alert_history.setdefault(
            symbol,
            [],
        )

        if key in history:
            return True

        history.append(key)

        if len(history) > MAX_ALERT_HISTORY:
            del history[:-MAX_ALERT_HISTORY]

        return False

    # ========================================================
    # BUILD ALERT
    # ========================================================

    def _build_alert(
        self,
        pattern,
        direction,
        symbol,
        reversal_pct,
        outer_difference_pct,
        separation,
        entry,
        **points,
    ):

        reversal_score = min(
            reversal_pct / MIN_REVERSAL_PCT,
            2.0,
        )

        difference_score = min(
            outer_difference_pct / MIN_OUTER_DIFFERENCE_PCT,
            2.0,
        )

        separation_score = min(
            separation / MIN_OUTER_CANDLE_SEPARATION,
            2.0,
        )

        confidence = (
            (reversal_score + difference_score + separation_score) / 6.0
        ) * 100.0

        confidence = max(
            0.0,
            min(confidence, 100.0),
        )

        normalized_points = {}

        for name, point in points.items():

            if point is None:
                continue

            normalized_points[name] = {
                "candle_id": point.candle_id,
                "timestamp": self._ist_timestamp(point.timestamp),
                "price": point.price,
            }

        alert = {
            "pattern": pattern,
            "direction": direction,
            "symbol": symbol,
            # ------------------------------------------------
            # IMPORTANT:
            # This identifies exactly which of the four
            # conditions occurred.
            # ------------------------------------------------
            "alert_type": pattern,
            "confidence": round(
                confidence,
                1,
            ),
            "entry": entry,
            "reversal_pct": round(
                reversal_pct,
                4,
            ),
            "outer_difference_pct": round(
                outer_difference_pct,
                4,
            ),
            "candle_separation": separation,
            **normalized_points,
        }

        logger.warning(
            "MW ALERT | "
            "%s | %s | %s | "
            "confidence=%.1f | "
            "reversal=%.4f%% | "
            "difference=%.4f%% | "
            "separation=%d | entry=%s",
            symbol,
            pattern,
            direction,
            confidence,
            reversal_pct,
            outer_difference_pct,
            separation,
            entry,
        )

        return alert

    # ========================================================
    # IST TIMESTAMP
    # ========================================================

    @staticmethod
    def _ist_timestamp(timestamp):

        if timestamp is None:
            return None

        try:

            if timestamp.tzinfo is None:

                timestamp = timestamp.replace(tzinfo=INDIA_TZ)

            else:

                timestamp = timestamp.astimezone(INDIA_TZ)

            return timestamp.isoformat()

        except Exception:

            return str(timestamp)
