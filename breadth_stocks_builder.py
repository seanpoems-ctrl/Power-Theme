"""
breadth_stocks_builder.py — Pre-generate stock lists for all breadth filters.

Fetches Finviz screener for each filter, computes 14-day ADR% via yfinance,
and writes one JSON file per filter to public/breadth_stocks_{filter}.json.

Run:
    python breadth_stocks_builder.py

Output:
    public/breadth_stocks_up4.json
    public/breadth_stocks_dn4.json
    public/breadth_stocks_up25q.json
    ... (10 files total)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

import httpx
import pandas as pd
import yfinance as yf
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PUBLIC_DIR = Path(__file__).parent / "public"

_BASE = "geo_usa,sh_avgvol_o100,sh_price_o5,cap_midover"

FILTER_MAP: dict[str, tuple[str, bool]] = {
    "up4":     (f"/screener.ashx?v=111&f={_BASE},ta_change_u4&o=-change",    False),
    "dn4":     (f"/screener.ashx?v=111&f={_BASE},ta_change_d-4&o=change",    True),
    "up25q":   (f"/screener.ashx?v=111&f={_BASE},ta_perf_q25o&o=-perf13w",  False),
    "dn25q":   (f"/screener.ashx?v=111&f={_BASE},ta_perf_q-25u&o=perf13w",  True),
    "up25m":   (f"/screener.ashx?v=111&f={_BASE},ta_perf_m25o&o=-perf4w",   False),
    "dn25m":   (f"/screener.ashx?v=111&f={_BASE},ta_perf_m-25u&o=perf4w",   True),
    "up50m":   (f"/screener.ashx?v=111&f={_BASE},ta_perf_m50o&o=-perf4w",   False),
    "dn50m":   (f"/screener.ashx?v=111&f={_BASE},ta_perf_m-50u&o=perf4w",   True),
    "up13_34": (f"/screener.ashx?v=111&f={_BASE},ta_perf34d_13o&o=-perf",   False),
    "dn13_34": (f"/screener.ashx?v=111&f={_BASE},ta_perf34d_-13u&o=perf",   True),
}

FILTER_LABELS: dict[str, str] = {
    "up4":     "Up 4%+ Today",
    "dn4":     "Down 4%+ Today",
    "up25q":   "Up 25%+ Quarterly",
    "dn25q":   "Down 25%+ Quarterly",
    "up25m":   "Up 25%+ Monthly",
    "dn25m":   "Down 25%+ Monthly",
    "up50m":   "Up 50%+ Monthly",
    "dn50m":   "Down 50%+ Monthly",
    "up13_34": "Up 13%+ 34-Day",
    "dn13_34": "Down 13%+ 34-Day",
}

FINVIZ_BASE = "https://finviz.com"
MAX_PAGES = 25
MIN_CAP_B = 1.0
YFINANCE_BATCH = 200

# Which field to use when re-sorting after yfinance enrichment (mirrors PERF_FIELD in BreadthStockModal.js)
SORT_FIELD_MAP: dict[str, str] = {
    "up4":     "change_pct",
    "dn4":     "change_pct",
    "up25q":   "perf_3m",
    "dn25q":   "perf_3m",
    "up25m":   "perf_1m",
    "dn25m":   "perf_1m",
    "up50m":   "perf_1m",
    "dn50m":   "perf_1m",
    "up13_34": "perf_34d",
    "dn13_34": "perf_34d",
}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Referer": "https://finviz.com/",
}

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_cap_b(raw: str) -> float | None:
    s = raw.strip().replace(",", "")
    if not s or s == "-":
        return None
    try:
        if s.endswith("T"): return float(s[:-1]) * 1000
        if s.endswith("B"): return float(s[:-1])
        if s.endswith("M"): return float(s[:-1]) / 1000
        if s.endswith("K"): return float(s[:-1]) / 1_000_000
        return float(s) / 1e9
    except ValueError:
        return None


def _parse_pct(raw: str) -> float | None:
    t = raw.strip().lstrip("+").replace("%", "")
    if not t or t == "-":
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _parse_price(raw: str) -> float | None:
    t = raw.strip().replace(",", "")
    if not t or t == "-":
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _parse_vol(raw: str) -> int | None:
    t = raw.strip().replace(",", "")
    if not t or t == "-":
        return None
    try:
        return int(float(t))
    except ValueError:
        return None


def _fmt_dollar_vol(price: float, vol: int) -> str:
    dv = price * vol
    if dv >= 1e9: return f"${dv/1e9:.1f}B"
    if dv >= 1e6: return f"${dv/1e6:.0f}M"
    if dv >= 1e3: return f"${dv/1e3:.0f}K"
    return f"${dv:.0f}"


def _parse_rows(html: str) -> list[dict]:
    """Parse Finviz v=111 overview screener HTML into raw row dicts."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        # Use recursive=False to avoid nested-table column shift
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 11:
            continue
        link = tds[1].find("a", href=lambda h: h and "quote.ashx?t=" in h)
        if not link:
            continue
        sym = link["href"].split("t=")[-1].split("&")[0].strip().upper()
        if not sym or not (1 <= len(sym) <= 8):
            continue
        rows.append({
            "ticker":   sym,
            "company":  tds[2].get_text(strip=True),
            "industry": tds[4].get_text(strip=True),
            "cap_raw":  tds[6].get_text(strip=True),
            "price_raw":tds[8].get_text(strip=True),
            "chg_raw":  tds[9].get_text(strip=True),
            "vol_raw":  tds[10].get_text(strip=True),
        })
    return rows


# ---------------------------------------------------------------------------
# yfinance metrics: ADR%, perf_1m, perf_3m, perf_34d (one batch download)
# ---------------------------------------------------------------------------

def _compute_yf_metrics(tickers: list[str]) -> dict[str, dict]:
    """
    Download ~4 months of daily OHLC for all tickers in batches.
    Returns a dict keyed by ticker with:
      adr_pct  — 14-day average daily range %
      perf_1m  — ~21-trading-day return %
      perf_3m  — ~63-trading-day return %
      perf_34d — 34-trading-day return %
    Any value that can't be computed is None.
    """
    empty = {"adr_pct": None, "perf_1m": None, "perf_3m": None, "perf_34d": None}
    result: dict[str, dict] = {t: dict(empty) for t in tickers}
    if not tickers:
        return result

    chunks = [tickers[i:i+YFINANCE_BATCH] for i in range(0, len(tickers), YFINANCE_BATCH)]
    for chunk in chunks:
        try:
            df = yf.download(chunk, period="4mo", interval="1d",
                             auto_adjust=True, progress=False)
        except Exception as exc:
            logger.warning("yfinance download failed: %s", exc)
            continue

        if df is None or getattr(df, "empty", True):
            continue

        is_multi = isinstance(df.columns, pd.MultiIndex)
        if is_multi:
            l0 = set(df.columns.get_level_values(0))
            l1 = set(df.columns.get_level_values(1))
            metric_first = "High" in l0
        else:
            metric_first = None

        for tkr in chunk:
            try:
                if not is_multi:
                    high  = df["High"].dropna()
                    low   = df["Low"].dropna()
                    close = df["Close"].dropna()
                elif metric_first:
                    if tkr not in l1: continue
                    high  = df["High"][tkr].dropna()
                    low   = df["Low"][tkr].dropna()
                    close = df["Close"][tkr].dropna()
                else:
                    if tkr not in l0: continue
                    high  = df[tkr]["High"].dropna()
                    low   = df[tkr]["Low"].dropna()
                    close = df[tkr]["Close"].dropna()

                nc = len(close)
                if nc < 2:
                    continue

                # ADR% — 14-day average of (High-Low)/Close
                n = min(len(high), len(low), nc, 14)
                if n >= 5:
                    adr = float(
                        ((high.iloc[-n:] - low.iloc[-n:]) / close.iloc[-n:] * 100).mean()
                    )
                    result[tkr]["adr_pct"] = round(adr, 1)

                latest = float(close.iloc[-1])

                # perf_1m — ~21 trading days
                if nc >= 21:
                    result[tkr]["perf_1m"] = round((latest / float(close.iloc[-21]) - 1) * 100, 2)

                # perf_3m — ~63 trading days
                if nc >= 63:
                    result[tkr]["perf_3m"] = round((latest / float(close.iloc[-63]) - 1) * 100, 2)

                # perf_34d — 34 trading days
                if nc >= 34:
                    result[tkr]["perf_34d"] = round((latest / float(close.iloc[-34]) - 1) * 100, 2)

            except Exception:
                pass
    return result


# ---------------------------------------------------------------------------
# Finviz fetcher (async with 429 backoff)
# ---------------------------------------------------------------------------

async def _fetch_page(client: httpx.AsyncClient, path: str, retries: int = 3) -> str:
    url = f"{FINVIZ_BASE}{path}"
    for attempt in range(retries):
        try:
            r = await client.get(url)
            if r.status_code == 429:
                wait = 2.0 * (attempt + 1)
                logger.warning("429 on %s — sleeping %.0fs", url, wait)
                await asyncio.sleep(wait)
                continue
            r.raise_for_status()
            return r.text
        except Exception as exc:
            if attempt < retries - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
            else:
                raise RuntimeError(f"Fetch failed after {retries} tries: {exc}")
    return ""


async def _fetch_filter(filter_key: str) -> dict[str, Any]:
    path, sort_asc = FILTER_MAP[filter_key]
    label = FILTER_LABELS[filter_key]
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    qualifying: list[dict] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(
        timeout=30.0, headers=BROWSER_HEADERS, follow_redirects=True
    ) as client:
        for page in range(MAX_PAGES):
            page_path = f"{path}&r={1 + page * 20}"
            try:
                html = await _fetch_page(client, page_path)
            except Exception as exc:
                logger.warning("[%s] page %d failed: %s", filter_key, page + 1, exc)
                break

            raw = _parse_rows(html)
            if not raw:
                break

            for row in raw:
                tkr = row["ticker"]
                if tkr in seen:
                    continue
                seen.add(tkr)

                cap_b = _parse_cap_b(row["cap_raw"])
                if cap_b is not None and cap_b < MIN_CAP_B:
                    continue

                price = _parse_price(row["price_raw"])
                chg   = _parse_pct(row["chg_raw"])
                vol   = _parse_vol(row["vol_raw"])

                qualifying.append({
                    "ticker":      tkr,
                    "company":     row["company"],
                    "industry":    row["industry"] or None,
                    "market_cap_b": round(cap_b, 3) if cap_b is not None else None,
                    "price":       price,
                    "change_pct":  chg,
                    "dollar_volume": _fmt_dollar_vol(price, vol) if price and vol else "—",
                    "adr_pct":     None,
                })

            await asyncio.sleep(0.3)
            if len(raw) < 20:
                break

    logger.info("[%s] fetched %d stocks", filter_key, len(qualifying))

    # Enrich with ADR% + performance metrics (sync in thread to avoid blocking event loop)
    if qualifying:
        tickers = [s["ticker"] for s in qualifying]
        metrics = await asyncio.to_thread(_compute_yf_metrics, tickers)
        for s in qualifying:
            m = metrics.get(s["ticker"], {})
            s["adr_pct"]  = m.get("adr_pct")
            s["perf_1m"]  = m.get("perf_1m")
            s["perf_3m"]  = m.get("perf_3m")
            s["perf_34d"] = m.get("perf_34d")

    # Re-sort by the canonical field for this filter (nulls last)
    sort_field = SORT_FIELD_MAP.get(filter_key, "change_pct")
    qualifying.sort(
        key=lambda s: (s.get(sort_field) is None, s.get(sort_field) or 0.0),
        reverse=not sort_asc,
    )

    return {
        "ok": True,
        "filter": filter_key,
        "label": label,
        "min_cap_b": MIN_CAP_B,
        "count": len(qualifying),
        "stocks": qualifying,
        "fetched_at_utc": now,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    errors = []

    for filter_key in FILTER_MAP:
        logger.info("=== Processing filter: %s ===", filter_key)
        t0 = monotonic()
        try:
            data = await _fetch_filter(filter_key)
        except Exception as exc:
            logger.error("[%s] failed: %s", filter_key, exc)
            errors.append(filter_key)
            data = {
                "ok": False, "filter": filter_key,
                "label": FILTER_LABELS[filter_key],
                "stocks": [], "count": 0,
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            }

        out = PUBLIC_DIR / f"breadth_stocks_{filter_key}.json"
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("[%s] wrote %s (%d stocks, %.1fs)",
                    filter_key, out.name, data["count"], monotonic() - t0)

        # polite delay between filters
        await asyncio.sleep(1.0)

    if errors:
        logger.warning("Filters with errors: %s", errors)
        sys.exit(1)
    else:
        logger.info("All filters complete.")


if __name__ == "__main__":
    asyncio.run(main())
