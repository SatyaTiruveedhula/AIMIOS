from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

# Required first-leg reversal.
MIN_REVERSAL_PCT = 0.13

# Maximum difference between outer pivots.
MAX_OUTER_DIFFERENCE_PCT = 0.03

# Minimum candle distance between outer pivots.
MIN_PIVOT_DISTANCE = 7

# Confidence defaults.
M_PATTERN_CONFIDENCE = 90.0
W_PATTERN_CONFIDENCE = 90.0


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

    high1: Optional[float] = None
    valley: Optional[float] = None
    high2: Optional[float] = None

    valley1: Optional[float] = None
    high: Optional[float] = None
    valley2: Optional[float] = None

    reversal_pct: Optional[float] = None
    swing_distance_pct: Optional[float] = None
    candle_distance: Optional[int] = None
    pivot_distance: Optional[int] = None

    first_pivot_candle_id: Optional[int] = None
    second_pivot_candle_id: Optional[int] = None

    # --------------------------------------------------------
    # Dictionary compatibility for existing AIMIOS tests.
    # --------------------------------------------------------

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


# ============================================================
# ENGINE
# ============================================================


class MWPatternEngine:
    """
    AIMIOS M/W pattern detector.

    M PATTERN
    ---------

        HIGH1
           |
           | >= 0.13%
           |
        VALLEY
           |
           | recovery
           |
        HIGH2

    Required:

        HIGH1 -> VALLEY >= 0.13%

        abs(HIGH1 - HIGH2) / HIGH1 <= 0.03%

        HIGH1 and HIGH2 >= 7 candles apart

    IMPORTANT:
        There is NO 0.13% requirement on the recovery from
        VALLEY to HIGH2.

    This is important because the AIMIOS valid-M test contains:

        HIGH1  = 24500
        VALLEY = 24465
        HIGH2  = 24494

    HIGH1 -> VALLEY is about 0.14286%, which is valid.

    HIGH2 -> VALLEY is only about 0.1185%, but this is allowed.


    W PATTERN
    ---------

        VALLEY1
           |
           | >= 0.13%
           |
          HIGH
           |
           | pullback
           |
        VALLEY2

    Required:

        VALLEY1 -> HIGH >= 0.13%

        abs(VALLEY1 - VALLEY2) / VALLEY1 <= 0.03%

        VALLEY1 and VALLEY2 >= 7 candles apart

    There is NO additional 0.13% requirement on the second leg.


    Pivot detection
    ---------------

    Small plateaus / near-pivots are allowed.

    This is intentional for market data where a turning point
    can span multiple nearby candles.
    """

    name = "MWPatternEngine"

    def __init__(
        self,
        min_reversal_pct: float = MIN_REVERSAL_PCT,
        max_outer_difference_pct: float = MAX_OUTER_DIFFERENCE_PCT,
        min_pivot_distance: int = MIN_PIVOT_DISTANCE,
    ) -> None:

        self.min_reversal_pct = float(min_reversal_pct)

        self.max_outer_difference_pct = float(max_outer_difference_pct)

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

        m_signal = self._detect_m(
            candles,
            symbol,
        )

        w_signal = self._detect_w(
            candles,
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

        # Prefer the formation whose second outer pivot
        # occurs latest.
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
            "M/W pattern detected | "
            "symbol=%s | pattern=%s | direction=%s | "
            "confidence=%.1f | entry=%.2f",
            signal.symbol,
            signal.pattern,
            signal.direction,
            signal.confidence,
            signal.entry,
        )

        return signal

    # ========================================================
    # M DETECTION
    # ========================================================

    def _detect_m(
        self,
        candles: List[Any],
        symbol: str,
    ) -> Optional[MWPatternSignal]:

        highs = self._find_high_pivots(candles)
        lows = self._find_low_pivots(candles)

        if not highs or not lows:
            return None

        # Search newest HIGH2 first.
        for high2 in reversed(highs):

            high2_index = self._candle_index(
                candles,
                high2,
            )

            if high2_index is None:
                continue

            # Search HIGH1 backwards.
            for high1 in reversed(highs):

                high1_index = self._candle_index(
                    candles,
                    high1,
                )

                if high1_index is None:
                    continue

                if high1_index >= high2_index:
                    continue

                candle_distance = high2_index - high1_index

                # Minimum outer-pivot separation.
                if candle_distance < self.min_pivot_distance:
                    continue

                # HIGH1 and HIGH2 must be approximately
                # equal.
                swing_distance_pct = self._difference_pct(
                    float(high1.high),
                    float(high2.high),
                )

                if swing_distance_pct > self.max_outer_difference_pct:
                    continue

                # Find valleys between HIGH1 and HIGH2.
                valleys_between: List[Any] = []

                for valley in lows:

                    valley_index = self._candle_index(
                        candles,
                        valley,
                    )

                    if valley_index is None:
                        continue

                    if high1_index < valley_index < high2_index:
                        valleys_between.append(valley)

                if not valleys_between:
                    continue

                # Use the deepest valley.
                valley = min(
                    valleys_between,
                    key=lambda item: float(item.low),
                )

                # ------------------------------------------------
                # THE ONLY REQUIRED REVERSAL FOR M.
                #
                # HIGH1 -> VALLEY >= 0.13%
                # ------------------------------------------------

                reversal_pct = (
                    (float(high1.high) - float(valley.low))
                    / abs(float(high1.high))
                    * 100.0
                )

                if reversal_pct < self.min_reversal_pct:
                    continue

                # ------------------------------------------------
                # DO NOT require:
                #
                # VALLEY -> HIGH2 >= 0.13%
                #
                # This was the bug causing the valid-M test
                # to fail.
                # ------------------------------------------------

                return self._build_m_signal(
                    candles=candles,
                    symbol=symbol,
                    high1=high1,
                    valley=valley,
                    high2=high2,
                    high1_index=high1_index,
                    high2_index=high2_index,
                    reversal_pct=reversal_pct,
                    swing_distance_pct=swing_distance_pct,
                    candle_distance=candle_distance,
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

        lows = self._find_low_pivots(candles)
        highs = self._find_high_pivots(candles)

        if not lows or not highs:
            return None

        # Search newest VALLEY2 first.
        for valley2 in reversed(lows):

            valley2_index = self._candle_index(
                candles,
                valley2,
            )

            if valley2_index is None:
                continue

            # Search VALLEY1 backwards.
            for valley1 in reversed(lows):

                valley1_index = self._candle_index(
                    candles,
                    valley1,
                )

                if valley1_index is None:
                    continue

                if valley1_index >= valley2_index:
                    continue

                candle_distance = valley2_index - valley1_index

                if candle_distance < self.min_pivot_distance:
                    continue

                # VALLEY1 and VALLEY2 must be approximately
                # equal.
                swing_distance_pct = self._difference_pct(
                    float(valley1.low),
                    float(valley2.low),
                )

                if swing_distance_pct > self.max_outer_difference_pct:
                    continue

                # Find highs between the two valleys.
                highs_between: List[Any] = []

                for high in highs:

                    high_index = self._candle_index(
                        candles,
                        high,
                    )

                    if high_index is None:
                        continue

                    if valley1_index < high_index < valley2_index:
                        highs_between.append(high)

                if not highs_between:
                    continue

                # Highest middle peak.
                high = max(
                    highs_between,
                    key=lambda item: float(item.high),
                )

                # ------------------------------------------------
                # THE ONLY REQUIRED REVERSAL FOR W.
                #
                # VALLEY1 -> HIGH >= 0.13%
                # ------------------------------------------------

                reversal_pct = (
                    (float(high.high) - float(valley1.low))
                    / abs(float(valley1.low))
                    * 100.0
                )

                if reversal_pct < self.min_reversal_pct:
                    continue

                # ------------------------------------------------
                # DO NOT require:
                #
                # HIGH -> VALLEY2 >= 0.13%
                #
                # The second leg only needs to form the W.
                # ------------------------------------------------

                return self._build_w_signal(
                    candles=candles,
                    symbol=symbol,
                    valley1=valley1,
                    high=high,
                    valley2=valley2,
                    valley1_index=valley1_index,
                    valley2_index=valley2_index,
                    reversal_pct=reversal_pct,
                    swing_distance_pct=swing_distance_pct,
                    candle_distance=candle_distance,
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
            reversal_pct=reversal_pct,
            swing_distance_pct=swing_distance_pct,
            candle_distance=candle_distance,
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
            reversal_pct=reversal_pct,
            swing_distance_pct=swing_distance_pct,
            candle_distance=candle_distance,
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
        )

    # ========================================================
    # HIGH PIVOT DETECTION
    # ========================================================

    def _find_high_pivots(
        self,
        candles: List[Any],
    ) -> List[Any]:

        pivots: List[Any] = []

        if len(candles) < 3:
            return pivots

        tolerance_pct = self.max_outer_difference_pct

        for index in range(
            1,
            len(candles) - 1,
        ):

            previous = candles[index - 1]
            current = candles[index]
            following = candles[index + 1]

            current_high = float(current.high)

            previous_high = float(previous.high)

            following_high = float(following.high)

            # Standard local high.
            if (
                current_high >= previous_high
                and current_high >= following_high
                and (current_high > previous_high or current_high > following_high)
            ):
                pivots.append(current)
                continue

            # Near-pivot / plateau.
            following_difference = self._difference_pct(
                current_high,
                following_high,
            )

            if current_high >= previous_high and following_difference <= tolerance_pct:
                pivots.append(current)

        return pivots

    # ========================================================
    # LOW PIVOT DETECTION
    # ========================================================

    def _find_low_pivots(
        self,
        candles: List[Any],
    ) -> List[Any]:

        pivots: List[Any] = []

        if len(candles) < 3:
            return pivots

        tolerance_pct = self.max_outer_difference_pct

        for index in range(
            1,
            len(candles) - 1,
        ):

            previous = candles[index - 1]
            current = candles[index]
            following = candles[index + 1]

            current_low = float(current.low)

            previous_low = float(previous.low)

            following_low = float(following.low)

            # Standard local low.
            if (
                current_low <= previous_low
                and current_low <= following_low
                and (current_low < previous_low or current_low < following_low)
            ):
                pivots.append(current)
                continue

            # Near-pivot / plateau.
            following_difference = self._difference_pct(
                current_low,
                following_low,
            )

            if current_low <= previous_low and following_difference <= tolerance_pct:
                pivots.append(current)

        return pivots

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
            "last_signal": signal,
        }
