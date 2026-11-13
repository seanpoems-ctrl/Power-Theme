from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
spdr_holdings_builder.py — FULL holdings lists for the State Street (SPDR) ETFs
=============================================================================
Yahoo (what scraper.py stores in thematic_data.json `etf_holdings`) only returns each fund's top ~10 holdings, which are the biggest and
slowest names. State Street publishes every holding of its funds as a daily xlsx, so for the SPDR funds in our ETF universe (XHB, XLK,
XBI, XRT, KRE, XSD …) we can list the whole fund. The other issuers (iShares, Invesco, VanEck, Global X …) have no file we can read, so
those stay at the top 10.

Output: public/spdr_holdings.json
  { generated_at, as_of, etfs: { "XHB": [ {t, n, wt, p, i, d, w, m, ext, dv, adr, cap, ind}, … ] } }
    wt  = weight in the fund (%)            p   = price                      i = intraday % (close vs open)
    d / w / m = 1D / 1W / 1M %              ext = ATR% multiple from the 50-MA (same formula as everywhere else)
    dv  = avg daily $ volume (10d)          adr = ATR / close x 100          cap = market cap ($)   ind = TradingView industry
  Only US-listed (NASDAQ / NYSE / AMEX) stocks / ADRs with market cap >= $500M (same floor as the stored holdings); everything else
  (cash, futures, foreign lines, small caps) is left out. Rows are sorted by weight.

Consumers: the Theme Leaderboard's "Highest-ADR stocks" panel (App.js, ThemeHighAdrStocks).
The file is only rewritten when at least MIN_FUNDS funds were read, so a blocked / failed night keeps the previous file.
"""
import io
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
OUTPUT_PATH = ROOT / "public" / "spdr_holdings.json"
ET = ZoneInfo("America/New_York")
URL = "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{t}.xlsx"
MIN_CAP = 500e6
MIN_FUNDS = 15            # the SPDR funds in our universe number ~34; refuse to publish a mostly-empty file
UA = {"User-Agent": "Mozilla/5.0"}


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _r(v, n=2):
    return None if v is None else round(v, n)


def fetch_fund(etf: str) -> tuple[str, str | None, list[tuple[str, str, float]]] | None:
    """(etf, as_of, [(ticker, name, weight %)]) from State Street's daily file, or None when the fund isn't a SPDR / the file is unreadable."""
    import openpyxl
    try:
        r = requests.get(URL.format(t=etf.lower()), headers=UA, timeout=30)
        if r.status_code != 200 or r.content[:2] != b"PK":
            return None
        rows = list(openpyxl.load_workbook(io.BytesIO(r.content), data_only=True, read_only=True).active.iter_rows(values_only=True))
    except Exception as exc:    # noqa: BLE001
        logger.warning("%s: %s", etf, exc)
        return None
    as_of = next((str(x[1]).replace("As of", "").strip() for x in rows[:6] if x and x[0] == "Holdings:" and x[1]), None)
    hi = next((i for i, x in enumerate(rows) if x and x[0] == "Name"), None)
    if hi is None:
        return None
    head = [str(c or "").strip() for c in rows[hi]]
    try:
        ti, ni, wi = head.index("Ticker"), head.index("Name"), head.index("Weight")
    except ValueError:
        return None
    out = []
    for x in rows[hi + 1:]:
        if not x or len(x) <= max(ti, ni, wi):
            continue
        t, w = str(x[ti] or "").strip().upper(), _num(x[wi])
        if not t or t == "-" or w is None or w <= 0:
            continue
        out.append((t.replace(" ", ""), str(x[ni] or "").strip(), w))
    return (etf, as_of, out) if out else None


def lookup(tickers: list[str]) -> dict[str, dict]:
    """TradingView row per ticker (US exchanges, stock / dr, cap >= MIN_CAP)."""
    from tradingview_screener import Query, col
    out: dict[str, dict] = {}
    for i in range(0, len(tickers), 400):
        chunk = tickers[i:i + 400]
        q = (Query().set_markets("america")
             .select("name", "open", "close", "market_cap_basic", "average_volume_10d_calc", "change", "Perf.W", "Perf.1M", "ATR", "SMA50", "industry")
             .where(col("name").isin(chunk), col("type").isin(["stock", "dr"]), col("exchange").isin(["NASDAQ", "NYSE", "AMEX"]),
                    col("market_cap_basic") >= MIN_CAP)
             .limit(len(chunk) + 100))
        q.query.pop("filter2", None)
        _, df = q.get_scanner_data()
        for _, r in df.iterrows():
            t = str(r["name"])
            if t in out:
                continue
            c, o, cap, vol, atr, sma50 = (_num(r.get(k)) for k in ("close", "open", "market_cap_basic", "average_volume_10d_calc", "ATR", "SMA50"))
            if not c or not cap:
                continue
            out[t] = {
                "p": round(c, 2), "i": _r((c / o - 1) * 100) if o else None, "d": _r(_num(r.get("change"))),
                "w": _r(_num(r.get("Perf.W"))), "m": _r(_num(r.get("Perf.1M"))),
                "ext": round(((c / sma50) - 1) / (atr / c), 2) if sma50 and atr and atr > 0 else None,
                "dv": round(c * vol) if vol else None, "adr": _r(atr / c * 100) if atr and atr > 0 else None,
                "cap": round(cap), "ind": str(r.get("industry") or "") or None,
            }
    return out


def build() -> dict | None:
    try:
        etfs = sorted(json.loads((ROOT / "public" / "thematic_data.json").read_text(encoding="utf-8")).get("etf_holdings", {}))
    except Exception as exc:    # noqa: BLE001
        logger.error("thematic_data.json unreadable (%s)", exc)
        return None
    with ThreadPoolExecutor(max_workers=8) as ex:
        funds = [f for f in ex.map(fetch_fund, etfs) if f]
    logger.info("SPDR funds read: %d of %d ETFs", len(funds), len(etfs))
    if len(funds) < MIN_FUNDS:
        logger.error("Only %d funds read (need %d) - leaving the existing file untouched", len(funds), MIN_FUNDS)
        return None
    # TradingView spells class shares BRK.B; State Street too, but Yahoo-style BRK-B can appear - normalise to the dot
    tickers = sorted({t.replace("-", ".") for _, _, rows in funds for t, _, _ in rows})
    tv = lookup(tickers)
    logger.info("TradingView matched %d of %d tickers", len(tv), len(tickers))
    out, as_of = {}, None
    for etf, d, rows in funds:
        as_of = as_of or d
        held = []
        for t, name, w in rows:
            row = tv.get(t.replace("-", "."))
            if row:
                held.append({"t": t, "n": name, "wt": round(w, 3), **row})
        if held:
            out[etf] = sorted(held, key=lambda h: -h["wt"])
    now = datetime.now(ET)
    return {"generated_at": now.strftime("%Y-%m-%d %H:%M ET"), "as_of": as_of, "etfs": out}


def main() -> None:
    data = build()
    if not data:
        return
    OUTPUT_PATH.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    logger.info("Wrote %s: %d funds, %d holdings (%.0f KB)", OUTPUT_PATH.name, len(data["etfs"]),
                sum(len(v) for v in data["etfs"].values()), OUTPUT_PATH.stat().st_size / 1024)


if __name__ == "__main__":
    main()
