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
    """Detect the H1 -> V1 -> H2 > H3 pattern for the first hour high setup.

    Rules implemented from the user requirement:
    - H1 is the max high in the first 60 minutes.
    - V1 is a later low below 99.9% of H1.
    - H2 is a later high above V1 and below 99.975% of H1.
    - H3 is a later value below H2 but above 99.925% of H1.
    - The time gap between H1 and H3 must exceed 30 minutes.
    - If a new higher H1 is printed after the first hour, the entire state resets.
    - The detector emits one CE alert and one PE alert per symbol/day once each.
    """

    def __init__(
        self,
        first_hour_minutes: int = 60,
        min_wait_minutes: int = 30,
        h1_v1_drop_pct: float = 0.999,
        h1_h2_upper_pct: float = 0.99975,
        h1_h3_lower_pct: float = 0.99925,
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

    def _first_hour_window(self, candle: Any) -> tuple[datetime, datetime]:
        candle_dt = self._normalize_datetime(candle.timestamp)
        market_open = candle_dt.replace(hour=9, minute=15, second=0, microsecond=0)
        return market_open, market_open + timedelta(minutes=self.first_hour_minutes)

    def _reset_state(self, state: _H1V1H2H3State) -> None:
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

        market_open, first_hour_end = self._first_hour_window(candle)

        sorted_history = self._sorted_history(candles)

        if not sorted_history:
            return []

        first_hour_values = [
            item
            for item in sorted_history
            if market_open <= self._normalize_datetime(item.timestamp) < first_hour_end
        ]

        if first_hour_values:
            first_hour_high = max(float(item.high) for item in first_hour_values)
            candidate_h1_ts = min(
                self._normalize_datetime(item.timestamp)
                for item in first_hour_values
                if float(item.high) == first_hour_high
            )
            if state.h1 is None or first_hour_high > float(state.h1):
                self._reset_state(state)
                state.h1 = first_hour_high
                state.h1_timestamp = candidate_h1_ts

        if state.h1 is None:
            state.h1 = float(candle.high)
            state.h1_timestamp = candle_dt

        if candle_dt >= first_hour_end and candle.high > float(state.h1):
            self._reset_state(state)
            state.h1 = float(candle.high)
            state.h1_timestamp = candle_dt

        if state.h1 is None or state.h1_timestamp is None:
            return []

        state.v1 = None
        state.v1_timestamp = None
        for item in sorted_history:
            item_dt = self._normalize_datetime(item.timestamp)
            if item_dt <= state.h1_timestamp:
                continue
            if float(item.low) < float(state.h1) * self.h1_v1_drop_pct:
                state.v1 = float(item.low)
                state.v1_timestamp = item_dt
                break

        if state.v1 is None or state.v1_timestamp is None:
            return []

        state.h2 = None
        state.h2_timestamp = None
        candidate_h2 = None
        candidate_h2_ts = None
        for item in sorted_history:
            item_dt = self._normalize_datetime(item.timestamp)
            if item_dt <= state.v1_timestamp:
                continue
            high_value = float(item.high)
            if (
                high_value > float(state.v1)
                and high_value < float(state.h1) * self.h1_h2_upper_pct
            ):
                if candidate_h2 is None or high_value > candidate_h2:
                    candidate_h2 = high_value
                    candidate_h2_ts = item_dt

        if candidate_h2 is not None and candidate_h2_ts is not None:
            state.h2 = candidate_h2
            state.h2_timestamp = candidate_h2_ts

        if state.h2 is None or state.h2_timestamp is None:
            return []

        state.h3 = None
        state.h3_timestamp = None
        for item in sorted_history:
            item_dt = self._normalize_datetime(item.timestamp)
            if item_dt <= state.h2_timestamp:
                continue
            value = float(item.low)
            if value > float(state.h1) * self.h1_h3_lower_pct and value < float(
                state.h2
            ):
                state.h3 = value
                state.h3_timestamp = item_dt
                break

        if state.h3 is None or state.h3_timestamp is None:
            return []

        elapsed_minutes = (
            state.h3_timestamp - state.h1_timestamp
        ).total_seconds() / 60.0
        if elapsed_minutes <= self.min_wait_minutes:
            return []

        alerts: List[Dict[str, Any]] = []
        for direction in ("CE", "PE"):
            if direction in alerted_today:
                continue
            if direction == "CE":
                alert = self._build_alert(
                    symbol,
                    candle,
                    "CE",
                    h1=float(state.h1),
                    v1=float(state.v1),
                    h2=float(state.h2),
                    h3=float(state.h3),
                    waiting_minutes=elapsed_minutes,
                )
            else:
                alert = self._build_alert(
                    symbol,
                    candle,
                    "PE",
                    h1=float(state.h1),
                    v1=float(state.v1),
                    h2=float(state.h2),
                    h3=float(state.h3),
                    waiting_minutes=elapsed_minutes,
                )
            alerts.append(alert)
            alerted_today.add(direction)

        self._daily_alerts[day_key] = alerted_today
        return alerts
