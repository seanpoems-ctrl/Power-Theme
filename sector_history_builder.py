from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
sector_history_builder.py — "Leadership over time" for the 11 SPDR sector ETFs (Watchlist → 🏆 Leadership → Sectors)
==================================================================================================================
For each of the last HISTORY_DAYS trading sessions and each window (1M / 3M / 6M / 1Y), rank the sectors by return over
that window and store the Strength score: (n - rank + 1) / n × 100, so the best sector is 100 and the worst is 100/n — the
same score the Sectors table shows today. Recomputed from price history every run (nothing accumulates), so the chart is
complete from day one.

Windows use the same bar lookbacks as etf_rs_builder.py (1M = 20 bars back, 3M = 63, 6M = 126, 1Y = 252), price-only closes.

Output: public/sector_leadership.json
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
OUTPUT_PATH = ROOT / "public" / "sector_leadership.json"
ET = ZoneInfo("America/New_York")

SECTORS = {"XLK": "Technology", "XLE": "Energy", "XLV": "Health Care", "XLF": "Financial Services", "XLI": "Industrials",
           "XLP": "Consumer Staples", "XLY": "Consumer Discretionary", "XLU": "Utilities", "XLRE": "Real Estate",
           "XLB": "Materials", "XLC": "Communication Services"}
WINDOWS = {"1M": 20, "3M": 63, "6M": 126, "1Y": 252}
HISTORY_DAYS = 30


def main() -> None:
    try:
        import yfinance as yf
        raw = yf.download(list(SECTORS), period="2y", interval="1d", auto_adjust=False, progress=False)["Close"]
    except Exception as exc:  # noqa: BLE001
        logger.error("Yahoo download failed (%s) - leaving existing file untouched", exc)
        return
    closes = raw.dropna(how="all")
    missing = [t for t in SECTORS if t not in closes.columns or closes[t].dropna().empty]
    if missing:
        logger.error("No price history for %s - leaving existing file untouched", missing)
        return
    closes = closes[list(SECTORS)].ffill()
    n = len(SECTORS)
    if len(closes) < max(WINDOWS.values()) + HISTORY_DAYS:
        logger.error("Only %d sessions of history - leaving existing file untouched", len(closes))
        return

    idx = list(range(len(closes) - HISTORY_DAYS, len(closes)))
    dates = [closes.index[i].strftime("%Y-%m-%d") for i in idx]
    windows: dict[str, dict[str, list[float]]] = {}
    for w, back in WINDOWS.items():
        series = {t: [] for t in SECTORS}
        for i in idx:
            rets = {t: closes[t].iloc[i] / closes[t].iloc[i - back] - 1 for t in SECTORS}
            order = sorted(SECTORS, key=lambda t: -rets[t])
            for rank, t in enumerate(order, start=1):
                series[t].append(round((n - rank + 1) / n * 100, 1))
        windows[w] = series
    payload = {"generated_at": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"), "dates": dates, "sectors": SECTORS, "windows": windows}
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("Written %s: %d sectors x %d sessions, last session %s", OUTPUT_PATH, n, len(dates), dates[-1])
    for w in WINDOWS:
        top = sorted(SECTORS, key=lambda t: -windows[w][t][-1])[:3]
        print(f"{w}: top 3 {[SECTORS[t] for t in top]}")


if __name__ == "__main__":
    main()
