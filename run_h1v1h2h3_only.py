from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List

from broker.kite_feed import KiteFeed
from aimios.engines.h1v1h2h3 import H1V1H2H3Detector

# ============================================================
# H1V1H2H3 ONLY LIVE ALERT RUNNER
# ============================================================
# This is a standalone runner for the recently created pattern logic.
# It intentionally avoids the old live feed / pattern engines.
# ============================================================

SYMBOLS = ["NIFTY", "BANKNIFTY", "SENSEX"]
INTERVAL_MINUTES = 5


class H1V1H2H3LiveRunner:
    def __init__(self) -> None:
        self.broker = KiteFeed()
        self.detector = H1V1H2H3Detector()
        self.history: Dict[str, List[Any]] = {symbol: [] for symbol in SYMBOLS}
        self.last_alert_key: Dict[str, str] = {}

    def _instrument_token(self, symbol: str) -> str:
        mapping = {
            "NIFTY": "256265",
            "BANKNIFTY": "260105",
            "SENSEX": "256265",
        }
        return mapping.get(symbol, "256265")

    def connect(self) -> None:
        self.broker.connect()
        self.broker.login()
        if not self.broker.logged_in:
            self.broker.generate_session()

        if self.broker.client is None or self.broker.access_token is None:
            raise RuntimeError("Kite broker did not provide a valid access token")

        print("H1V1H2H3 live runner connected to Kite")

    def fetch_recent_ohlc(self, symbol: str) -> List[Any]:
        instrument = self._instrument_token(symbol)
        now = datetime.now()
        start = datetime(now.year, now.month, now.day, 9, 15)
        end = now

        data = self.broker.get_historical(
            instrument,
            start,
            end,
            "5minute",
        )

        candles: List[Any] = []
        for item in data:

            class Candle:
                def __init__(self, payload: Dict[str, Any]) -> None:
                    self.timestamp = payload.get("datetime")
                    self.high = float(payload.get("high", 0.0))
                    self.low = float(payload.get("low", 0.0))
                    self.close = float(payload.get("close", 0.0))

            candles.append(Candle(item))

        self.history[symbol] = candles
        return candles

    def evaluate_symbol(self, symbol: str) -> None:
        candles = self.fetch_recent_ohlc(symbol)
        if not candles:
            return

        latest = candles[-1]
        alerts = self.detector.process_candle(symbol, latest, candles)
        if not alerts:
            return

        for alert in alerts:
            key = f"{symbol}:{alert['direction']}:{alert.get('day_key')}"
            if key == self.last_alert_key.get(symbol):
                continue
            self.last_alert_key[symbol] = key
            print("")
            print("=" * 70)
            print("H1V1H2H3 ALERT")
            print("=" * 70)
            print(f"SYMBOL     : {alert['symbol']}")
            print(f"DIRECTION  : {alert['direction']}")
            print(f"H1         : {alert['h1']}")
            print(f"V1         : {alert['v1']}")
            print(f"H2         : {alert['h2']}")
            print(f"H3         : {alert['h3']}")
            print(f"WAIT       : {alert['waiting_minutes']} min")
            print(f"TIME       : {alert['timestamp']}")
            print("=" * 70)
            print("")

    def run(self) -> None:
        self.connect()
        print("Running H1V1H2H3 alerts only ...")
        while True:
            try:
                for symbol in SYMBOLS:
                    self.evaluate_symbol(symbol)
                time.sleep(60)
            except KeyboardInterrupt:
                print("Stopping H1V1H2H3 only runner")
                break
            except Exception as exc:
                print(f"Runner loop error: {exc}")
                time.sleep(30)


if __name__ == "__main__":
    H1V1H2H3LiveRunner().run()
