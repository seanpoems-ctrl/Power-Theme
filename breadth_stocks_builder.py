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

_BASE = "sh_avgvol_o100,sh_price_o5,cap_midover"

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
MIN_DOLLAR_VOL = 50_000_000   # $50M average daily dollar volume
MIN_ADR_PCT    = 3.0          # 3% average daily range
YFINANCE_BATCH = 200

# Which field to use when re-sorting after yfinance enrichment (mirrors PERF_FIELD in BreadthStockModal.js)
SORT_FIELD_MAP: dict[str, str] = {
    "up4":     "change_pct",
    "dn4":     "change_pct",
    "up25q":   "perf_qtd",   # quarter-to-date — matches Finviz's quarterly definition
    "dn25q":   "perf_qtd",
    "up25m":   "perf_mtd",   # month-to-date — matches Finviz's monthly definition
    "dn25m":   "perf_mtd",
    "up50m":   "perf_mtd",
    "dn50m":   "perf_mtd",
    "up13_34": "perf_34d",
    "dn13_34": "perf_34d",
}

# Server-side threshold: drop stocks where the yfinance-computed perf field
# doesn't actually meet the scanner's stated minimum.  Finviz's own filters
# can return borderline stocks that are slightly below the threshold once
# yfinance recalculates using exact calendar periods.
# Positive value → field must be >= threshold; negative → must be <= threshold.
PERF_THRESHOLD_MAP: dict[str, tuple[str, float]] = {
    "up4":      ("change_pct", 4),
    "dn4":      ("change_pct", -4),
    "up25q":    ("perf_qtd",   25),
    "dn25q":    ("perf_qtd",  -25),
    "up25m":    ("perf_mtd",   25),
    "dn25m":    ("perf_mtd",  -25),
    "up50m":    ("perf_mtd",   50),
    "dn50m":    ("perf_mtd",  -50),
    "up13_34":  ("perf_34d",   13),
    "dn13_34":  ("perf_34d",  -13),
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
        link = tds[1].find("a", href=lambda h: h and "quote?t=" in h)
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
      perf_qtd — quarter-to-date return (from first trading day of current quarter)
      perf_mtd — month-to-date return (from first trading day of current month)
    Any value that can't be computed is None.
    """
    from datetime import date as date_type

    today = date_type.today()
    # Quarter start: Jan 1 / Apr 1 / Jul 1 / Oct 1
    q_month = ((today.month - 1) // 3) * 3 + 1
    quarter_start = pd.Timestamp(f"{today.year}-{q_month:02d}-01")
    month_start   = pd.Timestamp(f"{today.year}-{today.month:02d}-01")

    empty = {
        "adr_pct":  None, "perf_1m":  None, "perf_3m": None,
        "perf_34d": None, "perf_qtd": None, "perf_mtd": None,
    }
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

                # perf_qtd — quarter-to-date (matches Finviz quarterly definition)
                # Use the last close ON OR BEFORE the quarter start date as the base.
                tz = close.index.tz
                qs = quarter_start.tz_localize(tz) if tz is not None else quarter_start
                ms = month_start.tz_localize(tz) if tz is not None else month_start

                qtd_closes = close[close.index <= qs]
                if not qtd_closes.empty:
                    base_qtd = float(qtd_closes.iloc[-1])
                    if base_qtd > 0:
                        result[tkr]["perf_qtd"] = round((latest / base_qtd - 1) * 100, 2)

                # perf_mtd — month-to-date (matches Finviz monthly definition)
                mtd_closes = close[close.index <= ms]
                if not mtd_closes.empty:
                    base_mtd = float(mtd_closes.iloc[-1])
                    if base_mtd > 0:
                        result[tkr]["perf_mtd"] = round((latest / base_mtd - 1) * 100, 2)

            except Exception:
                pass
    return result


# ---------------------------------------------------------------------------
# SPX benchmark returns
# ---------------------------------------------------------------------------

def _fetch_spx_benchmarks() -> dict:
    """
    Download ^GSPC via yfinance and return benchmark returns for the same
    periods used by _compute_yf_metrics (1D, 1M ~21 td, 3M ~63 td, 34D, QTD, MTD).
    Any value that can't be computed is None.
    """
    from datetime import date as date_type
    today = date_type.today()
    q_month = ((today.month - 1) // 3) * 3 + 1
    quarter_start = pd.Timestamp(f"{today.year}-{q_month:02d}-01")
    month_start   = pd.Timestamp(f"{today.year}-{today.month:02d}-01")

    result: dict = {
        "spx_1d": None, "spx_1m": None, "spx_3m": None,
        "spx_34d": None, "spx_qtd": None, "spx_mtd": None,
    }
    try:
        df = yf.download("^GSPC", period="4mo", interval="1d",
                         auto_adjust=True, progress=False)
    except Exception as exc:
        logger.warning("SPX benchmark download failed: %s", exc)
        return result

    if df is None or getattr(df, "empty", True):
        return result

    try:
        # Handle both single-ticker (flat) and multi-index frames
        if isinstance(df.columns, pd.MultiIndex):
            l0 = set(df.columns.get_level_values(0))
            if "Close" in l0:
                close = df["Close"].iloc[:, 0].dropna()
            else:
                close = df.iloc[:, 0].dropna()
        else:
            close = df["Close"].dropna()

        nc = len(close)
        if nc < 2:
            return result

        latest = float(close.iloc[-1])

        # 1D return
        if nc >= 2:
            result["spx_1d"] = round((latest / float(close.iloc[-2]) - 1) * 100, 2)

        # 1M ~21 trading days
        if nc >= 21:
            result["spx_1m"] = round((latest / float(close.iloc[-21]) - 1) * 100, 2)

        # 3M ~63 trading days
        if nc >= 63:
            result["spx_3m"] = round((latest / float(close.iloc[-63]) - 1) * 100, 2)

        # 34D trading days
        if nc >= 34:
            result["spx_34d"] = round((latest / float(close.iloc[-34]) - 1) * 100, 2)

        # QTD — quarter-to-date (last close on or before start of current quarter)
        tz = close.index.tz
        qs = quarter_start.tz_localize(tz) if tz is not None else quarter_start
        ms = month_start.tz_localize(tz) if tz is not None else month_start

        qtd_closes = close[close.index <= qs]
        if not qtd_closes.empty:
            base = float(qtd_closes.iloc[-1])
            if base > 0:
                result["spx_qtd"] = round((latest / base - 1) * 100, 2)

        # MTD — month-to-date
        mtd_closes = close[close.index <= ms]
        if not mtd_closes.empty:
            base = float(mtd_closes.iloc[-1])
            if base > 0:
                result["spx_mtd"] = round((latest / base - 1) * 100, 2)

    except Exception as exc:
        logger.warning("SPX benchmark computation failed: %s", exc)

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


async def _fetch_filter(filter_key: str, rs_lookup: dict[str, int] | None = None) -> dict[str, Any]:
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

    logger.info("[%s] fetched %d stocks before liquidity filter", filter_key, len(qualifying))

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
            s["perf_qtd"] = m.get("perf_qtd")
            s["perf_mtd"] = m.get("perf_mtd")

    # Liquidity filter: $50M avg daily dollar volume AND ADR >= 3%
    def _parse_dv(v: str) -> float:
        if not v or v == "—": return 0.0
        v = str(v).replace("$", "").strip()
        mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get(v[-1].upper(), 1)
        try: return float(v[:-1]) * mult if v[-1].upper() in "KMB" else float(v)
        except: return 0.0

    before = len(qualifying)
    qualifying = [
        s for s in qualifying
        if _parse_dv(s.get("dollar_volume", "")) >= MIN_DOLLAR_VOL
        and (s.get("adr_pct") or 0) >= MIN_ADR_PCT
    ]
    logger.info("[%s] %d → %d stocks after liquidity filter ($%.0fM, ADR>=%.1f%%)",
                filter_key, before, len(qualifying), MIN_DOLLAR_VOL/1e6, MIN_ADR_PCT)

    # Period-threshold filter: enforce the scanner's stated minimum using the
    # yfinance-computed field.  Stocks where the field is None (yfinance failed)
    # are kept so we don't silently discard data; only confirmed failures are dropped.
    if filter_key in PERF_THRESHOLD_MAP:
        perf_field, perf_min = PERF_THRESHOLD_MAP[filter_key]
        before = len(qualifying)
        if perf_min >= 0:
            qualifying = [
                s for s in qualifying
                if s.get(perf_field) is None or s[perf_field] >= perf_min
            ]
        else:
            qualifying = [
                s for s in qualifying
                if s.get(perf_field) is None or s[perf_field] <= perf_min
            ]
        logger.info("[%s] %d → %d stocks after period threshold (%s %s %.0f%%)",
                    filter_key, before, len(qualifying), perf_field,
                    ">=" if perf_min >= 0 else "<=", perf_min)

    # Apply IBD RS from pre-built TradingView universe lookup
    if rs_lookup:
        for s in qualifying:
            s["rs_ibd"] = rs_lookup.get(s["ticker"])

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
        "spx_benchmarks": _fetch_spx_benchmarks(),
        "fetched_at_utc": now,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# TradingView screener — ATR Ext + >50 DMA scanner builders
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# IBD RS helpers
# ---------------------------------------------------------------------------

def _ibd_composite(p3m: float | None, p6m: float | None, p12m: float | None) -> float | None:
    """
    IBD-style RS composite using incremental quarterly returns.
      Q4 (0-3 mo, weight 40%): p3m
      Q3 (3-6 mo, weight 20%): incremental from p3m→p6m
      Q2+Q1 (6-12 mo, weight 40%): incremental from p6m→p12m
    Returns None when required data is missing or NaN.
    """
    def ok(v): return v is not None and v == v  # not None and not NaN

    if not (ok(p3m) and ok(p12m)):
        return None

    q4 = p3m
    if ok(p6m):
        q3   = ((1 + p6m  / 100) / (1 + p3m  / 100) - 1) * 100
        q2q1 = ((1 + p12m / 100) / (1 + p6m  / 100) - 1) * 100
        return 0.4 * q4 + 0.2 * q3 + 0.4 * q2q1
    else:
        # p6m unavailable — fold remaining 60% onto the 3-12 mo incremental
        rem = ((1 + p12m / 100) / (1 + p3m / 100) - 1) * 100
        return 0.4 * q4 + 0.6 * rem


def _build_rs_lookup(composites: dict[str, float]) -> dict[str, int]:
    """
    Rank a dict of {ticker: composite_score} as percentile 1-99.
    Ties are broken by fraction (standard competition ranking).
    """
    if not composites:
        return {}
    all_vals = sorted(composites.values())
    n = len(all_vals)
    result: dict[str, int] = {}
    for tkr, comp in composites.items():
        below = sum(1 for v in all_vals if v < comp)
        result[tkr] = max(1, min(99, round(below / n * 98) + 1))
    return result


def _build_tv_scanners_sync() -> tuple[dict, dict, dict]:
    """
    Use TradingView screener (~8 000+ US stocks) to:
      1. Build the IBD RS universe: fetch Perf.3M / Perf.6M / Perf.Y,
         compute the IBD composite, rank 1-99 → returns rs_lookup dict.
      2. atr_ext    : stocks where |change%| > 10 × (ATR / close × 100)
      3. above50dma : stocks where close > SMA50

    Returns (rs_lookup, atr_ext_data, above50dma_data).
    Runs synchronously; call via asyncio.to_thread in async context.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    empty_atr = {
        "ok": False, "filter": "atr_ext", "label": "10x ATR Extended",
        "stocks": [], "count": 0, "fetched_at_utc": now,
    }
    empty_dma = {
        "ok": False, "filter": "above50dma", "label": ">50 DMA",
        "stocks": [], "count": 0, "fetched_at_utc": now,
    }

    try:
        from tradingview_screener import Query, col as tv_col  # type: ignore
    except ImportError as exc:
        logger.warning("tradingview_screener not installed: %s", exc)
        return {}, empty_atr, empty_dma

    try:
        logger.info("Fetching TradingView screener for RS universe / ATR Ext / >50 DMA …")
        _, df = (
            Query()
            .select(
                "name", "description", "close", "change", "ATR", "SMA50",
                "industry", "market_cap_basic", "average_volume_10d_calc", "sector",
                "Perf.3M", "Perf.6M", "Perf.Y",   # IBD RS inputs
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
        logger.warning("TradingView screener fetch failed: %s", exc)
        return {}, empty_atr, empty_dma

    if df is None or getattr(df, "empty", True):
        logger.warning("TradingView screener returned empty result")
        return {}, empty_atr, empty_dma

    logger.info("TradingView screener: %d rows fetched", len(df))

    # Shared SPX benchmarks (reused by both outputs)
    spx = _fetch_spx_benchmarks()

    # Clean up — require close, change, ATR
    df = df.dropna(subset=["name", "close", "change", "ATR"]).copy()
    df = df[df["close"] > 0].copy()
    df["atr_pct"] = df["ATR"] / df["close"] * 100  # ATR as % of price

    # ── Build IBD RS universe ────────────────────────────────────────────────
    composites: dict[str, float] = {}
    for _, row in df.iterrows():
        tkr = str(row["name"])
        p3m  = row.get("Perf.3M")
        p6m  = row.get("Perf.6M")
        p12m = row.get("Perf.Y")
        try:
            p3m  = float(p3m)  if p3m  is not None else None
            p6m  = float(p6m)  if p6m  is not None else None
            p12m = float(p12m) if p12m is not None else None
        except (TypeError, ValueError):
            p3m = p6m = p12m = None
        c = _ibd_composite(p3m, p6m, p12m)
        if c is not None:
            composites[tkr] = c

    rs_lookup = _build_rs_lookup(composites)
    logger.info("IBD RS universe: %d stocks ranked", len(rs_lookup))

    def _cap_b(row):
        v = row.get("market_cap_basic")
        try:
            return round(float(v) / 1e9, 3) if v is not None and not pd.isna(v) else None
        except Exception:
            return None

    def _base(row):
        tkr      = str(row["name"])
        industry = str(row.get("industry") or row.get("sector") or "")
        return {
            "ticker":      tkr,
            "company":     str(row.get("description") or ""),
            "industry":    industry,
            "price":       round(float(row["close"]), 2),
            "change_pct":  round(float(row["change"]), 2),
            "adr_pct":     round(float(row["atr_pct"]), 1),
            "market_cap_b": _cap_b(row),
            "rs_ibd":      rs_lookup.get(tkr),
        }

    # ── ATR Ext scanner ──────────────────────────────────────────────────────
    # Restrict to Mkt Cap ≥ $1B so only institutionally relevant moves surface.
    df_atr = df.copy()
    df_atr = df_atr[df_atr["market_cap_basic"].notna() & (df_atr["market_cap_basic"] >= 1_000_000_000)].copy()
    df_atr["atr_ext_val"] = df_atr["change"].abs() / df_atr["atr_pct"]
    df_atr = df_atr[df_atr["atr_ext_val"] > 10].copy()
    df_atr = df_atr.sort_values("atr_ext_val", ascending=False)

    atr_stocks = []
    for _, row in df_atr.iterrows():
        s = _base(row)
        s["atr_ext_val"] = round(float(row["atr_ext_val"]), 2)
        atr_stocks.append(s)

    logger.info("ATR Ext scanner: %d stocks (|change%%| > 10×ATR%%, mkt cap ≥ $1B)", len(atr_stocks))
    atr_data = {
        "ok": True,
        "filter": "atr_ext",
        "label":  "10x ATR Extended",
        "count":  len(atr_stocks),
        "stocks": atr_stocks,
        "spx_benchmarks": spx,
        "fetched_at_utc": now,
    }

    # ── >50 DMA scanner — only stocks ≥50% above their 50DMA ────────────────
    df_dma = df.dropna(subset=["SMA50"]).copy()
    df_dma = df_dma[df_dma["SMA50"] > 0].copy()
    df_dma["above50dma_pct"] = ((df_dma["close"] - df_dma["SMA50"]) / df_dma["SMA50"] * 100)
    df_dma = df_dma[df_dma["above50dma_pct"] >= 50].copy()   # ≥50% above 50DMA
    df_dma = df_dma.sort_values("above50dma_pct", ascending=False)

    dma_stocks = []
    for _, row in df_dma.iterrows():
        s = _base(row)
        s["above50dma_pct"] = round(float(row["above50dma_pct"]), 1)
        dma_stocks.append(s)

    logger.info(">50 DMA scanner: %d stocks (≥50%% above SMA50)", len(dma_stocks))
    dma_data = {
        "ok": True,
        "filter": "above50dma",
        "label":  ">50 DMA",
        "count":  len(dma_stocks),
        "stocks": dma_stocks,
        "spx_benchmarks": spx,
        "fetched_at_utc": now,
    }

    return rs_lookup, atr_data, dma_data


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _build_compact_history(all_data: dict[str, dict], date_et: str) -> None:
    """
    Write a compact daily snapshot to public/breadth_history/YYYY-MM-DD.json.
    Stores up to 500 stocks per filter with only the essential fields.
    This lets the frontend load historical stock lists for any past trading day.
    """
    MAX_PER_FILTER = 500
    compact: dict[str, Any] = {"date": date_et, "filters": {}}

    for filter_key, data in all_data.items():
        stocks = data.get("stocks") or []
        compact["filters"][filter_key] = [
            {
                "t":    s.get("ticker", ""),
                "co":   s.get("company", ""),
                "p":    s.get("price"),
                "c":    s.get("change_pct"),
                "adr":  s.get("adr_pct"),
                "dv":   s.get("dollar_volume"),      # e.g. "$12.5M"
                "qtd":  s.get("perf_qtd"),
                "mtd":  s.get("perf_mtd"),
                "d34":  s.get("perf_34d"),
                "rs":   s.get("rs_ibd"),             # IBD RS 1-99
                "ind":  s.get("industry") or None,   # Industry group
                "dma":  s.get("above50dma_pct"),     # % above 50DMA (above50dma filter only)
                "ae":   s.get("atr_ext_val"),        # ATR extension multiple (atr_ext filter only)
            }
            for s in stocks[:MAX_PER_FILTER]
        ]

    history_dir = PUBLIC_DIR / "breadth_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    out = history_dir / f"{date_et}.json"
    out.write_text(json.dumps(compact, separators=(",", ":")), encoding="utf-8")
    total = sum(len(v) for v in compact["filters"].values())
    logger.info("Wrote history archive: %s (%d total stock entries)", out.name, total)


async def main() -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    errors = []
    all_data: dict[str, dict] = {}

    try:
        from zoneinfo import ZoneInfo
        date_et = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        date_et = datetime.now().strftime("%Y-%m-%d")

    # ── TradingView: build IBD RS universe + ATR Ext + >50 DMA ─────────────
    # Runs FIRST so rs_lookup is available for all Finviz filter enrichments.
    logger.info("=== Building TradingView RS universe / ATR Ext / >50 DMA ===")
    t0 = monotonic()
    rs_lookup: dict[str, int] = {}
    try:
        rs_lookup, atr_data, dma_data = await asyncio.to_thread(_build_tv_scanners_sync)
    except Exception as exc:
        logger.error("TradingView scanners failed: %s", exc)
        errors.extend(["atr_ext", "above50dma"])
        atr_data = {"ok": False, "filter": "atr_ext",    "label": "10x ATR Extended",
                    "stocks": [], "count": 0, "fetched_at_utc": datetime.now(timezone.utc).isoformat()}
        dma_data = {"ok": False, "filter": "above50dma", "label": ">50 DMA",
                    "stocks": [], "count": 0, "fetched_at_utc": datetime.now(timezone.utc).isoformat()}
    logger.info("TradingView done (%.1fs) — RS universe: %d tickers", monotonic() - t0, len(rs_lookup))

    for filter_key in FILTER_MAP:
        logger.info("=== Processing filter: %s ===", filter_key)
        t0 = monotonic()
        try:
            data = await _fetch_filter(filter_key, rs_lookup)
        except Exception as exc:
            logger.error("[%s] failed: %s", filter_key, exc)
            errors.append(filter_key)
            data = {
                "ok": False, "filter": filter_key,
                "label": FILTER_LABELS[filter_key],
                "stocks": [], "count": 0,
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            }

        all_data[filter_key] = data
        out = PUBLIC_DIR / f"breadth_stocks_{filter_key}.json"
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("[%s] wrote %s (%d stocks, %.1fs)",
                    filter_key, out.name, data["count"], monotonic() - t0)

        # polite delay between filters
        await asyncio.sleep(1.0)

    for key, data in [("atr_ext", atr_data), ("above50dma", dma_data)]:
        all_data[key] = data
        out = PUBLIC_DIR / f"breadth_stocks_{key}.json"
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("[%s] wrote %s (%d stocks, %.1fs)",
                    key, out.name, data["count"], monotonic() - t0)

    # ── Save compact daily history archive ───────────────────────────────────
    try:
        _build_compact_history(all_data, date_et)
    except Exception as exc:
        logger.warning("History archive write failed: %s", exc)

    if errors:
        logger.warning("Filters with errors: %s", errors)
        sys.exit(1)
    else:
        logger.info("All filters complete.")


if __name__ == "__main__":
    asyncio.run(main())
