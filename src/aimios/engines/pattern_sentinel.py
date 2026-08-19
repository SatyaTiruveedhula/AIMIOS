from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

INDIA_TZ = ZoneInfo("Asia/Kolkata")


# ============================================================
# CONFIGURATION
# ============================================================

# Required first-leg reversal.
#
# M:
#   HIGH1 -> VALLEY
#
# W:
#   LOW1 -> PEAK
#
# Minimum = 0.13%
MIN_REVERSAL_PCT = 0.13

# Required difference between outer points.
#
# M:
#   HIGH1 must be higher than HIGH2 by at least 0.03%
#
# W:
#   LOW2 must be higher than LOW1 by at least 0.03%
#
MIN_OUTER_DIFFERENCE_PCT = 0.03

# Minimum number of candles between the two
# outer points.
#
# IMPORTANT:
# If HIGH1 candle_id = 10
# and HIGH2 candle_id = 17
#
# separation = 7 candles
#
MIN_OUTER_CANDLE_SEPARATION = 7

# Number of candles on each side required to
# identify a local pivot.
#
# A pivot is therefore confirmed only after candles
# following the pivot have completed.
PIVOT_LEFT = 1
PIVOT_RIGHT = 1

# Search window.
MAX_PATTERN_CANDLES = 100

# Prevent the same pattern from repeatedly generating
# alerts on every subsequent completed candle.
MAX_ALERT_HISTORY = 1000


# ============================================================
# PIVOT
# ============================================================


@dataclass(frozen=True)
class Pivot:
    index: int
    candle_id: int
    timestamp: datetime
    price: float
    kind: str

    # "HIGH" or "LOW"


# ============================================================
# PATTERN SENTINEL
# ============================================================


class PatternSentinel:
    """
    Detects only the requested M/W pattern.

    M:

        HIGH1
           /\
          /  \
         /    \
                HIGH2
                /\
               /  \
              /

        HIGH1 -> VALLEY >= 0.13%

        HIGH1 > HIGH2 by >= 0.03%

        HIGH1 -> HIGH2 >= 7 candles

        RESULT = SELL


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

        RESULT = BUY


    Detection is performed only using completed candles.

    CandleBuffer already guarantees this by calling:

        process_candle(
            candle=completed_candle,
            candles=completed_candles,
            symbol=instrument_id,
        )
    """

    def __init__(self) -> None:

        self._alert_history: Dict[
            str,
            List[Tuple[str, int, int]],
        ] = {}

        logger.info(
            "PatternSentinel initialized | "
            "M/W | reversal=%.2f%% | "
            "outer_difference=%.2f%% | "
            "min_separation=%d candles",
            MIN_REVERSAL_PCT,
            MIN_OUTER_DIFFERENCE_PCT,
            MIN_OUTER_CANDLE_SEPARATION,
        )

    # ========================================================
    # START
    # ========================================================

    def start(self) -> None:
        """
        Kept for compatibility with CandleBuffer.

        PatternSentinel does not require a background thread.
        Detection happens synchronously when a completed
        candle is received.
        """

        logger.info("PatternSentinel started")

    # ========================================================
    # CLEAR
    # ========================================================

    def clear(self) -> None:

        self._alert_history.clear()

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
        """
        Process one newly completed candle.

        Returns:

            None
                No new pattern.

            dict
                M/W alert.
        """

        if candle is None:
            return None

        if not candles:
            return None

        if not symbol:
            return None

        # ----------------------------------------------------
        # ONLY COMPLETED CANDLES
        #
        # CandleBuffer passes completed history.
        # Make a defensive list so the detector never
        # modifies CandleBuffer's deque.
        # ----------------------------------------------------

        completed = list(candles)

        if len(completed) < 3:
            return None

        # ----------------------------------------------------
        # Use only the most recent search window.
        # ----------------------------------------------------

        if len(completed) > MAX_PATTERN_CANDLES:
            completed = completed[-MAX_PATTERN_CANDLES:]

        # ----------------------------------------------------
        # The newly completed candle should be the latest
        # candle supplied by CandleBuffer.
        # ----------------------------------------------------

        latest = completed[-1]

        # ----------------------------------------------------
        # We need at least enough candles to create
        # the outer 7-candle separation.
        # ----------------------------------------------------

        if len(completed) < (MIN_OUTER_CANDLE_SEPARATION + 1):
            return None

        # ----------------------------------------------------
        # Find confirmed pivots.
        # ----------------------------------------------------

        high_pivots = self._find_high_pivots(completed)

        low_pivots = self._find_low_pivots(completed)

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # We evaluate M first and W second.
        #
        # The pattern must terminate at the latest
        # completed candle / latest confirmed pivot.
        # ----------------------------------------------------

        m_alert = self._detect_m(
            completed=completed,
            high_pivots=high_pivots,
            low_pivots=low_pivots,
            symbol=symbol,
            latest=latest,
        )

        if m_alert is not None:
            return m_alert

        w_alert = self._detect_w(
            completed=completed,
            low_pivots=low_pivots,
            high_pivots=high_pivots,
            symbol=symbol,
            latest=latest,
        )

        if w_alert is not None:
            return w_alert

        return None

    # ========================================================
    # M DETECTION
    # ========================================================

    def _detect_m(
        self,
        completed,
        high_pivots: List[Pivot],
        low_pivots: List[Pivot],
        symbol: str,
        latest,
    ) -> Optional[Dict[str, object]]:
        """
        Detect:

            HIGH1 -> VALLEY -> HIGH2

        Conditions:

            HIGH1 -> VALLEY >= 0.13%

            HIGH1 > HIGH2 by >= 0.03%

            HIGH1 -> HIGH2 >= 7 candles

        Alert is generated when HIGH2 is confirmed.
        """

        if len(high_pivots) < 2:
            return None

        # ----------------------------------------------------
        # Work backwards so the newest completed M gets
        # priority.
        # ----------------------------------------------------

        for high2 in reversed(high_pivots):

            # HIGH2 must be the latest confirmed high pivot.
            #
            # This prevents an old M from generating an alert
            # again when a newer candle arrives.
            if high2.index >= len(completed) - PIVOT_RIGHT:
                pass
            else:
                continue

            # ------------------------------------------------
            # Need HIGH1 before HIGH2.
            # ------------------------------------------------

            previous_highs = [h for h in high_pivots if h.index < high2.index]

            if not previous_highs:
                continue

            # ------------------------------------------------
            # Use the nearest valid HIGH1 first.
            # ------------------------------------------------

            for high1 in reversed(previous_highs):

                separation = high2.candle_id - high1.candle_id

                if separation < (MIN_OUTER_CANDLE_SEPARATION):
                    continue

                # ------------------------------------------------
                # There must be a valley between HIGH1 and HIGH2.
                # ------------------------------------------------

                valleys = [
                    low for low in low_pivots if (high1.index < low.index < high2.index)
                ]

                if not valleys:
                    continue

                # ------------------------------------------------
                # Find the deepest valley between the two highs.
                # ------------------------------------------------

                valley = min(
                    valleys,
                    key=lambda x: x.price,
                )

                # ------------------------------------------------
                # HIGH1 -> VALLEY percentage.
                # ------------------------------------------------

                reversal_pct = self._down_pct(
                    high1.price,
                    valley.price,
                )

                if reversal_pct < (MIN_REVERSAL_PCT):
                    continue

                # ------------------------------------------------
                # HIGH1 must be higher than HIGH2
                # by at least 0.03%.
                #
                # Example:
                #
                # HIGH1 = 100
                #
                # HIGH2 must be <= 99.97
                # ------------------------------------------------

                high_difference_pct = self._difference_pct(
                    high1.price,
                    high2.price,
                )

                if high1.price <= high2.price:
                    continue

                if high_difference_pct < (MIN_OUTER_DIFFERENCE_PCT):
                    continue

                # ------------------------------------------------
                # Make sure HIGH2 is genuinely after the valley.
                # ------------------------------------------------

                if not (high1.index < valley.index < high2.index):
                    continue

                # ------------------------------------------------
                # Duplicate protection.
                # ------------------------------------------------

                if self._already_alerted(
                    symbol=symbol,
                    pattern="M",
                    outer1=high1.candle_id,
                    outer2=high2.candle_id,
                ):
                    continue

                return self._build_alert(
                    pattern="M",
                    direction="SELL",
                    symbol=symbol,
                    high1=high1,
                    valley=valley,
                    high2=high2,
                    reversal_pct=reversal_pct,
                    outer_difference_pct=(high_difference_pct),
                    separation=separation,
                    entry=float(latest.close),
                )

        return None

    # ========================================================
    # W DETECTION
    # ========================================================

    def _detect_w(
        self,
        completed,
        low_pivots: List[Pivot],
        high_pivots: List[Pivot],
        symbol: str,
        latest,
    ) -> Optional[Dict[str, object]]:
        """
        Detect:

            LOW1 -> PEAK -> LOW2

        Conditions:

            LOW1 -> PEAK >= 0.13%

            LOW2 > LOW1 by >= 0.03%

            LOW1 -> LOW2 >= 7 candles

        Alert is generated when LOW2 is confirmed.
        """

        if len(low_pivots) < 2:
            return None

        # ----------------------------------------------------
        # Newest LOW2 first.
        # ----------------------------------------------------

        for low2 in reversed(low_pivots):

            # LOW2 must be the latest confirmed low pivot.
            if low2.index >= len(completed) - PIVOT_RIGHT:
                pass
            else:
                continue

            previous_lows = [low for low in low_pivots if low.index < low2.index]

            if not previous_lows:
                continue

            # ------------------------------------------------
            # Nearest LOW1 first.
            # ------------------------------------------------

            for low1 in reversed(previous_lows):

                separation = low2.candle_id - low1.candle_id

                if separation < (MIN_OUTER_CANDLE_SEPARATION):
                    continue

                # ------------------------------------------------
                # Need a peak between LOW1 and LOW2.
                # ------------------------------------------------

                peaks = [
                    high
                    for high in high_pivots
                    if (low1.index < high.index < low2.index)
                ]

                if not peaks:
                    continue

                # ------------------------------------------------
                # Highest peak between LOW1 and LOW2.
                # ------------------------------------------------

                peak = max(
                    peaks,
                    key=lambda x: x.price,
                )

                # ------------------------------------------------
                # LOW1 -> PEAK percentage.
                # ------------------------------------------------

                reversal_pct = self._up_pct(
                    low1.price,
                    peak.price,
                )

                if reversal_pct < (MIN_REVERSAL_PCT):
                    continue

                # ------------------------------------------------
                # LOW2 must be higher than LOW1
                # by at least 0.03%.
                #
                # Example:
                #
                # LOW1 = 100
                #
                # LOW2 must be >= 100.03
                # ------------------------------------------------

                low_difference_pct = self._difference_pct(
                    low2.price,
                    low1.price,
                )

                if low2.price <= low1.price:
                    continue

                if low_difference_pct < (MIN_OUTER_DIFFERENCE_PCT):
                    continue

                # ------------------------------------------------
                # Correct ordering.
                # ------------------------------------------------

                if not (low1.index < peak.index < low2.index):
                    continue

                # ------------------------------------------------
                # Duplicate protection.
                # ------------------------------------------------

                if self._already_alerted(
                    symbol=symbol,
                    pattern="W",
                    outer1=low1.candle_id,
                    outer2=low2.candle_id,
                ):
                    continue

                return self._build_alert(
                    pattern="W",
                    direction="BUY",
                    symbol=symbol,
                    low1=low1,
                    peak=peak,
                    low2=low2,
                    reversal_pct=reversal_pct,
                    outer_difference_pct=(low_difference_pct),
                    separation=separation,
                    entry=float(latest.close),
                )

        return None

    # ========================================================
    # HIGH PIVOTS
    # ========================================================

    @staticmethod
    def _find_high_pivots(
        candles,
    ) -> List[Pivot]:

        pivots: List[Pivot] = []

        total = len(candles)

        for i in range(
            PIVOT_LEFT,
            total - PIVOT_RIGHT,
        ):

            candle = candles[i]

            is_high = True

            # ------------------------------------------------
            # Compare against candles to the left.
            # ------------------------------------------------

            for j in range(
                i - PIVOT_LEFT,
                i,
            ):

                if candles[j].high > candle.high:
                    is_high = False
                    break

            if not is_high:
                continue

            # ------------------------------------------------
            # Compare against candles to the right.
            # ------------------------------------------------

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
    def _find_low_pivots(
        candles,
    ) -> List[Pivot]:

        pivots: List[Pivot] = []

        total = len(candles)

        for i in range(
            PIVOT_LEFT,
            total - PIVOT_RIGHT,
        ):

            candle = candles[i]

            is_low = True

            # ------------------------------------------------
            # Compare against candles to the left.
            # ------------------------------------------------

            for j in range(
                i - PIVOT_LEFT,
                i,
            ):

                if candles[j].low < candle.low:
                    is_low = False
                    break

            if not is_low:
                continue

            # ------------------------------------------------
            # Compare against candles to the right.
            # ------------------------------------------------

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
    def _down_pct(
        start: float,
        end: float,
    ) -> float:

        if start <= 0:
            return 0.0

        return ((start - end) / start) * 100.0

    # --------------------------------------------------------

    @staticmethod
    def _up_pct(
        start: float,
        end: float,
    ) -> float:

        if start <= 0:
            return 0.0

        return ((end - start) / start) * 100.0

    # --------------------------------------------------------

    @staticmethod
    def _difference_pct(
        first: float,
        second: float,
    ) -> float:

        if first <= 0:
            return 0.0

        return (abs(first - second) / first) * 100.0

    # ========================================================
    # DUPLICATE PROTECTION
    # ========================================================

    def _already_alerted(
        self,
        symbol: str,
        pattern: str,
        outer1: int,
        outer2: int,
    ) -> bool:

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

        # Keep memory bounded.
        if len(history) > MAX_ALERT_HISTORY:
            del history[:-MAX_ALERT_HISTORY]

        return False

    # ========================================================
    # BUILD ALERT
    # ========================================================

    def _build_alert(
        self,
        pattern: str,
        direction: str,
        symbol: str,
        reversal_pct: float,
        outer_difference_pct: float,
        separation: int,
        entry: float,
        **points,
    ) -> Dict[str, object]:
        """
        Build the exact dictionary consumed by CandleBuffer.

        CandleBuffer expects:

            pattern
            direction
            confidence
            entry

        It subsequently adds:

            day_high
            day_low
            price
        """

        # ----------------------------------------------------
        # Confidence
        #
        # This is NOT used as a detection requirement.
        #
        # Detection is determined strictly by the user's
        # 0.13%, 0.03%, and 7-candle rules.
        #
        # Confidence is only an informational score.
        # ----------------------------------------------------

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
            min(
                confidence,
                100.0,
            ),
        )

        # ----------------------------------------------------
        # Standardize timestamps to IST.
        # ----------------------------------------------------

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
            "confidence": round(
                confidence,
                1,
            ),
            "entry": entry,
            # Detection measurements.
            "reversal_pct": round(
                reversal_pct,
                4,
            ),
            "outer_difference_pct": round(
                outer_difference_pct,
                4,
            ),
            "candle_separation": separation,
            # Pattern points.
            **normalized_points,
        }

        logger.warning(
            "MW DETECTED | "
            "%s | pattern=%s | direction=%s | "
            "confidence=%.1f | "
            "reversal=%.4f%% | "
            "outer_difference=%.4f%% | "
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
    def _ist_timestamp(
        timestamp: Optional[datetime],
    ) -> Optional[str]:

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
