# AIMIOS

A lightweight desktop AI assistant for Indian stock market traders.

## Phase 1 MVP

Features:
- Live market feed (Kite/Groww)
- Pattern Recognition
- Swing Detection
- Pressure Engine
- Cooling Engine
- Commander Engine
- Replay
- Alerts

## Target

- Windows 10
- Python 3.14
- SQLite
- Tkinter

## Architecture

- Modular design
- Independent engines
- Core system should not require change when engines are added

## Getting Started

1. Create a Python 3.14 virtual environment.
2. Install dependencies from `requirements.txt`.
3. Run `python -m aimios`.

## Live MVP: Kite Pattern Detection

### How to run

1. Ensure `.env` contains Kite API credentials:
   - `KITE_API_KEY`
   - `KITE_API_SECRET`
   - `REDIRECT_URL`
   - `CALLBACK_PORT`
2. Start the live feed:
   - `python -m app.kite_live_feed`
3. Authenticate with Kite when the browser opens.

### Expected output

When a candle closes and a pattern is detected, the console should print:

--------------------------------------------------
TIME: 2026-07-31 10:31:00
SYMBOL: NIFTY
PATTERN: DOUBLE_TOP
CONFIDENCE: 91%
PRICE: 24962
--------------------------------------------------

### Logs

Detected patterns are appended to `logs/patterns.csv` with columns:
- `timestamp`
- `symbol`
- `pattern`
- `confidence`
- `price`

### Architecture

```
Kite WebSocket
      ↓
MarketSnapshot
      ↓
CandleBuffer.update()
      ↓
PatternDetector.detect()
      ↓
Console / logs/patterns.csv
```
