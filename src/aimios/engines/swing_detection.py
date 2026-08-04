from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional

from .engine import BaseEngine
from aimios.market.candle_buffer import Candle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SwingPoint:
    timestamp: Any
    price: float
    kind: str
    strength: float
    candle_id: Optional[int] = None


class SwingDetectionEngine(BaseEngine):
    name = "SwingDetection"

    def __init__(self, app: Any = None, **_: Any) -> None:
        super().__init__(app)
        self.last_swing: Optional[SwingPoint] = None

    def start(self) -> None:
        super().start()
        logger.info("Swing detection engine started")

    def stop(self) -> None:
        super().stop()
        logger.info("Swing detection engine stopped")

    def process_candles(self, candles: List[Candle]) -> List[SwingPoint]:
        if len(candles) < 3:
            return []

        swings: List[SwingPoint] = []
        for index in range(1, len(candles) - 1):
            prev = candles[index - 1]
            current = candles[index]
            nxt = candles[index + 1]

            if current.high >= prev.high and current.high >= nxt.high:
                swings.append(
                    SwingPoint(
                        timestamp=current.timestamp,
                        price=current.high,
                        kind="high",
                        strength=max(current.high - min(prev.high, nxt.high), 0.0),
                        candle_id=current.candle_id,
                    )
                )
            if current.low <= prev.low and current.low <= nxt.low:
                swings.append(
                    SwingPoint(
                        timestamp=current.timestamp,
                        price=current.low,
                        kind="low",
                        strength=max(max(prev.low, nxt.low) - current.low, 0.0),
                        candle_id=current.candle_id,
                    )
                )

        if not swings and len(candles) >= 2:
            first = candles[0]
            last = candles[-1]
            swings.append(SwingPoint(first.timestamp, first.low, "low", max(first.high - first.low, 0.0), first.candle_id))
            swings.append(SwingPoint(last.timestamp, last.high, "high", max(last.high - last.low, 0.0), last.candle_id))

        self.last_swing = swings[-1] if swings else None
        return swings
