from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List

from .engine import BaseEngine
from .swing_detection import SwingPoint

logger = logging.getLogger(__name__)


# ==========================================================
# CONFIGURATION
# ==========================================================

MIN_SWINGS = 4

# Minimum percentage movement between comparable swings
MIN_TREND_MOVE_PCT = 0.08

# Confidence levels
STRONG_TREND_CONFIDENCE = 90.0
MODERATE_TREND_CONFIDENCE = 75.0
SIDEWAYS_CONFIDENCE = 50.0


# ==========================================================
# TREND STATE
# ==========================================================


@dataclass(frozen=True)
class TrendState:
    trend: str

    higher_high: bool
    higher_low: bool

    lower_high: bool
    lower_low: bool

    confidence: float


# ==========================================================
# ENGINE
# ==========================================================


class TrendDetectionEngine(BaseEngine):

    name = "TrendDetection"

    def __init__(self, app=None, **kwargs):

        super().__init__(app)

        self.current_state = TrendState(
            trend="SIDEWAYS",
            higher_high=False,
            higher_low=False,
            lower_high=False,
            lower_low=False,
            confidence=0.0,
        )

    # ======================================================
    # START
    # ======================================================

    def start(self):

        super().start()

        logger.info("Trend Detection Started")

    # ======================================================
    # STOP
    # ======================================================

    def stop(self):

        super().stop()

        logger.info("Trend Detection Stopped")

    # ======================================================
    # MAIN TREND DETECTION
    # ======================================================

    def detect_trend(
        self,
        swings: List[SwingPoint],
    ) -> TrendState:

        if len(swings) < MIN_SWINGS:
            return self.current_state

        # --------------------------------------------------
        # Normalize swing types
        # --------------------------------------------------

        highs = [s for s in swings if str(s.kind).upper() == "HIGH"]

        lows = [s for s in swings if str(s.kind).upper() == "LOW"]

        if len(highs) < 2 or len(lows) < 2:
            return self.current_state

        # --------------------------------------------------
        # Last two highs
        # --------------------------------------------------

        prev_high = highs[-2]
        last_high = highs[-1]

        # --------------------------------------------------
        # Last two lows
        # --------------------------------------------------

        prev_low = lows[-2]
        last_low = lows[-1]

        # --------------------------------------------------
        # Compare highs/lows
        # --------------------------------------------------

        higher_high = self._meaningful_higher(
            last_high.price,
            prev_high.price,
        )

        higher_low = self._meaningful_higher(
            last_low.price,
            prev_low.price,
        )

        lower_high = self._meaningful_lower(
            last_high.price,
            prev_high.price,
        )

        lower_low = self._meaningful_lower(
            last_low.price,
            prev_low.price,
        )

        # --------------------------------------------------
        # Determine trend
        #
        # IMPORTANT:
        #
        # UP requires BOTH:
        #   Higher High
        #   Higher Low
        #
        # DOWN requires BOTH:
        #   Lower High
        #   Lower Low
        #
        # Otherwise SIDEWAYS.
        #
        # This prevents a single HH or HL from creating
        # an artificial trend.
        # --------------------------------------------------

        trend = "SIDEWAYS"
        confidence = SIDEWAYS_CONFIDENCE

        # Strong uptrend
        if higher_high and higher_low:

            trend = "UP"
            confidence = STRONG_TREND_CONFIDENCE

        # Strong downtrend
        elif lower_high and lower_low:

            trend = "DOWN"
            confidence = STRONG_TREND_CONFIDENCE

        # --------------------------------------------------
        # Mixed structure
        #
        # Example:
        #
        # HH + LL
        # LH + HL
        #
        # These are structural transitions / ranges,
        # not confirmed trends.
        # --------------------------------------------------

        else:

            trend = "SIDEWAYS"
            confidence = SIDEWAYS_CONFIDENCE

        # --------------------------------------------------
        # Save state
        # --------------------------------------------------

        self.current_state = TrendState(
            trend=trend,
            higher_high=higher_high,
            higher_low=higher_low,
            lower_high=lower_high,
            lower_low=lower_low,
            confidence=confidence,
        )

        logger.debug(
            "Trend=%s HH=%s HL=%s LH=%s LL=%s Confidence=%.1f",
            trend,
            higher_high,
            higher_low,
            lower_high,
            lower_low,
            confidence,
        )

        return self.current_state

    # ======================================================
    # PRICE COMPARISON HELPERS
    # ======================================================

    def _meaningful_higher(
        self,
        current: float,
        previous: float,
    ) -> bool:

        if previous == 0:
            return current > previous

        move_pct = abs(current - previous) / abs(previous) * 100.0

        return current > previous and move_pct >= MIN_TREND_MOVE_PCT

    # ======================================================

    def _meaningful_lower(
        self,
        current: float,
        previous: float,
    ) -> bool:

        if previous == 0:
            return current < previous

        move_pct = abs(current - previous) / abs(previous) * 100.0

        return current < previous and move_pct >= MIN_TREND_MOVE_PCT

    # ======================================================
    # HELPERS
    # ======================================================

    def is_uptrend(self) -> bool:

        return self.current_state.trend == "UP"

    # ======================================================

    def is_downtrend(self) -> bool:

        return self.current_state.trend == "DOWN"

    # ======================================================

    def is_sideways(self) -> bool:

        return self.current_state.trend == "SIDEWAYS"

    # ======================================================

    def confidence(self) -> float:

        return self.current_state.confidence

    # ======================================================

    def get_state(self) -> TrendState:

        return self.current_state

    # ======================================================
    # RESET
    # ======================================================

    def clear(self):

        self.current_state = TrendState(
            trend="SIDEWAYS",
            higher_high=False,
            higher_low=False,
            lower_high=False,
            lower_low=False,
            confidence=0.0,
        )

        logger.info("Trend Detection reset.")
