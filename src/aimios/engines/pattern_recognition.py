from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

# ============================================================
# SIGNAL
# ============================================================


@dataclass
class PatternSignal:
    """
    Standard pattern signal returned by PatternRecognitionEngine.
    """

    pattern: str
    direction: str
    confidence: float
    entry: Optional[float] = None
    stoploss: Optional[float] = None
    target: Optional[float] = None
    timestamp: Any = None
    symbol: Optional[str] = None


# ============================================================
# PATTERN RECOGNITION ENGINE
# ============================================================


class PatternRecognitionEngine:
    """
    AIMIOS pattern-recognition compatibility engine.

    This class is the public engine expected by:

        tests/test_pattern.py

    and by CandleBuffer.

    It uses completed Candle objects and returns PatternSignal
    objects through detect_from_candles().
    """

    name = "PatternRecognitionEngine"

    def __init__(
        self,
        app: Any = None,
        swing_engine: Any = None,
    ) -> None:

        self.app = app
        self.swing_engine = swing_engine

        self.detector = PatternDetector()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def detect_from_candles(
        self,
        candles: List[Any],
        symbol: str = "",
    ) -> List[PatternSignal]:
        """
        Detect patterns from completed candles.

        Returns:
            List[PatternSignal]

        Returns an empty list when no pattern is detected.
        """

        if not candles:
            return []

        result = self.detector.detect(candles)

        if not result:
            return []

        pattern = str(
            result.get(
                "pattern",
                "",
            )
        ).upper()

        if not pattern:
            return []

        confidence = float(
            result.get(
                "confidence",
                0.0,
            )
        )

        entry = result.get(
            "entry",
            None,
        )

        if entry is None:
            entry = float(
                getattr(
                    candles[-1],
                    "close",
                    getattr(
                        candles[-1],
                        "ltp",
                        0.0,
                    ),
                )
            )

        direction = self._direction_for_pattern(
            pattern,
        )

        if direction is None:
            return []

        timestamp = getattr(
            candles[-1],
            "timestamp",
            None,
        )

        stoploss = result.get(
            "stoploss",
            None,
        )

        target = result.get(
            "target",
            None,
        )

        signal = PatternSignal(
            pattern=pattern,
            direction=direction,
            confidence=confidence,
            entry=entry,
            stoploss=stoploss,
            target=target,
            timestamp=timestamp,
            symbol=symbol,
        )

        return [signal]

    # ========================================================
    # DIRECTION
    # ========================================================

    @staticmethod
    def _direction_for_pattern(
        pattern: str,
    ) -> Optional[str]:
        """
        Map pattern to trading direction.

        M / DOUBLE_TOP:
            SELL

        W / DOUBLE_BOTTOM:
            BUY

        Other patterns:
            No M/W directional signal.
        """

        if pattern in {
            "M",
            "DOUBLE_TOP",
        }:
            return "SELL"

        if pattern in {
            "W",
            "DOUBLE_BOTTOM",
        }:
            return "BUY"

        return None

    # ========================================================
    # RESET / CLEAR
    # ========================================================

    def reset(self) -> None:
        self.detector.reset()

    def clear(self) -> None:
        self.detector.clear()

    # ========================================================
    # STATUS
    # ========================================================

    def status(self) -> dict:
        return {
            "engine": self.name,
            "detector": self.detector.status(),
        }


# ============================================================
# PATTERN DETECTOR
# ============================================================


class PatternDetector:
    """
    Minimal deterministic AIMIOS live pattern detector.

    Supported patterns:

        DOUBLE_TOP
        DOUBLE_BOTTOM
        V_REVERSAL
        W
        M
        BREAKOUT
        FAKE_BREAKOUT
        EXHAUSTION

    The detector works from completed candle close prices.
    """

    name = "PatternDetector"

    def __init__(self) -> None:
        self.last_pattern: str | None = None
        self.last_confidence: float = 0.0

    # ========================================================
    # PUBLIC API
    # ========================================================

    def detect(
        self,
        candles: List[Any],
    ) -> dict:
        """
        Detect the first valid pattern.

        Returns:

            {
                "pattern": str,
                "confidence": float,
            }

        or:

            {}
        """

        if not candles:
            return {}

        prices = self._prices(
            candles,
        )

        if not prices:
            return {}

        # ----------------------------------------------------
        # Structural patterns first.
        # ----------------------------------------------------

        result = self._detect_w(
            prices,
        )

        if result:
            return self._store(
                result,
            )

        result = self._detect_double_top(
            prices,
        )

        if result:
            return self._store(
                result,
            )

        result = self._detect_double_bottom(
            prices,
        )

        if result:
            return self._store(
                result,
            )

        result = self._detect_m(
            prices,
        )

        if result:
            return self._store(
                result,
            )

        result = self._detect_v_reversal(
            prices,
        )

        if result:
            return self._store(
                result,
            )

        result = self._detect_fake_breakout(
            prices,
        )

        if result:
            return self._store(
                result,
            )

        result = self._detect_exhaustion(
            prices,
        )

        if result:
            return self._store(
                result,
            )

        result = self._detect_breakout(
            prices,
        )

        if result:
            return self._store(
                result,
            )

        return {}

    # ========================================================
    # PRICE EXTRACTION
    # ========================================================

    def _prices(
        self,
        candles: List[Any],
    ) -> List[float]:
        """
        Extract close prices from Candle objects.

        Falls back to ltp when close is unavailable.
        """

        prices: List[float] = []

        for candle in candles:

            value = getattr(
                candle,
                "close",
                None,
            )

            if value is None:
                value = getattr(
                    candle,
                    "ltp",
                    None,
                )

            if value is None:
                continue

            try:
                prices.append(
                    float(value),
                )

            except TypeError, ValueError:
                continue

        return prices

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def _same_level(
        a: float,
        b: float,
        tolerance_pct: float = 3.0,
    ) -> bool:

        base = max(
            abs(a),
            abs(b),
            0.000001,
        )

        difference_pct = abs(a - b) / base * 100.0

        return difference_pct <= tolerance_pct

    @staticmethod
    def _confidence(
        value: float = 80.0,
    ) -> float:

        return max(
            1.0,
            min(
                100.0,
                float(value),
            ),
        )

    def _store(
        self,
        result: dict,
    ) -> dict:

        self.last_pattern = result.get(
            "pattern",
        )

        self.last_confidence = float(
            result.get(
                "confidence",
                0.0,
            )
        )

        return result

    # ========================================================
    # DOUBLE TOP
    # ========================================================

    def _detect_double_top(
        self,
        prices: List[float],
    ) -> dict | None:

        if len(prices) < 5:
            return None

        p = prices[-5:]

        first = p[0]
        valley = p[1]
        high1 = p[2]
        valley2 = p[3]
        high2 = p[4]

        if not (
            high1 > first and high1 > valley and high1 > valley2 and high1 >= high2
        ):
            return None

        if not self._same_level(
            high1,
            high2,
            tolerance_pct=6.0,
        ):
            return None

        if valley2 >= min(
            high1,
            high2,
        ):
            return None

        return {
            "pattern": "DOUBLE_TOP",
            "confidence": self._confidence(
                85.0,
            ),
            "entry": high2,
        }

    # ========================================================
    # DOUBLE BOTTOM
    # ========================================================

    def _detect_double_bottom(
        self,
        prices: List[float],
    ) -> dict | None:

        if len(prices) < 5:
            return None

        p = prices[-5:]

        first = p[0]
        high1 = p[1]
        low1 = p[2]
        high2 = p[3]
        low2 = p[4]

        if not (low1 < first and low1 < high1 and low1 < high2 and low1 <= low2):
            return None

        if not self._same_level(
            low1,
            low2,
            tolerance_pct=6.0,
        ):
            return None

        if high2 <= max(
            low1,
            low2,
        ):
            return None

        return {
            "pattern": "DOUBLE_BOTTOM",
            "confidence": self._confidence(
                85.0,
            ),
            "entry": low2,
        }

    # ========================================================
    # W PATTERN
    # ========================================================

    def _detect_w(
        self,
        prices: List[float],
    ) -> dict | None:

        if len(prices) < 5:
            return None

        p = prices[-5:]

        a = p[0]
        b = p[1]
        c = p[2]
        d = p[3]
        e = p[4]

        if not (c < b and c < d and e < d):
            return None

        if not self._same_level(
            c,
            e,
            tolerance_pct=6.0,
        ):
            return None

        if d <= max(
            c,
            e,
        ):
            return None

        if b <= a:
            return None

        return {
            "pattern": "W",
            "confidence": self._confidence(
                84.0,
            ),
            "entry": e,
        }

    def _detect_w_pattern(
        self,
        prices: List[float],
    ) -> dict | None:

        return self._detect_w(
            prices,
        )

    # ========================================================
    # M PATTERN
    # ========================================================

    def _detect_m(
        self,
        prices: List[float],
    ) -> dict | None:

        if len(prices) < 5:
            return None

        p = prices[-5:]

        a = p[0]
        b = p[1]
        c = p[2]
        d = p[3]
        e = p[4]

        if not (c > b and c > d and e <= d):
            return None

        # Second peak must be reasonably close.
        if not self._same_level(
            c,
            e,
            tolerance_pct=6.0,
        ):
            return None

        if d >= min(
            c,
            e,
        ):
            return None

        return {
            "pattern": "M",
            "confidence": self._confidence(
                84.0,
            ),
            "entry": e,
        }

    def _detect_m_pattern(
        self,
        prices: List[float],
    ) -> dict | None:

        return self._detect_m(
            prices,
        )

    # ========================================================
    # V REVERSAL
    # ========================================================

    def _detect_v_reversal(
        self,
        prices: List[float],
    ) -> dict | None:

        if len(prices) < 3:
            return None

        p = prices[-3:]

        first = p[0]
        middle = p[1]
        last = p[2]

        if middle < first and last > middle and last > first:
            return {
                "pattern": "V_REVERSAL",
                "confidence": self._confidence(
                    80.0,
                ),
                "entry": last,
            }

        if middle > first and last < middle and last < first:
            return {
                "pattern": "V_REVERSAL",
                "confidence": self._confidence(
                    80.0,
                ),
                "entry": last,
            }

        return None

    # ========================================================
    # BREAKOUT
    # ========================================================

    def _detect_breakout(
        self,
        prices: List[float],
    ) -> dict | None:

        if len(prices) < 3:
            return None

        previous = prices[-3:-1]
        last = prices[-1]

        resistance = max(
            previous,
        )

        support = min(
            previous,
        )

        if last > resistance:
            return {
                "pattern": "BREAKOUT",
                "confidence": self._confidence(
                    82.0,
                ),
                "entry": last,
            }

        if last < support:
            return {
                "pattern": "BREAKOUT",
                "confidence": self._confidence(
                    82.0,
                ),
                "entry": last,
            }

        return None

    # ========================================================
    # FAKE BREAKOUT
    # ========================================================

    def _detect_fake_breakout(
        self,
        prices: List[float],
    ) -> dict | None:

        if len(prices) < 3:
            return None

        first = prices[-3]
        breakout = prices[-2]
        last = prices[-1]

        if breakout > first and last < breakout and last > first:
            return {
                "pattern": "FAKE_BREAKOUT",
                "confidence": self._confidence(
                    78.0,
                ),
                "entry": last,
            }

        if breakout < first and last > breakout and last < first:
            return {
                "pattern": "FAKE_BREAKOUT",
                "confidence": self._confidence(
                    78.0,
                ),
                "entry": last,
            }

        return None

    # ========================================================
    # EXHAUSTION
    # ========================================================

    def _detect_exhaustion(
        self,
        prices: List[float],
    ) -> dict | None:

        if len(prices) < 3:
            return None

        first = prices[-3]
        second = prices[-2]
        last = prices[-1]

        move1 = abs(
            second - first,
        )

        move2 = abs(
            last - second,
        )

        if move1 <= 0:
            return None

        if move2 <= move1 * 0.20:
            return {
                "pattern": "EXHAUSTION",
                "confidence": self._confidence(
                    75.0,
                ),
                "entry": last,
            }

        return None

    # ========================================================
    # RESET
    # ========================================================

    def reset(self) -> None:
        self.last_pattern = None
        self.last_confidence = 0.0

    def clear(self) -> None:
        self.reset()

    # ========================================================
    # STATUS
    # ========================================================

    def status(self) -> dict:
        return {
            "engine": self.name,
            "last_pattern": self.last_pattern,
            "last_confidence": self.last_confidence,
        }
