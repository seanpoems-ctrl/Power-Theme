from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
industry_intraday_builder.py — "Intra" column for the Industry Matrix (Scanner tab)
====================================================================================
The Industry Matrix tiles come from Finviz's industry groups page, which only has Change (1D), 1W, 1M … — no "intraday" (today's open → now) figure.
This builds one: for every industry, the market-cap-weighted average of (close ÷ open − 1) across its stocks.

  * Industry membership = public/stock_db.json (ticker → industry). Its industry names are the same Yahoo/Morningstar-style names the Matrix uses
    ("Software - Application", "Insurance - Reinsurance", "Oil & Gas E&P" …), so no name mapping is needed.
  * Prices = ONE TradingView screener call (open, close, market cap) for NASDAQ/NYSE/AMEX stocks above MIN_CAP with some volume.
  * Industries with fewer than MIN_STOCKS priced stocks get no value (the tile shows "—").
  * Cap-weighting keeps one micro-cap's 30 % day from setting an industry's number; each stock's weight is capped (MAX_WEIGHT_SHARE) so a mega-cap
    can't be the whole industry either.

Run after the close it is the day's open→close move ("Intra" = what the day's candle body did); during the session it is open→last.
Output: public/industry_intraday.json  { generated_at, date, count, values: { "<industry>": { "intra": 0.42, "n": 17 } } }

The same TradingView call also feeds public/industry_stocks.json — the industry's real stocks (largest TOP_STOCKS by market cap) for the Industry
Matrix drill-down, so an industry tile that isn't one of the scanned top-5 themes shows its own companies instead of its parent theme's proxy ETF
(Apparel Manufacturing used to list XLY's AMZN / TSLA).  { generated_at, date, industries: { "<industry>": [ {t, n, p, i, d, w, m, m3, m6, y, dv, adr, cap, ext, rng}, … ] } }
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
OUT = ROOT / "public" / "industry_intraday.json"
DB = ROOT / "public" / "stock_db.json"
ET = ZoneInfo("America/New_York")

MIN_CAP = 500e6            # ignore small caps - not traded, and keeps every listed stock inside the stock panel's data sources
MIN_AVG_VOL = 100_000
MIN_STOCKS = 3
MAX_WEIGHT_SHARE = 0.35    # one stock can carry at most 35 % of an industry's weight
TOP_STOCKS = 30            # stocks kept per industry in industry_stocks.json
OUT_STOCKS = ROOT / "public" / "industry_stocks.json"
OUT_LOOKUP = ROOT / "public" / "stock_lookup.json"   # {ticker: [market cap $B, TradingView industry, exchange NASDAQ/NYSE/AMEX]} for EVERY stock above MIN_CAP - chart stats / stock panel fallback + the Nasdaq % column


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _r(v):
    return round(v, 2) if v is not None else None


def build() -> dict | None:
    from tradingview_screener import Query, col
    try:
        db = json.loads(DB.read_text(encoding="utf-8"))
    except Exception as exc:        # noqa: BLE001
        logger.error("stock_db.json unreadable: %s", exc)
        return None
    industry_of = {r["ticker"]: r.get("industry") for r in db if r.get("ticker") and r.get("industry")}
    company_of = {r["ticker"]: r.get("company") for r in db if r.get("ticker")}

    q = (Query().set_markets("america")
         .select("name", "open", "close", "market_cap_basic", "average_volume_10d_calc", "change", "Perf.W", "Perf.1M", "Perf.3M", "Perf.6M", "Perf.Y", "ATR", "SMA50",
                 "price_52_week_high", "price_52_week_low", "industry", "exchange")
         .where(col("type").isin(["stock", "dr"]), col("exchange").isin(["NASDAQ", "NYSE", "AMEX"]),
                col("market_cap_basic") >= MIN_CAP, col("average_volume_10d_calc") >= MIN_AVG_VOL)
         .order_by("market_cap_basic", ascending=False).limit(5000))
    q.query.pop("filter2", None)
    _, df = q.get_scanner_data()
    logger.info("TradingView: %d stocks above $%dM", len(df), MIN_CAP // 1_000_000)

    groups: dict[str, list[tuple[float, float]]] = {}
    stocks: dict[str, list[dict]] = {}
    lookup: dict[str, list] = {}
    for _, r in df.iterrows():
        t = str(r["name"])
        _cap = _num(r.get("market_cap_basic"))
        if _cap:
            lookup[t] = [round(_cap / 1e9, 2), str(r.get("industry") or "") or None, str(r.get("exchange") or "") or None]
        ind = industry_of.get(t)
        o, c, cap = _num(r.get("open")), _num(r.get("close")), _num(r.get("market_cap_basic"))
        if not ind or not o or not c or not cap or o <= 0:
            continue
        intra = (c / o - 1) * 100
        groups.setdefault(ind, []).append((intra, cap))
        vol, atr, sma50 = _num(r.get("average_volume_10d_calc")), _num(r.get("ATR")), _num(r.get("SMA50"))
        hi, lo = _num(r.get("price_52_week_high")), _num(r.get("price_52_week_low"))
        stocks.setdefault(ind, []).append({
            "t": t, "n": company_of.get(t) or "", "p": round(c, 2), "i": round(intra, 2),
            "d": _r(_num(r.get("change"))), "w": _r(_num(r.get("Perf.W"))), "m": _r(_num(r.get("Perf.1M"))),
            "m3": _r(_num(r.get("Perf.3M"))), "m6": _r(_num(r.get("Perf.6M"))), "y": _r(_num(r.get("Perf.Y"))),
            "ext": round(((c / sma50) - 1) / (atr / c), 2) if sma50 and atr else None,
            "rng": round((c - lo) / (hi - lo) * 100) if hi and lo and hi > lo else None,
            "dv": round(c * vol) if vol else None, "adr": round(atr / c * 100, 1) if atr else None, "cap": round(cap),
        })

    values = {}
    for ind, rows in groups.items():
        if len(rows) < MIN_STOCKS:
            continue
        tot = sum(w for _, w in rows)
        capw = [min(w / tot, MAX_WEIGHT_SHARE) for _, w in rows]
        s = sum(capw)
        values[ind] = {"intra": round(sum(p * w for (p, _), w in zip(rows, capw)) / s, 2), "n": len(rows)}
    logger.info("Industries with a value: %d (of %d with stocks)", len(values), len(groups))
    if len(values) < 40:
        logger.error("Only %d industries - refusing to publish", len(values))
        return None
    now = datetime.now(ET)
    industries = {ind: sorted(rows, key=lambda x: -x["cap"])[:TOP_STOCKS] for ind, rows in stocks.items()}
    stamp = now.strftime("%Y-%m-%d %H:%M ET")
    return {"generated_at": stamp, "date": now.strftime("%Y-%m-%d"), "count": len(values), "values": values,
            "_stocks": {"generated_at": stamp, "date": now.strftime("%Y-%m-%d"), "industries": industries},
            "_lookup": {"generated_at": stamp, "date": now.strftime("%Y-%m-%d"), "s": lookup}}


def main() -> None:
    import sys
    lookup_only = "--lookup-only" in sys.argv      # refresh just stock_lookup.json (leaves the nightly industry files alone)
    payload = build()
    if not payload:
        logger.error("Industry intraday build failed - leaving the existing file untouched")
        return
    lookup_payload = payload.pop("_lookup")
    if len(lookup_payload["s"]) >= 1500:     # same sanity idea as the industry floor: never publish a half-empty lookup
        OUT_LOOKUP.write_text(json.dumps(lookup_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        logger.info("Written %s (%d stocks)", OUT_LOOKUP, len(lookup_payload["s"]))
    else:
        logger.error("Only %d stocks in the lookup - not writing %s", len(lookup_payload["s"]), OUT_LOOKUP)
    if lookup_only:
        return
    stocks_payload = payload.pop("_stocks")
    OUT_STOCKS.write_text(json.dumps(stocks_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    logger.info("Written %s (%d industries)", OUT_STOCKS, len(stocks_payload["industries"]))
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    logger.info("Written %s (%d industries)", OUT, payload["count"])
    top = sorted(payload["values"].items(), key=lambda kv: -kv[1]["intra"])
    print("best :", [(k, v["intra"], v["n"]) for k, v in top[:4]])
    print("worst:", [(k, v["intra"], v["n"]) for k, v in top[-4:]])


if __name__ == "__main__":
    main()
