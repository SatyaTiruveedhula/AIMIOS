from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class UserAlertConfig:
    symbol: str
    trigger_price: float
    direction: str = "down"
    label: str = "user target"
    enabled: bool = True

    def match_key(self) -> str:
        return f"{self.symbol.upper()}::{self.label}"


class UserInitiatedPriceAlertManager:
    """Independent price-based alert watcher.

    This runs alongside pattern engines and is intended for manual user requests
    such as: "alert me when SENSEX reaches 80500".
    """

    def __init__(self, alerts: Iterable[dict[str, Any]] | None = None) -> None:
        self._alerts: dict[str, UserAlertConfig] = {}
        self._triggered: set[str] = set()

        for alert in alerts or []:
            config = self._coerce_alert(alert)
            if config is not None:
                self._alerts[config.match_key()] = config

    @staticmethod
    def _coerce_alert(alert: dict[str, Any]) -> UserAlertConfig | None:
        if not isinstance(alert, dict):
            return None

        symbol = str(alert.get("symbol", "")).strip().upper()
        if not symbol:
            return None

        try:
            trigger_price = float(alert.get("trigger_price"))
        except TypeError, ValueError:
            return None

        direction = str(alert.get("direction", "down")).strip().lower()
        if direction not in {"up", "down"}:
            direction = "down"

        label = str(alert.get("label") or "user target").strip() or "user target"
        enabled = bool(alert.get("enabled", True))

        return UserAlertConfig(
            symbol=symbol,
            trigger_price=trigger_price,
            direction=direction,
            label=label,
            enabled=enabled,
        )

    def register(
        self,
        symbol: str,
        trigger_price: float,
        direction: str = "down",
        label: str = "user target",
    ) -> None:
        config = UserAlertConfig(
            symbol=str(symbol).strip().upper(),
            trigger_price=float(trigger_price),
            direction=str(direction).strip().lower(),
            label=str(label).strip() or "user target",
            enabled=True,
        )
        self._alerts[config.match_key()] = config

    def process_price(self, symbol: str, current_price: float) -> None:
        if current_price is None:
            return

        for config in self._alerts.values():
            if not config.enabled or config.symbol != str(symbol).strip().upper():
                continue

            if config.match_key() in self._triggered:
                continue

            if config.direction == "down" and current_price <= config.trigger_price:
                self._fire(config, current_price)
            elif config.direction == "up" and current_price >= config.trigger_price:
                self._fire(config, current_price)

    def process_snapshot(self, symbol: str, snapshot: Any) -> None:
        if snapshot is None:
            return

        price = getattr(snapshot, "ltp", None)
        if price is None:
            try:
                price = float(snapshot.get("ltp", 0.0))
            except Exception:
                return

        self.process_price(symbol, float(price))

    def _fire(self, alert: UserAlertConfig, current_price: float) -> None:
        self._triggered.add(alert.match_key())
        print("")
        print("=" * 60)
        print("AIMIOS USER INITIATED ALERT")
        print("=" * 60)
        print(f"SYMBOL     : {alert.symbol}")
        print(f"TARGET     : {alert.trigger_price:.2f}")
        print(f"CURRENT    : {current_price:.2f}")
        print(f"DIRECTION  : {alert.direction.upper()}")
        print(f"LABEL      : {alert.label}")
        print("MODE       : PARALLEL USER WATCH")
        print("NOTE       : independent of pattern engine")
        print("=" * 60)
        print("")
