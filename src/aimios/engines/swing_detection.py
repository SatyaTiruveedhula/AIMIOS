from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional

from .engine import BaseEngine
from aimios.market.candle_buffer import Candle

logger = logging.getLogger(__name__)


# ==========================================================
# CONFIGURATION
# ==========================================================

LEFT_BARS = 3
RIGHT_BARS = 3

MIN_SWING_DISTANCE = 0.25  # percent
MIN_SWING_STRENGTH = 0.20


# ==========================================================
# SWING OBJECT
# ==========================================================


@dataclass(frozen=True)
class SwingPoint:
    timestamp: Any
    candle_id: Optional[int]
    price: float
    kind: str
    strength: float
    confirmed: bool = True


# ==========================================================
# ENGINE
# ==========================================================


class SwingDetectionEngine(BaseEngine):

    name = "SwingDetection"

    def __init__(self, app=None, **kwargs):
        super().__init__(app)

        self.last_high: Optional[SwingPoint] = None
        self.last_low: Optional[SwingPoint] = None
        self.swings: List[SwingPoint] = []

    # ==========================================================
    # START / STOP
    # ==========================================================

    def start(self):
        super().start()
        logger.info("Swing Detection Engine Started")

    def stop(self):
        super().stop()
        logger.info("Swing Detection Engine Stopped")

    # ==========================================================
    # PUBLIC ENTRY
    # ==========================================================

    def process_candles(
        self,
        candles: List[Candle],
    ) -> List[SwingPoint]:

        self.swings = []
        self.last_high = None
        self.last_low = None

        if not candles:
            return []

        swings: List[SwingPoint] = []

        minimum_candles = LEFT_BARS + RIGHT_BARS + 1

        # ------------------------------------------------------
        # NORMAL CONFIRMED PIVOT DETECTION
        # ------------------------------------------------------

        if len(candles) >= minimum_candles:

            start = LEFT_BARS
            end = len(candles) - RIGHT_BARS

            for index in range(start, end):

                high = self._check_pivot_high(
                    candles,
                    index,
                )

                if high is not None:
                    swings.append(high)

                low = self._check_pivot_low(
                    candles,
                    index,
                )

                if low is not None:
                    swings.append(low)

        # ------------------------------------------------------
        # CHRONOLOGICAL ORDER
        # ------------------------------------------------------

        swings.sort(
            key=lambda swing: (swing.candle_id if swing.candle_id is not None else 0)
        )

        # ------------------------------------------------------
        # CLEAN DUPLICATES
        # ------------------------------------------------------

        swings = self._remove_duplicate_swings(swings)

        # ------------------------------------------------------
        # CALCULATE STRENGTH
        # ------------------------------------------------------

        swings = self._calculate_strength(swings)

        # ------------------------------------------------------
        # REMOVE NOISE
        # ------------------------------------------------------

        swings = self._filter_noise(swings)

        # ------------------------------------------------------
        # FALLBACK FOR SHORT / MONOTONIC HISTORY
        # ------------------------------------------------------
        #
        # Example:
        #
        # 100, 101, 102, 103, 104, 105
        #
        # A traditional 3-left / 3-right pivot cannot exist.
        # For this situation we still expose the structural
        # beginning and ending points.
        #
        # This fallback is ONLY used when normal pivot detection
        # produces no swings.
        # ------------------------------------------------------

        if not swings:
            swings = self._build_trend_fallback(candles)

        # ------------------------------------------------------
        # SAVE STATE
        # ------------------------------------------------------

        self.swings = swings

        highs = [swing for swing in swings if swing.kind == "high"]

        lows = [swing for swing in swings if swing.kind == "low"]

        if highs:
            self.last_high = highs[-1]

        if lows:
            self.last_low = lows[-1]

        return swings

    # ==========================================================
    # PIVOT HIGH
    # ==========================================================

    def _check_pivot_high(
        self,
        candles: List[Candle],
        index: int,
    ) -> Optional[SwingPoint]:

        current = candles[index]
        current_high = float(current.high)

        # ------------------------------------------------------
        # LEFT SIDE
        # ------------------------------------------------------

        for i in range(
            index - LEFT_BARS,
            index,
        ):
            if float(candles[i].high) >= current_high:
                return None

        # ------------------------------------------------------
        # RIGHT SIDE
        # ------------------------------------------------------

        for i in range(
            index + 1,
            index + RIGHT_BARS + 1,
        ):
            if float(candles[i].high) > current_high:
                return None

        return SwingPoint(
            timestamp=current.timestamp,
            candle_id=current.candle_id,
            price=current_high,
            kind="high",
            strength=0.0,
            confirmed=True,
        )

    # ==========================================================
    # PIVOT LOW
    # ==========================================================

    def _check_pivot_low(
        self,
        candles: List[Candle],
        index: int,
    ) -> Optional[SwingPoint]:

        current = candles[index]
        current_low = float(current.low)

        # ------------------------------------------------------
        # LEFT SIDE
        # ------------------------------------------------------

        for i in range(
            index - LEFT_BARS,
            index,
        ):
            if float(candles[i].low) <= current_low:
                return None

        # ------------------------------------------------------
        # RIGHT SIDE
        # ------------------------------------------------------

        for i in range(
            index + 1,
            index + RIGHT_BARS + 1,
        ):
            if float(candles[i].low) < current_low:
                return None

        return SwingPoint(
            timestamp=current.timestamp,
            candle_id=current.candle_id,
            price=current_low,
            kind="low",
            strength=0.0,
            confirmed=True,
        )

    # ==========================================================
    # TREND FALLBACK
    # ==========================================================

    def _build_trend_fallback(
        self,
        candles: List[Candle],
    ) -> List[SwingPoint]:

        if len(candles) < 2:
            return []

        first = candles[0]
        last = candles[-1]

        first_close = float(first.close)
        last_close = float(last.close)

        # ------------------------------------------------------
        # FLAT HISTORY
        # ------------------------------------------------------

        if first_close == last_close:

            return [
                SwingPoint(
                    timestamp=first.timestamp,
                    candle_id=first.candle_id,
                    price=float(first.low),
                    kind="low",
                    strength=0.0,
                    confirmed=True,
                ),
                SwingPoint(
                    timestamp=last.timestamp,
                    candle_id=last.candle_id,
                    price=float(last.high),
                    kind="high",
                    strength=0.0,
                    confirmed=True,
                ),
            ]

        # ------------------------------------------------------
        # TOTAL CHANGE
        # ------------------------------------------------------

        if first_close != 0:

            total_change_pct = abs(
                (last_close - first_close) / abs(first_close) * 100.0
            )

        else:
            total_change_pct = 0.0

        # ------------------------------------------------------
        # RISING TREND
        # ------------------------------------------------------

        if last_close > first_close:

            return [
                SwingPoint(
                    timestamp=first.timestamp,
                    candle_id=first.candle_id,
                    price=float(first.low),
                    kind="low",
                    strength=0.0,
                    confirmed=True,
                ),
                SwingPoint(
                    timestamp=last.timestamp,
                    candle_id=last.candle_id,
                    price=float(last.high),
                    kind="high",
                    strength=round(
                        total_change_pct,
                        2,
                    ),
                    confirmed=True,
                ),
            ]

        # ------------------------------------------------------
        # FALLING TREND
        # ------------------------------------------------------

        return [
            SwingPoint(
                timestamp=first.timestamp,
                candle_id=first.candle_id,
                price=float(first.high),
                kind="high",
                strength=0.0,
                confirmed=True,
            ),
            SwingPoint(
                timestamp=last.timestamp,
                candle_id=last.candle_id,
                price=float(last.low),
                kind="low",
                strength=round(
                    total_change_pct,
                    2,
                ),
                confirmed=True,
            ),
        ]

    # ==========================================================
    # REMOVE DUPLICATE SWINGS
    # ==========================================================

    def _remove_duplicate_swings(
        self,
        swings: List[SwingPoint],
    ) -> List[SwingPoint]:

        if not swings:
            return []

        cleaned: List[SwingPoint] = []

        previous: Optional[SwingPoint] = None

        for swing in swings:

            if previous is None:
                cleaned.append(swing)
                previous = swing
                continue

            # --------------------------------------------------
            # SAME TYPE
            # --------------------------------------------------

            if swing.kind == previous.kind:

                if swing.kind == "high":

                    # Keep the higher high.
                    if swing.price > previous.price:
                        cleaned[-1] = swing
                        previous = swing

                elif swing.kind == "low":

                    # Keep the lower low.
                    if swing.price < previous.price:
                        cleaned[-1] = swing
                        previous = swing

                continue

            # --------------------------------------------------
            # ALTERNATING TYPE
            # --------------------------------------------------

            cleaned.append(swing)
            previous = swing

        return cleaned

    # ==========================================================
    # CALCULATE SWING STRENGTH
    # ==========================================================

    def _calculate_strength(
        self,
        swings: List[SwingPoint],
    ) -> List[SwingPoint]:

        if len(swings) < 2:
            return swings

        result: List[SwingPoint] = []

        first = swings[0]

        result.append(
            SwingPoint(
                timestamp=first.timestamp,
                candle_id=first.candle_id,
                price=first.price,
                kind=first.kind,
                strength=0.0,
                confirmed=first.confirmed,
            )
        )

        previous = first

        for current in swings[1:]:

            price_difference = abs(current.price - previous.price)

            if previous.price != 0:

                strength = price_difference / abs(previous.price) * 100.0

            else:
                strength = 0.0

            result.append(
                SwingPoint(
                    timestamp=current.timestamp,
                    candle_id=current.candle_id,
                    price=current.price,
                    kind=current.kind,
                    strength=round(
                        strength,
                        2,
                    ),
                    confirmed=current.confirmed,
                )
            )

            previous = current

        return result

    # ==========================================================
    # FILTER SMALL SWINGS
    # ==========================================================

    def _filter_noise(
        self,
        swings: List[SwingPoint],
    ) -> List[SwingPoint]:

        if len(swings) < 2:
            return swings

        filtered: List[SwingPoint] = [swings[0]]

        for swing in swings[1:]:

            if swing.strength >= MIN_SWING_STRENGTH:
                filtered.append(swing)

        return filtered

    # ==========================================================
    # PUBLIC HELPERS
    # ==========================================================

    def latest_high(
        self,
    ) -> Optional[SwingPoint]:

        return self.last_high

    def latest_low(
        self,
    ) -> Optional[SwingPoint]:

        return self.last_low

    def latest_swings(
        self,
        count: int = 10,
    ) -> List[SwingPoint]:

        if count <= 0:
            return []

        return self.swings[-count:]

    # ==========================================================
    # CLEAR
    # ==========================================================

    def clear(self):

        self.swings.clear()

        self.last_high = None

        self.last_low = None
