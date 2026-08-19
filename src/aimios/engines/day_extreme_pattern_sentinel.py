from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================
# USER PATTERN RULES
# ============================================================

MIN_REVERSAL_PCT = 0.13
MIN_SECOND_SWING_DIFFERENCE_PCT = 0.03
MIN_CANDLES_BETWEEN_EXTREMES = 7


@dataclass
class _MSetup:
    high1: float
    high1_timestamp: datetime
    high1_candle_id: int

    valley: Optional[float] = None
    valley_timestamp: Optional[datetime] = None
    valley_candle_id: Optional[int] = None

    high2: Optional[float] = None
    high2_timestamp: Optional[datetime] = None
    high2_candle_id: Optional[int] = None

    active: bool = True


@dataclass
class _WSetup:
    low1: float
    low1_timestamp: datetime
    low1_candle_id: int

    peak: Optional[float] = None
    peak_timestamp: Optional[datetime] = None
    peak_candle_id: Optional[int] = None

    low2: Optional[float] = None
    low2_timestamp: Optional[datetime] = None
    low2_candle_id: Optional[int] = None

    active: bool = True


class DayExtremePatternSentinel:
    """
    Separate DAY HIGH / DAY LOW pattern detector.

    IMPORTANT:
        This class does NOT replace PatternSentinel.

    It is completely independent from the existing candle-based
    M/W detector.

    Rules:

        DAY HIGH alert
        DAY LOW alert

        M:
            HIGH1 = day high
            HIGH1 -> VALLEY >= 0.13% down
            HIGH1 -> HIGH2 >= 7 candles
            HIGH1 > HIGH2 by >= 0.03%
            => SELL

        W:
            LOW1 = day low
            LOW1 -> PEAK >= 0.13% up
            LOW1 -> LOW2 >= 7 candles
            LOW2 > LOW1 by >= 0.03%
            => BUY

    Only completed candles are processed.
    """

    def __init__(
        self,
        min_reversal_pct: float = MIN_REVERSAL_PCT,
        min_second_swing_difference_pct: float = (MIN_SECOND_SWING_DIFFERENCE_PCT),
        min_candles_between_extremes: int = (MIN_CANDLES_BETWEEN_EXTREMES),
    ) -> None:

        self.min_reversal_pct = float(min_reversal_pct)

        self.min_second_swing_difference_pct = float(min_second_swing_difference_pct)

        self.min_candles_between_extremes = int(min_candles_between_extremes)

        self._last_day_high: Dict[str, float] = {}
        self._last_day_low: Dict[str, float] = {}

        self._m_setup: Dict[str, _MSetup] = {}
        self._w_setup: Dict[str, _WSetup] = {}

        # Prevent repeated alert for exactly the same
        # day extreme.
        self._last_high_alert_value: Dict[str, float] = {}
        self._last_low_alert_value: Dict[str, float] = {}

        # Prevent repeated M/W alert for same HIGH2/LOW2.
        self._last_m_alert_candle: Dict[str, int] = {}
        self._last_w_alert_candle: Dict[str, int] = {}

        logger.info(
            "DayExtremePatternSentinel initialized | "
            "reversal=%.2f%% | difference=%.2f%% | "
            "minimum_candles=%d",
            self.min_reversal_pct,
            self.min_second_swing_difference_pct,
            self.min_candles_between_extremes,
        )

    # ========================================================
    # RESET
    # ========================================================

    def clear(self) -> None:

        self._last_day_high.clear()
        self._last_day_low.clear()

        self._m_setup.clear()
        self._w_setup.clear()

        self._last_high_alert_value.clear()
        self._last_low_alert_value.clear()

        self._last_m_alert_candle.clear()
        self._last_w_alert_candle.clear()

    def clear_symbol(
        self,
        symbol: str,
    ) -> None:

        self._last_day_high.pop(symbol, None)
        self._last_day_low.pop(symbol, None)

        self._m_setup.pop(symbol, None)
        self._w_setup.pop(symbol, None)

        self._last_high_alert_value.pop(symbol, None)
        self._last_low_alert_value.pop(symbol, None)

        self._last_m_alert_candle.pop(symbol, None)
        self._last_w_alert_candle.pop(symbol, None)

    # ========================================================
    # PROCESS
    # ========================================================

    def process_candle(
        self,
        *,
        symbol: str,
        candle,
        candles: List,
        day_high: Optional[float],
        day_low: Optional[float],
    ) -> List[Dict[str, object]]:
        """
        Process ONE COMPLETED candle.

        Returns zero or more alerts.

        Possible patterns:

            DAY_HIGH
            DAY_LOW
            M
            W
        """

        if candle is None:
            return []

        if not candles:
            return []

        if day_high is None or day_low is None:
            return []

        try:
            day_high = float(day_high)
            day_low = float(day_low)
        except TypeError, ValueError:
            return []

        if day_high <= 0 or day_low <= 0:
            return []

        alerts: List[Dict[str, object]] = []

        # ====================================================
        # DAY HIGH ALERT
        # ====================================================

        previous_high = self._last_day_high.get(symbol)

        if previous_high is None or day_high > previous_high:

            self._last_day_high[symbol] = day_high

            last_alert = self._last_high_alert_value.get(symbol)

            if last_alert is None or day_high > last_alert:

                self._last_high_alert_value[symbol] = day_high

                alerts.append(
                    self._build_alert(
                        symbol=symbol,
                        pattern="DAY_HIGH",
                        direction="INFO",
                        price=day_high,
                        candle=candle,
                        confidence=100.0,
                        high1=day_high,
                        low1=day_low,
                    )
                )

                # A new day high invalidates the old M
                # reference because HIGH1 must be the
                # current day extreme.
                self._m_setup.pop(
                    symbol,
                    None,
                )

        # ====================================================
        # DAY LOW ALERT
        # ====================================================

        previous_low = self._last_day_low.get(symbol)

        if previous_low is None or day_low < previous_low:

            self._last_day_low[symbol] = day_low

            last_alert = self._last_low_alert_value.get(symbol)

            if last_alert is None or day_low < last_alert:

                self._last_low_alert_value[symbol] = day_low

                alerts.append(
                    self._build_alert(
                        symbol=symbol,
                        pattern="DAY_LOW",
                        direction="INFO",
                        price=day_low,
                        candle=candle,
                        confidence=100.0,
                        high1=day_high,
                        low1=day_low,
                    )
                )

                # New day low invalidates old W reference.
                self._w_setup.pop(
                    symbol,
                    None,
                )

        # ====================================================
        # M DETECTION
        # ====================================================

        m_alert = self._process_m(
            symbol=symbol,
            candle=candle,
            candles=candles,
            day_high=day_high,
        )

        if m_alert is not None:
            alerts.append(m_alert)

        # ====================================================
        # W DETECTION
        # ====================================================

        w_alert = self._process_w(
            symbol=symbol,
            candle=candle,
            candles=candles,
            day_low=day_low,
        )

        if w_alert is not None:
            alerts.append(w_alert)

        return alerts

    # ========================================================
    # M
    # ========================================================

    def _process_m(
        self,
        *,
        symbol: str,
        candle,
        candles: List,
        day_high: float,
    ) -> Optional[Dict[str, object]]:

        candle_id = int(getattr(candle, "candle_id", 0))

        candle_high = float(candle.high)
        candle_low = float(candle.low)

        setup = self._m_setup.get(symbol)

        # ----------------------------------------------------
        # START M FROM DAY HIGH
        #
        # The day high must actually be represented by this
        # candle. If day_high is higher than this candle,
        # this candle cannot be HIGH1.
        # ----------------------------------------------------

        if setup is None and candle_high >= day_high:

            setup = _MSetup(
                high1=day_high,
                high1_timestamp=candle.timestamp,
                high1_candle_id=candle_id,
            )

            self._m_setup[symbol] = setup

            return None

        if setup is None:
            return None

        # ----------------------------------------------------
        # A NEW DAY HIGH CANCELS THE OLD HIGH1
        # ----------------------------------------------------

        if day_high > setup.high1:

            setup = _MSetup(
                high1=day_high,
                high1_timestamp=candle.timestamp,
                high1_candle_id=candle_id,
            )

            self._m_setup[symbol] = setup

            return None

        # ----------------------------------------------------
        # WAIT FOR VALLEY
        # ----------------------------------------------------

        if setup.valley is None:

            fall_pct = ((setup.high1 - candle_low) / setup.high1) * 100.0

            if fall_pct >= self.min_reversal_pct:

                setup.valley = candle_low
                setup.valley_timestamp = candle.timestamp
                setup.valley_candle_id = candle_id

                return None

            return None

        # ----------------------------------------------------
        # VALLEY CAN MOVE LOWER
        # ----------------------------------------------------

        if candle_low < setup.valley and candle_id > setup.valley_candle_id:

            setup.valley = candle_low
            setup.valley_timestamp = candle.timestamp
            setup.valley_candle_id = candle_id

            return None

        # ----------------------------------------------------
        # HIGH2
        # ----------------------------------------------------

        candles_between = candle_id - setup.high1_candle_id

        if candles_between < self.min_candles_between_extremes:
            return None

        # High2 must be below HIGH1 by at least 0.03%.
        difference_pct = ((setup.high1 - candle_high) / setup.high1) * 100.0

        if difference_pct >= self.min_second_swing_difference_pct:

            # Price must have recovered from valley.
            if candle_high <= setup.valley:
                return None

            # Don't trigger repeatedly.
            previous_alert = self._last_m_alert_candle.get(symbol)

            if previous_alert == candle_id:
                return None

            self._last_m_alert_candle[symbol] = candle_id

            high2 = candle_high

            alert = self._build_alert(
                symbol=symbol,
                pattern="M",
                direction="SELL",
                price=candle.close,
                candle=candle,
                confidence=self._m_confidence(
                    setup.high1,
                    setup.valley,
                    high2,
                ),
                high1=setup.high1,
                valley=setup.valley,
                high2=high2,
            )

            # Pattern completed. Keep it from firing again
            # until a new day high creates a new HIGH1.
            self._m_setup.pop(
                symbol,
                None,
            )

            return alert

        return None

    # ========================================================
    # W
    # ========================================================

    def _process_w(
        self,
        *,
        symbol: str,
        candle,
        candles: List,
        day_low: float,
    ) -> Optional[Dict[str, object]]:

        candle_id = int(getattr(candle, "candle_id", 0))

        candle_high = float(candle.high)
        candle_low = float(candle.low)

        setup = self._w_setup.get(symbol)

        # ----------------------------------------------------
        # START W FROM DAY LOW
        # ----------------------------------------------------

        if setup is None and candle_low <= day_low:

            setup = _WSetup(
                low1=day_low,
                low1_timestamp=candle.timestamp,
                low1_candle_id=candle_id,
            )

            self._w_setup[symbol] = setup

            return None

        if setup is None:
            return None

        # ----------------------------------------------------
        # NEW DAY LOW CANCELS OLD LOW1
        # ----------------------------------------------------

        if day_low < setup.low1:

            setup = _WSetup(
                low1=day_low,
                low1_timestamp=candle.timestamp,
                low1_candle_id=candle_id,
            )

            self._w_setup[symbol] = setup

            return None

        # ----------------------------------------------------
        # WAIT FOR PEAK
        # ----------------------------------------------------

        if setup.peak is None:

            rise_pct = ((candle_high - setup.low1) / setup.low1) * 100.0

            if rise_pct >= self.min_reversal_pct:

                setup.peak = candle_high
                setup.peak_timestamp = candle.timestamp
                setup.peak_candle_id = candle_id

                return None

            return None

        # ----------------------------------------------------
        # PEAK CAN MOVE HIGHER
        # ----------------------------------------------------

        if candle_high > setup.peak and candle_id > setup.peak_candle_id:

            setup.peak = candle_high
            setup.peak_timestamp = candle.timestamp
            setup.peak_candle_id = candle_id

            return None

        # ----------------------------------------------------
        # LOW2
        # ----------------------------------------------------

        candles_between = candle_id - setup.low1_candle_id

        if candles_between < self.min_candles_between_extremes:
            return None

        # LOW2 must be above LOW1 by at least 0.03%.
        difference_pct = ((candle_low - setup.low1) / setup.low1) * 100.0

        if difference_pct >= self.min_second_swing_difference_pct:

            # Must have recovered above the peak first.
            if candle_low >= setup.peak:
                return None

            previous_alert = self._last_w_alert_candle.get(symbol)

            if previous_alert == candle_id:
                return None

            self._last_w_alert_candle[symbol] = candle_id

            low2 = candle_low

            alert = self._build_alert(
                symbol=symbol,
                pattern="W",
                direction="BUY",
                price=candle.close,
                candle=candle,
                confidence=self._w_confidence(
                    setup.low1,
                    setup.peak,
                    low2,
                ),
                low1=setup.low1,
                peak=setup.peak,
                low2=low2,
            )

            self._w_setup.pop(
                symbol,
                None,
            )

            return alert

        return None

    # ========================================================
    # CONFIDENCE
    # ========================================================

    def _m_confidence(
        self,
        high1: float,
        valley: float,
        high2: float,
    ) -> float:

        reversal = ((high1 - valley) / high1) * 100.0

        difference = ((high1 - high2) / high1) * 100.0

        score = 70.0

        if reversal >= 0.20:
            score += 10.0

        if difference >= 0.05:
            score += 10.0

        return min(score, 100.0)

    def _w_confidence(
        self,
        low1: float,
        peak: float,
        low2: float,
    ) -> float:

        reversal = ((peak - low1) / low1) * 100.0

        difference = ((low2 - low1) / low1) * 100.0

        score = 70.0

        if reversal >= 0.20:
            score += 10.0

        if difference >= 0.05:
            score += 10.0

        return min(score, 100.0)

    # ========================================================
    # ALERT
    # ========================================================

    @staticmethod
    def _build_alert(
        *,
        symbol: str,
        pattern: str,
        direction: str,
        price: float,
        candle,
        confidence: float,
        **extra,
    ) -> Dict[str, object]:

        alert: Dict[str, object] = {
            "pattern": pattern,
            "direction": direction,
            "confidence": round(
                float(confidence),
                1,
            ),
            "price": price,
            "timestamp": candle.timestamp,
        }

        alert.update(extra)

        return alert
