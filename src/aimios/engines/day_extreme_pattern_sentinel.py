from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

# ============================================================
# STRICT DAY-EXTREME M/W LOGIC
# ============================================================

# HIGH1 -> VALLEY
# LOW1  -> PEAK
MIN_REVERSAL_PCT = 0.13

# HIGH1 -> HIGH2
# LOW1  -> LOW2
MIN_SECOND_SWING_DIFFERENCE_PCT = 0.04

# Minimum candles from first outer point to second outer point.
MIN_CANDLES_BETWEEN_EXTREMES = 5


# ============================================================
# FEEL-GOOD DAILY SIGNAL
# ============================================================

FEEL_GOOD_MIN_REVERSAL_PCT = 0.09
FEEL_GOOD_MIN_SWING_DIFFERENCE_PCT = 0.03
FEEL_GOOD_MIN_CANDLES = 4
FEEL_GOOD_MIN_SCORE = 70.0


# ============================================================
# IST
# ============================================================

try:
    from zoneinfo import ZoneInfo

    INDIA_TZ = ZoneInfo("Asia/Kolkata")

except Exception:  # pragma: no cover
    INDIA_TZ = timezone.utc


# ============================================================
# INTERNAL M SETUP
# ============================================================


@dataclass
class _MSetup:
    high1: float
    high1_timestamp: datetime
    high1_candle_id: int

    valley: Optional[float] = None
    valley_timestamp: Optional[datetime] = None
    valley_candle_id: Optional[int] = None

    # --------------------------------------------------------
    # IMPORTANT:
    # Used so the "WAITING FOR HIGH2" message is printed
    # ONLY ONCE for this setup.
    # --------------------------------------------------------
    waiting_printed: bool = False

    active: bool = True


# ============================================================
# INTERNAL W SETUP
# ============================================================


@dataclass
class _WSetup:
    low1: float
    low1_timestamp: datetime
    low1_candle_id: int

    peak: Optional[float] = None
    peak_timestamp: Optional[datetime] = None
    peak_candle_id: Optional[int] = None

    # --------------------------------------------------------
    # IMPORTANT:
    # Used so the "WAITING FOR LOW2" message is printed
    # ONLY ONCE for this setup.
    # --------------------------------------------------------
    waiting_printed: bool = False

    active: bool = True


# ============================================================
# DAY EXTREME PATTERN SENTINEL
# ============================================================


class DayExtremePatternSentinel:
    """
    AIMIOS TWO-PATH M/W DETECTOR.

    ============================================================
    PATH 1 - STRICT DAY EXTREME
    ============================================================

    M / SELL:

        HIGH1
           |
           | >= 0.09%
           v
        VALLEY
           |
           | recovery
           v
        HIGH2

    Conditions:

        1. HIGH1 is the relevant current-day HIGH.
        2. VALLEY occurs after HIGH1.
        3. HIGH1 -> VALLEY >= 0.09%.
        4. HIGH2 occurs after VALLEY.
        5. HIGH1 -> HIGH2 >= 4 candles.
        6. HIGH2 is at least 0.05% below HIGH1.
        7. HIGH2 is above VALLEY.
        8. HIGH2 does not make a new HIGH1.

    W / BUY:

        LOW1
          |
          | >= 0.09%
          v
         PEAK
           |
           | recovery
           v
         LOW2

    Conditions:

        1. LOW1 is the relevant current-day LOW.
        2. PEAK occurs after LOW1.
        3. LOW1 -> PEAK >= 0.09%.
        4. LOW2 occurs after PEAK.
        5. LOW1 -> LOW2 >= 4 candles.
        6. LOW2 is at least 0.05% above LOW1.
        7. LOW2 is below PEAK.
        8. LOW2 does not make a new LOW1.

    ============================================================
    WAITING PRINT
    ============================================================

    Once:

        HIGH1 -> VALLEY >= 0.09%

    is achieved, AIMIOS prints ONE message:

        M SETUP CONFIRMED | WAITING FOR HIGH2

    It does not print this repeatedly.

    Likewise, once:

        LOW1 -> PEAK >= 0.09%

    is achieved, AIMIOS prints ONE message:

        W SETUP CONFIRMED | WAITING FOR LOW2

    It then waits for the final outer-point condition.

    ============================================================
    PATH 2 - FEEL GOOD
    ============================================================

    Separate softer confirmation path.

    FEEL_GOOD_M -> SELL
    FEEL_GOOD_W -> BUY

    Maximum one BUY and one SELL per symbol per day.
    """

    def __init__(
        self,
        min_reversal_pct: float = 0.13,
        min_second_swing_difference_pct: float = 0.04,
        min_candles_between_extremes: int = 5,
        max_buy_per_day: int = 2,
        max_sell_per_day: int = 2,
        min_signal_confidence: float = 80.0,
    ) -> None:

        self.min_reversal_pct = float(min_reversal_pct)

        self.min_second_swing_difference_pct = float(min_second_swing_difference_pct)

        self.min_candles_between_extremes = int(min_candles_between_extremes)

        self.max_buy_per_day = int(max_buy_per_day)

        self.max_sell_per_day = int(max_sell_per_day)

        self.min_signal_confidence = float(min_signal_confidence)

        if self.min_reversal_pct < 0:
            raise ValueError("min_reversal_pct cannot be negative")

        if self.min_second_swing_difference_pct < 0:
            raise ValueError("min_second_swing_difference_pct cannot be negative")

        if self.min_candles_between_extremes < 1:
            raise ValueError("min_candles_between_extremes must be at least 1")

        if self.min_signal_confidence < 0:
            raise ValueError("min_signal_confidence cannot be negative")

        # ----------------------------------------------------
        # DAY EXTREMES
        # ----------------------------------------------------

        self._last_day_high: Dict[str, float] = {}
        self._last_day_low: Dict[str, float] = {}

        # ----------------------------------------------------
        # STRICT M/W SETUPS
        # ----------------------------------------------------

        self._m_setup: Dict[str, _MSetup] = {}
        self._w_setup: Dict[str, _WSetup] = {}

        # ----------------------------------------------------
        # DAY IDENTIFIER
        # ----------------------------------------------------

        self._day_key: Dict[str, str] = {}

        # ----------------------------------------------------
        # DUPLICATE PROTECTION
        # ----------------------------------------------------

        self._last_m_alert_candle: Dict[str, int] = {}
        self._last_w_alert_candle: Dict[str, int] = {}

        # ----------------------------------------------------
        # FEEL-GOOD DAILY ALERT PROTECTION
        # ----------------------------------------------------

        self._feel_good_buy_sent: Dict[str, bool] = {}
        self._feel_good_sell_sent: Dict[str, bool] = {}

        # ----------------------------------------------------
        # DAILY SIGNAL CAP
        # ----------------------------------------------------

        self._signal_counts: Dict[str, Dict[str, int]] = {}

        # ----------------------------------------------------
        # EOD SIGNAL HISTORY AND SELF-LEARNING
        # ----------------------------------------------------

        self._signal_history: Dict[str, List[Dict[str, object]]] = {}
        self._learning_state: Dict[str, Dict[str, float]] = {}

    # ========================================================
    # CLEAR
    # ========================================================

    def clear(self) -> None:

        self._last_day_high.clear()
        self._last_day_low.clear()

        self._m_setup.clear()
        self._w_setup.clear()

        self._day_key.clear()

        self._last_m_alert_candle.clear()
        self._last_w_alert_candle.clear()

        self._feel_good_buy_sent.clear()
        self._feel_good_sell_sent.clear()
        self._signal_counts.clear()
        self._signal_history.clear()
        self._learning_state.clear()

    # ========================================================
    # CLEAR SYMBOL
    # ========================================================

    def clear_symbol(
        self,
        symbol: str,
    ) -> None:

        symbol = str(symbol)

        self._last_day_high.pop(symbol, None)
        self._last_day_low.pop(symbol, None)

        self._m_setup.pop(symbol, None)
        self._w_setup.pop(symbol, None)

        self._day_key.pop(symbol, None)

        self._last_m_alert_candle.pop(symbol, None)
        self._last_w_alert_candle.pop(symbol, None)

        self._feel_good_buy_sent.pop(symbol, None)
        self._feel_good_sell_sent.pop(symbol, None)
        self._signal_counts.pop(symbol, None)
        self._signal_history.pop(symbol, None)
        self._learning_state.pop(symbol, None)

    # ========================================================
    # PROCESS CANDLE
    # ========================================================

    def process_candle(
        self,
        *,
        symbol: str,
        candle,
        candles: List,
        day_high: Optional[float] = None,
        day_low: Optional[float] = None,
    ) -> List[Dict[str, object]]:
        """
        Process ONE COMPLETED candle.

        Returns only actual alerts.

        Alert types:

            DAY_EXTREME
                M / SELL
                W / BUY

            FEEL_GOOD
                FEEL_GOOD_M / SELL
                FEEL_GOOD_W / BUY
        """

        if candle is None or not candles:
            return []

        symbol = str(symbol)

        try:
            candle_high = float(candle.high)
            candle_low = float(candle.low)
            candle_close = float(candle.close)

        except (
            AttributeError,
            TypeError,
            ValueError,
        ):
            return []

        if candle_high <= 0 or candle_low <= 0 or candle_close <= 0:
            return []

        try:
            candle_id = int(
                getattr(
                    candle,
                    "candle_id",
                    0,
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            candle_id = 0

        timestamp = getattr(
            candle,
            "timestamp",
            None,
        )

        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        # ====================================================
        # CALCULATE DAY EXTREMES
        # ====================================================

        calculated_day_high = self._calculate_day_high(candles)

        calculated_day_low = self._calculate_day_low(candles)

        if day_high is None:
            day_high = calculated_day_high

        if day_low is None:
            day_low = calculated_day_low

        try:
            day_high = float(day_high)
            day_low = float(day_low)

        except (
            TypeError,
            ValueError,
        ):
            return []

        if day_high <= 0 or day_low <= 0:
            return []

        # ====================================================
        # NEW INDIAN TRADING DAY
        # ====================================================

        current_day_key = self._india_day_key(timestamp)

        previous_day_key = self._day_key.get(symbol)

        if previous_day_key is not None and previous_day_key != current_day_key:
            self._reset_day_state(symbol)

        self._day_key[symbol] = current_day_key

        self._ensure_daily_signal_counter(symbol, current_day_key)

        # ====================================================
        # UPDATE CURRENT DAY EXTREMES
        # ====================================================

        previous_high = self._last_day_high.get(symbol)

        if previous_high is None or day_high > previous_high:

            self._last_day_high[symbol] = day_high

            # New HIGH1 invalidates old M setup.
            self._m_setup.pop(
                symbol,
                None,
            )

        previous_low = self._last_day_low.get(symbol)

        if previous_low is None or day_low < previous_low:

            self._last_day_low[symbol] = day_low

            # New LOW1 invalidates old W setup.
            self._w_setup.pop(
                symbol,
                None,
            )

        alerts: List[Dict[str, object]] = []

        # ====================================================
        # PATH 1 - STRICT M
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
        # PATH 1 - STRICT W
        # ====================================================

        w_alert = self._process_w(
            symbol=symbol,
            candle=candle,
            candles=candles,
            day_low=day_low,
        )

        if w_alert is not None:
            alerts.append(w_alert)

        # ====================================================
        # PATH 2 - FEEL GOOD SELL
        # ====================================================

        if not self._feel_good_sell_sent.get(
            symbol,
            False,
        ):

            feel_good_sell = self._process_feel_good_sell(
                symbol=symbol,
                candle=candle,
                candles=candles,
                day_high=day_high,
            )

            if feel_good_sell is not None:

                alerts.append(feel_good_sell)

                self._feel_good_sell_sent[symbol] = True

        # ====================================================
        # PATH 2 - FEEL GOOD BUY
        # ====================================================

        if not self._feel_good_buy_sent.get(
            symbol,
            False,
        ):

            feel_good_buy = self._process_feel_good_buy(
                symbol=symbol,
                candle=candle,
                candles=candles,
                day_low=day_low,
            )

            if feel_good_buy is not None:

                alerts.append(feel_good_buy)

                self._feel_good_buy_sent[symbol] = True

        return self._merge_signal_alerts(alerts)

    # ========================================================
    # STRICT M
    # ========================================================

    def _process_m(
        self,
        *,
        symbol: str,
        candle,
        candles: List,
        day_high: float,
    ) -> Optional[Dict[str, object]]:
        """
        Strict M:

            HIGH1 -> VALLEY -> HIGH2

        NEW WAITING PRINT:

            HIGH1 -> VALLEY >= 0.09%

        causes ONE print:

            M SETUP CONFIRMED | WAITING FOR HIGH2

        The message is not repeated.
        """

        try:
            candle_id = int(candle.candle_id)

            candle_high = float(candle.high)

            candle_low = float(candle.low)

        except (
            AttributeError,
            TypeError,
            ValueError,
        ):
            return None

        setup = self._m_setup.get(symbol)

        # ====================================================
        # START HIGH1
        # ====================================================

        if setup is None:

            if candle_high >= day_high:

                self._m_setup[symbol] = _MSetup(
                    high1=day_high,
                    high1_timestamp=candle.timestamp,
                    high1_candle_id=candle_id,
                )

            return None

        # ====================================================
        # NEW HIGH1
        # ====================================================

        if day_high > setup.high1:

            self._m_setup[symbol] = _MSetup(
                high1=day_high,
                high1_timestamp=candle.timestamp,
                high1_candle_id=candle_id,
            )

            return None

        if candle_id <= setup.high1_candle_id:
            return None

        # ====================================================
        # FIND VALLEY
        # ====================================================

        if setup.valley is None:

            current_close = float(getattr(candle, "close", candle_high))
            if current_close > 0:
                gap_pct = abs(current_close - setup.high1) / setup.high1 * 100.0
                if gap_pct > self.min_second_swing_difference_pct * 5.0:
                    self._m_setup.pop(symbol, None)
                    return None

            fall_pct = ((setup.high1 - candle_low) / setup.high1) * 100.0

            if fall_pct >= self.min_reversal_pct:

                setup.valley = candle_low
                setup.valley_timestamp = candle.timestamp
                setup.valley_candle_id = candle_id

                # ------------------------------------------------
                # ONE WAITING PRINT
                # ------------------------------------------------

                if not setup.waiting_printed:

                    setup.waiting_printed = True

                    logger.info(
                        "M SETUP CONFIRMED | "
                        "symbol=%s | "
                        "HIGH1=%.2f | "
                        "VALLEY=%.2f | "
                        "REVERSAL=%.3f%% | "
                        "WAITING FOR HIGH2 | "
                        "MIN_CANDLES=%d | "
                        "REQUIRED_HIGH2_DIFF=%.3f%%",
                        symbol,
                        setup.high1,
                        setup.valley,
                        fall_pct,
                        self.min_candles_between_extremes,
                        self.min_second_swing_difference_pct,
                    )

                    print(
                        f"M SETUP CONFIRMED | "
                        f"{symbol} | "
                        f"HIGH1={setup.high1:.2f} | "
                        f"VALLEY={setup.valley:.2f} | "
                        f"REVERSAL={fall_pct:.3f}% | "
                        f"WAITING FOR HIGH2"
                    )

            return None

        # ====================================================
        # EXTEND VALLEY
        #
        # Still before HIGH2.
        # A lower low updates the valley.
        # ====================================================

        if (
            setup.valley_candle_id is not None
            and candle_id > setup.valley_candle_id
            and candle_low < setup.valley
        ):

            setup.valley = candle_low

            setup.valley_timestamp = candle.timestamp

            setup.valley_candle_id = candle_id

            return None

        # ====================================================
        # HIGH2 MUST BE AFTER VALLEY
        # ====================================================

        if setup.valley_candle_id is None or candle_id <= setup.valley_candle_id:
            return None

        # ====================================================
        # HIGH1 -> HIGH2 MINIMUM 4 CANDLES
        # ====================================================

        candles_distance = candle_id - setup.high1_candle_id

        if candles_distance < self.min_candles_between_extremes:
            return None

        # ====================================================
        # HIGH2 MUST BE AT LEAST 0.05% BELOW HIGH1
        # ====================================================

        difference_pct = ((setup.high1 - candle_high) / setup.high1) * 100.0

        if difference_pct < self.min_second_swing_difference_pct:
            return None

        # ====================================================
        # HIGH2 MUST BE ABOVE VALLEY
        # ====================================================

        if candle_high <= setup.valley:
            return None

        # ====================================================
        # HIGH2 CANNOT BECOME NEW HIGH1
        # ====================================================

        if candle_high >= setup.high1:
            return None

        # ====================================================
        # DUPLICATE PROTECTION
        # ====================================================

        if self._last_m_alert_candle.get(symbol) == candle_id:
            return None

        if not self._can_emit_signal(symbol, "SELL", candle_id):
            return None

        self._last_m_alert_candle[symbol] = candle_id

        # ====================================================
        # FINAL M CONDITION SATISFIED
        # ====================================================

        confidence = self._m_confidence(
            setup.high1,
            setup.valley,
            candle_high,
        )

        if not self._passes_confidence_gate(confidence):
            return None

        alert = self._build_alert(
            symbol=symbol,
            pattern="M",
            alert_type="AI_LOGIC",
            direction="SELL",
            price=float(candle.close),
            candle=candle,
            confidence=confidence,
            logic="AI",
            high1=setup.high1,
            valley=setup.valley,
            high2=candle_high,
            high1_timestamp=setup.high1_timestamp,
            valley_timestamp=setup.valley_timestamp,
            high2_timestamp=candle.timestamp,
            high1_candle_id=setup.high1_candle_id,
            valley_candle_id=setup.valley_candle_id,
            high2_candle_id=candle_id,
            candles_distance=candles_distance,
            reversal_pct=round(
                ((setup.high1 - setup.valley) / setup.high1) * 100.0,
                4,
            ),
            outer_difference_pct=round(
                difference_pct,
                4,
            ),
        )

        self._register_signal(symbol, "SELL", alert)

        print(
            f"M ALERT | "
            f"{symbol} | "
            f"SELL | "
            f"HIGH1={setup.high1:.2f} | "
            f"VALLEY={setup.valley:.2f} | "
            f"HIGH2={candle_high:.2f} | "
            f"REVERSAL={((setup.high1 - setup.valley) / setup.high1) * 100.0:.3f}% | "
            f"HIGH1-HIGH2={difference_pct:.3f}% | "
            f"CANDLES={candles_distance}"
        )

        self._m_setup.pop(
            symbol,
            None,
        )

        return alert

    # ========================================================
    # STRICT W
    # ========================================================

    def _process_w(
        self,
        *,
        symbol: str,
        candle,
        candles: List,
        day_low: float,
    ) -> Optional[Dict[str, object]]:
        """
        Strict W:

            LOW1 -> PEAK -> LOW2

        NEW WAITING PRINT:

            LOW1 -> PEAK >= 0.09%

        causes ONE print:

            W SETUP CONFIRMED | WAITING FOR LOW2

        The message is not repeated.
        """

        try:
            candle_id = int(candle.candle_id)

            candle_high = float(candle.high)

            candle_low = float(candle.low)

        except (
            AttributeError,
            TypeError,
            ValueError,
        ):
            return None

        setup = self._w_setup.get(symbol)

        # ====================================================
        # START LOW1
        # ====================================================

        if setup is None:

            if candle_low <= day_low:

                self._w_setup[symbol] = _WSetup(
                    low1=day_low,
                    low1_timestamp=candle.timestamp,
                    low1_candle_id=candle_id,
                )

            return None

        # ====================================================
        # NEW LOW1
        # ====================================================

        if day_low < setup.low1:

            self._w_setup[symbol] = _WSetup(
                low1=day_low,
                low1_timestamp=candle.timestamp,
                low1_candle_id=candle_id,
            )

            return None

        if candle_id <= setup.low1_candle_id:
            return None

        # ====================================================
        # FIND PEAK
        # ====================================================

        if setup.peak is None:

            current_close = float(getattr(candle, "close", candle_low))
            if current_close > 0:
                gap_pct = abs(current_close - setup.low1) / setup.low1 * 100.0
                if gap_pct > self.min_second_swing_difference_pct * 5.0:
                    self._w_setup.pop(symbol, None)
                    return None

            rise_pct = ((candle_high - setup.low1) / setup.low1) * 100.0

            if rise_pct >= self.min_reversal_pct:

                setup.peak = candle_high

                setup.peak_timestamp = candle.timestamp

                setup.peak_candle_id = candle_id

                # ------------------------------------------------
                # ONE WAITING PRINT
                # ------------------------------------------------

                if not setup.waiting_printed:

                    setup.waiting_printed = True

                    logger.info(
                        "W SETUP CONFIRMED | "
                        "symbol=%s | "
                        "LOW1=%.2f | "
                        "PEAK=%.2f | "
                        "REVERSAL=%.3f%% | "
                        "WAITING FOR LOW2 | "
                        "MIN_CANDLES=%d | "
                        "REQUIRED_LOW2_DIFF=%.3f%%",
                        symbol,
                        setup.low1,
                        setup.peak,
                        rise_pct,
                        self.min_candles_between_extremes,
                        self.min_second_swing_difference_pct,
                    )

                    print(
                        f"W SETUP CONFIRMED | "
                        f"{symbol} | "
                        f"LOW1={setup.low1:.2f} | "
                        f"PEAK={setup.peak:.2f} | "
                        f"REVERSAL={rise_pct:.3f}% | "
                        f"WAITING FOR LOW2"
                    )

            return None

        # ====================================================
        # EXTEND PEAK
        # ====================================================

        if (
            setup.peak_candle_id is not None
            and candle_id > setup.peak_candle_id
            and candle_high > setup.peak
        ):

            setup.peak = candle_high

            setup.peak_timestamp = candle.timestamp

            setup.peak_candle_id = candle_id

            return None

        # ====================================================
        # LOW2 MUST BE AFTER PEAK
        # ====================================================

        if setup.peak_candle_id is None or candle_id <= setup.peak_candle_id:
            return None

        # ====================================================
        # LOW1 -> LOW2 MINIMUM 4 CANDLES
        # ====================================================

        candles_distance = candle_id - setup.low1_candle_id

        if candles_distance < self.min_candles_between_extremes:
            return None

        # ====================================================
        # LOW2 MUST BE AT LEAST 0.05% ABOVE LOW1
        # ====================================================

        difference_pct = ((candle_low - setup.low1) / setup.low1) * 100.0

        if difference_pct < self.min_second_swing_difference_pct:
            return None

        # ====================================================
        # LOW2 MUST BE BELOW PEAK
        # ====================================================

        if candle_low >= setup.peak:
            return None

        # ====================================================
        # LOW2 CANNOT BECOME NEW LOW1
        # ====================================================

        if candle_low <= setup.low1:
            return None

        # ====================================================
        # DUPLICATE PROTECTION
        # ====================================================

        if self._last_w_alert_candle.get(symbol) == candle_id:
            return None

        if not self._can_emit_signal(symbol, "BUY", candle_id):
            return None

        self._last_w_alert_candle[symbol] = candle_id

        # ====================================================
        # FINAL W CONDITION SATISFIED
        # ====================================================

        confidence = self._w_confidence(
            setup.low1,
            setup.peak,
            candle_low,
        )

        if not self._passes_confidence_gate(confidence):
            return None

        alert = self._build_alert(
            symbol=symbol,
            pattern="W",
            alert_type="AI_LOGIC",
            direction="BUY",
            price=float(candle.close),
            candle=candle,
            confidence=confidence,
            logic="AI",
            low1=setup.low1,
            peak=setup.peak,
            low2=candle_low,
            low1_timestamp=setup.low1_timestamp,
            peak_timestamp=setup.peak_timestamp,
            low2_timestamp=candle.timestamp,
            low1_candle_id=setup.low1_candle_id,
            peak_candle_id=setup.peak_candle_id,
            low2_candle_id=candle_id,
            candles_distance=candles_distance,
            reversal_pct=round(
                ((setup.peak - setup.low1) / setup.low1) * 100.0,
                4,
            ),
            outer_difference_pct=round(
                difference_pct,
                4,
            ),
        )

        self._register_signal(symbol, "BUY", alert)

        print(
            f"W ALERT | "
            f"{symbol} | "
            f"BUY | "
            f"LOW1={setup.low1:.2f} | "
            f"PEAK={setup.peak:.2f} | "
            f"LOW2={candle_low:.2f} | "
            f"REVERSAL={((setup.peak - setup.low1) / setup.low1) * 100.0:.3f}% | "
            f"LOW1-LOW2={difference_pct:.3f}% | "
            f"CANDLES={candles_distance}"
        )

        self._w_setup.pop(
            symbol,
            None,
        )

        return alert

    # ========================================================
    # FEEL-GOOD M / SELL
    # ========================================================

    def _process_feel_good_sell(
        self,
        *,
        symbol: str,
        candle,
        candles: List,
        day_high: float,
    ) -> Optional[Dict[str, object]]:

        if len(candles) < 5:
            return None

        ordered = sorted(
            candles,
            key=lambda x: int(
                getattr(
                    x,
                    "candle_id",
                    0,
                )
            ),
        )

        try:
            current_id = int(candle.candle_id)

            current_close = float(candle.close)

            current_high = float(candle.high)

        except (
            AttributeError,
            TypeError,
            ValueError,
        ):
            return None

        try:
            if int(ordered[-1].candle_id) != current_id:
                return None

        except (
            AttributeError,
            TypeError,
            ValueError,
        ):
            return None

        high_candidates = [
            x
            for x in ordered[:-1]
            if float(
                getattr(
                    x,
                    "high",
                    0.0,
                )
            )
            > 0
        ]

        if not high_candidates:
            return None

        high1_candle = max(
            high_candidates,
            key=lambda x: float(
                getattr(
                    x,
                    "high",
                    0.0,
                )
            ),
        )

        try:
            high1 = float(high1_candle.high)

            high1_id = int(high1_candle.candle_id)

        except (
            AttributeError,
            TypeError,
            ValueError,
        ):
            return None

        if high1 <= 0 or current_id <= high1_id:
            return None

        if high1 < float(day_high) * 0.999999:
            return None

        after_high = [
            x
            for x in ordered
            if int(
                getattr(
                    x,
                    "candle_id",
                    0,
                )
            )
            > high1_id
        ]

        if len(after_high) < 3:
            return None

        valley_candidates = [
            x
            for x in after_high[:-1]
            if float(
                getattr(
                    x,
                    "low",
                    0.0,
                )
            )
            > 0
        ]

        if not valley_candidates:
            return None

        valley_candle = min(
            valley_candidates,
            key=lambda x: float(
                getattr(
                    x,
                    "low",
                    float("inf"),
                )
            ),
        )

        try:
            valley = float(valley_candle.low)

            valley_id = int(valley_candle.candle_id)

        except (
            AttributeError,
            TypeError,
            ValueError,
        ):
            return None

        if valley_id <= high1_id or valley_id >= current_id:
            return None

        reversal_pct = ((high1 - valley) / high1) * 100.0

        if reversal_pct < FEEL_GOOD_MIN_REVERSAL_PCT:
            return None

        distance = current_id - high1_id

        if distance < FEEL_GOOD_MIN_CANDLES:
            return None

        if current_high >= high1:
            return None

        outer_difference_pct = ((high1 - current_high) / high1) * 100.0

        if outer_difference_pct < FEEL_GOOD_MIN_SWING_DIFFERENCE_PCT:
            return None

        swing = high1 - valley

        if swing <= 0:
            return None

        position = (current_close - valley) / swing

        if not (0.0 < position <= 0.50):
            return None

        recent = ordered[-3:]

        bearish_count = sum(
            1
            for x in recent
            if bool(
                getattr(
                    x,
                    "bearish",
                    False,
                )
            )
        )

        current_open = float(
            getattr(
                candle,
                "open",
                current_close,
            )
        )

        current_range = max(
            current_high - float(candle.low),
            0.0,
        )

        current_body = abs(current_close - current_open)

        strong_current_bearish = (
            current_close < current_open
            and current_range > 0
            and (current_body / current_range) >= 0.45
        )

        if bearish_count < 2 and not strong_current_bearish:
            return None

        score = self._feel_good_m_score(
            high1=high1,
            valley=valley,
            current_price=current_close,
            day_high=day_high,
            candles=ordered,
        )

        if score < FEEL_GOOD_MIN_SCORE:
            return None

        if not self._can_emit_signal(symbol, "SELL", current_id):
            return None

        if not self._passes_confidence_gate(score):
            return None

        alert = self._build_alert(
            symbol=symbol,
            pattern="MY_LOGIC_SELL",
            alert_type="MY_LOGIC",
            direction="SELL",
            price=current_close,
            candle=candle,
            confidence=score,
            logic="MY_LOGIC",
            high1=high1,
            valley=valley,
            high2=current_high,
            high1_timestamp=high1_candle.timestamp,
            valley_timestamp=valley_candle.timestamp,
            high2_timestamp=candle.timestamp,
            high1_candle_id=high1_id,
            valley_candle_id=valley_id,
            high2_candle_id=current_id,
            candles_distance=distance,
            reversal_pct=round(
                reversal_pct,
                4,
            ),
            outer_difference_pct=round(
                outer_difference_pct,
                4,
            ),
        )
        self._register_signal(symbol, "SELL", alert)
        return alert

    # ========================================================
    # FEEL-GOOD W / BUY
    # ========================================================

    def _process_feel_good_buy(
        self,
        *,
        symbol: str,
        candle,
        candles: List,
        day_low: float,
    ) -> Optional[Dict[str, object]]:

        if len(candles) < 5:
            return None

        ordered = sorted(
            candles,
            key=lambda x: int(
                getattr(
                    x,
                    "candle_id",
                    0,
                )
            ),
        )

        try:
            current_id = int(candle.candle_id)

            current_close = float(candle.close)

            current_low = float(candle.low)

        except (
            AttributeError,
            TypeError,
            ValueError,
        ):
            return None

        try:
            if int(ordered[-1].candle_id) != current_id:
                return None

        except (
            AttributeError,
            TypeError,
            ValueError,
        ):
            return None

        low_candidates = [
            x
            for x in ordered[:-1]
            if float(
                getattr(
                    x,
                    "low",
                    0.0,
                )
            )
            > 0
        ]

        if not low_candidates:
            return None

        low1_candle = min(
            low_candidates,
            key=lambda x: float(
                getattr(
                    x,
                    "low",
                    float("inf"),
                )
            ),
        )

        try:
            low1 = float(low1_candle.low)

            low1_id = int(low1_candle.candle_id)

        except (
            AttributeError,
            TypeError,
            ValueError,
        ):
            return None

        if low1 <= 0 or current_id <= low1_id:
            return None

        if low1 > float(day_low) * 1.000001:
            return None

        after_low = [
            x
            for x in ordered
            if int(
                getattr(
                    x,
                    "candle_id",
                    0,
                )
            )
            > low1_id
        ]

        if len(after_low) < 3:
            return None

        peak_candidates = [
            x
            for x in after_low[:-1]
            if float(
                getattr(
                    x,
                    "high",
                    0.0,
                )
            )
            > 0
        ]

        if not peak_candidates:
            return None

        peak_candle = max(
            peak_candidates,
            key=lambda x: float(
                getattr(
                    x,
                    "high",
                    0.0,
                )
            ),
        )

        try:
            peak = float(peak_candle.high)

            peak_id = int(peak_candle.candle_id)

        except (
            AttributeError,
            TypeError,
            ValueError,
        ):
            return None

        if peak_id <= low1_id or peak_id >= current_id:
            return None

        reversal_pct = ((peak - low1) / low1) * 100.0

        if reversal_pct < FEEL_GOOD_MIN_REVERSAL_PCT:
            return None

        distance = current_id - low1_id

        if distance < FEEL_GOOD_MIN_CANDLES:
            return None

        if current_low <= low1:
            return None

        outer_difference_pct = ((current_low - low1) / low1) * 100.0

        if outer_difference_pct < FEEL_GOOD_MIN_SWING_DIFFERENCE_PCT:
            return None

        swing = peak - low1

        if swing <= 0:
            return None

        position = (current_close - low1) / swing

        if not (0.50 <= position < 1.0):
            return None

        recent = ordered[-3:]

        bullish_count = sum(
            1
            for x in recent
            if bool(
                getattr(
                    x,
                    "bullish",
                    False,
                )
            )
        )

        current_open = float(
            getattr(
                candle,
                "open",
                current_close,
            )
        )

        current_range = max(
            float(candle.high) - current_low,
            0.0,
        )

        current_body = abs(current_close - current_open)

        strong_current_bullish = (
            current_close > current_open
            and current_range > 0
            and (current_body / current_range) >= 0.45
        )

        if bullish_count < 2 and not strong_current_bullish:
            return None

        score = self._feel_good_w_score(
            low1=low1,
            peak=peak,
            current_price=current_close,
            day_low=day_low,
            candles=ordered,
        )

        if score < FEEL_GOOD_MIN_SCORE:
            return None

        if not self._can_emit_signal(symbol, "BUY", current_id):
            return None

        if not self._passes_confidence_gate(score):
            return None

        alert = self._build_alert(
            symbol=symbol,
            pattern="MY_LOGIC_BUY",
            alert_type="MY_LOGIC",
            direction="BUY",
            price=current_close,
            candle=candle,
            confidence=score,
            logic="MY_LOGIC",
            low1=low1,
            peak=peak,
            low2=current_low,
            low1_timestamp=low1_candle.timestamp,
            peak_timestamp=peak_candle.timestamp,
            low2_timestamp=candle.timestamp,
            low1_candle_id=low1_id,
            peak_candle_id=peak_id,
            low2_candle_id=current_id,
            candles_distance=distance,
            reversal_pct=round(
                reversal_pct,
                4,
            ),
            outer_difference_pct=round(
                outer_difference_pct,
                4,
            ),
        )
        self._register_signal(symbol, "BUY", alert)
        return alert

    # ========================================================
    # FEEL-GOOD M SCORE
    # ========================================================

    def _feel_good_m_score(
        self,
        *,
        high1: float,
        valley: float,
        current_price: float,
        day_high: float,
        candles: List,
    ) -> float:

        score = 60.0

        reversal = ((high1 - valley) / high1) * 100.0

        if reversal >= 0.09:
            score += 10.0

        if reversal >= 0.15:
            score += 5.0

        difference = (
            (
                high1
                - max(
                    float(
                        getattr(
                            c,
                            "high",
                            0.0,
                        )
                    )
                    for c in candles[-1:]
                )
            )
            / high1
        ) * 100.0

        if difference >= 0.05:
            score += 5.0

        swing = high1 - valley

        if swing > 0:

            position = (current_price - valley) / swing

            if 0.10 <= position <= 0.50:
                score += 10.0

        recent = candles[-3:]

        bearish_count = sum(
            1
            for x in recent
            if bool(
                getattr(
                    x,
                    "bearish",
                    False,
                )
            )
        )

        if bearish_count >= 2:
            score += 10.0

        return min(
            score,
            95.0,
        )

    # ========================================================
    # FEEL-GOOD W SCORE
    # ========================================================

    def _feel_good_w_score(
        self,
        *,
        low1: float,
        peak: float,
        current_price: float,
        day_low: float,
        candles: List,
    ) -> float:

        score = 60.0

        reversal = ((peak - low1) / low1) * 100.0

        if reversal >= 0.09:
            score += 10.0

        if reversal >= 0.15:
            score += 5.0

        recent_low = min(
            float(
                getattr(
                    c,
                    "low",
                    float("inf"),
                )
            )
            for c in candles[-1:]
        )

        difference = ((recent_low - low1) / low1) * 100.0

        if difference >= 0.05:
            score += 5.0

        swing = peak - low1

        if swing > 0:

            position = (current_price - low1) / swing

            if 0.50 <= position <= 0.90:
                score += 10.0

        recent = candles[-3:]

        bullish_count = sum(
            1
            for x in recent
            if bool(
                getattr(
                    x,
                    "bullish",
                    False,
                )
            )
        )

        if bullish_count >= 2:
            score += 10.0

        return min(
            score,
            95.0,
        )

    # ========================================================
    # STRICT CONFIDENCE - M
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

        if reversal >= 0.15:
            score += 10.0

        if difference >= 0.08:
            score += 10.0

        return min(
            score,
            100.0,
        )

    # ========================================================
    # STRICT CONFIDENCE - W
    # ========================================================

    def _w_confidence(
        self,
        low1: float,
        peak: float,
        low2: float,
    ) -> float:

        reversal = ((peak - low1) / low1) * 100.0

        difference = ((low2 - low1) / low1) * 100.0

        score = 70.0

        if reversal >= 0.15:
            score += 10.0

        if difference >= 0.08:
            score += 10.0

        return min(
            score,
            100.0,
        )

    # ========================================================
    # BUILD ALERT
    # ========================================================

    @classmethod
    def _build_alert(
        cls,
        *,
        symbol: str,
        pattern: str,
        alert_type: str,
        direction: str,
        price: float,
        candle,
        confidence: float,
        **extra,
    ) -> Dict[str, object]:

        timestamp = getattr(
            candle,
            "timestamp",
            None,
        )

        timestamp = cls._to_ist(timestamp)

        logic = extra.pop("logic", "AI")

        alert: Dict[str, object] = {
            "symbol": symbol,
            "alert_type": alert_type,
            "pattern": pattern,
            "direction": direction,
            "logic": logic,
            "confidence": round(
                float(confidence),
                1,
            ),
            "price": float(price),
            "entry": float(price),
            "timestamp": timestamp,
        }

        for key in (
            "high1_timestamp",
            "valley_timestamp",
            "high2_timestamp",
            "low1_timestamp",
            "peak_timestamp",
            "low2_timestamp",
        ):

            if key in extra:

                extra[key] = cls._to_ist(extra[key])

        alert.update(extra)

        return alert

    # ========================================================
    # MERGE SIGNAL ALERTS
    # ========================================================

    @staticmethod
    def _signal_sort_key(alert: Dict[str, object]) -> tuple:
        direction = str(alert.get("direction", "")).upper()
        logic = str(alert.get("logic", "")).upper()
        confidence = float(alert.get("confidence", 0.0))
        logic_rank = 1 if logic == "AI" else 0
        return (confidence, logic_rank, 1 if direction == "BUY" else 0)

    def _merge_signal_alerts(
        self,
        alerts: List[Dict[str, object]],
    ) -> List[Dict[str, object]]:
        best_by_direction: Dict[str, Dict[str, object]] = {}

        for alert in alerts or []:
            if not isinstance(alert, dict):
                continue

            direction = str(alert.get("direction", "")).upper()
            if direction not in {"BUY", "SELL"}:
                continue

            current_best = best_by_direction.get(direction)
            if current_best is None:
                best_by_direction[direction] = alert
                continue

            if self._signal_sort_key(alert) > self._signal_sort_key(current_best):
                best_by_direction[direction] = alert

        merged: List[Dict[str, object]] = []
        for direction in ("BUY", "SELL"):
            alert = best_by_direction.get(direction)
            if alert is not None:
                merged.append(alert)

        return merged

    # ========================================================
    # CALCULATE DAY HIGH
    # ========================================================

    @staticmethod
    def _calculate_day_high(
        candles: List,
    ) -> Optional[float]:

        highest = None

        for candle in candles:

            try:
                value = float(candle.high)

            except (
                AttributeError,
                TypeError,
                ValueError,
            ):
                continue

            if value <= 0:
                continue

            if highest is None or value > highest:
                highest = value

        return highest

    # ========================================================
    # CALCULATE DAY LOW
    # ========================================================

    @staticmethod
    def _calculate_day_low(
        candles: List,
    ) -> Optional[float]:

        lowest = None

        for candle in candles:

            try:
                value = float(candle.low)

            except (
                AttributeError,
                TypeError,
                ValueError,
            ):
                continue

            if value <= 0:
                continue

            if lowest is None or value < lowest:
                lowest = value

        return lowest

    # ========================================================
    # INDIA DAY KEY
    # ========================================================

    @staticmethod
    def _india_day_key(
        timestamp: datetime,
    ) -> str:

        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        return timestamp.astimezone(INDIA_TZ).strftime("%Y-%m-%d")

    # ========================================================
    # TO IST
    # ========================================================

    @staticmethod
    def _to_ist(
        timestamp: Optional[datetime],
    ) -> datetime:

        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        if timestamp.tzinfo is None:

            timestamp = timestamp.replace(tzinfo=timezone.utc)

        return timestamp.astimezone(INDIA_TZ)

    def _passes_confidence_gate(
        self,
        confidence: float,
    ) -> bool:

        return float(confidence) >= self.min_signal_confidence

    def _ensure_daily_signal_counter(
        self,
        symbol: str,
        day_key: str,
    ) -> None:

        counts = self._signal_counts.get(symbol)

        if counts is None or counts.get("_day_key") != day_key:
            self._signal_counts[symbol] = {"buy": 0, "sell": 0, "_day_key": day_key}

    def _can_emit_signal(
        self,
        symbol: str,
        direction: str,
        candle_id: Optional[int] = None,
    ) -> bool:

        self._ensure_daily_signal_counter(
            symbol,
            self._day_key.get(symbol, ""),
        )

        counts = self._signal_counts.get(symbol, {"buy": 0, "sell": 0})
        normalized = direction.upper()

        # Basic daily cap check
        if normalized == "BUY":
            if counts.get("buy", 0) >= self.max_buy_per_day:
                return False

            next_index = counts.get("buy", 0) + 1
            last_candle = self._last_w_alert_candle.get(symbol)

        elif normalized == "SELL":
            if counts.get("sell", 0) >= self.max_sell_per_day:
                return False

            next_index = counts.get("sell", 0) + 1
            last_candle = self._last_m_alert_candle.get(symbol)

        else:
            return True

        # Enforce escalating minimum spacing between successive alerts of the same type.
        # Behavior:
        #  - 2nd alert: min_gap = 2 * base
        #  - 3rd alert: min_gap = 2 * 2 * base
        #  - 4th alert: min_gap = 2 * 2 * 2 * base
        # i.e. multiplier = 2 ** (next_index - 1)
        if last_candle is not None and candle_id is not None and next_index > 1:
            base = int(self.min_candles_between_extremes)
            required_gap = base * (2 ** (next_index - 1))
            actual_gap = int(candle_id) - int(last_candle)

            if actual_gap < required_gap:
                logger.debug(
                    "Blocked %s signal for %s: need gap %d, have %d (next_index=%d)",
                    normalized,
                    symbol,
                    required_gap,
                    actual_gap,
                    next_index,
                )
                return False

        return True

    def _register_signal(
        self,
        symbol: str,
        direction: str,
        alert: Optional[Dict[str, object]] = None,
    ) -> None:

        self._ensure_daily_signal_counter(
            symbol,
            self._day_key.get(symbol, ""),
        )

        counts = self._signal_counts.get(symbol)

        if counts is None:
            return

        normalized = direction.upper()

        if normalized == "BUY":
            counts["buy"] = counts.get("buy", 0) + 1
        elif normalized == "SELL":
            counts["sell"] = counts.get("sell", 0) + 1

        if alert is not None:
            symbol_name = str(symbol)
            self._signal_history.setdefault(symbol_name, []).append(
                {
                    "symbol": symbol_name,
                    "direction": normalized,
                    "entry_price": float(alert.get("entry", alert.get("price", 0.0))),
                    "timestamp": alert.get("timestamp"),
                    "confidence": float(alert.get("confidence", 0.0)),
                    "pattern": alert.get("pattern", ""),
                    "alert_type": alert.get("alert_type", ""),
                    "logic": alert.get("logic", ""),
                    "day_key": self._day_key.get(symbol_name, ""),
                }
            )

    # ========================================================
    # RESET DAY STATE
    # ========================================================

    def _reset_day_state(
        self,
        symbol: str,
    ) -> None:

        self._last_day_high.pop(
            symbol,
            None,
        )

        self._last_day_low.pop(
            symbol,
            None,
        )

        self._m_setup.pop(
            symbol,
            None,
        )

        self._w_setup.pop(
            symbol,
            None,
        )

        self._last_m_alert_candle.pop(
            symbol,
            None,
        )

        self._last_w_alert_candle.pop(
            symbol,
            None,
        )

        self._feel_good_buy_sent.pop(
            symbol,
            None,
        )

        self._feel_good_sell_sent.pop(
            symbol,
            None,
        )

    # ========================================================
    # GET M SETUP
    # ========================================================

    def get_m_setup(
        self,
        symbol: str,
    ) -> Optional[Dict[str, object]]:

        setup = self._m_setup.get(str(symbol))

        if setup is None:
            return None

        return {
            "high1": setup.high1,
            "high1_timestamp": self._to_ist(setup.high1_timestamp),
            "high1_candle_id": setup.high1_candle_id,
            "valley": setup.valley,
            "valley_timestamp": (
                self._to_ist(setup.valley_timestamp) if setup.valley_timestamp else None
            ),
            "valley_candle_id": setup.valley_candle_id,
            "waiting_for_high2": setup.waiting_printed,
            "active": setup.active,
        }

    # ========================================================
    # GET W SETUP
    # ========================================================

    def get_w_setup(
        self,
        symbol: str,
    ) -> Optional[Dict[str, object]]:

        setup = self._w_setup.get(str(symbol))

        if setup is None:
            return None

        return {
            "low1": setup.low1,
            "low1_timestamp": self._to_ist(setup.low1_timestamp),
            "low1_candle_id": setup.low1_candle_id,
            "peak": setup.peak,
            "peak_timestamp": (
                self._to_ist(setup.peak_timestamp) if setup.peak_timestamp else None
            ),
            "peak_candle_id": setup.peak_candle_id,
            "waiting_for_low2": setup.waiting_printed,
            "active": setup.active,
        }

    # ========================================================
    # GET DAY EXTREMES
    # ========================================================

    def get_day_extremes(
        self,
        symbol: str,
    ) -> Dict[str, Optional[float]]:

        symbol = str(symbol)

        return {
            "day_high": self._last_day_high.get(symbol),
            "day_low": self._last_day_low.get(symbol),
        }

    # ========================================================
    # GET FEEL-GOOD STATUS
    # ========================================================

    def get_feel_good_status(
        self,
        symbol: str,
    ) -> Dict[str, object]:

        symbol = str(symbol)

        return {
            "buy_sent": self._feel_good_buy_sent.get(
                symbol,
                False,
            ),
            "sell_sent": self._feel_good_sell_sent.get(
                symbol,
                False,
            ),
            "max_buy_per_day": 1,
            "max_sell_per_day": 1,
        }

    # ========================================================
    # EOD SIGNAL QUALITY
    # ========================================================

    def evaluate_eod_signal_quality(
        self,
        symbol: str,
        *,
        day_close_price: Optional[float] = None,
        day_key: Optional[str] = None,
        apply_learning: bool = True,
    ) -> Dict[str, object]:

        symbol = str(symbol)
        history = list(self._signal_history.get(symbol, []))

        if day_key is not None:
            history = [entry for entry in history if entry.get("day_key") == day_key]

        if not history:
            return {
                "symbol": symbol,
                "total_signals": 0,
                "buy_count": 0,
                "sell_count": 0,
                "buy_good": 0,
                "sell_good": 0,
                "win_rate": 0.0,
                "status": "NO_DATA",
                "suggestion": "No signal history available for EOD review.",
                "learning_adjusted": False,
            }

        if day_close_price is None:
            day_close_price = 0.0

        try:
            close_price = float(day_close_price)
        except TypeError, ValueError:
            close_price = 0.0

        buy_count = sum(1 for entry in history if entry.get("direction") == "BUY")
        sell_count = sum(1 for entry in history if entry.get("direction") == "SELL")

        buy_good = 0
        sell_good = 0

        for entry in history:
            direction = str(entry.get("direction", "")).upper()
            entry_price = float(entry.get("entry_price", 0.0))

            if direction == "BUY":
                if close_price >= entry_price:
                    buy_good += 1
            elif direction == "SELL":
                if close_price <= entry_price:
                    sell_good += 1

        total_good = buy_good + sell_good
        total_signals = len(history)
        win_rate = (total_good / total_signals) * 100.0 if total_signals else 0.0

        if win_rate >= 60.0:
            status = "GOOD"
        elif win_rate >= 40.0:
            status = "MIXED"
        else:
            status = "BAD"

        suggestions: List[str] = []
        if buy_count and buy_good < buy_count:
            suggestions.append(
                "Buy logic is weak: tighten the buy confidence gate or wait for stronger low-side reversals."
            )
        if sell_count and sell_good < sell_count:
            suggestions.append(
                "Sell logic is weak: tighten the sell confidence gate or wait for stronger high-side reversals."
            )
        if not suggestions:
            suggestions.append(
                "Strategy is aligned with the day move; keep the current signal logic."
            )

        result = {
            "symbol": symbol,
            "total_signals": total_signals,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "buy_good": buy_good,
            "sell_good": sell_good,
            "win_rate": round(win_rate, 2),
            "status": status,
            "suggestion": " ".join(suggestions),
            "learning_adjusted": False,
        }

        if apply_learning:
            result["learning_adjusted"] = self._apply_learning_from_eod(symbol, result)

        return result

    def _apply_learning_from_eod(
        self,
        symbol: str,
        summary: Dict[str, object],
    ) -> bool:

        symbol = str(symbol)
        win_rate = float(summary.get("win_rate", 0.0))
        status = str(summary.get("status", "NO_DATA")).upper()

        state = self._learning_state.setdefault(
            symbol,
            {
                "confidence_floor": float(self.min_signal_confidence),
                "buy_bias": 1.0,
                "sell_bias": 1.0,
            },
        )

        if status == "BAD":
            state["confidence_floor"] = min(95.0, state["confidence_floor"] + 5.0)
            state["buy_bias"] = max(0.5, state["buy_bias"] * 0.8)
            state["sell_bias"] = max(0.5, state["sell_bias"] * 0.8)
            self.min_signal_confidence = float(state["confidence_floor"])
            return True

        if status == "GOOD":
            state["confidence_floor"] = max(80.0, state["confidence_floor"] - 2.0)
            state["buy_bias"] = min(1.2, state["buy_bias"] * 1.05)
            state["sell_bias"] = min(1.2, state["sell_bias"] * 1.05)
            self.min_signal_confidence = float(state["confidence_floor"])
            return True

        if win_rate >= 50.0:
            state["confidence_floor"] = max(80.0, state["confidence_floor"] - 1.0)
            self.min_signal_confidence = float(state["confidence_floor"])
            return True

        return False
