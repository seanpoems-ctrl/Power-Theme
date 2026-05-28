"""
backfill_archive_fields.py
==========================
Patches public/breadth_history/YYYY-MM-DD.json archives with three fixes:

  1. "ae"  (atr_ext_val) — For atr_ext stocks missing the ATR extension
     multiple, compute it from stored fields:  ae = abs(c) / adr
     (c = daily change%, adr = ATR as % of price).  No API needed; exact.

  2. "dma" (above50dma_pct) — For above50dma stocks missing the % above
     50 DMA, fetch current SMA50 from TradingView screener and estimate
     dma = (close - SMA50) / SMA50 × 100.  Approximate for older dates.

  3. Reconstruct empty filter buckets — For any archive where filters like
     up25q / dn4 / up13_34 etc. have 0 stocks but the archive contains
     stocks with qtd/mtd/d34/c values, rebuild those buckets by filtering
     the available stock pool against each filter's threshold.

Run once:
    python backfill_archive_fields.py
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HISTORY_DIR = Path("public") / "breadth_history"

# ---------------------------------------------------------------------------
# Filter reconstruction rules  (field, operator "ge"|"le", threshold)
# Only applied to buckets that are currently empty (0 stocks).
# ---------------------------------------------------------------------------
RECONSTRUCT_RULES: dict[str, tuple[str, str, float]] = {
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


def _safe_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Phase 1 — backfill "ae" (atr_ext_val) from stored c / adr
# ---------------------------------------------------------------------------

def _fix_ae(filters: dict) -> bool:
    changed = False
    for s in filters.get("atr_ext", []):
        if s.get("ae") is not None:
            continue
        c   = _safe_float(s.get("c"))
        adr = _safe_float(s.get("adr"))
        if c is not None and adr is not None and adr > 0:
            s["ae"] = round(abs(c) / adr, 2)
            changed = True
    return changed


# ---------------------------------------------------------------------------
# Phase 2 — backfill "dma" (above50dma_pct) from TradingView current SMA50
# ---------------------------------------------------------------------------

def _fetch_dma_lookup(tickers: list[str]) -> dict[str, float]:
    """
    Returns {ticker: dma_pct} using TradingView screener (current data).
    dma_pct = (close - SMA50) / SMA50 × 100
    Falls back to yfinance if tradingview_screener is unavailable.
    """
    if not tickers:
        return {}

    # ── TradingView path ───────────────────────────────────────────────────
    try:
        from tradingview_screener import Query, col as tv_col  # type: ignore
        logger.info("Fetching SMA50 from TradingView for %d tickers …", len(tickers))
        batch = 1500
        lookup: dict[str, float] = {}
        for i in range(0, len(tickers), batch):
            chunk = tickers[i : i + batch]
            try:
                _, df = (
                    Query()
                    .select("name", "close", "SMA50")
                    .where(tv_col("name").isin(chunk))
                    .limit(len(chunk) + 50)
                    .get_scanner_data()
                )
                for _, row in df.iterrows():
                    tkr   = str(row["name"])
                    close = _safe_float(row.get("close"))
                    sma50 = _safe_float(row.get("SMA50"))
                    if close and sma50 and sma50 > 0:
                        lookup[tkr] = round((close - sma50) / sma50 * 100, 1)
            except Exception as exc:
                logger.warning("TradingView batch %d failed: %s", i, exc)
        logger.info("TradingView SMA50 lookup: %d/%d tickers resolved", len(lookup), len(tickers))
        return lookup
    except ImportError:
        logger.info("tradingview_screener not available; falling back to yfinance")

    # ── yfinance fallback ──────────────────────────────────────────────────
    try:
        import yfinance as yf
        import pandas as pd
        lookup = {}
        for tkr in tickers:
            try:
                hist = yf.Ticker(tkr).history(period="60d", interval="1d", auto_adjust=True)
                if not hist.empty and len(hist) >= 10:
                    sma50 = hist["Close"].mean()
                    close = float(hist["Close"].iloc[-1])
                    if sma50 > 0:
                        lookup[tkr] = round((close - sma50) / sma50 * 100, 1)
            except Exception:
                pass
        logger.info("yfinance SMA50 lookup: %d/%d tickers resolved", len(lookup), len(tickers))
        return lookup
    except ImportError:
        logger.warning("Neither tradingview_screener nor yfinance available; skipping dma backfill")
        return {}


def _fix_dma(filters: dict, lookup: dict[str, float]) -> bool:
    changed = False
    for s in filters.get("above50dma", []):
        if s.get("dma") is not None:
            continue
        dma = lookup.get(s.get("t", ""))
        if dma is not None:
            s["dma"] = dma
            changed = True
    return changed


# ---------------------------------------------------------------------------
# Phase 3 — reconstruct empty filter buckets from available stock pool
# ---------------------------------------------------------------------------

def _reconstruct_filters(filters: dict) -> bool:
    # Build a deduplicated pool of all stocks in this archive
    pool: dict[str, dict] = {}
    for stocks in filters.values():
        for s in stocks:
            tkr = s.get("t", "")
            if tkr and tkr not in pool:
                pool[tkr] = s

    if not pool:
        return False

    changed = False
    for filter_key, (field, op, threshold) in RECONSTRUCT_RULES.items():
        if filter_key not in filters:
            continue
        if len(filters[filter_key]) > 0:
            continue  # already has data

        candidates = []
        for s in pool.values():
            val = _safe_float(s.get(field))
            if val is None:
                continue
            if op == "ge" and val >= threshold:
                candidates.append(s)
            elif op == "le" and val <= threshold:
                candidates.append(s)

        if candidates:
            reverse = (op == "ge")
            candidates.sort(key=lambda x: _safe_float(x.get(field)) or 0, reverse=reverse)
            filters[filter_key] = candidates
            logger.info("    %s: reconstructed %d stocks from pool (partial)",
                        filter_key, len(candidates))
            changed = True

    return changed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    files = sorted(HISTORY_DIR.glob("*.json"))
    files = [f for f in files if " " not in f.stem]  # skip backup files

    if not files:
        logger.error("No archive files found in %s", HISTORY_DIR)
        return

    # ── Collect tickers that need dma backfill ────────────────────────────
    tickers_need_dma: list[str] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for s in data.get("filters", {}).get("above50dma", []):
                if s.get("dma") is None:
                    tkr = s.get("t", "")
                    if tkr and tkr not in tickers_need_dma:
                        tickers_need_dma.append(tkr)
        except Exception:
            pass

    dma_lookup = _fetch_dma_lookup(tickers_need_dma)

    # ── Process each archive ───────────────────────────────────────────────
    stats = {"ae": 0, "dma": 0, "reconstruct": 0, "files": 0}

    for path in files:
        try:
            data    = json.loads(path.read_text(encoding="utf-8"))
            filters = data.get("filters", {})
            changed = False

            # Phase 1 — ae
            if _fix_ae(filters):
                stats["ae"] += 1
                changed = True

            # Phase 2 — dma
            if _fix_dma(filters, dma_lookup):
                stats["dma"] += 1
                changed = True

            # Phase 3 — reconstruct missing filter buckets
            logger.debug("Checking filter reconstruction for %s …", path.stem)
            if _reconstruct_filters(filters):
                stats["reconstruct"] += 1
                changed = True

            if changed:
                data["filters"] = filters
                path.write_text(
                    json.dumps(data, separators=(",", ":")),
                    encoding="utf-8",
                )
                stats["files"] += 1
                logger.info("Updated %s", path.name)

        except Exception as exc:
            logger.warning("Could not process %s: %s", path.name, exc)

    logger.info(
        "Done: %d files updated | ae=%d  dma=%d  reconstruct=%d",
        stats["files"], stats["ae"], stats["dma"], stats["reconstruct"],
    )


if __name__ == "__main__":
    main()
