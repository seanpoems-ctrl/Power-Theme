"""
backfill_atr_history.py — Rebuild atr_ext stock lists in breadth_history/*.json
using yfinance historical data + Wilder's RMA ATR formula.

Run once to fix history files generated with the old (wrong) formula.

    python backfill_atr_history.py
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import yfinance as yf
from tradingview_screener import Query, col  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PUBLIC_DIR   = Path(__file__).parent / "public"
HISTORY_DIR  = PUBLIC_DIR / "breadth_history"
BM_JSON      = PUBLIC_DIR / "breadth_monitor.json"

ATR_N        = 14
SMA_N        = 50
MIN_MKT_CAP  = 1_000_000_000   # $1B
MIN_ATR_MULT = 10.0             # |multiple| ≥ 10


# ---------------------------------------------------------------------------
# Step 1 — get universe from TradingView screener (same filter as builder)
# ---------------------------------------------------------------------------

def get_tv_universe() -> pd.DataFrame:
    logger.info("Fetching TradingView universe …")
    _, df = (
        Query()
        .select("name", "close", "change", "ATR", "SMA50", "market_cap_basic",
                "average_volume_10d_calc", "sector", "industry")
        .where(
            col("close") >= 2,
            col("average_volume_10d_calc") >= 50_000,
            col("type").isin(["stock", "dr"]),
            col("exchange").isin(["NYSE", "NASDAQ", "AMEX", "NYSE ARCA"]),
            col("market_cap_basic") >= MIN_MKT_CAP,
        )
        .limit(10_000)
        .get_scanner_data()
    )
    logger.info("TradingView returned %d rows", len(df))
    return df


# ---------------------------------------------------------------------------
# Step 2 — download yfinance historical OHLCV for the universe
# ---------------------------------------------------------------------------

def download_history(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """
    Returns a DataFrame with MultiIndex columns (field, ticker).
    Index is DatetimeIndex (tz-naive, date strings).
    """
    logger.info("Downloading yfinance history for %d tickers (%s → %s) …", len(tickers), start, end)
    yf_tickers = [t.replace(".", "-").replace("/", "-") for t in tickers]
    raw = yf.download(
        yf_tickers,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    raw.index = raw.index.strftime("%Y-%m-%d")
    return raw


# ---------------------------------------------------------------------------
# Step 3 — compute ATR extension for every date × ticker
# ---------------------------------------------------------------------------

def compute_atr_ext(raw: pd.DataFrame, tickers: list[str]) -> dict[str, list[str]]:
    """
    Returns {date_str: [list of tickers with |ATR multiple| ≥ 10]}.
    """
    yf_map = {t: t.replace(".", "-").replace("/", "-") for t in tickers}

    closes: dict[str, pd.Series] = {}
    highs:  dict[str, pd.Series] = {}
    lows:   dict[str, pd.Series] = {}

    for orig, yf_t in yf_map.items():
        try:
            c = raw["Close"][yf_t].dropna()
            h = raw["High"][yf_t].dropna()
            lo = raw["Low"][yf_t].dropna()
            if len(c) >= SMA_N + ATR_N:
                closes[orig] = c
                highs[orig]  = h
                lows[orig]   = lo
        except (KeyError, TypeError):
            continue

    logger.info("Computing ATR ext for %d tickers …", len(closes))

    # Build per-date result
    date_to_tickers: dict[str, list[str]] = {}

    for ticker, c in closes.items():
        h  = highs[ticker]
        lo = lows[ticker]

        # True range
        prev_c = c.shift(1)
        tr = pd.concat([h - lo, (h - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)

        # Wilder's RMA (same as TradingView)
        atr = tr.ewm(alpha=1 / ATR_N, min_periods=ATR_N, adjust=False).mean()

        # SMA 50
        sma50 = c.rolling(SMA_N, min_periods=SMA_N).mean()

        # 3-step formula
        atr_pct      = atr / c
        pct_gain_50  = (c - sma50) / sma50
        atr_mult     = pct_gain_50 / atr_pct

        qualified = atr_mult[atr_mult.abs() >= MIN_ATR_MULT]
        for date_str, _ in qualified.items():
            date_to_tickers.setdefault(date_str, []).append(ticker)

    return date_to_tickers


# ---------------------------------------------------------------------------
# Step 4 — patch history files
# ---------------------------------------------------------------------------

def patch_history(
    dates_to_fix: list[str],
    date_to_tickers: dict[str, list[str]],
    tv_df: pd.DataFrame,
) -> None:
    """
    For each date in dates_to_fix, rebuild the atr_ext list in the history file
    using the yfinance-computed ticker set.
    """
    # Build a quick lookup: ticker → row from TradingView (for RS, ADR%, price etc.)
    tv_lookup: dict[str, dict] = {}
    for _, row in tv_df.iterrows():
        tv_lookup[str(row["name"])] = row.to_dict()

    for date_str in dates_to_fix:
        path = HISTORY_DIR / f"{date_str}.json"
        if not path.exists():
            logger.warning("History file not found: %s — skipping", path.name)
            continue

        hist = json.loads(path.read_text(encoding="utf-8"))
        tickers_for_date = date_to_tickers.get(date_str, [])

        # Build compact stock entries (same schema as _build_compact_history)
        compact_stocks = []
        for t in tickers_for_date:
            tv = tv_lookup.get(t, {})
            compact_stocks.append({
                "t":   t,
                "co":  tv.get("name", t),      # use ticker as fallback company name
                "p":   tv.get("close"),
                "c":   tv.get("change"),
                "adr": None,                    # not available from TradingView screener
                "dv":  None,
                "qtd": None,
                "mtd": None,
                "d34": None,
                "rs":  None,
                "ind": tv.get("industry") or None,
                "ae":  None,                    # would need per-date atr_mult value
            })

        old_count = len(hist["filters"].get("atr_ext", []))
        hist["filters"]["atr_ext"] = compact_stocks
        path.write_text(json.dumps(hist, separators=(",", ":")), encoding="utf-8")
        logger.info("Patched %s: atr_ext %d → %d stocks", date_str, old_count, len(compact_stocks))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Load breadth_monitor.json to find dates that need fixing
    bm_rows = {r["date"]: r for r in json.loads(BM_JSON.read_text(encoding="utf-8"))["rows"]}

    # Find all history files where atr_ext count != the correct count from breadth_monitor
    dates_to_fix: list[str] = []
    for f in sorted(HISTORY_DIR.glob("[0-9]*.json")):
        date_str = f.stem
        hist = json.loads(f.read_text(encoding="utf-8"))
        hist_count = len(hist["filters"].get("atr_ext", []))
        correct = (bm_rows.get(date_str) or {}).get("atr_10x_ext")
        if correct is not None and hist_count != correct:
            dates_to_fix.append(date_str)
            logger.info("Will fix %s: has %d, should have %d", date_str, hist_count, correct)

    if not dates_to_fix:
        logger.info("All history files already have correct atr_ext counts.")
        return

    logger.info("%d dates to fix: %s … %s", len(dates_to_fix), dates_to_fix[0], dates_to_fix[-1])

    # Get universe from TradingView
    tv_df = get_tv_universe()
    tickers = tv_df["name"].dropna().tolist()

    # Download enough history: earliest date minus 70 days (SMA50 + ATR14 warm-up)
    earliest = min(dates_to_fix)
    start_dt = (pd.Timestamp(earliest) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    latest   = max(dates_to_fix)
    # end is day AFTER latest so yfinance includes that date
    end_dt   = (pd.Timestamp(latest) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    raw = download_history(tickers, start=start_dt, end=end_dt)

    # Compute ATR extension for all dates
    date_to_tickers = compute_atr_ext(raw, tickers)

    # Log computed counts vs expected
    logger.info("Computed ATR ext counts:")
    for d in dates_to_fix:
        got = len(date_to_tickers.get(d, []))
        exp = (bm_rows.get(d) or {}).get("atr_10x_ext", "?")
        logger.info("  %s: computed=%d  expected=%s", d, got, exp)

    # Patch history files
    patch_history(dates_to_fix, date_to_tickers, tv_df)

    logger.info("Done. All %d history files patched.", len(dates_to_fix))


if __name__ == "__main__":
    main()
