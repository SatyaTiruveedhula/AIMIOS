from __future__ import annotations

import logging
from typing import Callable, List, Optional

from .engine import BaseEngine
from .mw_pattern_engine import MWPatternEngine
from aimios.market.candle_buffer import Candle

logger = logging.getLogger(__name__)


class PatternSentinel(BaseEngine):
    """
    Live M/W pattern watcher.

    Flow:

        completed Candle
              ↓
        PatternSentinel
              ↓
        MWPatternEngine
              ↓
        BUY / SELL alert

    IMPORTANT:
    Only completed candles should be supplied here.
    """

    name = "PatternSentinel"

    def __init__(
        self,
        app=None,
        alert_callback: Optional[Callable[[dict], None]] = None,
        **kwargs,
    ) -> None:

        super().__init__(app)

        self.engine = MWPatternEngine()

        self.alert_callback = alert_callback

        # Prevent duplicate alerts for the same completed
        # pattern structure.
        self._last_alert_key: Optional[str] = None

        self._started = False

    # ==========================================================
    # START
    # ==========================================================

    def start(self) -> None:

        if self._started:
            return

        super().start()

        self._started = True

        logger.info("Pattern Sentinel Started")

    # ==========================================================
    # STOP
    # ==========================================================

    def stop(self) -> None:

        if not self._started:
            return

        self._started = False

        super().stop()

        logger.info("Pattern Sentinel Stopped")

    # ==========================================================
    # PROCESS COMPLETED CANDLE
    # ==========================================================

    def process_candle(
        self,
        candle: Candle,
        candles: List[Candle],
        symbol: str,
    ) -> Optional[dict]:
        """
        Process one newly completed candle.

        `candles` must contain completed candles only.
        """

        if not self._started:
            return None

        # M/W detector requires enough candles.
        if len(candles) < 7:
            return None

        try:

            signal = self.engine.detect(
                candles,
                symbol=symbol,
            )

        except Exception:

            logger.exception(
                "MWPatternEngine failed for %s",
                symbol,
            )

            return None

        if signal is None:
            return None

        payload = self._build_alert(
            signal=signal,
            candle=candle,
            symbol=symbol,
        )

        if payload is None:
            return None

        # ======================================================
        # DUPLICATE PROTECTION
        # ======================================================

        alert_key = self._make_alert_key(
            payload,
        )

        if alert_key == self._last_alert_key:

            logger.debug(
                "Duplicate M/W signal ignored: %s",
                alert_key,
            )

            return None

        self._last_alert_key = alert_key

        # ======================================================
        # LOG ALERT
        # ======================================================

        logger.warning(
            "MW ALERT | %s | pattern=%s | direction=%s | "
            "confidence=%.1f | entry=%s | stoploss=%s | target=%s",
            symbol,
            payload["pattern"],
            payload["direction"],
            payload["confidence"],
            payload.get("entry"),
            payload.get("stoploss"),
            payload.get("target"),
        )

        # ======================================================
        # CALLBACK
        # ======================================================

        if self.alert_callback is not None:

            try:

                self.alert_callback(
                    payload,
                )

            except Exception:

                logger.exception("Pattern Sentinel alert callback failed")

        return payload

    # ==========================================================
    # BUILD ALERT
    # ==========================================================

    def _build_alert(
        self,
        signal,
        candle: Candle,
        symbol: str,
    ) -> Optional[dict]:

        pattern = str(
            getattr(
                signal,
                "pattern",
                "",
            )
        ).upper()

        # ------------------------------------------------------
        # Only M/W family belongs to this sentinel.
        # ------------------------------------------------------

        if pattern not in {
            "M",
            "W",
            "DOUBLE_TOP",
            "DOUBLE_BOTTOM",
        }:

            return None

        # ======================================================
        # DIRECTION
        # ======================================================

        if pattern in {
            "M",
            "DOUBLE_TOP",
        }:

            direction = "SELL"

        elif pattern in {
            "W",
            "DOUBLE_BOTTOM",
        }:

            direction = "BUY"

        else:

            return None

        # ======================================================
        # SIGNAL VALUES
        # ======================================================

        confidence = float(
            getattr(
                signal,
                "confidence",
                0.0,
            )
        )

        entry = getattr(
            signal,
            "entry",
            None,
        )

        if entry is None:
            entry = candle.close

        stoploss = getattr(
            signal,
            "stoploss",
            None,
        )

        target = getattr(
            signal,
            "target",
            None,
        )

        timestamp = getattr(
            signal,
            "timestamp",
            None,
        )

        if timestamp is None:
            timestamp = candle.timestamp

        # ======================================================
        # OPTIONAL STRUCTURE INFORMATION
        # ======================================================
        #
        # These fields are useful for debugging live behaviour.
        # If MWPatternEngine provides them, they will be included.
        # Otherwise they remain None.
        #

        high1 = getattr(
            signal,
            "high1",
            None,
        )

        valley = getattr(
            signal,
            "valley",
            None,
        )

        high2 = getattr(
            signal,
            "high2",
            None,
        )

        low1 = getattr(
            signal,
            "low1",
            None,
        )

        peak = getattr(
            signal,
            "peak",
            None,
        )

        low2 = getattr(
            signal,
            "low2",
            None,
        )

        # ======================================================
        # RETURN ALERT
        # ======================================================

        return {
            "engine": self.name,
            "symbol": symbol,
            "pattern": pattern,
            "direction": direction,
            "confidence": confidence,
            "entry": entry,
            "stoploss": stoploss,
            "target": target,
            "timestamp": timestamp,
            "candle_id": candle.candle_id,
            # M structure
            "high1": high1,
            "valley": valley,
            "high2": high2,
            # W structure
            "low1": low1,
            "peak": peak,
            "low2": low2,
        }

    # ==========================================================
    # ALERT KEY
    # ==========================================================

    def _make_alert_key(
        self,
        payload: dict,
    ) -> str:

        return "|".join(
            [
                str(
                    payload.get(
                        "symbol",
                    )
                ),
                str(
                    payload.get(
                        "pattern",
                    )
                ),
                str(
                    payload.get(
                        "direction",
                    )
                ),
                str(
                    payload.get(
                        "candle_id",
                    )
                ),
            ]
        )

    # ==========================================================
    # RESET
    # ==========================================================

    def clear(self) -> None:

        self.engine.clear()

        self._last_alert_key = None

        logger.info("Pattern Sentinel reset")
