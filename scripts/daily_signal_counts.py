#!/usr/bin/env python3
import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "logs" / "day_extremes.csv"


def parse_date(ts: str):
    try:
        return datetime.fromisoformat(ts).date()
    except Exception:
        # try common formats in file (space separated)
        try:
            return datetime.strptime(ts.split()[0], "%Y-%m-%d").date()
        except Exception:
            return None


def main():
    if not LOG.exists():
        print(f"Log file not found: {LOG}")
        return 1

    counts = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    # counts[date][symbol][direction] = int

    with LOG.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ts = row.get("timestamp", "")
            date = parse_date(ts)
            if date is None:
                continue
            symbol = (row.get("symbol") or "").strip()
            direction = (row.get("direction") or "").strip().upper()
            if not symbol or not direction:
                continue
            counts[str(date)][symbol][direction] += 1

    # Print summary sorted by date
    for date in sorted(counts.keys(), reverse=True):
        print(f"Date: {date}")
        for symbol in sorted(counts[date].keys()):
            buys = counts[date][symbol].get("BUY", 0)
            sells = counts[date][symbol].get("SELL", 0)
            print(f"  {symbol}: BUY={buys} | SELL={sells}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
