from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional

from .engine import BaseEngine
from .swing_detection import SwingDetectionEngine
from aimios.market.candle_buffer import Candle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PatternSignal:
    pattern: str
    direction: str
    confidence: float
    entry: float
    stoploss: float
    target: float
    timestamp: Any
    symbol: str


@dataclass(frozen=True)
class PatternCandidate:
    pattern: str
    score: float
    confidence: float
    reason: str


class PatternRecognitionEngine(BaseEngine):
    name = "PatternRecognition"

    def __init__(self, app: Any = None, swing_engine: Optional[SwingDetectionEngine] = None, **_: Any) -> None:
        super().__init__(app)
        self.swing_engine = swing_engine or SwingDetectionEngine(app)

    def start(self) -> None:
        super().start()
        logger.info("Pattern recognition engine started")

    def stop(self) -> None:
        super().stop()
        logger.info("Pattern recognition engine stopped")

    def detect_from_candles(self, candles: List[Candle], symbol: str = "") -> List[PatternSignal]:
        if len(candles) < 3:
            return []

        last = candles[-1]
        candidates = self._collect_candidates(candles)
        if not candidates:
            return []

        ranked = sorted(candidates, key=lambda item: (item.score, item.confidence), reverse=True)
        selected = self._select_best_candidate(ranked)
        confidence = self._apply_confidence_modifiers(selected, ranked)

        return [
            PatternSignal(
                pattern="W_PATTERN" if selected.pattern == "W" else selected.pattern,
                direction=self._direction_for_pattern(selected.pattern),
                confidence=confidence,
                entry=last.close,
                stoploss=min(last.low, last.close),
                target=last.close + max(last.high - last.low, 1.0),
                timestamp=last.timestamp,
                symbol=symbol,
            )
        ]

    def _collect_candidates(self, candles: List[Candle]) -> List[PatternCandidate]:
        detectors = [
            self._detect_w_pattern,
            self._detect_double_bottom,
            self._detect_double_top,
            self._detect_m_pattern,
            self._detect_v_reversal,
            self._detect_choch,
            self._detect_breakout,
        ]
        candidates: List[PatternCandidate] = []
        for detector in detectors:
            candidate = detector(candles)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _select_best_candidate(self, ranked: List[PatternCandidate]) -> PatternCandidate:
        selected = ranked[0]
        if selected.pattern == "CHOCH":
            reversal = next((item for item in ranked if item.pattern in {"W", "DOUBLE_BOTTOM", "DOUBLE_TOP", "M", "V_REVERSAL"}), None)
            if reversal is not None:
                return reversal
        if selected.pattern == "BREAKOUT":
            reversal = next((item for item in ranked if item.pattern in {"W", "DOUBLE_BOTTOM", "DOUBLE_TOP", "M", "V_REVERSAL"}), None)
            if reversal is not None:
                return reversal
        return selected

    def _apply_confidence_modifiers(self, selected: PatternCandidate, candidates: List[PatternCandidate]) -> float:
        confidence = min(100.0, max(0.0, selected.confidence))
        has_reversal = any(item.pattern in {"W", "DOUBLE_BOTTOM", "DOUBLE_TOP", "M", "V_REVERSAL"} for item in candidates)
        has_choch = any(item.pattern == "CHOCH" for item in candidates)
        has_breakout = any(item.pattern == "BREAKOUT" for item in candidates)

        if selected.pattern in {"W", "DOUBLE_BOTTOM", "DOUBLE_TOP", "M", "V_REVERSAL"} and has_reversal:
            if has_choch:
                confidence = min(100.0, confidence + 5.0)
            if has_breakout:
                confidence = min(100.0, confidence + 3.0)
        elif selected.pattern == "CHOCH":
            if has_breakout:
                confidence = min(100.0, confidence + 3.0)

        return round(confidence, 2)

    def _direction_for_pattern(self, pattern: str) -> str:
        if pattern in {"DOUBLE_BOTTOM", "W", "V_REVERSAL"}:
            return "BUY"
        if pattern in {"DOUBLE_TOP", "M"}:
            return "SELL"
        return "SELL"

    def _detect_w_pattern(self, candles: List[Candle]) -> Optional[PatternCandidate]:
        if len(candles) < 5:
            return None

        prices = [c.close for c in candles[-5:]]
        minima: List[float] = []
        for index in range(1, len(prices) - 1):
            if prices[index] <= prices[index - 1] and prices[index] <= prices[index + 1]:
                minima.append(prices[index])

        if len(minima) >= 2:
            trough_a = minima[-2]
            trough_b = minima[-1]
            if prices[-1] >= max(trough_a, trough_b) and abs(trough_a - trough_b) / max(max(trough_a, trough_b), 1.0) > 0.03:
                return PatternCandidate(
                    pattern="W",
                    score=100.0,
                    confidence=88.0,
                    reason="Two distinct troughs with a recovery into the final close",
                )
        return None

    def _detect_double_bottom(self, candles: List[Candle]) -> Optional[PatternCandidate]:
        if len(candles) < 5:
            return None

        prices = [c.close for c in candles[-5:]]
        minima: List[float] = []
        for index in range(1, len(prices) - 1):
            if prices[index] <= prices[index - 1] and prices[index] <= prices[index + 1]:
                minima.append(prices[index])

        if len(minima) >= 2:
            trough_a = minima[-2]
            trough_b = minima[-1]
            if abs(trough_a - trough_b) / max(max(trough_a, trough_b), 1.0) <= 0.03 and prices[-1] >= max(trough_a, trough_b):
                return PatternCandidate(
                    pattern="DOUBLE_BOTTOM",
                    score=95.0,
                    confidence=86.0,
                    reason="Near-equal troughs with a successful breakout above the neckline",
                )
        if len(prices) >= 4 and prices[-1] > min(prices[:-1]) and prices[-1] < max(prices[:-1]):
            return PatternCandidate(
                pattern="DOUBLE_BOTTOM",
                score=90.0,
                confidence=82.0,
                reason="Bullish recovery from a swing low",
            )
        return None

    def _detect_double_top(self, candles: List[Candle]) -> Optional[PatternCandidate]:
        if len(candles) < 5:
            return None

        prices = [c.close for c in candles[-5:]]
        maxima: List[float] = []
        for index in range(1, len(prices) - 1):
            if prices[index] >= prices[index - 1] and prices[index] >= prices[index + 1]:
                maxima.append(prices[index])

        if len(maxima) >= 2:
            peak_a = maxima[-2]
            peak_b = maxima[-1]
            if abs(peak_a - peak_b) / max(max(peak_a, peak_b), 1.0) <= 0.03 and prices[-1] <= min(peak_a, peak_b):
                return PatternCandidate(
                    pattern="DOUBLE_TOP",
                    score=90.0,
                    confidence=84.0,
                    reason="Near-equal swing highs followed by a move below the neckline",
                )
        return None

    def _detect_m_pattern(self, candles: List[Candle]) -> Optional[PatternCandidate]:
        if len(candles) < 5:
            return None

        prices = [c.close for c in candles[-5:]]
        maxima: List[float] = []
        for index in range(1, len(prices) - 1):
            if prices[index] >= prices[index - 1] and prices[index] >= prices[index + 1]:
                maxima.append(prices[index])

        if len(maxima) >= 2:
            peak_a = maxima[-2]
            peak_b = maxima[-1]
            if abs(peak_a - peak_b) / max(max(peak_a, peak_b), 1.0) <= 0.03 and prices[-1] <= min(peak_a, peak_b):
                return PatternCandidate(
                    pattern="M",
                    score=88.0,
                    confidence=80.0,
                    reason="M-shaped structure with a close below the second swing high",
                )
        return None

    def _detect_v_reversal(self, candles: List[Candle]) -> Optional[PatternCandidate]:
        if len(candles) < 4:
            return None

        prices = [c.close for c in candles[-4:]]
        if prices[0] > prices[1] and prices[-1] >= prices[0] * 0.95 and prices[-1] >= prices[-2]:
            return PatternCandidate(
                pattern="V_REVERSAL",
                score=82.0,
                confidence=75.0,
                reason="Sharp reversal after a bearish pivot",
            )
        return None

    def _detect_choch(self, candles: List[Candle]) -> Optional[PatternCandidate]:
        if len(candles) < 2:
            return None

        last = candles[-1]
        prev = candles[-2]
        if last.close < prev.low and last.close < prev.close:
            return PatternCandidate(
                pattern="CHOCH",
                score=70.0,
                confidence=70.0,
                reason="Close below the previous swing low",
            )
        return None

    def _detect_breakout(self, candles: List[Candle]) -> Optional[PatternCandidate]:
        if len(candles) < 4:
            return None

        prices = [c.close for c in candles[-4:]]
        if prices[-1] >= prices[-2] and prices[-2] >= prices[-3] and prices[-1] > max(prices[:-1]):
            return PatternCandidate(
                pattern="BREAKOUT",
                score=60.0,
                confidence=65.0,
                reason="Momentum break above the preceding trend",
            )
        return None
