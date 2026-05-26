"""
backfill_rs_history.py
======================
One-time backfill: stamps IBD RS ratings AND industry onto all existing
public/breadth_history/YYYY-MM-DD.json archive files.

Uses TODAY's TradingView RS universe (the same computation as
breadth_stocks_builder.py).  IBD RS is a 12-month weighted metric that
changes slowly, so applying current ranks to files from the past few
weeks is a reasonable approximation.

Industry is a static classification that does not change over time.

Run once:
    python backfill_rs_history.py

Also automatically stamps rs_ibd and industry onto the latest live JSON files
(breadth_stocks_*.json) so the modal shows IBD RS and groups immediately
without waiting for tonight's nightly scraper.
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PUBLIC_DIR  = Path("public")
HISTORY_DIR = PUBLIC_DIR / "breadth_history"


# ---------------------------------------------------------------------------
# Reuse IBD RS helpers from breadth_stocks_builder
# ---------------------------------------------------------------------------

def _ibd_composite(p3m, p6m, p12m):
    def ok(v): return v is not None and v == v
    if not (ok(p3m) and ok(p12m)):
        return None
    q4 = p3m
    if ok(p6m):
        q3   = ((1 + p6m  / 100) / (1 + p3m  / 100) - 1) * 100
        q2q1 = ((1 + p12m / 100) / (1 + p6m  / 100) - 1) * 100
        return 0.4 * q4 + 0.2 * q3 + 0.4 * q2q1
    else:
        rem = ((1 + p12m / 100) / (1 + p3m / 100) - 1) * 100
        return 0.4 * q4 + 0.6 * rem


def _build_rs_lookup(composites: dict) -> dict:
    if not composites:
        return {}
    all_vals = sorted(composites.values())
    n = len(all_vals)
    result = {}
    for tkr, comp in composites.items():
        below = sum(1 for v in all_vals if v < comp)
        result[tkr] = max(1, min(99, round(below / n * 98) + 1))
    return result


# ---------------------------------------------------------------------------
# Fetch RS universe AND industry from TradingView (single call)
# ---------------------------------------------------------------------------

def build_lookups() -> tuple[dict, dict]:
    """Returns (rs_lookup, industry_lookup) both keyed by ticker."""
    try:
        from tradingview_screener import Query, col as tv_col
    except ImportError:
        logger.error("tradingview_screener not installed — pip install tradingview_screener")
        return {}, {}

    logger.info("Fetching TradingView screener for IBD RS + industry …")
    try:
        _, df = (
            Query()
            .select(
                "name", "close", "Perf.3M", "Perf.6M", "Perf.Y",
                "industry", "sector",
            )
            .where(
                tv_col("close") >= 2,
                tv_col("average_volume_10d_calc") >= 50_000,
                tv_col("type").isin(["stock", "dr"]),
                tv_col("exchange").isin(["NYSE", "NASDAQ", "AMEX", "NYSE ARCA"]),
            )
            .limit(10_000)
            .get_scanner_data()
        )
    except Exception as exc:
        logger.error("TradingView fetch failed: %s", exc)
        return {}, {}

    if df is None or getattr(df, "empty", True):
        logger.error("TradingView returned empty result")
        return {}, {}

    logger.info("TradingView: %d rows fetched", len(df))

    composites    = {}
    industry_lookup: dict[str, str] = {}

    for _, row in df.iterrows():
        tkr = str(row["name"])

        # Industry: prefer industry field, fall back to sector
        ind = row.get("industry") or row.get("sector") or None
        if ind and str(ind).strip() and str(ind) != "nan":
            industry_lookup[tkr] = str(ind).strip()

        # IBD RS composite
        try:
            p3m  = float(row["Perf.3M"]) if row.get("Perf.3M")  is not None else None
            p6m  = float(row["Perf.6M"]) if row.get("Perf.6M")  is not None else None
            p12m = float(row["Perf.Y"])  if row.get("Perf.Y")   is not None else None
        except (TypeError, ValueError):
            continue
        c = _ibd_composite(p3m, p6m, p12m)
        if c is not None:
            composites[tkr] = c

    rs_lookup = _build_rs_lookup(composites)
    logger.info("RS universe built: %d tickers ranked", len(rs_lookup))
    logger.info("Industry lookup built: %d tickers classified", len(industry_lookup))
    return rs_lookup, industry_lookup


# ---------------------------------------------------------------------------
# Backfill history archives
# ---------------------------------------------------------------------------

def backfill_history(rs_lookup: dict, industry_lookup: dict) -> None:
    files = sorted(HISTORY_DIR.glob("*.json"))
    logger.info("Backfilling %d history archive files …", len(files))

    total_rs = 0
    total_ind = 0
    total_missing = 0

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read %s: %s", path.name, exc)
            continue

        changed = False
        for filt, stocks in data.get("filters", {}).items():
            for s in stocks:
                tkr = s.get("t", "")

                # Stamp RS
                if rs_lookup:
                    rs = rs_lookup.get(tkr)
                    if rs is not None:
                        if s.get("rs") != rs:
                            s["rs"] = rs
                            changed = True
                        total_rs += 1
                    else:
                        total_missing += 1

                # Stamp industry
                ind = industry_lookup.get(tkr)
                if ind and s.get("ind") != ind:
                    s["ind"] = ind
                    changed = True
                    total_ind += 1

        if changed:
            path.write_text(
                json.dumps(data, separators=(",", ":")),
                encoding="utf-8",
            )
            logger.info("  Updated %s", path.name)
        else:
            logger.info("  Unchanged %s", path.name)

    logger.info(
        "History backfill complete: %d RS stamped, %d industry stamped, %d tickers not in RS universe",
        total_rs, total_ind, total_missing,
    )


# ---------------------------------------------------------------------------
# Also stamp the live breadth_stocks_*.json files
# ---------------------------------------------------------------------------

def stamp_live_files(rs_lookup: dict, industry_lookup: dict) -> None:
    live_files = list(PUBLIC_DIR.glob("breadth_stocks_*.json"))
    logger.info("Stamping %d live breadth_stocks_*.json files …", len(live_files))

    for path in live_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read %s: %s", path.name, exc)
            continue

        changed = False
        for s in data.get("stocks", []):
            tkr = s.get("ticker", "")

            if rs_lookup:
                rs = rs_lookup.get(tkr)
                if rs is not None and s.get("rs_ibd") != rs:
                    s["rs_ibd"] = rs
                    changed = True

            ind = industry_lookup.get(tkr)
            if ind and not s.get("industry"):
                s["industry"] = ind
                changed = True

        if changed:
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("  Stamped %s (%d stocks)", path.name, len(data.get("stocks", [])))

    logger.info("Live file stamping complete")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    rs_lookup, industry_lookup = build_lookups()
    if rs_lookup or industry_lookup:
        backfill_history(rs_lookup, industry_lookup)
        stamp_live_files(rs_lookup, industry_lookup)
    else:
        logger.error("No data fetched — nothing updated")
