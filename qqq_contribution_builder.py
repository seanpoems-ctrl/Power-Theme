from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
qqq_contribution_builder.py — "QQQ / QQQE contribution by theme" (Themes -> Theme Leaders)
=========================================================================================
Which themes actually move QQQ?  For each fund this takes the FULL holdings list, puts every stock in the theme its industry belongs to on the
Theme Leaderboard / Industry Matrix (`industry_rankings[].fine_theme`), and sums weight x return per theme -> points of fund return.

  * QQQ  = Invesco's daily holdings feed (public JSON, ~100 names, market-cap-ish weights).
  * QQQE = Direxion's daily holdings CSV (equal weight, ~1 % each).  Neither issuer is covered by spdr_holdings_builder.py.
  * Stock returns (1W / 1M / 3M / YTD, price only) and the industry come from public/industry_stocks.json (TradingView, nightly).  Stocks missing from it
    (new listings, < $500M) get their returns from one TradingView call and are shown under "Unclassified".
  * Apple's industry (Consumer Electronics) sits inside the Semiconductors theme bucket in Finviz's grouping, which would make "Semiconductors" look 8 points
    bigger than it is, so Consumer Electronics is reported as its own row.

Contribution is weight x stock return, so the rows add up to roughly the fund's own move (not exactly: weights are the latest holdings applied to
trailing returns, the fund's cash / rebalancing is ignored, and TradingView's 3M window is calendar-based).

Output: public/qqq_contribution.json
  { generated_at, funds: { QQQ: { as_of, n, total: {w, p1w, p1m, p3m, pytd}, themes: [ {theme, w, n, p1w, p1m, p3m, pytd, top: [[ticker, weight, r1w, r1m, r3m, rytd], ...]} ] } } }
Only rewritten when both funds have weights (fresh, or the previous file's when a feed is blocked), so a blocked night still refreshes the returns.
"""
import csv
import io
import json
import logging
import math
import os
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "public" / "qqq_contribution.json"
ET = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0"}
QQQ_URL = "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/QQQ/holdings/fund?idType=ticker&interval=daily&productType=ETF"
QQQE_URL = "https://www.direxion.com/holdings/QQQE.csv"
WINDOWS = ("p1w", "p1m", "p3m", "pytd")
TOP_PER_THEME = 40          # keep every stock (a theme has <= ~25), so the page can rank the biggest movers per window - QQQE is equal weight, so "top by weight" would be arbitrary
SPLIT_INDUSTRIES = {"Consumer Electronics": "Consumer Electronics (Apple)"}   # own row instead of the Semiconductors bucket


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _parse_qqq(j: dict) -> tuple[str, list[tuple[str, float]]] | None:
    rows = [(str(h["ticker"]).strip().upper(), float(h["percentageOfTotalNetAssets"])) for h in j.get("holdings", [])
            if h.get("securityTypeName") == "Common Stock" and h.get("ticker") and _num(h.get("percentageOfTotalNetAssets"))]
    return (str(j.get("effectiveDate") or ""), rows) if len(rows) >= 80 else None


def fetch_qqq() -> tuple[str, list[tuple[str, float]]] | None:
    """Invesco's feed answers HTTP 406 for stretches (seen from this machine after a handful of calls), so try a few times and, as a last resort,
    QQQ_HOLDINGS_FILE (a saved copy of the JSON) lets the weights be supplied by hand."""
    manual = os.environ.get("QQQ_HOLDINGS_FILE")
    if manual:
        try:
            return _parse_qqq(json.loads(Path(manual).read_text(encoding="utf-8")))
        except Exception as exc:    # noqa: BLE001
            logger.warning("QQQ_HOLDINGS_FILE unreadable: %s", exc)
    for attempt in range(3):
        try:
            r = requests.get(QQQ_URL, headers=UA, timeout=30)
            r.raise_for_status()
            got = _parse_qqq(r.json())
            if got:
                return got
        except Exception as exc:    # noqa: BLE001
            logger.warning("QQQ holdings (try %d): %s", attempt + 1, exc)
        time.sleep(5 * (attempt + 1))
    return None


def previous_weights(fund: str) -> tuple[str, list[tuple[str, float]]] | None:
    """Weights from the last good output (every stock is stored), so a blocked night still refreshes the returns."""
    try:
        f = json.loads(OUT.read_text(encoding="utf-8"))["funds"][fund]
        rows = [(x[0], float(x[1])) for t in f["themes"] for x in t["top"]]
        return (f["as_of"], rows) if len(rows) >= 80 else None
    except Exception:    # noqa: BLE001
        return None


def fetch_qqqe() -> tuple[str, list[tuple[str, float]]] | None:
    try:
        r = requests.get(QQQE_URL, headers=UA, timeout=30)
        r.raise_for_status()
        lines = r.text.splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.startswith('"TradeDate"'))
        rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))
    except Exception as exc:    # noqa: BLE001
        logger.warning("QQQE holdings: %s", exc)
        return None
    out = [(str(x["StockTicker"]).strip().upper(), float(x["HoldingsPercent"])) for x in rows if x.get("StockTicker") and _num(x.get("HoldingsPercent"))]
    as_of = (rows[0].get("TradeDate") or "").split(" ")[0] if rows else ""
    try:
        as_of = datetime.strptime(as_of, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        pass
    return (as_of, out) if len(out) >= 80 else None


def tv_returns(tickers: list[str]) -> dict[str, dict]:
    """1W / 1M / 3M / YTD for tickers the industry file doesn't have (TradingView, any cap)."""
    if not tickers:
        return {}
    from tradingview_screener import Query, col
    q = (Query().set_markets("america").select("name", "Perf.W", "Perf.1M", "Perf.3M", "Perf.YTD")
         .where(col("name").isin(tickers), col("exchange").isin(["NASDAQ", "NYSE", "AMEX"])).limit(len(tickers) + 50))
    q.query.pop("filter2", None)
    out: dict[str, dict] = {}
    try:
        _, df = q.get_scanner_data()
    except Exception as exc:    # noqa: BLE001
        logger.warning("TradingView fallback failed: %s", exc)
        return out
    for _, r in df.iterrows():
        out.setdefault(str(r["name"]), {"w": _num(r.get("Perf.W")), "m": _num(r.get("Perf.1M")), "m3": _num(r.get("Perf.3M")), "ytd": _num(r.get("Perf.YTD"))})
    return out


def build() -> dict | None:
    try:
        ind = json.loads((ROOT / "public" / "industry_stocks.json").read_text(encoding="utf-8"))["industries"]
        rank = json.loads((ROOT / "public" / "thematic_data.json").read_text(encoding="utf-8")).get("industry_rankings", [])
    except Exception as exc:    # noqa: BLE001
        logger.error("input files unreadable: %s", exc)
        return None
    fine_of = {x["name"]: x.get("fine_theme") for x in rank if x.get("name")}
    by: dict[str, dict] = {}
    for name, rows in ind.items():
        for r in rows:
            by.setdefault(r["t"], {**r, "industry": name})

    funds_raw = {"QQQ": fetch_qqq(), "QQQE": fetch_qqqe()}
    for k in funds_raw:
        if not funds_raw[k] and (old := previous_weights(k)):
            logger.warning("%s feed unavailable - reusing the previous weights (as of %s)", k, old[0])
            funds_raw[k] = old
    if not all(funds_raw.values()):
        logger.error("A holdings feed failed (%s) - leaving the existing file untouched", [k for k, v in funds_raw.items() if not v])
        return None
    # TradingView class shares use a dot; Invesco / Direxion may use a dash or space
    norm = lambda t: t.replace("-", ".").replace(" ", "")      # noqa: E731
    missing = sorted({norm(t) for _, (_, rows) in funds_raw.items() for t, _ in rows if norm(t) not in by})
    extra = tv_returns(missing)
    logger.info("%d holdings not in industry_stocks.json (%d priced via TradingView): %s", len(missing), len(extra), missing)

    funds = {}
    for fund, (as_of, rows) in funds_raw.items():
        groups: dict[str, dict] = defaultdict(lambda: {"w": 0.0, "n": 0, **{k: 0.0 for k in WINDOWS}, "top": []})
        total = {"w": 0.0, **{k: 0.0 for k in WINDOWS}}
        for t, w in rows:
            t = norm(t)
            r = by.get(t)
            if r:
                theme = SPLIT_INDUSTRIES.get(r["industry"]) or fine_of.get(r["industry"]) or "Unclassified"
                rets = (r.get("w"), r.get("m"), r.get("m3"), r.get("ytd"))
            else:
                theme, e = "Unclassified", extra.get(t) or {}
                rets = (e.get("w"), e.get("m"), e.get("m3"), e.get("ytd"))
            g = groups[theme]
            g["w"] += w
            g["n"] += 1
            for k, v in zip(WINDOWS, rets):
                if v is not None:
                    g[k] += w * v / 100
                    total[k] += w * v / 100
            total["w"] += w
            g["top"].append([t, round(w, 2), *[None if v is None else round(v, 1) for v in rets]])
        themes = []
        for theme, g in groups.items():
            g["top"].sort(key=lambda x: -x[1])
            themes.append({"theme": theme, "w": round(g["w"], 2), "n": g["n"], **{k: round(g[k], 3) for k in WINDOWS}, "top": g["top"][:TOP_PER_THEME]})
        themes.sort(key=lambda x: -x["w"])
        funds[fund] = {"as_of": as_of, "n": len(rows), "total": {k: round(v, 3) for k, v in total.items()}, "themes": themes}
    return {"generated_at": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"), "funds": funds}


def main() -> None:
    data = build()
    if not data:
        return
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for f, d in data["funds"].items():
        logger.info("%s as of %s: %d stocks, %d themes, total 1M %.2f pts", f, d["as_of"], d["n"], len(d["themes"]), d["total"]["p1m"])
    logger.info("Wrote %s (%.1f KB)", OUT.name, OUT.stat().st_size / 1024)


if __name__ == "__main__":
    main()
