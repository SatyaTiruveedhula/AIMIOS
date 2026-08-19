from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

# Required first-leg reversal.
MIN_REVERSAL_PCT = 0.13

# M:
# HIGH1 must be higher than HIGH2 by at least 0.03%.
MIN_HIGH1_HIGH2_DIFFERENCE_PCT = 0.03

# W:
# VALLEY1 and VALLEY2 must be within 0.03%.
MAX_OUTER_DIFFERENCE_PCT = 0.03

# Minimum distance between the two outer points.
MIN_PIVOT_DISTANCE = 7

# Indian market timezone.
INDIA_TZ = ZoneInfo("Asia/Kolkata")


# ============================================================
# SIGNAL
# ============================================================


@dataclass(frozen=True)
class MWPatternSignal:

    pattern: str
    direction: str
    confidence: float

    entry: float
    stoploss: float
    target: float

    timestamp: Any
    symbol: str

    # M
    high1: Optional[float] = None
    valley: Optional[float] = None
    high2: Optional[float] = None

    # W
    valley1: Optional[float] = None
    high: Optional[float] = None
    valley2: Optional[float] = None

    # Metrics
    reversal_pct: Optional[float] = None
    swing_distance_pct: Optional[float] = None
    candle_distance: Optional[int] = None
    pivot_distance: Optional[int] = None

    first_pivot_candle_id: Optional[int] = None
    second_pivot_candle_id: Optional[int] = None

    # Day information
    day_high: Optional[float] = None
    day_low: Optional[float] = None

    day_high_candle_id: Optional[int] = None
    day_low_candle_id: Optional[int] = None

    # --------------------------------------------------------
    # Existing AIMIOS compatibility
    # --------------------------------------------------------

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


# ============================================================
# ENGINE
# ============================================================


class MWPatternEngine:
    """
    AIMIOS M/W pattern detector using CURRENT TRADING DAY
    HIGH / LOW.

    ============================================================
    M PATTERN
    ============================================================

                   DAY HIGH / HIGH1
                         |
                         |
                         | >= 0.13%
                         |
                      VALLEY
                         |
                         |
                      HIGH2
                         |
                         |

    Requirements:

        1. HIGH1 is the current day's HIGH.

        2. HIGH1 must occur before HIGH2.

        3. HIGH1 -> VALLEY >= 0.13%.

        4. HIGH1 > HIGH2.

        5. HIGH1 -> HIGH2 difference >= 0.03%.

        6. HIGH1 -> HIGH2 >= 7 candles.

        7. HIGH2 must already be completed.

    ============================================================
    W PATTERN
    ============================================================

                 VALLEY1 / DAY LOW
                         |
                         |
                         | >= 0.13%
                         |
                        HIGH
                         |
                         |
                      VALLEY2
                         |
                         |

    Requirements:

        1. VALLEY1 is the current day's LOW.

        2. VALLEY1 must occur before VALLEY2.

        3. VALLEY1 -> HIGH >= 0.13%.

        4. VALLEY1 and VALLEY2 within 0.03%.

        5. VALLEY1 -> VALLEY2 >= 7 candles.

        6. VALLEY2 must already be completed.

    ============================================================

    IMPORTANT
    ============================================================

    The engine is causal.

    It does NOT use future candles.

    Example:

        10:00  -> day high = 100
        10:01  -> falls
        ...
        10:10  -> HIGH2 = 99.95

    The 10:10 candle can confirm an M if all rules
    are satisfied.

    If a new higher day high is made later, that later
    high becomes the new day high for subsequent analysis.
    """

    name = "MWPatternEngine"

    def __init__(
        self,
        min_reversal_pct: float = MIN_REVERSAL_PCT,
        max_outer_difference_pct: float = MAX_OUTER_DIFFERENCE_PCT,
        min_high1_high2_difference_pct: float = MIN_HIGH1_HIGH2_DIFFERENCE_PCT,
        min_pivot_distance: int = MIN_PIVOT_DISTANCE,
    ) -> None:

        self.min_reversal_pct = float(min_reversal_pct)

        self.max_outer_difference_pct = float(max_outer_difference_pct)

        self.min_high1_high2_difference_pct = float(min_high1_high2_difference_pct)

        self.min_pivot_distance = int(min_pivot_distance)

        self.last_signal: Optional[MWPatternSignal] = None

    # ========================================================
    # PUBLIC DETECT
    # ========================================================

    def detect(
        self,
        candles: List[Any],
        symbol: str = "",
    ) -> Optional[MWPatternSignal]:

        if not candles:
            return None

        if len(candles) < self.min_pivot_distance + 1:
            return None

        # ----------------------------------------------------
        # Work only with the CURRENT TRADING DAY.
        # ----------------------------------------------------

        day_candles = self._get_current_trading_day_candles(candles)

        if len(day_candles) < self.min_pivot_distance + 1:
            return None

        # ----------------------------------------------------
        # Detect M.
        # ----------------------------------------------------

        m_signal = self._detect_m(
            day_candles,
            symbol,
        )

        # ----------------------------------------------------
        # Detect W.
        # ----------------------------------------------------

        w_signal = self._detect_w(
            day_candles,
            symbol,
        )

        candidates = [
            signal
            for signal in (
                m_signal,
                w_signal,
            )
            if signal is not None
        ]

        if not candidates:
            return None

        # ----------------------------------------------------
        # Prefer the pattern whose second outer point is
        # latest.
        # ----------------------------------------------------

        candidates.sort(
            key=lambda signal: (
                signal.second_pivot_candle_id
                if signal.second_pivot_candle_id is not None
                else -1
            ),
            reverse=True,
        )

        signal = candidates[0]

        self.last_signal = signal

        logger.info(
            "DAY M/W pattern detected | "
            "symbol=%s | pattern=%s | direction=%s | "
            "confidence=%.1f | entry=%.2f | "
            "day_high=%s | day_low=%s",
            signal.symbol,
            signal.pattern,
            signal.direction,
            signal.confidence,
            signal.entry,
            signal.day_high,
            signal.day_low,
        )

        return signal

    # ========================================================
    # CURRENT TRADING DAY
    # ========================================================

    def _get_current_trading_day_candles(
        self,
        candles: List[Any],
    ) -> List[Any]:
        """
        Return only candles belonging to the latest trading day.

        Candle timestamps are converted to Asia/Kolkata before
        comparing dates.

        This is important because AIMIOS internally uses UTC.
        """

        if not candles:
            return []

        latest = candles[-1]

        latest_date = self._trading_date(
            getattr(
                latest,
                "timestamp",
                None,
            )
        )

        if latest_date is None:
            return candles

        result: List[Any] = []

        for candle in candles:

            candle_date = self._trading_date(
                getattr(
                    candle,
                    "timestamp",
                    None,
                )
            )

            if candle_date == latest_date:
                result.append(candle)

        return result

    # ========================================================
    # TRADING DATE
    # ========================================================

    @staticmethod
    def _trading_date(
        timestamp: Any,
    ):

        if timestamp is None:
            return None

        try:

            if not isinstance(
                timestamp,
                datetime,
            ):
                return None

            if timestamp.tzinfo is None:

                timestamp = timestamp.replace(tzinfo=ZoneInfo("UTC"))

            return timestamp.astimezone(INDIA_TZ).date()

        except Exception:

            return None

    # ========================================================
    # M DETECTION
    # ========================================================

    def _detect_m(
        self,
        candles: List[Any],
        symbol: str,
    ) -> Optional[MWPatternSignal]:

        if len(candles) < self.min_pivot_distance + 1:
            return None

        # ----------------------------------------------------
        # CURRENT DAY HIGH
        # ----------------------------------------------------

        day_high_candle = max(
            candles,
            key=lambda item: float(item.high),
        )

        day_high = float(day_high_candle.high)

        high1_index = self._candle_index(
            candles,
            day_high_candle,
        )

        if high1_index is None:
            return None

        # ----------------------------------------------------
        # HIGH1 must not be the final candle.
        #
        # We need candles AFTER the day high to form the
        # valley and HIGH2.
        # ----------------------------------------------------

        if high1_index >= len(candles) - 1:
            return None

        # ----------------------------------------------------
        # HIGH2 candidates occur AFTER DAY HIGH.
        # ----------------------------------------------------

        high2_candidates = []

        for index in range(
            high1_index + self.min_pivot_distance,
            len(candles),
        ):

            candle = candles[index]

            # Last candle can be used because CandleBuffer
            # sends only COMPLETED candles to this engine.

            high2_candidates.append((index, candle))

        if not high2_candidates:
            return None

        # ----------------------------------------------------
        # Search newest HIGH2 first.
        # ----------------------------------------------------

        for high2_index, high2 in reversed(high2_candidates):

            high2_value = float(high2.high)

            # ------------------------------------------------
            # HIGH1 must be higher than HIGH2.
            # ------------------------------------------------

            if day_high <= high2_value:
                continue

            swing_distance_pct = (day_high - high2_value) / abs(day_high) * 100.0

            if swing_distance_pct < self.min_high1_high2_difference_pct:
                continue

            # ------------------------------------------------
            # Find local valleys between HIGH1 and HIGH2.
            # ------------------------------------------------

            valleys_between = []

            for index in range(
                high1_index + 1,
                high2_index,
            ):

                candle = candles[index]

                if self._is_low_pivot(
                    candles,
                    index,
                ):

                    valleys_between.append((index, candle))

            if not valleys_between:
                continue

            # ------------------------------------------------
            # Deepest valley.
            # ------------------------------------------------

            valley_index, valley = min(
                valleys_between,
                key=lambda item: float(item[1].low),
            )

            valley_value = float(valley.low)

            # ------------------------------------------------
            # HIGH1 -> VALLEY >= 0.13%
            # ------------------------------------------------

            reversal_pct = (day_high - valley_value) / abs(day_high) * 100.0

            if reversal_pct < self.min_reversal_pct:
                continue

            candle_distance = high2_index - high1_index

            # ------------------------------------------------
            # CONFIRMED M
            # ------------------------------------------------

            return self._build_m_signal(
                candles=candles,
                symbol=symbol,
                high1=day_high_candle,
                valley=valley,
                high2=high2,
                high1_index=high1_index,
                high2_index=high2_index,
                reversal_pct=reversal_pct,
                swing_distance_pct=swing_distance_pct,
                candle_distance=candle_distance,
                day_high=day_high,
                day_low=min(float(c.low) for c in candles),
            )

        return None

    # ========================================================
    # W DETECTION
    # ========================================================

    def _detect_w(
        self,
        candles: List[Any],
        symbol: str,
    ) -> Optional[MWPatternSignal]:

        if len(candles) < self.min_pivot_distance + 1:
            return None

        # ----------------------------------------------------
        # CURRENT DAY LOW
        # ----------------------------------------------------

        day_low_candle = min(
            candles,
            key=lambda item: float(item.low),
        )

        day_low = float(day_low_candle.low)

        valley1_index = self._candle_index(
            candles,
            day_low_candle,
        )

        if valley1_index is None:
            return None

        # ----------------------------------------------------
        # DAY LOW must have candles after it.
        # ----------------------------------------------------

        if valley1_index >= len(candles) - 1:
            return None

        # ----------------------------------------------------
        # VALLEY2 must occur later.
        # ----------------------------------------------------

        valley2_candidates = []

        for index in range(
            valley1_index + self.min_pivot_distance,
            len(candles),
        ):

            candle = candles[index]

            valley2_candidates.append((index, candle))

        if not valley2_candidates:
            return None

        # ----------------------------------------------------
        # Search newest VALLEY2 first.
        # ----------------------------------------------------

        for valley2_index, valley2 in reversed(valley2_candidates):

            valley2_value = float(valley2.low)

            # ------------------------------------------------
            # VALLEY1 vs VALLEY2 <= 0.03%
            # ------------------------------------------------

            swing_distance_pct = self._difference_pct(
                day_low,
                valley2_value,
            )

            if swing_distance_pct > self.max_outer_difference_pct:
                continue

            # ------------------------------------------------
            # Find highest middle high.
            # ------------------------------------------------

            highs_between = []

            for index in range(
                valley1_index + 1,
                valley2_index,
            ):

                candle = candles[index]

                if self._is_high_pivot(
                    candles,
                    index,
                ):

                    highs_between.append((index, candle))

            if not highs_between:
                continue

            # ------------------------------------------------
            # Highest middle high.
            # ------------------------------------------------

            high_index, high = max(
                highs_between,
                key=lambda item: float(item[1].high),
            )

            high_value = float(high.high)

            # ------------------------------------------------
            # VALLEY1 -> HIGH >= 0.13%
            # ------------------------------------------------

            reversal_pct = (high_value - day_low) / abs(day_low) * 100.0

            if reversal_pct < self.min_reversal_pct:
                continue

            candle_distance = valley2_index - valley1_index

            # ------------------------------------------------
            # CONFIRMED W
            # ------------------------------------------------

            return self._build_w_signal(
                candles=candles,
                symbol=symbol,
                valley1=day_low_candle,
                high=high,
                valley2=valley2,
                valley1_index=valley1_index,
                valley2_index=valley2_index,
                reversal_pct=reversal_pct,
                swing_distance_pct=swing_distance_pct,
                candle_distance=candle_distance,
                day_high=max(float(c.high) for c in candles),
                day_low=day_low,
            )

        return None

    # ========================================================
    # BUILD M SIGNAL
    # ========================================================

    def _build_m_signal(
        self,
        candles: List[Any],
        symbol: str,
        high1: Any,
        valley: Any,
        high2: Any,
        high1_index: int,
        high2_index: int,
        reversal_pct: float,
        swing_distance_pct: float,
        candle_distance: int,
        day_high: float,
        day_low: float,
    ) -> MWPatternSignal:

        last = candles[-1]

        entry = float(
            getattr(
                last,
                "close",
                high2.high,
            )
        )

        stoploss = float(
            max(
                float(high1.high),
                float(high2.high),
            )
        )

        risk = max(
            stoploss - entry,
            0.0,
        )

        if risk <= 0.0:

            risk = max(
                float(high2.high) - float(valley.low),
                0.0,
            )

        target = entry - (risk * 2.0)

        confidence = self._calculate_confidence(
            reversal_pct,
            swing_distance_pct,
            candle_distance,
        )

        return MWPatternSignal(
            pattern="M",
            direction="SELL",
            confidence=confidence,
            entry=entry,
            stoploss=stoploss,
            target=target,
            timestamp=getattr(
                last,
                "timestamp",
                None,
            ),
            symbol=symbol,
            high1=float(high1.high),
            valley=float(valley.low),
            high2=float(high2.high),
            reversal_pct=float(reversal_pct),
            swing_distance_pct=float(swing_distance_pct),
            candle_distance=int(candle_distance),
            pivot_distance=int(candle_distance),
            first_pivot_candle_id=self._candle_id(
                high1,
                high1_index,
            ),
            second_pivot_candle_id=self._candle_id(
                high2,
                high2_index,
            ),
            day_high=float(day_high),
            day_low=float(day_low),
            day_high_candle_id=self._candle_id(
                high1,
                high1_index,
            ),
        )

    # ========================================================
    # BUILD W SIGNAL
    # ========================================================

    def _build_w_signal(
        self,
        candles: List[Any],
        symbol: str,
        valley1: Any,
        high: Any,
        valley2: Any,
        valley1_index: int,
        valley2_index: int,
        reversal_pct: float,
        swing_distance_pct: float,
        candle_distance: int,
        day_high: float,
        day_low: float,
    ) -> MWPatternSignal:

        last = candles[-1]

        entry = float(
            getattr(
                last,
                "close",
                valley2.low,
            )
        )

        stoploss = float(
            min(
                float(valley1.low),
                float(valley2.low),
            )
        )

        risk = max(
            entry - stoploss,
            0.0,
        )

        if risk <= 0.0:

            risk = max(
                float(high.high) - float(valley2.low),
                0.0,
            )

        target = entry + (risk * 2.0)

        confidence = self._calculate_confidence(
            reversal_pct,
            swing_distance_pct,
            candle_distance,
        )

        return MWPatternSignal(
            pattern="W",
            direction="BUY",
            confidence=confidence,
            entry=entry,
            stoploss=stoploss,
            target=target,
            timestamp=getattr(
                last,
                "timestamp",
                None,
            ),
            symbol=symbol,
            valley1=float(valley1.low),
            high=float(high.high),
            valley2=float(valley2.low),
            reversal_pct=float(reversal_pct),
            swing_distance_pct=float(swing_distance_pct),
            candle_distance=int(candle_distance),
            pivot_distance=int(candle_distance),
            first_pivot_candle_id=self._candle_id(
                valley1,
                valley1_index,
            ),
            second_pivot_candle_id=self._candle_id(
                valley2,
                valley2_index,
            ),
            day_high=float(day_high),
            day_low=float(day_low),
            day_low_candle_id=self._candle_id(
                valley1,
                valley1_index,
            ),
        )

    # ========================================================
    # HIGH PIVOT
    # ========================================================

    def _is_high_pivot(
        self,
        candles: List[Any],
        index: int,
    ) -> bool:

        if index <= 0:
            return False

        if index >= len(candles) - 1:
            return False

        previous = float(candles[index - 1].high)

        current = float(candles[index].high)

        following = float(candles[index + 1].high)

        # Normal local high.
        if (
            current >= previous
            and current >= following
            and (current > previous or current > following)
        ):
            return True

        # Small plateau.
        return (
            current >= previous
            and self._difference_pct(
                current,
                following,
            )
            <= self.max_outer_difference_pct
        )

    # ========================================================
    # LOW PIVOT
    # ========================================================

    def _is_low_pivot(
        self,
        candles: List[Any],
        index: int,
    ) -> bool:

        if index <= 0:
            return False

        if index >= len(candles) - 1:
            return False

        previous = float(candles[index - 1].low)

        current = float(candles[index].low)

        following = float(candles[index + 1].low)

        # Normal local low.
        if (
            current <= previous
            and current <= following
            and (current < previous or current < following)
        ):
            return True

        # Small plateau.
        return (
            current <= previous
            and self._difference_pct(
                current,
                following,
            )
            <= self.max_outer_difference_pct
        )

    # ========================================================
    # CANDLE INDEX
    # ========================================================

    @staticmethod
    def _candle_index(
        candles: List[Any],
        candle: Any,
    ) -> Optional[int]:

        candle_id = getattr(
            candle,
            "candle_id",
            None,
        )

        if candle_id is not None:

            for index, item in enumerate(candles):

                if (
                    getattr(
                        item,
                        "candle_id",
                        None,
                    )
                    == candle_id
                ):
                    return index

        for index, item in enumerate(candles):

            if item is candle:
                return index

        return None

    # ========================================================
    # CANDLE ID
    # ========================================================

    @staticmethod
    def _candle_id(
        candle: Any,
        fallback: int,
    ) -> int:

        value = getattr(
            candle,
            "candle_id",
            None,
        )

        if value is None:
            return fallback

        try:
            return int(value)

        except (
            TypeError,
            ValueError,
        ):
            return fallback

    # ========================================================
    # PERCENT DIFFERENCE
    # ========================================================

    @staticmethod
    def _difference_pct(
        first: float,
        second: float,
    ) -> float:

        denominator = max(
            abs(first),
            abs(second),
            1e-12,
        )

        return abs(first - second) / denominator * 100.0

    # ========================================================
    # CONFIDENCE
    # ========================================================

    def _calculate_confidence(
        self,
        reversal_pct: float,
        swing_distance_pct: float,
        candle_distance: int,
    ) -> float:

        confidence = 80.0

        extra_reversal = max(
            reversal_pct - self.min_reversal_pct,
            0.0,
        )

        confidence += min(
            extra_reversal * 20.0,
            10.0,
        )

        if swing_distance_pct <= self.max_outer_difference_pct / 2.0:
            confidence += 5.0

        if candle_distance >= 10:
            confidence += 3.0

        return min(
            round(
                confidence,
                1,
            ),
            100.0,
        )

    # ========================================================
    # RESET
    # ========================================================

    def reset(self) -> None:

        self.last_signal = None

        logger.info("MWPatternEngine reset")

    # ========================================================
    # STATUS
    # ========================================================

    def status(self) -> dict:

        signal = self.last_signal

        return {
            "engine": self.name,
            "last_pattern": (signal.pattern if signal is not None else None),
            "last_direction": (signal.direction if signal is not None else None),
            "last_confidence": (signal.confidence if signal is not None else None),
            "last_day_high": (signal.day_high if signal is not None else None),
            "last_day_low": (signal.day_low if signal is not None else None),
            "last_signal": signal,
        }
