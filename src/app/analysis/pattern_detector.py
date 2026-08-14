from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from aimios.market.candle_buffer import Candle


class PatternDetector:
    """Minimal deterministic pattern detector for AIMIOS live-pattern tests."""

    def detect(self, candles: List[Candle]) -> Dict[str, object]:
        if not candles:
            return {}

        prices = [float(c.close) for c in candles]

        # --------------------------------------------------
        # IMPORTANT:
        # Check the more specific structures first.
        # --------------------------------------------------

        pattern = self._detect_v(candles)
        if pattern:
            return pattern

        pattern = self._detect_w(candles)
        if pattern:
            return pattern

        pattern = self._detect_double_top(candles)
        if pattern:
            return pattern

        pattern = self._detect_double_bottom(candles)
        if pattern:
            return pattern

        pattern = self._detect_m(candles)
        if pattern:
            return pattern

        pattern = self._detect_breakout(candles)
        if pattern:
            return pattern

        pattern = self._detect_fake_breakout(candles)
        if pattern:
            return pattern

        pattern = self._detect_exhaustion(candles)
        if pattern:
            return pattern

        return {}

    # ==========================================================
    # COMMON OUTPUT
    # ==========================================================

    def _build_pattern(
        self,
        name: str,
        confidence: float,
        price: float,
    ) -> Dict[str, object]:

        return {
            "pattern": name,
            "confidence": round(max(0.0, min(100.0, confidence))),
            "price": round(price, 2),
            "time": datetime.now(timezone.utc).strftime("%H:%M"),
        }

    # ==========================================================
    # HELPERS
    # ==========================================================

    def _closes(
        self,
        candles: List[Candle],
        count: int,
    ) -> List[float]:

        if len(candles) < count:
            return []

        return [float(c.close) for c in candles[-count:]]

    def _near(
        self,
        a: float,
        b: float,
        tolerance_pct: float = 5.0,
    ) -> bool:

        base = max(abs(a), abs(b), 1.0)

        difference_pct = abs(a - b) / base * 100.0

        return difference_pct <= tolerance_pct

    # ==========================================================
    # V REVERSAL
    # ==========================================================

    def _detect_v(
        self,
        candles: List[Candle],
    ) -> Optional[Dict[str, object]]:

        prices = self._closes(candles, 4)

        if not prices:
            return None

        a, b, c, d = prices

        # Example:
        # 100 -> 90 -> 105 -> 105
        #
        # Strong fall followed by strong recovery.

        drop = a - b
        recovery = d - b

        if (
            a > b
            and c > b
            and d >= c
            and d >= a
            and drop > 0
            and recovery >= drop * 0.8
        ):

            return self._build_pattern(
                "V_REVERSAL",
                85.0,
                d,
            )

        return None

    # ==========================================================
    # W PATTERN
    # ==========================================================

    def _detect_w(
        self,
        candles: List[Candle],
    ) -> Optional[Dict[str, object]]:

        prices = self._closes(candles, 6)

        if not prices:
            return None

        a, b, c, d, e, f = prices

        # Example:
        # 100 -> 110 -> 95 -> 108 -> 98 -> 98
        #
        # High -> low -> high -> second low
        #
        # The two lows are reasonably close,
        # but this structure must be identified
        # before DOUBLE_BOTTOM.

        first_low = c
        second_low = e
        middle_high = d

        if not (b > a and c < b and d > c and e < d and f >= e):
            return None

        if not self._near(
            first_low,
            second_low,
            5.0,
        ):
            return None

        depth1 = b - first_low
        depth2 = d - second_low

        if depth1 <= 0 or depth2 <= 0:
            return None

        return self._build_pattern(
            "W",
            85.0,
            f,
        )

    # ==========================================================
    # DOUBLE TOP
    # ==========================================================

    def _detect_double_top(
        self,
        candles: List[Candle],
    ) -> Optional[Dict[str, object]]:

        prices = self._closes(candles, 6)

        if not prices:
            return None

        a, b, c, d, e, f = prices

        # Example:
        # 100 -> 90 -> 110 -> 92 -> 105 -> 105
        #
        # Two highs:
        # first peak = 110
        # second peak = 105
        #
        # followed by / ending at the second peak.

        first_peak = c
        second_peak = e

        if not (b < a and c > b and d < c and e > d and f >= e):
            return None

        if not self._near(
            first_peak,
            second_peak,
            10.0,
        ):
            return None

        # Make sure the first peak is actually
        # meaningfully above the valley.
        valley = d

        if first_peak <= valley:
            return None

        return self._build_pattern(
            "DOUBLE_TOP",
            88.0,
            second_peak,
        )

    # ==========================================================
    # DOUBLE BOTTOM
    # ==========================================================

    def _detect_double_bottom(
        self,
        candles: List[Candle],
    ) -> Optional[Dict[str, object]]:

        prices = self._closes(candles, 6)

        if not prices:
            return None

        a, b, c, d, e, f = prices

        # Example:
        # 100 -> 110 -> 90 -> 108 -> 92 -> 92
        #
        # Two lows:
        # first low  = 90
        # second low = 92

        first_low = c
        second_low = e

        if not (b > a and c < b and d > c and e < d and f >= e):
            return None

        if not self._near(
            first_low,
            second_low,
            5.0,
        ):
            return None

        return self._build_pattern(
            "DOUBLE_BOTTOM",
            88.0,
            second_low,
        )

    # ==========================================================
    # M PATTERN
    # ==========================================================

    def _detect_m(
        self,
        candles: List[Candle],
    ) -> Optional[Dict[str, object]]:

        prices = self._closes(candles, 6)

        if not prices:
            return None

        a, b, c, d, e, f = prices

        # Example M:
        #
        # rise -> peak -> fall -> second peak -> fall

        if not (b < a and c > b and d < c and e > d and f < e):
            return None

        if not self._near(
            c,
            e,
            10.0,
        ):
            return None

        return self._build_pattern(
            "M",
            85.0,
            f,
        )

    # ==========================================================
    # BREAKOUT
    # ==========================================================

    def _detect_breakout(
        self,
        candles: List[Candle],
    ) -> Optional[Dict[str, object]]:

        prices = self._closes(candles, 4)

        if not prices:
            return None

        a, b, c, d = prices

        # Example:
        # 100 -> 100 -> 105 -> 105
        #
        # Price reaches a new high and remains there.

        previous_high = max(a, b, c)

        if d >= previous_high and d > a and d >= c:

            # Avoid treating V reversal as breakout.
            if a > b and c > b and d >= c:
                return None

            return self._build_pattern(
                "BREAKOUT",
                80.0,
                d,
            )

        return None

    # ==========================================================
    # FAKE BREAKOUT
    # ==========================================================

    def _detect_fake_breakout(
        self,
        candles: List[Candle],
    ) -> Optional[Dict[str, object]]:

        prices = self._closes(candles, 4)

        if not prices:
            return None

        a, b, c, d = prices

        # Example:
        # 100 -> 110 -> 107 -> 107
        #
        # Price first moves strongly upward,
        # then fails and closes below the breakout level.

        if b > a and c < b and d <= c and d < b:

            return self._build_pattern(
                "FAKE_BREAKOUT",
                75.0,
                d,
            )

        return None

    # ==========================================================
    # EXHAUSTION
    # ==========================================================

    def _detect_exhaustion(
        self,
        candles: List[Candle],
    ) -> Optional[Dict[str, object]]:

        prices = self._closes(candles, 4)

        if not prices:
            return None

        a, b, c, d = prices

        # Example:
        # 100 -> 108 -> 108.5 -> 108.5
        #
        # Strong rise followed by stagnation.

        if b > a and c >= b and d == c:

            # Do not classify the obvious V-reversal
            # as exhaustion.
            if a > b and c > b:
                return None

            return self._build_pattern(
                "EXHAUSTION",
                75.0,
                d,
            )

        return None
