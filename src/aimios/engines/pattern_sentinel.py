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
        CandleBuffer
              ↓
        PatternSentinel
              ↓
        MWPatternEngine
              ↓
        BUY / SELL alert

    IMPORTANT
    ---------
    Only completed candles must be supplied to process_candle().

    M RULES
    -------
        HIGH1
           |
           | >= 0.13%
           |
        VALLEY
           |
           | recovery
           |
        HIGH2

        HIGH1 -> VALLEY >= 0.13%
        HIGH1 vs HIGH2 <= 0.03%
        HIGH1 -> HIGH2 >= 7 candles

    W RULES
    -------
        VALLEY1
           |
           | >= 0.13%
           |
          HIGH
           |
           | pullback
           |
        VALLEY2

        VALLEY1 -> HIGH >= 0.13%
        VALLEY1 vs VALLEY2 <= 0.03%
        VALLEY1 -> VALLEY2 >= 7 candles
    """

    name = "PatternSentinel"

    def __init__(
        self,
        app=None,
        alert_callback: Optional[Callable[[dict], None]] = None,
        **kwargs,
    ) -> None:

        super().__init__(app)

        # ------------------------------------------------------
        # M/W ENGINE
        # ------------------------------------------------------

        self.engine = MWPatternEngine()

        # ------------------------------------------------------
        # ALERT CALLBACK
        # ------------------------------------------------------

        self.alert_callback = alert_callback

        # ------------------------------------------------------
        # DUPLICATE PROTECTION
        #
        # Keep the complete structural identity of the last
        # alert instead of only the latest candle.
        # ------------------------------------------------------

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

        candles:
            Completed candles only.

        candle:
            The newly completed candle.

        Returns:
            Alert payload if a NEW M/W pattern is detected.
            None otherwise.
        """

        if not self._started:
            return None

        # ------------------------------------------------------
        # Need enough completed candles.
        #
        # Minimum outer-pivot distance is 7 candles.
        # ------------------------------------------------------

        if len(candles) < 8:
            return None

        # ------------------------------------------------------
        # Make sure the supplied candle is actually the latest
        # completed candle.
        # ------------------------------------------------------

        if not candles:
            return None

        latest_candle = candles[-1]

        if getattr(
            latest_candle,
            "candle_id",
            None,
        ) != getattr(
            candle,
            "candle_id",
            None,
        ):
            logger.warning(
                "PatternSentinel received candle that is "
                "not the latest completed candle | "
                "symbol=%s | candle_id=%s | latest_id=%s",
                symbol,
                getattr(candle, "candle_id", None),
                getattr(latest_candle, "candle_id", None),
            )

            return None

        # ======================================================
        # RUN M/W ENGINE
        # ======================================================

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

        # ======================================================
        # BUILD ALERT
        # ======================================================

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
                "Duplicate M/W signal ignored | " "symbol=%s | key=%s",
                symbol,
                alert_key,
            )

            return None

        # ------------------------------------------------------
        # Save new alert key.
        # ------------------------------------------------------

        self._last_alert_key = alert_key

        # ======================================================
        # LOG ALERT
        # ======================================================

        logger.warning(
            "MW ALERT | %s | "
            "pattern=%s | "
            "direction=%s | "
            "confidence=%.1f | "
            "entry=%s | "
            "stoploss=%s | "
            "target=%s | "
            "high1=%s | "
            "valley=%s | "
            "high2=%s | "
            "valley1=%s | "
            "high=%s | "
            "valley2=%s",
            symbol,
            payload["pattern"],
            payload["direction"],
            payload["confidence"],
            payload.get("entry"),
            payload.get("stoploss"),
            payload.get("target"),
            payload.get("high1"),
            payload.get("valley"),
            payload.get("high2"),
            payload.get("valley1"),
            payload.get("high"),
            payload.get("valley2"),
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
        """
        Convert MWPatternSignal into AIMIOS alert payload.
        """

        pattern = str(
            getattr(
                signal,
                "pattern",
                "",
            )
        ).upper()

        # ------------------------------------------------------
        # Only M/W patterns are accepted.
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

        confidence = self._safe_float(
            getattr(
                signal,
                "confidence",
                0.0,
            ),
            default=0.0,
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
        # STRUCTURE
        # ======================================================

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

        valley1 = getattr(
            signal,
            "valley1",
            None,
        )

        high = getattr(
            signal,
            "high",
            None,
        )

        valley2 = getattr(
            signal,
            "valley2",
            None,
        )

        # ======================================================
        # METRICS
        # ======================================================

        reversal_pct = getattr(
            signal,
            "reversal_pct",
            None,
        )

        swing_distance_pct = getattr(
            signal,
            "swing_distance_pct",
            None,
        )

        candle_distance = getattr(
            signal,
            "candle_distance",
            None,
        )

        pivot_distance = getattr(
            signal,
            "pivot_distance",
            None,
        )

        first_pivot_candle_id = getattr(
            signal,
            "first_pivot_candle_id",
            None,
        )

        second_pivot_candle_id = getattr(
            signal,
            "second_pivot_candle_id",
            None,
        )

        # ======================================================
        # RETURN PAYLOAD
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
            "candle_id": getattr(
                candle,
                "candle_id",
                None,
            ),
            # --------------------------------------------------
            # M STRUCTURE
            # --------------------------------------------------
            "high1": high1,
            "valley": valley,
            "high2": high2,
            # --------------------------------------------------
            # W STRUCTURE
            # --------------------------------------------------
            "valley1": valley1,
            "high": high,
            "valley2": valley2,
            # --------------------------------------------------
            # STRUCTURE METRICS
            # --------------------------------------------------
            "reversal_pct": reversal_pct,
            "swing_distance_pct": swing_distance_pct,
            "candle_distance": candle_distance,
            "pivot_distance": pivot_distance,
            "first_pivot_candle_id": first_pivot_candle_id,
            "second_pivot_candle_id": second_pivot_candle_id,
        }

    # ==========================================================
    # ALERT KEY
    # ==========================================================

    def _make_alert_key(
        self,
        payload: dict,
    ) -> str:
        """
        Build a structural alert key.

        IMPORTANT:

        candle_id alone is NOT sufficient.

        A pattern can remain the same while several candles
        arrive after the pattern has formed.

        Therefore the two outer pivot candle IDs are used.

        M:
            HIGH1 candle + HIGH2 candle

        W:
            VALLEY1 candle + VALLEY2 candle
        """

        symbol = str(
            payload.get(
                "symbol",
                "",
            )
        )

        pattern = str(
            payload.get(
                "pattern",
                "",
            )
        )

        direction = str(
            payload.get(
                "direction",
                "",
            )
        )

        first_pivot = payload.get("first_pivot_candle_id")

        second_pivot = payload.get("second_pivot_candle_id")

        # ------------------------------------------------------
        # Fallback for any legacy signal that does not expose
        # pivot candle IDs.
        # ------------------------------------------------------

        if first_pivot is None:

            first_pivot = payload.get(
                "candle_id",
                "",
            )

        if second_pivot is None:

            second_pivot = payload.get(
                "candle_id",
                "",
            )

        return "|".join(
            [
                symbol,
                pattern,
                direction,
                str(first_pivot),
                str(second_pivot),
            ]
        )

    # ==========================================================
    # SAFE FLOAT
    # ==========================================================

    @staticmethod
    def _safe_float(
        value,
        default: float = 0.0,
    ) -> float:

        try:

            return float(value)

        except (
            TypeError,
            ValueError,
        ):

            return default

    # ==========================================================
    # RESET
    # ==========================================================

    def clear(self) -> None:
        """
        Reset Sentinel and underlying M/W engine.
        """

        try:

            self.engine.reset()

        except Exception:

            logger.exception("Failed to reset MWPatternEngine")

            try:
                self.engine.clear()
            except Exception:
                pass

        self._last_alert_key = None

        logger.info("Pattern Sentinel reset")
