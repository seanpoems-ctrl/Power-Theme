"""
backfill_missing_buckets.py
============================
Reconstructs filter buckets (up25q, dn25q, up25m, dn25m, up50m, dn50m,
up13_34, dn13_34, dn4) for archive dates where Finviz was rate-limited
and returned 0 stocks.

Unlike the previous backfill_archive_fields.py — which incorrectly filtered
the small pool of today's up4 movers — this script independently fetches a
BROAD universe of US equities and computes true QTD / MTD / 34D performance
as of each archive date using yfinance historical prices.

Universe: TradingView US equities (mkt cap >= $1B, price >= $5)  ~3-5K stocks
Prices:   yfinance batch download (start: 2 months before earliest target date)

Usage:
    python backfill_missing_buckets.py              # auto-detect dates needing fix
    python backfill_missing_buckets.py 2026-05-26   # specific date only
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date as date_type, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HISTORY_DIR   = Path("public") / "breadth_history"
MIN_CAP_B     = 1.0     # $1B — matches builder's MIN_CAP_B
MIN_PRICE     = 5.0
YFINANCE_BATCH = 200    # tickers per batch download
MAX_PER_FILTER = 500    # max stocks per bucket in archive

# Buckets to reconstruct only when currently empty (len == 0)
FILTER_RULES: dict[str, tuple[str, str, float]] = {
    "dn4":     ("c",   "le", -4.0),
    "up25q":   ("qtd", "ge", 25.0),
    "dn25q":   ("qtd", "le", -25.0),
    "up25m":   ("mtd", "ge", 25.0),
    "dn25m":   ("mtd", "le", -25.0),
    "up50m":   ("mtd", "ge", 50.0),
    "dn50m":   ("mtd", "le", -50.0),
    "up13_34": ("d34", "ge", 13.0),
    "dn13_34": ("d34", "le", -13.0),
}

# True = sort descending (best performers first), False = ascending (worst first)
SORT_DESC = {
    "dn4": False, "up25q": True, "dn25q": False,
    "up25m": True, "dn25m": False, "up50m": True, "dn50m": False,
    "up13_34": True, "dn13_34": False,
}


def _safe_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Step 1 — Identify archive dates that need reconstruction
# ---------------------------------------------------------------------------

def _find_dates_needing_fix(target_date: str | None) -> list[tuple[Path, str]]:
    """Return list of (archive_path, date_str) for archives with empty buckets."""
    results = []
    files = sorted(HISTORY_DIR.glob("*.json"))
    files = [f for f in files if " " not in f.stem]

    for path in files:
        if target_date and path.stem != target_date:
            continue
        try:
            data    = json.loads(path.read_text(encoding="utf-8"))
            filters = data.get("filters", {})
            needs   = any(
                k in filters and len(filters[k]) == 0
                for k in FILTER_RULES
            )
            if needs:
                results.append((path, path.stem))
        except Exception as exc:
            logger.warning("Could not read %s: %s", path.name, exc)

    return results


# ---------------------------------------------------------------------------
# Step 2 — Fetch broad US equity universe from TradingView
# ---------------------------------------------------------------------------

def _fetch_tv_universe() -> tuple[list[dict], dict[str, int]]:
    """
    Returns (universe_list, rs_lookup).

    universe_list: list of dicts with ticker, company, industry, mkt_cap_b,
                   avg_vol_10d, adr_pct (current, used as fallback).
    rs_lookup:     ticker -> IBD RS 1-99 (current).
    """
    try:
        from tradingview_screener import Query, col as tv_col  # type: ignore
    except ImportError:
        logger.error("tradingview_screener not installed. Install with: pip install tradingview-screener")
        return [], {}

    logger.info("Fetching TradingView universe (mkt cap >= $1B, US equities)…")
    try:
        _, df = (
            Query()
            .select(
                "name", "description", "industry", "sector",
                "close", "ATR",
                "market_cap_basic", "average_volume_10d_calc",
                "Perf.3M", "Perf.6M", "Perf.Y",
            )
            .where(
                tv_col("close") >= MIN_PRICE,
                tv_col("average_volume_10d_calc") >= 50_000,
                tv_col("type").isin(["stock", "dr"]),
                tv_col("exchange").isin(["NYSE", "NASDAQ", "AMEX", "NYSE ARCA"]),
            )
            .limit(10_000)
            .get_scanner_data()
        )
    except Exception as exc:
        logger.error("TradingView fetch failed: %s", exc)
        return [], {}

    # atr_pct = ATR as % of price (same formula as builder line 675)
    df = df.dropna(subset=["name", "close", "ATR"]).copy()
    df = df[df["close"] > 0].copy()
    df["atr_pct"] = df["ATR"] / df["close"] * 100

    logger.info("TradingView returned %d stocks", len(df))

    # ── IBD RS composite: 40% last Q + 20% Q-1 + 20% Q-2 + 20% Q-3 ─────────
    # Approximate from available Perf.3M / Perf.6M / Perf.Y
    composites: dict[str, float] = {}
    for _, row in df.iterrows():
        tkr  = str(row["name"])
        p3m  = _safe_float(row.get("Perf.3M"))
        p6m  = _safe_float(row.get("Perf.6M"))
        p12m = _safe_float(row.get("Perf.Y"))
        if None not in (p3m, p6m, p12m):
            q4 = p3m
            q3 = p6m  - p3m
            q2 = p12m - p6m
            composites[tkr] = 0.4 * q4 + 0.2 * q3 + 0.2 * q2 + 0.2 * q2  # q1≈q2 as fallback

    sorted_tickers = sorted(composites, key=composites.__getitem__)
    n = len(sorted_tickers)
    rs_lookup: dict[str, int] = {
        tkr: max(1, min(99, round(1 + (i / max(n - 1, 1)) * 98)))
        for i, tkr in enumerate(sorted_tickers)
    }

    # ── Build universe list (apply cap filter post-fetch) ─────────────────────
    universe = []
    for _, row in df.iterrows():
        tkr     = str(row["name"])
        mkt_cap = _safe_float(row.get("market_cap_basic"))
        if mkt_cap is None or mkt_cap < MIN_CAP_B * 1e9:
            continue
        industry = str(row.get("industry") or row.get("sector") or "")
        universe.append({
            "ticker":    tkr,
            "company":   str(row.get("description") or ""),
            "industry":  industry,
            "mkt_cap_b": round(mkt_cap / 1e9, 2),
            "avg_vol":   int(_safe_float(row.get("average_volume_10d_calc")) or 0),
            "adr_pct":   _safe_float(row.get("atr_pct")),   # current ATR%, fallback
        })

    logger.info("Universe size: %d stocks", len(universe))
    return universe, rs_lookup


# ---------------------------------------------------------------------------
# Step 3 — Batch-download yfinance history
# ---------------------------------------------------------------------------

def _download_history(tickers: list[str], start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Batch-download daily OHLC for all tickers from start to end (inclusive).
    Returns (close_df, high_df, low_df) where columns are tickers and index
    is DatetimeIndex of trading days.
    """
    all_close: list[pd.DataFrame] = []
    all_high:  list[pd.DataFrame] = []
    all_low:   list[pd.DataFrame] = []

    chunks = [tickers[i : i + YFINANCE_BATCH] for i in range(0, len(tickers), YFINANCE_BATCH)]
    logger.info("Downloading yfinance history (%d tickers, %d batches) from %s to %s …",
                len(tickers), len(chunks), start, end)

    for i, chunk in enumerate(chunks, 1):
        try:
            raw = yf.download(
                chunk,
                start=start,
                end=(pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                timeout=60,
                interval="1d",
                auto_adjust=True,
                progress=False,
            )
        except Exception as exc:
            logger.warning("Batch %d/%d download failed: %s", i, len(chunks), exc)
            continue

        if raw is None or raw.empty:
            continue

        is_multi = isinstance(raw.columns, pd.MultiIndex)
        if is_multi:
            l0 = set(raw.columns.get_level_values(0))
            metric_first = "Close" in l0

            def _col(metric, tkr):
                return raw[metric][tkr] if metric_first else raw[tkr][metric]
        else:
            def _col(metric, _tkr):
                return raw[metric]

        for tkr in chunk:
            try:
                close = _col("Close", tkr).dropna()
                high  = _col("High",  tkr).dropna()
                low   = _col("Low",   tkr).dropna()
                if len(close) < 2:
                    continue
                all_close.append(close.rename(tkr))
                all_high.append(high.rename(tkr))
                all_low.append(low.rename(tkr))
            except Exception:
                continue

        logger.info("  … batch %d/%d done", i, len(chunks))

    if not all_close:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    close_df = pd.concat(all_close, axis=1).sort_index()
    high_df  = pd.concat(all_high,  axis=1).sort_index()
    low_df   = pd.concat(all_low,   axis=1).sort_index()
    logger.info("History download complete: %d tickers × %d trading days",
                close_df.shape[1], close_df.shape[0])
    return close_df, high_df, low_df


# ---------------------------------------------------------------------------
# Step 4 — Compute per-ticker metrics as-of a specific date
# ---------------------------------------------------------------------------

def _metrics_as_of(
    close_df: pd.DataFrame,
    high_df:  pd.DataFrame,
    low_df:   pd.DataFrame,
    target_date: pd.Timestamp,
    quarter_start: pd.Timestamp,
    month_start:   pd.Timestamp,
) -> dict[str, dict]:
    """
    For each ticker in close_df, compute performance metrics AS OF target_date.
    Returns dict keyed by ticker.
    """
    # Truncate to [start, target_date]
    c = close_df.loc[:target_date]
    h = high_df.loc[:target_date]
    lo = low_df.loc[:target_date]

    if c.empty:
        return {}

    results: dict[str, dict] = {}

    for tkr in c.columns:
        try:
            cs = c[tkr].dropna()
            hs = h[tkr].dropna() if tkr in h.columns else pd.Series(dtype=float)
            ls = lo[tkr].dropna() if tkr in lo.columns else pd.Series(dtype=float)

            nc = len(cs)
            if nc < 2:
                continue

            latest = float(cs.iloc[-1])
            prev   = float(cs.iloc[-2])

            # Check the last date in cs is actually our target date (or very close)
            last_date = cs.index[-1].normalize()
            if abs((last_date - target_date).days) > 5:
                continue  # Data doesn't reach our target date; skip

            # Price and daily change on target date
            p   = round(latest, 2)
            chg = round((latest / prev - 1) * 100, 2)

            # ADR% — 14-day average of (High-Low)/Close
            adr_pct = None
            if len(hs) >= 5 and len(ls) >= 5:
                n_adr = min(len(hs), len(ls), nc, 14)
                adr_pct = round(
                    float(((hs.iloc[-n_adr:] - ls.iloc[-n_adr:]) / cs.iloc[-n_adr:] * 100).mean()), 1
                )

            # QTD — base = last close on or before quarter_start
            qtd = None
            tz = cs.index.tz
            qs = quarter_start.tz_localize(tz) if tz else quarter_start
            ms = month_start.tz_localize(tz)   if tz else month_start
            td = target_date.tz_localize(tz)   if tz else target_date

            qtd_base_s = cs[cs.index <= qs]
            if not qtd_base_s.empty:
                base = float(qtd_base_s.iloc[-1])
                if base > 0:
                    qtd = round((latest / base - 1) * 100, 2)

            # MTD — base = last close on or before month_start
            mtd = None
            mtd_base_s = cs[cs.index <= ms]
            if not mtd_base_s.empty:
                base = float(mtd_base_s.iloc[-1])
                if base > 0:
                    mtd = round((latest / base - 1) * 100, 2)

            # 34D — 34 trading days back
            d34 = None
            if nc >= 35:
                base = float(cs.iloc[-35])
                if base > 0:
                    d34 = round((latest / base - 1) * 100, 2)

            results[tkr] = {
                "p":   p,
                "c":   chg,
                "adr": adr_pct,
                "qtd": qtd,
                "mtd": mtd,
                "d34": d34,
            }

        except Exception:
            continue

    return results


# ---------------------------------------------------------------------------
# Step 5 — Build filter buckets from metrics
# ---------------------------------------------------------------------------

def _build_buckets(
    metrics:    dict[str, dict],   # ticker -> {p, c, adr, qtd, mtd, d34}
    universe:   list[dict],        # TradingView universe rows
    rs_lookup:  dict[str, int],
) -> dict[str, list[dict]]:
    """
    Apply FILTER_RULES to the metrics dict and return populated buckets.
    """
    # Index universe for O(1) lookup
    uni_idx = {u["ticker"]: u for u in universe}

    buckets: dict[str, list[dict]] = {k: [] for k in FILTER_RULES}

    for tkr, m in metrics.items():
        uni = uni_idx.get(tkr)
        if uni is None:
            continue  # not in our universe (mkt cap / price filtered out)

        # Build the compact stock record
        avg_vol   = uni.get("avg_vol") or 0
        price     = m["p"] or 0
        dv_raw    = round(price * avg_vol) if price and avg_vol else None

        stock = {
            "t":   tkr,
            "co":  uni.get("company", ""),
            "p":   m["p"],
            "c":   m["c"],
            "adr": m.get("adr") or uni.get("adr_pct"),  # prefer computed, fallback to TV
            "dv":  dv_raw,
            "qtd": m.get("qtd"),
            "mtd": m.get("mtd"),
            "d34": m.get("d34"),
            "rs":  rs_lookup.get(tkr),
            "ind": uni.get("industry") or None,
            "dma": None,
            "ae":  None,
        }

        # Check each filter rule
        for filter_key, (field, op, threshold) in FILTER_RULES.items():
            val = _safe_float(stock.get(field))
            if val is None:
                continue
            if op == "ge" and val >= threshold:
                buckets[filter_key].append(stock)
            elif op == "le" and val <= threshold:
                buckets[filter_key].append(stock)

    # Sort each bucket
    for filter_key, stocks in buckets.items():
        field    = FILTER_RULES[filter_key][0]
        desc     = SORT_DESC[filter_key]
        buckets[filter_key] = sorted(
            stocks,
            key=lambda s: _safe_float(s.get(field)) or 0,
            reverse=desc,
        )[:MAX_PER_FILTER]

    return buckets


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    target_date_arg = sys.argv[1] if len(sys.argv) > 1 else None

    # ── Find archives needing fix ─────────────────────────────────────────────
    to_fix = _find_dates_needing_fix(target_date_arg)
    if not to_fix:
        logger.info("No archives found with empty filter buckets. Nothing to do.")
        return

    logger.info("Archives needing reconstruction: %s", [d for _, d in to_fix])

    # ── Fetch TradingView universe (once, reused for all dates) ───────────────
    universe, rs_lookup = _fetch_tv_universe()
    if not universe:
        logger.error("Could not build stock universe. Aborting.")
        return

    tickers = [u["ticker"] for u in universe]

    # ── Determine yfinance download range ─────────────────────────────────────
    # Need data from at least 2 months before earliest target to cover QTD/34D.
    dates_sorted = sorted(d for _, d in to_fix)
    earliest = pd.Timestamp(dates_sorted[0])
    latest   = pd.Timestamp(dates_sorted[-1])

    # QTD base = end of prior quarter. Start downloading from that quarter's start.
    # Safest: start from 3 months before earliest target date.
    download_start = (earliest - pd.DateOffset(months=3)).strftime("%Y-%m-%d")
    download_end   = latest.strftime("%Y-%m-%d")

    # ── Download price history ────────────────────────────────────────────────
    close_df, high_df, low_df = _download_history(tickers, download_start, download_end)
    if close_df.empty:
        logger.error("yfinance download returned no data. Aborting.")
        return

    # ── Process each archive date ─────────────────────────────────────────────
    for path, date_str in to_fix:
        logger.info("=== Reconstructing buckets for %s ===", date_str)
        target_ts = pd.Timestamp(date_str)

        # Quarter and month starts for this date
        q_month        = ((target_ts.month - 1) // 3) * 3 + 1
        quarter_start  = pd.Timestamp(f"{target_ts.year}-{q_month:02d}-01")
        month_start    = pd.Timestamp(f"{target_ts.year}-{target_ts.month:02d}-01")

        logger.info("  QTD base: %s  |  MTD base: %s", quarter_start.date(), month_start.date())

        # Compute metrics as of target date
        metrics = _metrics_as_of(close_df, high_df, low_df, target_ts, quarter_start, month_start)
        logger.info("  Computed metrics for %d tickers", len(metrics))

        # Build filter buckets
        new_buckets = _build_buckets(metrics, universe, rs_lookup)
        for k, v in new_buckets.items():
            logger.info("    %-10s → %d stocks", k, len(v))

        # Load existing archive and merge (only fill empty buckets)
        data    = json.loads(path.read_text(encoding="utf-8"))
        filters = data.get("filters", {})
        changed = False

        for filter_key, new_stocks in new_buckets.items():
            if filter_key not in filters:
                continue
            if len(filters[filter_key]) > 0:
                logger.info("    %s already has %d stocks — skipping",
                            filter_key, len(filters[filter_key]))
                continue
            if new_stocks:
                filters[filter_key] = new_stocks
                changed = True
                logger.info("    ✓ %s filled with %d reconstructed stocks", filter_key, len(new_stocks))

        if changed:
            data["filters"] = filters
            path.write_text(
                json.dumps(data, separators=(",", ":")),
                encoding="utf-8",
            )
            logger.info("  Saved %s", path.name)
        else:
            logger.info("  No changes for %s", date_str)

    logger.info("Done.")


if __name__ == "__main__":
    main()
