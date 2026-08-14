from __future__ import annotations

import logging

from dataclasses import dataclass
from typing import List
from typing import Optional

from .engine import BaseEngine
from .swing_detection import SwingPoint
from .trend_detection import TrendState

logger = logging.getLogger(__name__)

# ==========================================================
# BREAKOUT STATE
# ==========================================================


@dataclass(frozen=True)
class BreakoutSignal:

    breakout_type: str

    direction: str

    confidence: float

    breakout_price: float

    candle_id: int

    reason: str


# ==========================================================
# ENGINE
# ==========================================================


class BreakoutEngine(BaseEngine):

    name = "BreakoutEngine"

    def __init__(
        self,
        app=None,
        **kwargs,
    ):

        super().__init__(app)

        self.last_breakout = None

    # ==========================================================

    def start(self):

        super().start()

        logger.info("Breakout Engine Started")

    # ==========================================================

    def stop(self):

        super().stop()

        logger.info("Breakout Engine Stopped")
        # ==========================================================

    # MAIN BREAKOUT DETECTOR
    # ==========================================================

    def detect_breakout(
        self,
        swings: List[SwingPoint],
        trend: TrendState,
    ) -> Optional[BreakoutSignal]:

        if len(swings) < 4:
            return None

        highs = [s for s in swings if s.kind == "HIGH"]
        lows = [s for s in swings if s.kind == "LOW"]

        if len(highs) < 2 or len(lows) < 2:
            return None

        last_high = highs[-1]
        prev_high = highs[-2]

        last_low = lows[-1]
        prev_low = lows[-2]

        # ======================================================
        # BREAK OF STRUCTURE (BOS)
        # ======================================================

        if trend.trend == "UP":

            if last_high.price > prev_high.price:

                signal = BreakoutSignal(
                    breakout_type="BOS",
                    direction="BUY",
                    confidence=90.0,
                    breakout_price=last_high.price,
                    candle_id=last_high.candle_id,
                    reason="Higher High confirmed",
                )

                self.last_breakout = signal

                return signal

        if trend.trend == "DOWN":

            if last_low.price < prev_low.price:

                signal = BreakoutSignal(
                    breakout_type="BOS",
                    direction="SELL",
                    confidence=90.0,
                    breakout_price=last_low.price,
                    candle_id=last_low.candle_id,
                    reason="Lower Low confirmed",
                )

                self.last_breakout = signal

                return signal

        # ======================================================
        # CHANGE OF CHARACTER (CHOCH)
        # ======================================================

        if trend.trend == "UP":

            if last_low.price < prev_low.price:

                signal = BreakoutSignal(
                    breakout_type="CHOCH",
                    direction="SELL",
                    confidence=85.0,
                    breakout_price=last_low.price,
                    candle_id=last_low.candle_id,
                    reason="Uptrend broken",
                )

                self.last_breakout = signal

                return signal

        if trend.trend == "DOWN":

            if last_high.price > prev_high.price:

                signal = BreakoutSignal(
                    breakout_type="CHOCH",
                    direction="BUY",
                    confidence=85.0,
                    breakout_price=last_high.price,
                    candle_id=last_high.candle_id,
                    reason="Downtrend broken",
                )

                self.last_breakout = signal

                return signal

        return None
        # ==========================================================

    # LIQUIDITY SWEEP
    # ==========================================================

    def detect_liquidity_sweep(
        self,
        swings: List[SwingPoint],
    ) -> Optional[BreakoutSignal]:

        if len(swings) < 3:
            return None

        last = swings[-1]
        prev = swings[-2]

        if last.kind == "HIGH":

            if last.price > prev.price:

                return BreakoutSignal(
                    breakout_type="LIQUIDITY_SWEEP",
                    direction="SELL",
                    confidence=70.0,
                    breakout_price=last.price,
                    candle_id=last.candle_id,
                    reason="Buy-side liquidity taken",
                )

        if last.kind == "LOW":

            if last.price < prev.price:

                return BreakoutSignal(
                    breakout_type="LIQUIDITY_SWEEP",
                    direction="BUY",
                    confidence=70.0,
                    breakout_price=last.price,
                    candle_id=last.candle_id,
                    reason="Sell-side liquidity taken",
                )

        return None

    # ==========================================================
    # FAKE BREAKOUT
    # ==========================================================

    def detect_fake_breakout(
        self,
        swings: List[SwingPoint],
    ) -> Optional[BreakoutSignal]:

        if len(swings) < 3:
            return None

        last = swings[-1]
        prev = swings[-2]

        # Failed upside breakout
        if last.kind == "HIGH" and last.price > prev.price and last.strength < 0.30:

            return BreakoutSignal(
                breakout_type="FAKE_BREAKOUT",
                direction="SELL",
                confidence=65.0,
                breakout_price=last.price,
                candle_id=last.candle_id,
                reason="Weak breakout rejected",
            )

        # Failed downside breakout
        if last.kind == "LOW" and last.price < prev.price and last.strength < 0.30:

            return BreakoutSignal(
                breakout_type="FAKE_BREAKOUT",
                direction="BUY",
                confidence=65.0,
                breakout_price=last.price,
                candle_id=last.candle_id,
                reason="Weak breakdown rejected",
            )

        return None

    # ==========================================================
    # HELPERS
    # ==========================================================

    def latest_breakout(self) -> Optional[BreakoutSignal]:

        return self.last_breakout

    def clear(self):

        self.last_breakout = None
