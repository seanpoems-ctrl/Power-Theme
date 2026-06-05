from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
screener_builder.py — Independent Hot-Money Stock Screener
===========================================================
Fetches the top momentum stocks from TradingView screener, sorted by
ADR% × Avg$Vol (daily dollar volatility = institutional hot-money signal).

Universe: all NYSE/NASDAQ stocks, mkt cap ≥ $1B, avg daily vol ≥ 500K shares,
          price ≥ $5, not ETF/fund.

Output: public/screener_stocks.json
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_PATH = Path("public/screener_stocks.json")
ET = ZoneInfo("America/New_York")

FIELDS = [
    "name",                     # ticker symbol
    "description",              # company name
    "close",                    # current price
    "change",                   # 1D change %
    "volume",                   # today's volume
    "average_volume_10d_calc",  # 10-day avg volume
    "market_cap_basic",         # market cap
    "sector",                   # sector
    "industry",                 # industry
    "price_52_week_high",       # 52W high
    "price_52_week_low",        # 52W low
    "ATR",                      # Average True Range (14-day)
    "Perf.W",                   # 1W performance %
    "Perf.1M",                  # 1M performance %
    "Perf.3M",                  # 3M performance %
    "Perf.6M",                  # 6M performance %
    "Relative.Volume",          # relative volume vs avg
]

MIN_MKTCAP   = 1e9     # $1B minimum market cap
MIN_AVG_VOL  = 500_000 # 500K avg daily shares
MIN_PRICE    = 5.0     # $5 minimum price
MIN_ADR      = 4.0     # 4% minimum ADR
TOP_N        = 500     # fetch top 500 by avg dollar volume


def build_screener() -> list[dict]:
    try:
        from tradingview_screener import Query, col
    except ImportError:
        logger.error("tradingview_screener not installed")
        return []

    logger.info("Querying TradingView screener for top %d stocks…", TOP_N)

    try:
        _, df = (
            Query()
            .set_markets("america")
            .select(*FIELDS)
            .where(
                col("market_cap_basic") > MIN_MKTCAP,
                col("average_volume_10d_calc") > MIN_AVG_VOL,
                col("close") > MIN_PRICE,
                col("type") == "stock",
                col("exchange").isin(["NASDAQ", "NYSE"]),
            )
            .order_by("average_volume_10d_calc", ascending=False)
            .limit(TOP_N)
            .get_scanner_data()
        )
    except Exception as e:
        logger.error("TradingView query failed: %s", e)
        return []

    logger.info("Fetched %d stocks from TradingView", len(df))

    stocks = []
    for _, row in df.iterrows():
        ticker = str(row.get("name", "")).strip()
        if not ticker:
            continue

        price    = row.get("close")
        avg_vol  = row.get("average_volume_10d_calc")
        atr      = row.get("ATR")
        hi52     = row.get("price_52_week_high")
        lo52     = row.get("price_52_week_low")

        if price is None or avg_vol is None:
            continue

        # ADR% from ATR: (ATR / close) * 100
        adr_pct = round(float(atr) / float(price) * 100, 2) if atr and price else None

        # Avg dollar volume
        avg_dv = float(price) * float(avg_vol)

        # ADR × AvgDolVol  (ADR as the raw number e.g. 10.4, NOT as decimal 0.104)
        # Formula: ADR % × Avg$Vol  e.g. ASTS: 10.4 × $3.22B = $33.5B
        # Skip stocks below minimum ADR threshold
        if adr_pct is None or adr_pct < MIN_ADR:
            continue

        # ADR × AvgDolVol  (ADR as the raw number e.g. 10.4, NOT as decimal 0.104)
        adr_dvol = round(adr_pct * avg_dv)

        # % of 52W Range
        pct_52w = None
        if hi52 and lo52 and price:
            rng = float(hi52) - float(lo52)
            if rng > 0:
                pct_52w = round(min(100, max(0, (float(price) - float(lo52)) / rng * 100)), 1)

        # Relative volume
        rvol = row.get("Relative.Volume")

        stocks.append({
            "ticker":              ticker,
            "company":             str(row.get("description", "")),
            "sector":              str(row.get("sector", "")) or None,
            "industry":            str(row.get("industry", "")) or None,
            "price":               round(float(price), 2),
            "change_pct":          round(float(row["change"]), 2) if row.get("change") is not None else None,
            "avg_volume":          int(avg_vol),
            "avg_dollar_volume":   round(avg_dv),
            "market_cap_b":        round(float(row["market_cap_basic"]) / 1e9, 2) if row.get("market_cap_basic") else None,
            "adr_pct":             adr_pct,
            "adr_dvol":            adr_dvol,
            "week52_high":         round(float(hi52), 2) if hi52 else None,
            "week52_low":          round(float(lo52), 2) if lo52 else None,
            "pct_52w_range":       pct_52w,
            "perf_1w":             round(float(row["Perf.W"]), 2) if row.get("Perf.W") is not None else None,
            "perf_1m":             round(float(row["Perf.1M"]), 2) if row.get("Perf.1M") is not None else None,
            "perf_3m":             round(float(row["Perf.3M"]), 2) if row.get("Perf.3M") is not None else None,
            "perf_6m":             round(float(row["Perf.6M"]), 2) if row.get("Perf.6M") is not None else None,
            "rvol":                round(float(rvol), 2) if rvol is not None else None,
        })

    # Sort by ADR × AvgDolVol descending — hottest money at the top
    stocks.sort(key=lambda s: -(s["adr_dvol"] or 0))

    logger.info("Screener built: %d stocks", len(stocks))
    return stocks


def main():
    stocks = build_screener()
    if not stocks:
        logger.error("No stocks — aborting write")
        return

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
        "count":         len(stocks),
        "stocks":        stocks,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Written → %s  (%d stocks)", OUTPUT_PATH, len(stocks))

    # Print top 20 for verification
    print(f"\n{'#':>3}  {'Ticker':<8}  {'ADR×DolVol':>14}  {'ADR%':>6}  {'52WR%':>6}  {'Industry'}")
    print("-" * 80)
    for i, s in enumerate(stocks[:20], 1):
        dvol = s["adr_dvol"]
        dvol_fmt = f"${dvol/1e9:.2f}B" if dvol and dvol >= 1e9 else (f"${dvol/1e6:.0f}M" if dvol else "—")
        pct = f"{s['pct_52w_range']:.0f}%" if s["pct_52w_range"] is not None else "—"
        print(f"{i:>3}  {s['ticker']:<8}  {dvol_fmt:>14}  {s['adr_pct'] or 0:>5.1f}%  {pct:>6}  {s['industry'] or ''}")


if __name__ == "__main__":
    main()
