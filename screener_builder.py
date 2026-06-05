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
    "Perf.Y",                   # 1Y performance %
    "Relative.Volume",          # relative volume vs avg
]

MIN_MKTCAP   = 1e9     # $1B minimum market cap
MIN_AVG_VOL  = 500_000 # 500K avg daily shares
MIN_PRICE    = 5.0     # $5 minimum price
TOP_N        = 500     # fetch top 500 by avg dollar volume

# ── Single-Stock ETF mapping  ─────────────────────────────────────────────────
# Maps underlying ticker → list of single-stock ETFs (2x bull / -1x bear)
# Only highly liquid ones included. Source: GraniteShares, Direxion, Rex Shares.
SINGLE_STOCK_ETFS: dict[str, list[str]] = {
    "AAPL":  ["AAPU", "AAPD"],
    "AMAT":  ["AMAU"],
    "AMD":   ["AMDU", "AMDS"],
    "AMZN":  ["AMZU", "AMZD"],
    "ASTS":  ["ASTL"],
    "AVGO":  ["AVGX"],
    "BABA":  ["BABX"],
    "BAC":   ["BACU"],
    "COIN":  ["CONL", "COND"],
    "DELL":  ["DELX"],
    "GME":   ["GMEX"],
    "GOOG":  ["GGLL", "GGLS"],
    "GOOGL": ["GGLL", "GGLS"],
    "GS":    ["GSAL"],
    "HOOD":  ["HODU"],
    "INTC":  ["INTU"],
    "IONQ":  ["IONX"],
    "JPM":   ["JPMO"],
    "MARA":  ["MRAL", "MRAD"],
    "META":  ["METU", "METD"],
    "MRVL":  ["MRVX"],
    "MSTR":  ["MSTU", "MSTZ"],
    "MU":    ["MUU"],
    "NFLX":  ["NFLX"],
    "NVDA":  ["NVDL", "NVDS", "NVDU"],
    "ORCL":  ["ORCL"],
    "PANW":  ["PANX"],
    "PLTR":  ["PLTU", "PLTD"],
    "PYPL":  ["PYPU"],
    "RKLB":  ["RKLX"],
    "RXRX":  ["RXRX"],
    "SMCI":  ["SMCU", "SMCD"],
    "SNOW":  ["SNWX"],
    "SOFI":  ["SOFL"],
    "SQ":    ["SQU"],
    "TSLA":  ["TSLL", "TSLS", "TSLQ"],
    "TSM":   ["TSML"],
    "UBER":  ["UBEX"],
    "UPST":  ["UPSX"],
    "XOM":   ["XOMU"],
}
# Flat set of all SS-ETF tickers for quick lookup
ALL_SS_ETF_TICKERS: set[str] = {t for v in SINGLE_STOCK_ETFS.values() for t in v}


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
        adr_dvol = round(adr_pct * avg_dv) if adr_pct is not None else 0

        # % of 52W Range
        pct_52w = None
        if hi52 and lo52 and price:
            rng = float(hi52) - float(lo52)
            if rng > 0:
                pct_52w = round(min(100, max(0, (float(price) - float(lo52)) / rng * 100)), 1)

        # Relative volume
        rvol = row.get("Relative.Volume")

        # Attach list of SS-ETFs for this ticker (populated later)
        ss_etfs = SINGLE_STOCK_ETFS.get(ticker, [])

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
            "perf_1d":             round(float(row["change"]), 2) if row.get("change") is not None else None,
            "perf_1w":             round(float(row["Perf.W"]), 2) if row.get("Perf.W") is not None else None,
            "perf_1m":             round(float(row["Perf.1M"]), 2) if row.get("Perf.1M") is not None else None,
            "perf_3m":             round(float(row["Perf.3M"]), 2) if row.get("Perf.3M") is not None else None,
            "perf_6m":             round(float(row["Perf.6M"]), 2) if row.get("Perf.6M") is not None else None,
            "perf_1y":             round(float(row["Perf.Y"]), 2) if row.get("Perf.Y") is not None else None,
            "rvol":                round(float(rvol), 2) if rvol is not None else None,
            "ss_etfs":             ss_etfs,   # single-stock ETF tickers (enriched below)
        })

    # Sort by ADR × AvgDolVol descending — hottest money at the top
    stocks.sort(key=lambda s: -(s["adr_dvol"] or 0))

    logger.info("Screener built: %d stocks", len(stocks))
    return stocks


def fetch_ss_etf_data() -> dict[str, dict]:
    """
    Fetch live data for all known single-stock ETFs from TradingView.
    Returns dict keyed by ticker with price, change_pct, avg_volume, avg_dollar_volume.
    Only returns ETFs that pass a minimum liquidity check (avg vol > 100K shares).
    """
    try:
        from tradingview_screener import Query, col
    except ImportError:
        return {}

    tickers = sorted(ALL_SS_ETF_TICKERS)
    logger.info("Fetching SS-ETF data for %d tickers…", len(tickers))

    try:
        _, df = (
            Query()
            .set_markets("america")
            .select("name", "description", "close", "change",
                    "average_volume_10d_calc", "ATR",
                    "price_52_week_high", "price_52_week_low", "Relative.Volume")
            .where(col("name").isin(tickers))
            .limit(len(tickers) + 10)
            .get_scanner_data()
        )
    except Exception as e:
        logger.warning("SS-ETF fetch failed: %s", e)
        return {}

    result = {}
    for _, row in df.iterrows():
        ticker  = str(row.get("name", "")).strip()
        price   = row.get("close")
        avg_vol = row.get("average_volume_10d_calc")
        atr     = row.get("ATR")
        if not ticker or price is None or avg_vol is None:
            continue
        if float(avg_vol) < 100_000:   # minimum liquidity for SS-ETFs
            continue
        avg_dv  = float(price) * float(avg_vol)
        adr_pct = round(float(atr) / float(price) * 100, 2) if atr and price else None
        hi52    = row.get("price_52_week_high")
        lo52    = row.get("price_52_week_low")
        pct_52w = None
        if hi52 and lo52 and price:
            rng = float(hi52) - float(lo52)
            if rng > 0:
                pct_52w = round(min(100, max(0, (float(price) - float(lo52)) / rng * 100)), 1)
        rvol = row.get("Relative.Volume")
        result[ticker] = {
            "ticker":            ticker,
            "company":           str(row.get("description", "")),
            "price":             round(float(price), 2),
            "change_pct":        round(float(row["change"]), 2) if row.get("change") is not None else None,
            "avg_volume":        int(avg_vol),
            "avg_dollar_volume": round(avg_dv),
            "adr_pct":           adr_pct,
            "adr_dvol":          round(adr_pct * avg_dv) if adr_pct else None,
            "week52_high":       round(float(hi52), 2) if hi52 else None,
            "week52_low":        round(float(lo52), 2) if lo52 else None,
            "pct_52w_range":     pct_52w,
            "rvol":              round(float(rvol), 2) if rvol is not None else None,
            "is_ss_etf":         True,
        }
    logger.info("SS-ETF data: %d liquid ETFs found", len(result))
    return result


def main():
    stocks = build_screener()
    if not stocks:
        logger.error("No stocks — aborting write")
        return

    # Fetch single-stock ETF live data and attach to each stock
    ss_etf_data = fetch_ss_etf_data()

    # Build reverse map: ss_etf_ticker → parent ticker
    ss_etf_parent: dict[str, str] = {}
    for parent, etfs in SINGLE_STOCK_ETFS.items():
        for e in etfs:
            ss_etf_parent[e] = parent

    # Enrich each stock: replace ss_etfs list with full data objects (liquid only)
    screener_tickers = {s["ticker"] for s in stocks}
    for s in stocks:
        raw_etfs = SINGLE_STOCK_ETFS.get(s["ticker"], [])
        enriched = []
        for etf_ticker in raw_etfs:
            data = ss_etf_data.get(etf_ticker)
            if data:  # only include if liquid & found
                enriched.append(data)
        s["ss_etfs"] = enriched

    # Also add SS-ETFs as standalone rows when their parent is in the screener
    # This lets users sort/filter the ETFs alongside their parent stocks
    ss_etf_rows = []
    added_ss = set()
    for etf_ticker, etf_data in ss_etf_data.items():
        parent = ss_etf_parent.get(etf_ticker)
        if parent and parent in screener_tickers and etf_ticker not in added_ss:
            row = {
                **etf_data,
                "industry":    f"SS-ETF → {parent}",
                "sector":      "Single-Stock ETF",
                "ss_etfs":     [],
                "parent_ticker": parent,
            }
            ss_etf_rows.append(row)
            added_ss.add(etf_ticker)

    ss_etf_rows.sort(key=lambda s: -(s["adr_dvol"] or 0))
    logger.info("Added %d liquid SS-ETF rows", len(ss_etf_rows))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
        "count":         len(stocks),
        "ss_etf_count":  len(ss_etf_rows),
        "stocks":        stocks,
        "ss_etfs":       ss_etf_rows,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Written → %s  (%d stocks + %d SS-ETFs)", OUTPUT_PATH, len(stocks), len(ss_etf_rows))

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
