from __future__ import annotations

import csv
from pathlib import Path

HISTORY_FILE = Path(__file__).resolve().parent / "logs" / "patterns.csv"

# Update these values after market close with the actual day close for each symbol.
CLOSE_MAP = {
    "NIFTY": 23820.0,
    "BANKNIFTY": 52050.0,
    "FINNIFTY": 21200.0,
}


def normalize_symbol(raw_symbol: str) -> str:
    symbol = str(raw_symbol).strip()
    symbol = symbol.replace("NSE:", "").replace("BSE:", "")
    symbol = symbol.replace("NIFTY 50", "NIFTY")
    symbol = symbol.replace("NIFTY BANK", "BANKNIFTY")
    return symbol.strip()


def infer_direction(pattern_name: str) -> str:
    pattern = str(pattern_name).upper()

    buy_keywords = [
        "BOTTOM",
        "DOUBLE_BOTTOM",
        "BUY",
        "UP",
        "BULL",
        "REVERSAL",
        "W_PATTERN",
        "W-",
    ]
    sell_keywords = [
        "TOP",
        "DOUBLE_TOP",
        "SELL",
        "DOWN",
        "BEAR",
        "EXHAUSTION",
        "FAKE_BREAKOUT",
        "M_PATTERN",
        "M-",
    ]

    if any(keyword in pattern for keyword in buy_keywords):
        return "BUY"
    if any(keyword in pattern for keyword in sell_keywords):
        return "SELL"
    return "UNKNOWN"


def load_history_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    if not HISTORY_FILE.exists():
        raise FileNotFoundError(f"History file not found: {HISTORY_FILE}")

    with HISTORY_FILE.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row:
                continue

            symbol = normalize_symbol(row.get("symbol", ""))
            pattern = str(row.get("pattern", ""))
            direction = infer_direction(pattern)

            if not symbol or direction == "UNKNOWN":
                continue

            try:
                entry_price = float(row.get("price", 0.0))
            except TypeError, ValueError:
                continue

            rows.append(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "entry_price": entry_price,
                    "pattern": pattern,
                    "timestamp": row.get("timestamp", ""),
                }
            )

    return rows


def keep_analysis_signals(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[str, str, float, str]] = set()
    filtered: list[dict[str, object]] = []

    for row in rows:
        symbol = str(row["symbol"])
        direction = str(row["direction"])
        pattern = str(row["pattern"])
        entry_price = float(row["entry_price"])

        if entry_price <= 0:
            continue

        signature = (symbol, direction, round(entry_price, 2), pattern)
        if signature in seen:
            continue
        seen.add(signature)
        filtered.append(row)

    return filtered


def summarize_history() -> None:
    raw_rows = load_history_rows()
    analysis_rows = keep_analysis_signals(raw_rows)
    grouped: dict[str, list[dict[str, object]]] = {}

    for row in analysis_rows:
        symbol = str(row["symbol"])
        grouped.setdefault(symbol, []).append(row)

    print("=== EOD SIGNAL TRIGGER REVIEW FROM HISTORY ===")
    print("Rule: BUY is good if close >= entry; SELL is good if close <= entry")
    print(f"Raw history entries: {len(raw_rows)}")
    print(f"EOD analysis signals: {len(analysis_rows)}")
    print()

    if not grouped:
        print("No valid historical signals found in logs/patterns.csv")
        return

    for symbol in sorted(grouped):
        events = grouped[symbol]
        print(f"SYMBOL: {symbol}")

        close_price = CLOSE_MAP.get(symbol)
        if close_price is None:
            print(
                "  Close price not available for this symbol; set CLOSE_MAP to enable EOD result checks."
            )

        for event in events:
            direction = str(event["direction"])
            pattern = str(event["pattern"])
            entry_price = float(event["entry_price"])
            timestamp = str(event.get("timestamp", ""))
            result = "UNKNOWN"
            outcome = "CLOSE_MISSING"
            close_label = "N/A"

            if close_price is not None:
                is_good = (direction == "BUY" and close_price >= entry_price) or (
                    direction == "SELL" and close_price <= entry_price
                )
                result = "GOOD" if is_good else "WRONG"
                outcome = result
                close_label = f"{close_price:.2f}"

            print(
                f"  - {timestamp} | {pattern} | {direction} | entry={entry_price:.2f} "
                f"| close={close_label} | result={outcome}"
            )

        total = len(events)
        buy_count = sum(1 for event in events if str(event["direction"]) == "BUY")
        sell_count = sum(1 for event in events if str(event["direction"]) == "SELL")
        good = 0
        bad = 0

        if close_price is not None:
            for event in events:
                direction = str(event["direction"])
                entry_price = float(event["entry_price"])
                is_good = (direction == "BUY" and close_price >= entry_price) or (
                    direction == "SELL" and close_price <= entry_price
                )
                if is_good:
                    good += 1
                else:
                    bad += 1

        win_rate = round((good / total) * 100.0, 2) if total else 0.0
        status = "GOOD" if win_rate >= 60 else "MIXED" if win_rate >= 40 else "BAD"

        print(
            f"  Raw historical entries: {sum(1 for row in raw_rows if str(row['symbol']) == symbol)}"
        )
        print(f"  EOD analysis signals: {total}")
        print(f"  BUY: {buy_count} | SELL: {sell_count}")
        print(f"  GOOD: {good} | WRONG: {bad}")
        print(f"  Win rate: {win_rate}%")
        print(f"  EOD status: {status}")
        print("-" * 90)


def main() -> None:
    summarize_history()


if __name__ == "__main__":
    main()
