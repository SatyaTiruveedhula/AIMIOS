from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from aimios.market.candle_buffer import Candle


class PatternDetector:
    """Minimal pattern detector for completed candle history."""

    def detect(self, candles: List[Candle]) -> Dict[str, object]:
        if pattern := self._detect_exhaustion(candles):
            return pattern
        if pattern := self._detect_double_top(candles):
            return pattern
        if pattern := self._detect_double_bottom(candles):
            return pattern
        if pattern := self._detect_w(candles):
            return pattern
        if pattern := self._detect_m(candles):
            return pattern
        if pattern := self._detect_v(candles):
            return pattern
        if pattern := self._detect_breakout(candles):
            return pattern
        if pattern := self._detect_fake_breakout(candles):
            return pattern
        return {}

    def _build_pattern(self, name: str, confidence: float, price: float) -> Dict[str, object]:
        return {
            "pattern": name,
            "confidence": round(min(max(confidence, 0.0), 100.0)),
            "price": round(price, 2),
            "time": datetime.now(timezone.utc).strftime("%H:%M"),
        }

    def _find_local_extrema(self, values: List[float]) -> tuple[List[float], List[float]]:
        maxima: List[float] = []
        minima: List[float] = []
        for index in range(1, len(values) - 1):
            if values[index] >= values[index - 1] and values[index] >= values[index + 1]:
                maxima.append(values[index])
            if values[index] <= values[index - 1] and values[index] <= values[index + 1]:
                minima.append(values[index])
        return maxima, minima

    def _detect_double_top(self, candles: List[Candle]) -> Optional[Dict[str, object]]:
        if len(candles) < 5:
            return None

        prices = [c.close for c in candles[-5:]]
        maxima, _ = self._find_local_extrema(prices)
        if len(maxima) >= 2:
            peak_a = maxima[-2]
            peak_b = maxima[-1]
            if abs(peak_a - peak_b) / max(max(peak_a, peak_b), 1.0) < 0.15 and prices[-1] <= min(peak_a, peak_b):
                strength = 1.0 - (min(peak_a, peak_b) / max(peak_a, peak_b))
                return self._build_pattern("DOUBLE_TOP", strength * 100.0, max(peak_a, peak_b))
        return None

    def _detect_double_bottom(self, candles: List[Candle]) -> Optional[Dict[str, object]]:
        if len(candles) < 5:
            return None

        prices = [c.close for c in candles[-5:]]
        _, minima = self._find_local_extrema(prices)
        if len(minima) >= 2:
            trough_a = minima[-2]
            trough_b = minima[-1]
            if abs(trough_a - trough_b) / max(max(trough_a, trough_b), 1.0) < 0.03 and prices[-1] >= max(trough_a, trough_b):
                strength = (max(trough_a, trough_b) - min(trough_a, trough_b)) / max(max(trough_a, trough_b), 1.0)
                return self._build_pattern("DOUBLE_BOTTOM", strength * 100.0, min(trough_a, trough_b))
        return None

    def _detect_v(self, candles: List[Candle]) -> Optional[Dict[str, object]]:
        if len(candles) < 4:
            return None

        values = [c.close for c in candles[-4:]]
        if len(values) >= 4 and values[0] > values[1] and values[-1] >= values[0] * 0.95 and values[-1] >= values[-2]:
            strength = (values[-1] - values[1]) / max(values[0], 1.0)
            return self._build_pattern("V_REVERSAL", strength * 100.0, values[-1])
        return None

    def _detect_w(self, candles: List[Candle]) -> Optional[Dict[str, object]]:
        if len(candles) < 5:
            return None

        prices = [c.close for c in candles[-5:]]
        _, minima = self._find_local_extrema(prices)
        if len(minima) >= 2:
            trough_a = minima[-2]
            trough_b = minima[-1]
            if abs(trough_a - trough_b) / max(max(trough_a, trough_b), 1.0) > 0.03 and prices[-1] >= max(trough_a, trough_b):
                strength = (prices[-1] - min(trough_a, trough_b)) / max(prices[-1], 1.0)
                return self._build_pattern("W", strength * 100.0, prices[-1])
        return None

    def _detect_m(self, candles: List[Candle]) -> Optional[Dict[str, object]]:
        if len(candles) < 5:
            return None

        prices = [c.close for c in candles[-5:]]
        maxima, _ = self._find_local_extrema(prices)
        if len(maxima) >= 2:
            peak_a = maxima[-2]
            peak_b = maxima[-1]
            if abs(peak_a - peak_b) / max(max(peak_a, peak_b), 1.0) < 0.15 and prices[-1] <= min(peak_a, peak_b):
                strength = (max(peak_a, peak_b) - prices[-1]) / max(max(peak_a, peak_b), 1.0)
                return self._build_pattern("M", strength * 100.0, prices[-1])
        return None

    def _detect_breakout(self, candles: List[Candle]) -> Optional[Dict[str, object]]:
        if len(candles) < 4:
            return None

        prices = [c.close for c in candles[-4:]]
        if prices[-1] >= prices[-2] and prices[-2] >= prices[-3] and prices[-1] >= max(prices[:-1]):
            return self._build_pattern("BREAKOUT", 80.0, prices[-1])
        return None

    def _detect_fake_breakout(self, candles: List[Candle]) -> Optional[Dict[str, object]]:
        if len(candles) < 4:
            return None

        prices = [c.close for c in candles[-4:]]
        if prices[-2] < prices[-3] and prices[-1] <= prices[-2]:
            return self._build_pattern("FAKE_BREAKOUT", 60.0, prices[-1])
        return None

    def _detect_exhaustion(self, candles: List[Candle]) -> Optional[Dict[str, object]]:
        if len(candles) < 4:
            return None

        prices = [c.close for c in candles[-4:]]
        if prices[-1] == prices[-2] and prices[-2] >= prices[-3] and prices[-3] > prices[-4]:
            return self._build_pattern("EXHAUSTION", 75.0, prices[-1])
        return None
