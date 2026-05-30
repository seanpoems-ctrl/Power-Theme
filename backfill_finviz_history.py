"""
backfill_finviz_history.py — Rebuild Finviz-equivalent filter stock lists
(up4, dn4, up25q, dn25q, up25m, dn25m, up50m, dn50m, up13_34, dn13_34)
for dates where the nightly Finviz scraper returned 0 stocks.

Uses yfinance + TradingView universe as a drop-in approximation.
Run once to fix history files for 2026-05-27, 2026-05-28, 2026-05-29.

    python backfill_finviz_history.py
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

PUBLIC_DIR  = Path(__file__).parent / "public"
HISTORY_DIR = PUBLIC_DIR / "breadth_history"

# Finviz filter definitions: (field, threshold, direction)
# direction: "up" = field >= threshold, "down" = field <= -threshold
FILTER_DEFS = {
    "up4":     ("change_1d_pct",  4.0,  "up"),
    "dn4":     ("change_1d_pct",  4.0,  "down"),
    "up25q":   ("perf_13w_pct",  25.0,  "up"),
    "dn25q":   ("perf_13w_pct",  25.0,  "down"),
    "up25m":   ("perf_4w_pct",   25.0,  "up"),
    "dn25m":   ("perf_4w_pct",   25.0,  "down"),
    "up50m":   ("perf_4w_pct",   50.0,  "up"),
    "dn50m":   ("perf_4w_pct",   50.0,  "down"),
    "up13_34": ("perf_34d_pct",  13.0,  "up"),
    "dn13_34": ("perf_34d_pct",  13.0,  "down"),
}

FILTER_SORT = {
    "up4":     ("change_1d_pct", False),
    "dn4":     ("change_1d_pct", True),
    "up25q":   ("perf_13w_pct",  False),
    "dn25q":   ("perf_13w_pct",  True),
    "up25m":   ("perf_4w_pct",   False),
    "dn25m":   ("perf_4w_pct",   True),
    "up50m":   ("perf_4w_pct",   False),
    "dn50m":   ("perf_4w_pct",   True),
    "up13_34": ("perf_34d_pct",  False),
    "dn13_34": ("perf_34d_pct",  True),
}

# Lookback needed per metric (trading days, plus warm-up)
LOOKBACK_DAYS = 120   # ~6 months covers all metrics + warm-up

MIN_PRICE     = 5.0
MIN_AVG_VOL   = 100_000
MIN_CAP_B     = 1.0       # $1B market cap
MIN_ADR_PCT   = 3.0


# ---------------------------------------------------------------------------
def fmt_dollar_vol(price: float | None, avg_vol: float | None) -> str | None:
    """Format avg daily dollar volume as '$X.XXB' / '$X.XM' string."""
    if not price or not avg_vol:
        return None
    dv = price * avg_vol
    if dv >= 1e9:
        return f"${dv/1e9:.2f}B"
    if dv >= 1e6:
        return f"${dv/1e6:.1f}M"
    if dv >= 1e3:
        return f"${dv/1e3:.0f}K"
    return f"${dv:.0f}"


def get_tv_universe() -> pd.DataFrame:
    logger.info("Fetching TradingView universe (price ≥ $5, avg vol ≥ 100K, mkt cap ≥ $1B) …")
    _, df = (
        Query()
        .select("name", "close", "market_cap_basic", "average_volume_10d_calc",
                "sector", "industry", "change")
        .where(
            col("close") >= MIN_PRICE,
            col("average_volume_10d_calc") >= MIN_AVG_VOL,
            col("type").isin(["stock", "dr"]),
            col("exchange").isin(["NYSE", "NASDAQ", "AMEX", "NYSE ARCA"]),
            col("market_cap_basic") >= MIN_CAP_B * 1e9,
        )
        .limit(10_000)
        .get_scanner_data()
    )
    logger.info("TradingView returned %d rows", len(df))
    return df


def download_history(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    yf_tickers = [t.replace(".", "-").replace("/", "-") for t in tickers]
    logger.info("Downloading yfinance history for %d tickers (%s → %s) …", len(tickers), start, end)
    raw = yf.download(yf_tickers, start=start, end=end,
                      auto_adjust=True, progress=False, threads=True)
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    raw.index = raw.index.strftime("%Y-%m-%d")
    return raw


def compute_metrics(raw: pd.DataFrame, tv_df: pd.DataFrame,
                    dates: list[str]) -> dict[str, dict[str, dict]]:
    """
    Returns {date_str: {ticker: {change_1d_pct, perf_4w_pct, perf_13w_pct,
                                  perf_34d_pct, dollar_vol_str, adr_pct}}}
    """
    # Build ticker → TradingView metadata lookup
    tv_meta: dict[str, dict] = {}
    for _, row in tv_df.iterrows():
        t = str(row["name"])
        tv_meta[t] = {
            "company":     t,
            "industry":    row.get("industry") or None,
            "avg_vol_10d": row.get("average_volume_10d_calc"),
        }

    # Map original ticker → yfinance ticker
    tickers = list(tv_meta.keys())
    yf_map  = {t: t.replace(".", "-").replace("/", "-") for t in tickers}

    results: dict[str, dict[str, dict]] = {d: {} for d in dates}

    # Pre-compute ADR% (14-day avg of daily range / close)
    ADR_WINDOW = 14

    for orig, yf_t in yf_map.items():
        try:
            c  = raw["Close"][yf_t].dropna()
            h  = raw["High"][yf_t].dropna()
            lo = raw["Low"][yf_t].dropna()
            if len(c) < 5:
                continue
        except (KeyError, TypeError):
            continue

        daily_range_pct = ((h - lo) / c * 100).rolling(ADR_WINDOW).mean()

        for date_str in dates:
            if date_str not in c.index:
                continue
            idx = c.index.get_loc(date_str)
            if idx < 1:
                continue

            close = float(c.iloc[idx])
            prev_close = float(c.iloc[idx - 1])

            # 1-day change
            chg_1d = (close - prev_close) / prev_close * 100

            # Find idx-N trading-day closes (approx 4wk=20d, 13wk=65d, 34d=34d)
            def perf_back(n_days: int) -> float | None:
                back_idx = idx - n_days
                if back_idx < 0:
                    return None
                past = float(c.iloc[back_idx])
                return (close - past) / past * 100 if past else None

            perf_4w  = perf_back(20)
            perf_13w = perf_back(65)
            perf_34d = perf_back(34)

            adr = float(daily_range_pct.iloc[idx]) if not pd.isna(daily_range_pct.iloc[idx]) else None

            meta = tv_meta[orig]
            dv_str = fmt_dollar_vol(close, meta["avg_vol_10d"])

            results[date_str][orig] = {
                "ticker":        orig,
                "company":       meta["company"],
                "industry":      meta["industry"],
                "price":         round(close, 2),
                "change_1d_pct": round(chg_1d, 2),
                "perf_4w_pct":   round(perf_4w,  2) if perf_4w  is not None else None,
                "perf_13w_pct":  round(perf_13w, 2) if perf_13w is not None else None,
                "perf_34d_pct":  round(perf_34d, 2) if perf_34d is not None else None,
                "adr_pct":       round(adr, 2) if adr is not None else None,
                "dollar_volume": dv_str,
                "rs_ibd":        None,   # RS not available for historical dates
            }

    return results


def apply_filters(date_metrics: dict[str, dict]) -> dict[str, list[dict]]:
    """Apply each filter to the per-ticker metrics and return sorted stock lists."""
    filter_stocks: dict[str, list[dict]] = {}

    for fkey, (field, threshold, direction) in FILTER_DEFS.items():
        stocks = []
        for ticker, m in date_metrics.items():
            val = m.get(field)
            if val is None:
                continue
            if m.get("adr_pct") is not None and m["adr_pct"] < MIN_ADR_PCT:
                continue
            if direction == "up" and val >= threshold:
                stocks.append(m)
            elif direction == "down" and val <= -threshold:
                stocks.append(m)

        sort_field, asc = FILTER_SORT[fkey]
        stocks.sort(key=lambda s: (s.get(sort_field) is None,
                                   s.get(sort_field) or 0.0),
                    reverse=not asc)
        filter_stocks[fkey] = stocks

    return filter_stocks


def compact_stock(s: dict) -> dict:
    """Convert full stock dict to compact history format."""
    return {
        "t":   s.get("ticker", ""),
        "co":  s.get("company", ""),
        "p":   s.get("price"),
        "c":   s.get("change_1d_pct"),
        "adr": s.get("adr_pct"),
        "dv":  s.get("dollar_volume"),
        "qtd": None,
        "mtd": s.get("perf_4w_pct"),
        "d34": s.get("perf_34d_pct"),
        "rs":  s.get("rs_ibd"),
        "ind": s.get("industry"),
        "dma": s.get("above50dma_pct"),
        "ae":  s.get("atr_ext_val"),
    }


def patch_history_files(dates: list[str],
                        all_filter_stocks: dict[str, dict[str, list[dict]]]) -> None:
    FINVIZ_FILTERS = set(FILTER_DEFS.keys())

    for date_str in dates:
        path = HISTORY_DIR / f"{date_str}.json"
        if not path.exists():
            logger.warning("History file not found for %s — skipping", date_str)
            continue

        hist = json.loads(path.read_text(encoding="utf-8"))
        filter_stocks = all_filter_stocks.get(date_str, {})

        for fkey in FINVIZ_FILTERS:
            old_count = len(hist["filters"].get(fkey, []))
            stocks = filter_stocks.get(fkey, [])
            hist["filters"][fkey] = [compact_stock(s) for s in stocks]
            logger.info("  %s [%s]: %d → %d stocks", date_str, fkey, old_count, len(stocks))

        path.write_text(json.dumps(hist, separators=(",", ":")), encoding="utf-8")
        logger.info("Patched %s", date_str)


def main() -> None:
    # Find dates where ALL Finviz filters are empty
    dates_to_fix: list[str] = []
    for f in sorted(HISTORY_DIR.glob("[0-9]*.json")):
        hist = json.loads(f.read_text(encoding="utf-8"))
        filters = hist.get("filters", {})
        finviz_total = sum(len(filters.get(k, [])) for k in FILTER_DEFS)
        if finviz_total == 0:
            dates_to_fix.append(f.stem)
            logger.info("Will fix %s (all Finviz filters empty)", f.stem)

    if not dates_to_fix:
        logger.info("No dates need fixing.")
        return

    # Get universe
    tv_df  = get_tv_universe()
    tickers = tv_df["name"].dropna().tolist()

    # Download history covering earliest date - LOOKBACK_DAYS
    earliest = min(dates_to_fix)
    start_dt = (pd.Timestamp(earliest) - pd.Timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    end_dt   = (pd.Timestamp(max(dates_to_fix)) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    raw = download_history(tickers, start=start_dt, end=end_dt)

    # Compute metrics per date
    logger.info("Computing per-date metrics for %d dates …", len(dates_to_fix))
    all_metrics = compute_metrics(raw, tv_df, dates_to_fix)

    # Apply filters
    all_filter_stocks: dict[str, dict[str, list[dict]]] = {}
    for date_str, metrics in all_metrics.items():
        all_filter_stocks[date_str] = apply_filters(metrics)
        counts = {k: len(v) for k, v in all_filter_stocks[date_str].items()}
        logger.info("%s counts: %s", date_str, counts)

    # Patch history files
    patch_history_files(dates_to_fix, all_filter_stocks)
    logger.info("Done. Patched %d history files.", len(dates_to_fix))


if __name__ == "__main__":
    main()
