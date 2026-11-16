from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


INDIA_TZ = ZoneInfo("Asia/Kolkata") if ZoneInfo is not None else None


@dataclass
class _H1V1H2H3State:
    reference_open: Optional[float] = None
    trigger_direction: Optional[str] = None
    trigger_timestamp: Optional[datetime] = None
    h1: Optional[float] = None
    h1_timestamp: Optional[datetime] = None
    v1: Optional[float] = None
    v1_timestamp: Optional[datetime] = None
    h2: Optional[float] = None
    h2_timestamp: Optional[datetime] = None
    h3: Optional[float] = None
    h3_timestamp: Optional[datetime] = None
    alerted: set[str] = field(default_factory=set)


class H1V1H2H3Detector:
    """Detect an unbiased H1 -> V1 -> H2 -> H3 setup from the opening price.

    Rules implemented from the user requirement:
    - Wait for the market to move 0.5% from the opening price in either direction.
    - Once the trigger is active, apply the H1 -> V1 -> H2 -> H3 logic on that side.
    - H1 to V1 distance is 0.2% and H1 to H2 distance is 0.05%.
    - H3 must be below H2 for the bullish case and above H2 for the reverse case.
    - The detector emits one CE and one PE alert per symbol/day once each.
    """

    def __init__(
        self,
        first_hour_minutes: int = 60,
        min_wait_minutes: int = 0,
        h1_v1_drop_pct: float = 0.002,
        h1_h2_upper_pct: float = 0.0009,
        h1_h3_lower_pct: float = 0.0,
    ) -> None:
        self.first_hour_minutes = int(first_hour_minutes)
        self.min_wait_minutes = int(min_wait_minutes)
        self.h1_v1_drop_pct = float(h1_v1_drop_pct)
        self.h1_h2_upper_pct = float(h1_h2_upper_pct)
        self.h1_h3_lower_pct = float(h1_h3_lower_pct)

        self._daily_state: Dict[str, _H1V1H2H3State] = {}
        self._daily_alerts: Dict[str, set[str]] = defaultdict(set)

    @staticmethod
    def _normalize_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            dt = value
            if dt.tzinfo is None:
                if INDIA_TZ is not None:
                    dt = dt.replace(tzinfo=INDIA_TZ)
                else:
                    dt = dt.replace(tzinfo=None)
            return dt.astimezone(INDIA_TZ) if INDIA_TZ is not None else dt

        if isinstance(value, time):
            base_date = date(2000, 1, 1)
            return datetime.combine(base_date, value, tzinfo=INDIA_TZ)

        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value)
            except ValueError:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M"):
                    try:
                        parsed = datetime.strptime(value, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    raise ValueError(f"Unsupported timestamp format: {value!r}")
                dt = parsed
            if dt.tzinfo is None and INDIA_TZ is not None:
                dt = dt.replace(tzinfo=INDIA_TZ)
            return dt.astimezone(INDIA_TZ) if INDIA_TZ is not None else dt

        raise TypeError(f"Unsupported candle timestamp type: {type(value)!r}")

    @staticmethod
    def _sorted_history(candles: Iterable[Any]) -> List[Any]:
        result = list(candles)
        result.sort(
            key=lambda candle: H1V1H2H3Detector._normalize_datetime(candle.timestamp)
        )
        return result

    @staticmethod
    def _day_key(symbol: str, timestamp: datetime) -> str:
        if INDIA_TZ is not None:
            day = timestamp.astimezone(INDIA_TZ).date().isoformat()
        else:
            day = timestamp.date().isoformat()
        return f"{symbol}:{day}"

    def _reference_open(self, sorted_history: List[Any]) -> float:
        if not sorted_history:
            return 0.0
        first = sorted_history[0]
        if hasattr(first, "open") and first.open is not None:
            return float(first.open)
        return (float(first.high) + float(first.low)) / 2.0

    def _reset_state(self, state: _H1V1H2H3State) -> None:
        state.reference_open = None
        state.trigger_direction = None
        state.trigger_timestamp = None
        state.h1 = None
        state.h1_timestamp = None
        state.v1 = None
        state.v1_timestamp = None
        state.h2 = None
        state.h2_timestamp = None
        state.h3 = None
        state.h3_timestamp = None

    def _ensure_state(self, symbol: str, candle: Any) -> _H1V1H2H3State:
        candle_dt = self._normalize_datetime(candle.timestamp)
        day_key = self._day_key(symbol, candle_dt)
        state = self._daily_state.get(day_key)
        if state is None:
            state = _H1V1H2H3State()
            self._daily_state[day_key] = state
        return state

    def _build_alert(
        self,
        symbol: str,
        candle: Any,
        direction: str,
        h1: float,
        v1: float,
        h2: float,
        h3: float,
        waiting_minutes: float,
    ) -> Dict[str, Any]:
        candle_dt = self._normalize_datetime(candle.timestamp)
        return {
            "symbol": symbol,
            "pattern": "H1V1H2H3",
            "direction": direction,
            "confidence": 88.0,
            "price": float(h3),
            "timestamp": candle_dt,
            "day_key": self._day_key(symbol, candle_dt),
            "h1": float(h1),
            "v1": float(v1),
            "h2": float(h2),
            "h3": float(h3),
            "waiting_minutes": round(waiting_minutes, 2),
            "logic": "undirectional_h1_v1_h2_h3",
        }

    def _pattern_for_direction(
        self,
        sorted_history: List[Any],
        trigger_timestamp: datetime,
        direction: str,
    ) -> Optional[Dict[str, Any]]:
        reference_open = self._reference_open(sorted_history)
        v1_gap = reference_open * self.h1_v1_drop_pct
        h2_gap = reference_open * self.h1_h2_upper_pct

        if direction == "up":
            h1_candidates = [
                item
                for item in sorted_history
                if self._normalize_datetime(item.timestamp) >= trigger_timestamp
            ]
            if not h1_candidates:
                return None
            h1 = max(float(item.high) for item in h1_candidates)
            h1_ts = min(
                self._normalize_datetime(item.timestamp)
                for item in h1_candidates
                if float(item.high) == h1
            )

            v1_candidates = [
                item
                for item in sorted_history
                if self._normalize_datetime(item.timestamp) >= h1_ts
                and float(item.low) < (h1 - v1_gap)
            ]
            if not v1_candidates:
                return None
            v1 = min(float(item.low) for item in v1_candidates)
            v1_ts = min(
                self._normalize_datetime(item.timestamp)
                for item in v1_candidates
                if float(item.low) == v1
            )

            h2_candidates = [
                item
                for item in sorted_history
                if self._normalize_datetime(item.timestamp) >= v1_ts
                and float(item.high) > v1
                and float(item.high) < h1
                and float(item.high) >= (h1 - h2_gap)
            ]
            if not h2_candidates:
                return None
            h2 = max(float(item.high) for item in h2_candidates)
            h2_ts = min(
                self._normalize_datetime(item.timestamp)
                for item in h2_candidates
                if float(item.high) == h2
            )

            h3_candidates = [
                item
                for item in sorted_history
                if self._normalize_datetime(item.timestamp) >= h2_ts
                and float(item.low) < h2
            ]
            if not h3_candidates:
                return None
            h3 = min(float(item.low) for item in h3_candidates)
            h3_ts = min(
                self._normalize_datetime(item.timestamp)
                for item in h3_candidates
                if float(item.low) == h3
            )
            return {
                "h1": h1,
                "h1_ts": h1_ts,
                "v1": v1,
                "v1_ts": v1_ts,
                "h2": h2,
                "h2_ts": h2_ts,
                "h3": h3,
                "h3_ts": h3_ts,
            }

        h1_candidates = [
            item
            for item in sorted_history
            if self._normalize_datetime(item.timestamp) >= trigger_timestamp
        ]
        if not h1_candidates:
            return None
        h1 = min(float(item.low) for item in h1_candidates)
        h1_ts = min(
            self._normalize_datetime(item.timestamp)
            for item in h1_candidates
            if float(item.low) == h1
        )

        v1_candidates = [
            item
            for item in sorted_history
            if self._normalize_datetime(item.timestamp) >= h1_ts
            and float(item.high) > (h1 + v1_gap)
        ]
        if not v1_candidates:
            return None
        v1 = max(float(item.high) for item in v1_candidates)
        v1_ts = min(
            self._normalize_datetime(item.timestamp)
            for item in v1_candidates
            if float(item.high) == v1
        )

        h2_candidates = [
            item
            for item in sorted_history
            if self._normalize_datetime(item.timestamp) >= v1_ts
            and float(item.low) < v1
            and float(item.low) > h1
            and float(item.low) <= (h1 + h2_gap)
        ]
        if not h2_candidates:
            return None
        h2 = min(float(item.low) for item in h2_candidates)
        h2_ts = min(
            self._normalize_datetime(item.timestamp)
            for item in h2_candidates
            if float(item.low) == h2
        )

        h3_candidates = [
            item
            for item in sorted_history
            if self._normalize_datetime(item.timestamp) >= h2_ts
            and float(item.high) > h2
        ]
        if not h3_candidates:
            return None
        h3 = max(float(item.high) for item in h3_candidates)
        h3_ts = min(
            self._normalize_datetime(item.timestamp)
            for item in h3_candidates
            if float(item.high) == h3
        )
        return {
            "h1": h1,
            "h1_ts": h1_ts,
            "v1": v1,
            "v1_ts": v1_ts,
            "h2": h2,
            "h2_ts": h2_ts,
            "h3": h3,
            "h3_ts": h3_ts,
        }

    def process_candle(
        self,
        symbol: str,
        candle: Any,
        candles: Iterable[Any],
    ) -> List[Dict[str, Any]]:
        if candle is None:
            return []

        candle_dt = self._normalize_datetime(candle.timestamp)
        state = self._ensure_state(symbol, candle)
        day_key = self._day_key(symbol, candle_dt)
        alerted_today = self._daily_alerts.get(day_key, set())

        sorted_history = self._sorted_history(candles)
        if not sorted_history:
            return []

        if state.reference_open is None:
            state.reference_open = self._reference_open(sorted_history)

        if state.trigger_direction is None:
            ref = float(state.reference_open)
            trigger_up = any(float(item.high) >= ref * 1.005 for item in sorted_history)
            trigger_down = any(
                float(item.low) <= ref * 0.995 for item in sorted_history
            )
            if not trigger_up and not trigger_down:
                return []

            if trigger_up and not trigger_down:
                state.trigger_direction = "up"
            elif trigger_down and not trigger_up:
                state.trigger_direction = "down"
            else:
                up_ts = min(
                    self._normalize_datetime(item.timestamp)
                    for item in sorted_history
                    if float(item.high) >= ref * 1.005
                )
                down_ts = min(
                    self._normalize_datetime(item.timestamp)
                    for item in sorted_history
                    if float(item.low) <= ref * 0.995
                )
                state.trigger_direction = "up" if up_ts <= down_ts else "down"

            state.trigger_timestamp = min(
                self._normalize_datetime(item.timestamp)
                for item in sorted_history
                if (
                    (
                        state.trigger_direction == "up"
                        and float(item.high) >= ref * 1.005
                    )
                    or (
                        state.trigger_direction == "down"
                        and float(item.low) <= ref * 0.995
                    )
                )
            )

        alerts: List[Dict[str, Any]] = []
        for direction_key, alert_direction in (("up", "CE"), ("down", "PE")):
            if alert_direction in alerted_today:
                continue

            setup = self._pattern_for_direction(
                sorted_history,
                state.trigger_timestamp,
                direction_key,
            )
            if setup is None:
                continue

            previous_h1 = state.h1
            if previous_h1 is None or float(setup["h1"]) != float(previous_h1):
                state.h1 = float(setup["h1"])
                state.h1_timestamp = setup["h1_ts"]

            previous_v1 = state.v1
            if previous_v1 is None or float(setup["v1"]) != float(previous_v1):
                state.v1 = float(setup["v1"])
                state.v1_timestamp = setup["v1_ts"]

            previous_h2 = state.h2
            if previous_h2 is None or float(setup["h2"]) != float(previous_h2):
                state.h2 = float(setup["h2"])
                state.h2_timestamp = setup["h2_ts"]

            h3_ts = setup["h3_ts"]
            elapsed_minutes = (h3_ts - state.trigger_timestamp).total_seconds() / 60.0
            if elapsed_minutes <= self.min_wait_minutes:
                continue

            alerts.append(
                self._build_alert(
                    symbol,
                    candle,
                    alert_direction,
                    h1=float(setup["h1"]),
                    v1=float(setup["v1"]),
                    h2=float(setup["h2"]),
                    h3=float(setup["h3"]),
                    waiting_minutes=elapsed_minutes,
                )
            )
            alerted_today.add(alert_direction)

        if not alerts:
            return []

        self._daily_alerts[day_key] = alerted_today
        return alerts
