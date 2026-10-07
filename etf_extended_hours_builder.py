from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
etf_extended_hours_builder.py — pre-market and after-hours % change for the dashboard's ETFs
=============================================================================================
Feeds the Premarket / After hours periods of the ETF Performance heatmap (Watchlist → 🏆 Leadership → Themes). TradingView's
screener returns `premarket_change` and `postmarket_change` (% vs the prior regular-session close) for ETFs. It does NOT return
overnight data (the `overnight_*` fields come back null for every ticker), so there is no Overnight period.

These fields move during the day, so this runs from two workflows: the nightly scrape (after-hours is complete by then) and
the pre-market gapper workflow (8:55 AM ET, when the day's pre-market is already trading).

Output: public/etf_extended_hours.json  {generated_at, etfs: {TICKER: {premarket, afterhours}}}
"""
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
OUTPUT_PATH = ROOT / "public" / "etf_extended_hours.json"
ET = ZoneInfo("America/New_York")


def _pct(v):
    try:
        f = float(v)
        return round(f, 2) if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def main() -> None:
    try:
        meta = json.loads((ROOT / "public" / "etf_metadata.json").read_text(encoding="utf-8"))
        from tradingview_screener import Query, col
    except Exception as exc:  # noqa: BLE001
        logger.error("Setup failed (%s) - leaving existing file untouched", exc)
        return
    tickers = sorted({m["ticker"] for m in meta if m.get("ticker")})
    etfs: dict[str, dict] = {}
    try:
        for i in range(0, len(tickers), 400):
            chunk = tickers[i:i + 400]
            q = (Query().set_markets("america").select("name", "premarket_change", "postmarket_change")
                 .where(col("name").isin(chunk), col("type") == "fund").limit(len(chunk) + 100))
            q.query.pop("filter2", None)       # tradingview_screener >= 3.2 hides funds by default
            _, df = q.get_scanner_data()
            for _, r in df.iterrows():
                t = str(r["name"])
                if t in etfs:
                    continue
                etfs[t] = {"premarket": _pct(r.get("premarket_change")), "afterhours": _pct(r.get("postmarket_change"))}
    except Exception as exc:  # noqa: BLE001
        logger.error("TradingView lookup failed (%s) - leaving existing file untouched", exc)
        return
    if len(etfs) < 0.5 * len(tickers):
        logger.error("Only %d / %d ETFs resolved - refusing to publish", len(etfs), len(tickers))
        return
    payload = {"generated_at": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"), "etfs": etfs}
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    pre = sum(1 for e in etfs.values() if e["premarket"] is not None)
    post = sum(1 for e in etfs.values() if e["afterhours"] is not None)
    logger.info("Written %s: %d ETFs (%d with premarket, %d with after-hours)", OUTPUT_PATH, len(etfs), pre, post)


if __name__ == "__main__":
    main()
