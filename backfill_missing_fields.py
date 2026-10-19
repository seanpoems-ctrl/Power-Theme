from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
backfill_missing_fields.py — fill the gaps in public/breadth_history/YYYY-MM-DD.json (the Market Breadth drill-down snapshots)
===============================================================================================================================
The snapshots started on 2026-05-04 and gained columns over time, so the oldest days have blanks:
  * mc  (market cap, $B)  — only stored from 2026-05-30; every earlier row shows "—" in the Mkt Cap column.
  * rs / adr / dv / ind   — missing for a few days around the end of May (and for scattered rows).

What this does (idempotent — only touches rows where the field is null/missing, never overwrites a stored value):
  * mc  = that day's stored price × the stock's CURRENT share count (current market cap ÷ current close, from one TradingView call).
          Share counts drift slowly (buybacks / issuance), so this is an approximation — but it puts the stock in the right size bucket, and it is
          far better than a blank. Delisted / acquired tickers that TradingView no longer lists stay blank.
  * rs, adr, dv, ind = the same ticker's value from the nearest other snapshot (within ±7 days). RS is a percentile of a 12-month composite and ADR a
          14-day average, so neighbouring days are very close; the true value for that exact day can't be reconstructed afterwards.
Each patched file gets a top-level "approx": ["mc", "rs", …] list naming the fields that contain estimates; the UI shows "≈" next to estimated
market caps. Fields that are legitimately empty by design are left alone: each scanner stores only its own performance column (qtd / mtd / d34),
and ae / dma exist only on the ATR-extension / above-50DMA lists.

Run:  python backfill_missing_fields.py            (dry run prints counts; add --write to save)
"""
import glob
import json
import logging
from datetime import date
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
HIST = ROOT / "public" / "breadth_history"
NEIGHBOUR_FIELDS = ("rs", "adr", "dv", "ind")
MAX_DAYS = 7


def _norm(t: str) -> str:
    return str(t).replace(".", "-").upper()


def tv_shares() -> dict[str, float]:
    """ticker -> shares outstanding ≈ current market cap ÷ current close."""
    from tradingview_screener import Query, col
    out: dict[str, float] = {}
    # TradingView serves at most ~10,000 rows per query: take the largest 10,000 by market cap, then (below) retry the rest by volume rank
    for order, limit in (("market_cap_basic", 10000), ("volume", 10000)):
        q = (Query().set_markets("america").select("name", "close", "market_cap_basic")
             .where(col("market_cap_basic") > 0).order_by(order, ascending=False).limit(limit))
        q.query.pop("filter2", None)
        try:
            _, df = q.get_scanner_data()
        except Exception as exc:        # noqa: BLE001 - TradingView answers null past its last page
            logger.info("query by %s failed (%s)", order, type(exc).__name__)
            continue
        if df is None or df.empty:
            break
        for _, r in df.iterrows():
            try:
                cap, px = float(r["market_cap_basic"]), float(r["close"])
            except (TypeError, ValueError):
                continue
            if cap > 0 and px > 0:
                out.setdefault(_norm(r["name"]), cap / px)
    logger.info("TradingView share counts for %d tickers", len(out))
    return out


def main() -> None:
    write = "--write" in sys.argv
    files = sorted(glob.glob(str(HIST / "*.json")))
    days = []
    for f in files:
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        days.append((f, d))
    logger.info("%d snapshot files", len(days))

    # per-ticker known values by date (for the neighbour fill)
    known: dict[str, dict[str, list]] = {k: {} for k in NEIGHBOUR_FIELDS}      # field -> ticker -> [(date, value)]
    for _, d in days:
        day = date.fromisoformat(d["date"])
        for lst in d["filters"].values():
            for s in (lst if isinstance(lst, list) else lst.get("stocks", [])):
                for k in NEIGHBOUR_FIELDS:
                    if s.get(k) not in (None, ""):
                        known[k].setdefault(s["t"], []).append((day, s[k]))

    def nearest(field: str, ticker: str, day: date):
        best = None
        for dd, v in known[field].get(ticker, ()):
            gap = abs((dd - day).days)
            if gap <= MAX_DAYS and (best is None or gap < best[0]):
                best = (gap, v)
        return best[1] if best else None

    shares = tv_shares()
    totals = {"mc": 0, "rs": 0, "adr": 0, "dv": 0, "ind": 0}
    still = {"mc": 0, "rs": 0, "adr": 0, "dv": 0, "ind": 0}
    files_touched = 0
    for f, d in days:
        day = date.fromisoformat(d["date"])
        approx = set(d.get("approx", []))
        changed = False
        for lst in d["filters"].values():
            for s in (lst if isinstance(lst, list) else lst.get("stocks", [])):
                if s.get("mc") is None and s.get("p"):
                    sh = shares.get(_norm(s["t"]))
                    if sh:
                        s["mc"] = round(s["p"] * sh / 1e9, 3)
                        totals["mc"] += 1; approx.add("mc"); changed = True
                    else:
                        still["mc"] += 1
                for k in NEIGHBOUR_FIELDS:
                    if s.get(k) in (None, ""):
                        v = nearest(k, s["t"], day)
                        if v is not None:
                            s[k] = v; totals[k] += 1; approx.add(k); changed = True
                        else:
                            still[k] += 1
        if changed:
            d["approx"] = sorted(approx)
            files_touched += 1
            if write:
                Path(f).write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    logger.info("filled: %s | still blank: %s | files touched: %d | %s", totals, still, files_touched, "WRITTEN" if write else "dry run (use --write)")


if __name__ == "__main__":
    main()
