from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent
HISTORY_FILE = PROJECT_ROOT / "logs" / "patterns.csv"

DEFAULT_CLOSE_MAP: Dict[str, float] = {
    "NIFTY": 23820.0,
    "BANKNIFTY": 52050.0,
    "FINNIFTY": 21200.0,
    "SENSEX": 77167.93,
}


def normalize_symbol(raw_symbol: str) -> str:
    symbol = str(raw_symbol or "").strip()
    symbol = symbol.replace("NSE:", "").replace("BSE:", "")
    symbol = symbol.replace("NIFTY 50", "NIFTY")
    symbol = symbol.replace("NIFTY BANK", "BANKNIFTY")
    return symbol.strip()


def infer_direction(pattern_name: str) -> str:
    pattern = str(pattern_name or "").upper()

    buy_keywords = [
        "BOTTOM",
        "DOUBLE_BOTTOM",
        "BUY",
        "UP",
        "BULL",
        "REVERSAL",
        "W_PATTERN",
        "W",
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
        "M",
    ]

    if any(keyword in pattern for keyword in buy_keywords):
        return "BUY"
    if any(keyword in pattern for keyword in sell_keywords):
        return "SELL"
    return "UNKNOWN"


def parse_close_map(raw: str | None) -> Dict[str, float]:
    result = dict(DEFAULT_CLOSE_MAP)
    if not raw:
        return result

    for item in raw.split(","):
        if not item or "=" not in item:
            continue
        symbol, value = item.split("=", 1)
        try:
            result[symbol.strip().upper()] = float(value.strip())
        except ValueError:
            continue
    return result


def load_rows() -> List[dict]:
    rows: List[dict] = []
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
                    "pattern": pattern,
                    "entry_price": entry_price,
                    "timestamp": str(row.get("timestamp", "")).strip(),
                }
            )

    return rows


def filter_by_date(rows: Iterable[dict], target_date: str | None) -> List[dict]:
    if not target_date:
        return list(rows)

    selected: List[dict] = []
    for row in rows:
        timestamp = str(row.get("timestamp", ""))
        if timestamp.startswith(target_date):
            selected.append(row)
    return selected


def dedupe_analysis_signals(rows: Iterable[dict]) -> List[dict]:
    seen: Set[Tuple[str, str, float, str]] = set()
    cleaned: List[dict] = []

    for row in rows:
        if float(row.get("entry_price", 0.0)) <= 0:
            continue

        key = (
            str(row["symbol"]),
            str(row["direction"]),
            round(float(row["entry_price"]), 2),
            str(row["pattern"]),
        )
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(row)

    return cleaned


def evaluate_signal_verdict(
    direction: str,
    entry_price: float,
    close_price: float | None,
    active_move_pct: float | None = None,
    threshold_pct: float = 0.05,
) -> Dict[str, object]:
    direction = str(direction or "").upper()
    entry_price = float(entry_price)
    threshold_pct = float(threshold_pct)

    if close_price is None:
        return {
            "close_verdict": "CLOSE_MISSING",
            "lifecycle_verdict": "UNKNOWN",
            "overall_verdict": "UNKNOWN",
            "move_pct": 0.0,
            "active_move_pct": float(active_move_pct or 0.0),
            "threshold_pct": threshold_pct,
        }

    close_price = float(close_price)
    if direction == "BUY":
        favorable_close = close_price >= entry_price
        move_pct = (
            ((close_price - entry_price) / entry_price) * 100.0 if entry_price else 0.0
        )
    elif direction == "SELL":
        favorable_close = close_price <= entry_price
        move_pct = (
            ((entry_price - close_price) / entry_price) * 100.0 if entry_price else 0.0
        )
    else:
        favorable_close = False
        move_pct = 0.0

    close_verdict = "GOOD" if favorable_close else "BAD"

    active_move_pct_value = (
        float(active_move_pct) if active_move_pct is not None else abs(move_pct)
    )
    lifecycle_verdict = (
        "USEFUL_ACTIVE"
        if active_move_pct_value >= threshold_pct
        else "WEAK_OR_NO_LIFECYCLE"
    )

    if close_verdict == "GOOD" and lifecycle_verdict == "USEFUL_ACTIVE":
        overall_verdict = "GOOD"
    elif close_verdict == "BAD" and lifecycle_verdict == "USEFUL_ACTIVE":
        overall_verdict = "MIXED"
    elif close_verdict == "GOOD":
        overall_verdict = "GOOD_WEAK_LIFECYCLE"
    else:
        overall_verdict = "BAD"

    return {
        "close_verdict": close_verdict,
        "lifecycle_verdict": lifecycle_verdict,
        "overall_verdict": overall_verdict,
        "move_pct": move_pct,
        "active_move_pct": active_move_pct_value,
        "threshold_pct": threshold_pct,
    }


def evaluate_row(
    signal: dict, close_price: float | None, threshold_pct: float = 0.05
) -> Tuple[str, str, str, str, float, float]:
    direction = str(signal["direction"])
    entry_price = float(signal["entry_price"])

    if close_price is None:
        verdict = evaluate_signal_verdict(
            direction=direction,
            entry_price=entry_price,
            close_price=None,
            active_move_pct=0.0,
            threshold_pct=threshold_pct,
        )
        return (
            "CLOSE_MISSING",
            verdict["lifecycle_verdict"],
            verdict["overall_verdict"],
            "N/A",
            0.0,
            0.0,
        )

    active_move_pct = (
        abs(((close_price - entry_price) / entry_price) * 100.0) if entry_price else 0.0
    )
    verdict = evaluate_signal_verdict(
        direction=direction,
        entry_price=entry_price,
        close_price=close_price,
        active_move_pct=active_move_pct,
        threshold_pct=threshold_pct,
    )
    return (
        str(verdict["close_verdict"]),
        str(verdict["lifecycle_verdict"]),
        str(verdict["overall_verdict"]),
        f"{close_price:.2f}",
        float(verdict["move_pct"]),
        float(verdict["active_move_pct"]),
    )


def build_report(
    rows: List[dict], close_map: Dict[str, float], threshold_pct: float = 0.05
) -> dict:
    grouped: Dict[str, List[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row["symbol"])].append(row)

    total_buy = 0
    total_sell = 0
    total_good_close = 0
    total_bad_close = 0
    total_useful_active = 0
    total_weak_active = 0
    total_good = 0
    total_mixed = 0
    total_bad = 0
    detailed: List[str] = []

    for symbol in sorted(grouped):
        events = grouped[symbol]
        close_price = close_map.get(symbol)

        for event in events:
            direction = str(event["direction"])
            pattern = str(event["pattern"])
            entry_price = float(event["entry_price"])
            timestamp = str(event.get("timestamp", ""))

            (
                close_verdict,
                lifecycle_verdict,
                overall_verdict,
                close_label,
                move_pct,
                active_move_pct,
            ) = evaluate_row(
                event,
                close_price,
                threshold_pct=threshold_pct,
            )
            if direction == "BUY":
                total_buy += 1
            else:
                total_sell += 1

            if close_verdict == "GOOD":
                total_good_close += 1
            else:
                total_bad_close += 1

            if lifecycle_verdict == "USEFUL_ACTIVE":
                total_useful_active += 1
            else:
                total_weak_active += 1

            if overall_verdict == "GOOD":
                total_good += 1
            elif overall_verdict == "MIXED":
                total_mixed += 1
            else:
                total_bad += 1

            detailed.append(
                f"{timestamp} | {symbol} | {pattern} | {direction} | entry={entry_price:.2f} | close={close_label} | close_verdict={close_verdict} | lifecycle={lifecycle_verdict} | move_pct={move_pct:.3f}% | active_move_pct={active_move_pct:.3f}% | overall={overall_verdict}"
            )

    total_signals = len(rows)
    win_rate = round((total_good / total_signals) * 100.0, 2) if total_signals else 0.0
    status = "GOOD" if win_rate >= 60 else "MIXED" if win_rate >= 40 else "BAD"

    return {
        "total_signals": total_signals,
        "buy": total_buy,
        "sell": total_sell,
        "close_good": total_good_close,
        "close_bad": total_bad_close,
        "useful_active": total_useful_active,
        "weak_active": total_weak_active,
        "good": total_good,
        "mixed": total_mixed,
        "bad": total_bad,
        "win_rate": win_rate,
        "status": status,
        "threshold_pct": threshold_pct,
        "signals": detailed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="EOD signal verification for AIMIOS pattern history."
    )
    parser.add_argument(
        "--date", help="Filter by date in YYYY-MM-DD format. Defaults to today."
    )
    parser.add_argument(
        "--close-map",
        help="Optional close values, example: NIFTY=23820,BANKNIFTY=52050,SENSEX=77167.93",
    )
    parser.add_argument(
        "--threshold-pct",
        type=float,
        default=0.05,
        help="Minimum active move threshold in percent for a signal to count as useful during its lifecycle.",
    )
    args = parser.parse_args()

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")
    close_map = parse_close_map(args.close_map)

    raw_rows = load_rows()
    filtered_rows = filter_by_date(raw_rows, target_date)
    analysis_rows = dedupe_analysis_signals(filtered_rows)
    report = build_report(analysis_rows, close_map, threshold_pct=args.threshold_pct)

    print("=== EOD SIGNAL REPORT ===")
    print(f"Date: {target_date}")
    print(f"Raw history entries: {len(raw_rows)}")
    print(f"EOD analysis signals: {report['total_signals']}")
    print(f"BUY: {report['buy']} | SELL: {report['sell']}")
    print(f"CLOSE GOOD: {report['close_good']} | CLOSE BAD: {report['close_bad']}")
    print(
        f"USEFUL ACTIVE: {report['useful_active']} | WEAK ACTIVE: {report['weak_active']}"
    )
    print(
        f"OVERALL GOOD: {report['good']} | OVERALL MIXED: {report['mixed']} | OVERALL BAD: {report['bad']}"
    )
    print(f"Win rate: {report['win_rate']}%")
    print(f"EOD status: {report['status']}")
    print(f"Active threshold: {report['threshold_pct']:.3f}%")
    print()
    print("Decision logic details:")
    if not report["signals"]:
        print("No signal activity for this date.")
    else:
        for signal in report["signals"]:
            print(f"  - {signal}")


if __name__ == "__main__":
    main()
