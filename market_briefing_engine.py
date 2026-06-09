#!/usr/bin/env python3
from __future__ import annotations
"""
market_briefing_engine.py — Market Intelligence Briefing Engine

Dual-phase (PRE/POST) automated pipeline:
  • Aggregates FRED (BAMLH0A0HYM2 + T10Y2Y), yfinance (SPY/QQQ/VIX/CL=F/^N225/^GDAXI),
    and TradingView breadth (S&P 500 stocks above SMA50/S&P stocks above SMA200) via asyncio — target < 10 seconds
  • Tags market state: credit regime, yield curve, reversal signals
    (Hammer / Bullish Engulfing / Undercut & Reclaim vs. 20-day low)
  • Generates structured brief via Gemini with Content Architecture
  • Sends to Telegram (MarkdownV2) at 07:30 ET (PRE) and 16:30 ET (POST)
  • Persists raw JSON to public/market_intelligence.json + history to
    public/market_briefs.json + optional PostgreSQL market_briefs table

Usage:
    python market_briefing_engine.py pre    # Pre-Market  (07:30 ET)
    python market_briefing_engine.py post   # Post-Market (16:30 ET)
    python market_briefing_engine.py auto   # Auto-detect from current hour

Required env vars:
    GEMINI_API_KEY       — Google Gemini API key
    TELEGRAM_BOT_TOKEN   — Telegram bot token
    TELEGRAM_CHAT_ID     — Telegram chat / channel ID

Optional:
    FRED_API_KEY         — Free FRED API key (https://fred.stlouisfed.org/docs/api/api_key.html)
                           Required for reliable HY Credit Spread + Yield Curve data
    DATABASE_URL         — PostgreSQL connection string (Supabase / Postgres)
"""

import asyncio
import json
import os
import re
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

import httpx
import yfinance as yf
import pandas as pd
from dotenv import load_dotenv

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")
DATABASE_URL   = os.getenv("DATABASE_URL", "")
FRED_API_KEY   = os.getenv("FRED_API_KEY", "")

ET          = ZoneInfo("America/New_York")
BRIEFS_FILE = Path("public/market_briefs.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Snapshot display order (sym → label)
SNAPSHOT_ASSETS = [
    ("SPY",    "S&P 500 ETF"),
    ("QQQ",    "Nasdaq 100 ETF"),
    ("^VIX",   "VIX"),
    ("CL=F",   "Crude Oil"),
    ("^N225",  "Nikkei 225"),
    ("^GDAXI", "DAX"),
]


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LAYER — Async / Threaded Fetchers
# ═══════════════════════════════════════════════════════════════════════════════

def _flatten_df(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten yfinance MultiIndex columns and drop NaN rows."""
    df = df.dropna()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


async def _fetch_fred_series(client: httpx.AsyncClient, series_id: str) -> float | None:
    """
    Fetch the latest non-null value from a FRED series.
    Uses official FRED API if FRED_API_KEY is set; otherwise tries free CSV endpoint.
    """
    if FRED_API_KEY:
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&api_key={FRED_API_KEY}"
            f"&sort_order=desc&limit=10&file_type=json"
        )
        for attempt in range(2):
            try:
                r = await client.get(url, timeout=20)
                r.raise_for_status()
                obs = r.json().get("observations", [])
                for o in obs:
                    v = o.get("value", ".")
                    if v not in (".", ""):
                        try:
                            return float(v)
                        except ValueError:
                            continue
                return None
            except Exception as e:
                log.warning(f"FRED API {series_id} attempt {attempt + 1}: {e}")
                if attempt == 0:
                    await asyncio.sleep(3)
        return None

    # Fallback: free CSV endpoint
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "python-requests/2.31.0",
    ]
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    for attempt in range(3):
        try:
            headers = {"User-Agent": user_agents[attempt % len(user_agents)]}
            r = await client.get(url, timeout=25, headers=headers)
            r.raise_for_status()
            for line in reversed(r.text.strip().split("\n")[1:]):
                parts = line.strip().split(",")
                if len(parts) == 2 and parts[1] not in (".", ""):
                    try:
                        return float(parts[1])
                    except ValueError:
                        continue
            return None
        except Exception as e:
            log.warning(f"FRED CSV {series_id} attempt {attempt + 1}: {e}")
            if attempt < 2:
                await asyncio.sleep(5 if attempt == 0 else 10)
    return None


async def _fetch_t10y2y_treasury(client: httpx.AsyncClient) -> float | None:
    """
    Compute 10Y-2Y spread from US Treasury XML feed — free, no API key, reliable.
    Used as fallback when FRED T10Y2Y fetch fails.
    """
    import xml.etree.ElementTree as ET
    from datetime import date, timedelta

    for months_back in range(3):
        d = date.today().replace(day=1)
        for _ in range(months_back):
            d = (d - timedelta(days=1)).replace(day=1)
        ym = d.strftime("%Y%m")
        url = (
            "https://home.treasury.gov/resource-center/data-chart-center"
            f"/interest-rates/pages/xml?data=daily_treasury_yield_curve"
            f"&field_tdr_date_value={ym}"
        )
        try:
            r = await client.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            root = ET.fromstring(r.text)
            ns_m = "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
            ns_d = "http://schemas.microsoft.com/ado/2007/08/dataservices"
            entries = root.findall(f".//{{{ns_m}}}properties")
            for props in reversed(entries):
                y2_el  = props.find(f"{{{ns_d}}}BC_2YEAR")
                y10_el = props.find(f"{{{ns_d}}}BC_10YEAR")
                if (y2_el is not None and y10_el is not None
                        and y2_el.text and y10_el.text
                        and y2_el.text.strip() and y10_el.text.strip()):
                    spread = float(y10_el.text) - float(y2_el.text)
                    log.info(f"Treasury API T10Y2Y: {spread:+.3f} (from {ym})")
                    return spread
        except Exception as e:
            log.warning(f"Treasury API T10Y2Y {ym}: {e}")
    return None


def _load_cached_fred(key: str) -> float | None:
    """
    Load the last known FRED value from public/market_intelligence.json.
    key: "hy_oas" or "yield_curve"
    """
    try:
        p = Path("public/market_intelligence.json")
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        # New schema: market_briefing_engine.py stores fred.hy_oas / fred.yield_curve
        v = (data.get("fred") or {}).get(key)
        if v is not None:
            return float(v)
        # market_briefs.json fallback (compact entries array)
        briefs_p = Path("public/market_briefs.json")
        if briefs_p.exists():
            briefs = json.loads(briefs_p.read_text(encoding="utf-8"))
            for entry in briefs:
                v = entry.get(key)
                if v is not None:
                    return float(v)
        # Old schema: market_intelligence.py (hy_oas only)
        if key == "hy_oas":
            v = (data.get("credit") or {}).get("baml_hy")
            if v is not None:
                return float(v)
    except Exception:
        pass
    return None


def _load_cached_hy_oas() -> float | None:
    return _load_cached_fred("hy_oas")


async def _fetch_fred_data() -> dict:
    """
    Fetch BAMLH0A0HYM2 (ICE BofA HY OAS) and T10Y2Y (10Y-2Y Yield Curve) concurrently.
    No API key required — uses FRED's free CSV endpoint.

    Fallback chain for BAMLH0A0HYM2 if live fetch fails:
      1. Retry once after 3 seconds (transient network issue)
      2. Load last known value from public/market_intelligence.json (previous run)
    hy_oas_stale=True when the cached value is used — Gemini will note this.
    """
    async with httpx.AsyncClient() as client:
        hy_oas, yield_curve = await asyncio.gather(
            _fetch_fred_series(client, "BAMLH0A0HYM2"),
            _fetch_fred_series(client, "T10Y2Y"),
        )

        # Treasury API fallback for T10Y2Y while client is still open
        if yield_curve is None:
            yield_curve = await _fetch_t10y2y_treasury(client)
            if yield_curve is not None:
                log.info(f"FRED T10Y2Y: using Treasury API fallback {yield_curve:+.3f}")

    hy_oas_stale     = False
    yield_curve_stale = False

    if hy_oas is None:
        cached = _load_cached_hy_oas()
        if cached is not None:
            hy_oas       = cached
            hy_oas_stale = True
            log.info(f"FRED BAMLH0A0HYM2: live fetch failed — using cached value {hy_oas:.2f}%")
        else:
            log.warning("FRED BAMLH0A0HYM2: live fetch failed and no cached value available")

    if yield_curve is None:
        cached_yc = _load_cached_fred("yield_curve")
        if cached_yc is not None:
            yield_curve       = cached_yc
            yield_curve_stale = True
            log.info(f"FRED T10Y2Y: live fetch failed — using cached value {yield_curve:+.3f}")
        else:
            log.warning("FRED T10Y2Y: live fetch failed and no cached value available")

    log.info(f"FRED: HY OAS={hy_oas}{'(cached)' if hy_oas_stale else ''}  T10Y2Y={yield_curve}{'(cached)' if yield_curve_stale else ''}")
    return {"hy_oas": hy_oas, "yield_curve": yield_curve, "hy_oas_stale": hy_oas_stale, "yield_curve_stale": yield_curve_stale}


def _fetch_prices_sync() -> dict:
    """
    Fetch 30 days of OHLCV for SPY, QQQ, VIX, CL=F, ^N225, ^GDAXI via yfinance.
    Stores _lows_20d on SPY/QQQ for Undercut & Reclaim detection.
    """
    result: dict = {}
    for sym, label in SNAPSHOT_ASSETS:
        try:
            df = _flatten_df(yf.download(sym, period="30d", interval="1d",
                                          progress=False, auto_adjust=True))
            if len(df) < 2:
                result[sym] = None
                continue
            today = df.iloc[-1]
            prev  = df.iloc[-2]
            tc    = float(today["Close"])
            pc    = float(prev["Close"])
            result[sym] = {
                "label":      label,
                "price":      round(tc, 2),
                "open":       round(float(today["Open"]), 2),
                "high":       round(float(today["High"]), 2),
                "low":        round(float(today["Low"]),  2),
                "close":      round(tc, 2),
                "prev_open":  round(float(prev["Open"]), 2),
                "prev_close": round(pc, 2),
                "volume":     int(today.get("Volume", 0) or 0),
                "change_pct": round((tc - pc) / pc * 100, 2),
                # Private: used by reversal detection, stripped before persistence
                "_lows_20d":  [round(float(df["Low"].iloc[i]), 2)
                               for i in range(max(0, len(df) - 20), len(df))],
            }
        except Exception as e:
            log.warning(f"Price fetch [{sym}]: {e}")
            result[sym] = None
    return result


def _fetch_breadth_sync() -> dict:
    """
    Approximate S&P 500 stocks above SMA50 (% stocks > 50DMA) and S&P stocks above SMA200 (% stocks > 200DMA)
    via TradingView Screener — large-cap US stocks proxy (~600 stocks).
    """
    try:
        from tradingview_screener import Query, col
        _, df = (
            Query()
            .select("name", "close", "SMA50", "SMA200")
            .where(
                col("exchange").isin(["NYSE", "NASDAQ"]),
                col("type") == "stock",
                col("market_cap_basic") > 5e8,
                col("average_volume_10d_calc") > 300000,
            )
            .limit(600)
            .get_scanner_data()
        )
        if df is None or df.empty:
            return {"s5fi": None, "mmth": None}
        sma50  = next((c for c in df.columns if c in ("SMA50",  "simple_moving_average_50")),  None)
        sma200 = next((c for c in df.columns if c in ("SMA200", "simple_moving_average_200")), None)
        if not sma50 or not sma200:
            return {"s5fi": None, "mmth": None}
        df = df.dropna(subset=["close", sma50, sma200])
        if df.empty:
            return {"s5fi": None, "mmth": None}
        n    = len(df)
        s5fi = round(float((df["close"] > df[sma50]).sum())  / n * 100, 1)
        mmth = round(float((df["close"] > df[sma200]).sum()) / n * 100, 1)
        log.info(f"Breadth: {n} stocks  S&P500>SMA50={s5fi}%  S&P>SMA200={mmth}%")
        return {"s5fi": s5fi, "mmth": mmth}
    except Exception as e:
        log.warning(f"fetch_breadth: {e}")
        return {"s5fi": None, "mmth": None}


async def fetch_market_pulse() -> dict:
    """
    Aggregate all market data concurrently via asyncio.
    FRED via async httpx; yfinance + TradingView via asyncio.to_thread.
    Target: < 10 seconds total.

    Returns pulse dict with keys: timestamp, assets, fred, breadth.
    """
    log.info("Fetching market pulse (async)…")
    t0 = time.monotonic()

    fred_data, price_data, breadth_data = await asyncio.gather(
        _fetch_fred_data(),
        asyncio.to_thread(_fetch_prices_sync),
        asyncio.to_thread(_fetch_breadth_sync),
    )

    elapsed = time.monotonic() - t0
    log.info(f"Market pulse fetched in {elapsed:.1f}s")

    return {
        "timestamp": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
        "assets":    price_data,
        "fred":      fred_data,
        "breadth":   breadth_data,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# REVERSAL & REGIME LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

def _tag_credit_status(hy_oas: float | None) -> str:
    """BAMLH0A0HYM2 → Complacent / Yellow Flag / Stress."""
    if hy_oas is None: return "Unknown"
    if hy_oas < 3.5:   return "Complacent"
    if hy_oas < 4.5:   return "Yellow Flag"
    return "Stress"


def _tag_yield_curve(t10y2y: float | None) -> str:
    """T10Y2Y → yield curve regime label."""
    if t10y2y is None: return "Unknown"
    if t10y2y < -0.5:  return "Deeply Inverted"
    if t10y2y < 0:     return "Inverted"
    if t10y2y < 0.3:   return "Flat"
    return "Normal / Steepening"


def _detect_reversal(assets: dict, s5fi: float | None) -> dict:
    """
    Detect reversal candle patterns — only active when S&P 500 stocks above SMA50 < 15.

    Patterns checked on SPY and QQQ:
      Hammer:             lower_wick > 2×body  AND  upper_wick < 0.2×body
      Bullish Engulfing:  close > open  AND  open ≤ prev_close  AND  close ≥ prev_open
      Undercut & Reclaim: low < min(last_20d_lows[:-1])  AND  close > that min  [SPY only]

    S&P 500 stocks above SMA50 < 10 → GENERATIONAL BUY ZONE label added regardless of candle patterns.
    """
    result = {
        "signal_detected":    False,
        "signal_description": "",
        "patterns":           {},
    }
    if s5fi is None or s5fi >= 15:
        return result

    found: list[str] = []
    if s5fi < 10:
        found.append(f"GENERATIONAL BUY ZONE (S&P500>SMA50={s5fi:.1f}%)")

    for key in ("SPY", "QQQ"):
        d = assets.get(key)
        if not d:
            continue

        o, h, l, c = d["open"], d["high"], d["low"], d["close"]
        po, pc      = d["prev_open"], d["prev_close"]
        body        = abs(c - o) or 0.0001
        lower_wick  = min(o, c) - l
        upper_wick  = h - max(o, c)

        patterns: dict = {}

        # Hammer
        if lower_wick > 2 * body and upper_wick < 0.2 * body:
            patterns["hammer"] = True
            found.append(f"{key} Hammer")

        # Bullish Engulfing
        if c > o and o <= pc and c >= po:
            patterns["bullish_engulfing"] = True
            found.append(f"{key} Bullish Engulfing")

        # Undercut & Reclaim (SPY only, vs. 20-day low)
        if key == "SPY":
            lows = d.get("_lows_20d", [])
            if len(lows) > 1:
                min_20d = min(lows[:-1])   # exclude today
                if l < min_20d and c > min_20d:
                    patterns["undercut_reclaim"] = True
                    found.append(f"SPY Undercut & Reclaim (20d low={min_20d:.2f})")

        result["patterns"][key] = patterns

    if found:
        result["signal_detected"]    = True
        result["signal_description"] = " | ".join(found)

    return result


def tag_market_state(pulse: dict) -> dict:
    """
    Compute credit status, yield curve regime, and reversal signals.
    Returns a regime dict to be stored alongside the pulse.
    """
    hy_oas      = pulse["fred"].get("hy_oas")
    yield_curve = pulse["fred"].get("yield_curve")
    s5fi        = pulse["breadth"].get("s5fi")

    credit_status = _tag_credit_status(hy_oas)
    yc_status     = _tag_yield_curve(yield_curve)
    reversal      = _detect_reversal(pulse["assets"], s5fi)

    return {
        "credit_status":         credit_status,
        "yc_status":             yc_status,
        "breadth_flush":         bool(s5fi is not None and s5fi < 15),
        "generational_buy_zone": bool(s5fi is not None and s5fi < 10),
        "reversal":              reversal,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# RECYCLED NEWS CHECK
# ═══════════════════════════════════════════════════════════════════════════════

def load_recent_tickers(hours: int = 48) -> set[str]:
    """
    Return the set of tickers featured in briefs stored in the last `hours` hours.
    These become C-Grade "Recycled" candidates in the Ticker Intel section.
    """
    try:
        if not BRIEFS_FILE.exists():
            return set()
        entries = json.loads(BRIEFS_FILE.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            return set()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        tickers: set[str] = set()
        for entry in entries:
            ts_str = entry.get("generated_at", "")
            try:
                # Stored format: "YYYY-MM-DD HH:MM ET"
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M ET").replace(tzinfo=ET)
                if ts.astimezone(timezone.utc) < cutoff:
                    continue
            except Exception:
                continue
            ti = entry.get("ticker_intel") or {}
            for t in ti.get("a_grade", []):
                if t.get("ticker"):
                    tickers.add(t["ticker"])
            for t in ti.get("c_grade", []):
                if t.get("ticker"):
                    tickers.add(t["ticker"])
        return tickers
    except Exception as e:
        log.warning(f"load_recent_tickers: {e}")
        return set()


def load_todays_gappers() -> list[dict]:
    """Return today's A+/A grade gappers from gapper_data.json (if available)."""
    path = Path("public/gapper_data.json")
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            today = datetime.now(ET).strftime("%Y-%m-%d")
            if today in data.get("scan_time", ""):
                return [g for g in data.get("gappers", []) if g.get("grade") in ("A+", "A")]
    except Exception as e:
        log.warning(f"load_todays_gappers: {e}")
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# GEMINI BRIEF GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def _sentiment_icon(change_pct: float | None) -> str:
    if change_pct is None:  return "➡️"
    if change_pct >= 0.5:   return "📈"
    if change_pct <= -0.5:  return "📉"
    return "➡️"


def _build_snapshot_table(assets: dict) -> str:
    rows = [
        "| Asset | Last | % Change | Sentiment |",
        "|-------|------|----------|-----------|",
    ]
    for sym, label in SNAPSHOT_ASSETS:
        d = assets.get(sym)
        if not d:
            rows.append(f"| {label} | N/A | — | ➡️ |")
            continue
        chg   = d.get("change_pct")
        chg_s = f"{chg:+.2f}%" if chg is not None else "—"
        rows.append(f"| {label} | {d['price']:.2f} | {chg_s} | {_sentiment_icon(chg)} |")
    return "\n".join(rows)


def _generate_brief_sync(
    phase: str,
    pulse: dict,
    regime: dict,
    recycled_tickers: set,
    gappers: list,
    now_et: datetime,
) -> dict:
    """Synchronous Gemini call (wrapped in asyncio.to_thread by the caller)."""
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not set"}

    assets      = pulse["assets"]
    fred        = pulse["fred"]
    breadth     = pulse["breadth"]
    reversal    = regime["reversal"]

    phase_label = "Pre-Market" if phase == "PRE" else "Post-Market"
    date_str    = now_et.strftime("%B %d, %Y")

    hy_oas      = fred.get("hy_oas")
    yield_curve = fred.get("yield_curve")
    s5fi        = breadth.get("s5fi")
    mmth        = breadth.get("mmth")

    snapshot_table = _build_snapshot_table(assets)

    # Technical signal instruction
    if reversal["signal_detected"]:
        tech_instruction = (
            f'MUST begin with exactly: "[🚨 REVERSAL SIGNAL DETECTED] — '
            f'{reversal["signal_description"]}" followed by your mechanical assessment (2-3 sentences).'
        )
    else:
        tech_instruction = (
            "State 'No reversal pattern confirmed yet.' followed by 2-3 sentences on current "
            "technical context and what to watch for."
        )

    # Recycled tickers note
    recycled_note = ""
    if recycled_tickers:
        recycled_note = (
            f"\nRecycled tickers (featured in last 48h — eligible C-Grade candidates): "
            f"{', '.join(sorted(recycled_tickers))}"
        )

    # Gapper seeding
    gapper_note = ""
    if gappers:
        g_list = ", ".join(
            f"{g['ticker']} ({g.get('theme', g.get('category', 'N/A'))})" for g in gappers[:5]
        )
        gapper_note = (
            f"\nToday's high-conviction gappers (A/A+ grade from pre-market scan) — "
            f"use for A-Grade candidates: {g_list}"
        )

    prompt = f"""You are writing the {phase_label} Market Intelligence Brief for {date_str}.

═══ SNAPSHOT DATA ═══
{snapshot_table}

FRED Macro Data:
  BAMLH0A0HYM2 (HY Credit Spread): {f"{hy_oas:.2f}%{'  ⚠ CACHED — live FRED fetch failed, value is from previous run' if fred.get('hy_oas_stale') else ''}" if hy_oas is not None else "N/A — live FRED fetch failed and no cache available"} → {regime["credit_status"]}
  T10Y2Y (10Y-2Y Yield Curve):      {f"{yield_curve:+.3f}{'  ⚠ CACHED — live FRED fetch failed, value is from previous run' if fred.get('yield_curve_stale') else ''}" if yield_curve is not None else "N/A — live FRED fetch failed and no cache available"} → {regime["yc_status"]}
  S&P 500 stocks above SMA50 (% stocks > 50DMA):  {f"{s5fi:.1f}%" if s5fi is not None else "N/A"}
  S&P stocks above SMA200 (% stocks > 200DMA):    {f"{mmth:.1f}%" if mmth is not None else "N/A"}
{recycled_note}{gapper_note}

═══ CONTENT ARCHITECTURE — follow exactly ═══

**Header**
  - Title: "{phase_label} Brief: {date_str}"
  - Mood Emoji: single emoji capturing market character
  - Mood: 2-6 word phrase (e.g. "Risk-Off / Credit Stress", "Cautious Accumulation")
  - Narrative: 2-sentence summary of today's market character

**Snapshot Table** — reproduce the table above exactly

**Macro Section**
{"  - FRED data unavailable today. Write exactly one sentence: 'FRED macro data (HY Credit Spread, Yield Curve) was unavailable at publication time.' Do NOT speculate or discuss uncertainty further." if hy_oas is None and yield_curve is None else f"  - 2-3 sentences explicitly naming the BAMLH0A0HYM2 value, the identified regime ({regime['credit_status']}), the yield curve reading ({regime['yc_status']}), and what this implies for institutional risk appetite."}

**Analysis Para 1 — Global Tape → Credit**
  - 3-5 sentences connecting Nikkei / DAX / Oil performance to US HY credit spreads.
    Cite specific numbers. What does the global tape imply for US risk appetite?

**Analysis Para 2 — Mechanical Catalyst**
  - 3-5 sentences identifying the single dominant 'Why' behind today's primary flow.
    Name the mechanical catalyst (policy decision, macro data, positioning squeeze, etc.)
    and explain why it drives the observed price action.

**Analysis Para 3 — Mechanical Plan**
  - 3-5 sentences giving the actionable trading plan: specific price levels to watch (e.g. SPY 540 as support),
    key triggers for long/short entries, risk-off thresholds, and what the trader should do TODAY.

**Technical Signal**
  {tech_instruction}

**Ticker Intel** — Momentum Catalyst Intelligence rubric:
  - 2× A-Grade: must have BOTH (a) a fresh, specific news catalyst today AND (b) high liquidity
    (avg $ vol ≥ $100M, ADR ≥ 4%). Well-known S&P 500 names preferred.
  - 1× C-Grade: Recycled headline OR pure technical/flow — no new fundamental catalyst.
    If recycled tickers are available, prefer those.{f" Recycled list: {', '.join(sorted(recycled_tickers))}" if recycled_tickers else ""}

═══ TERMINOLOGY (MANDATORY — never use abbreviations in output) ═══
  "S&P 500 stocks above SMA50" — never "S5FI"
  "S&P stocks above SMA200"    — never "MMTH"
  "Trading Index"              — never "TRIN"

═══ OUTPUT FORMAT ═══
Return ONLY a single valid JSON object — no markdown wrapping, no code blocks:
{{
  "title":            "{phase_label} Brief: {date_str}",
  "mood_emoji":       "<single emoji>",
  "mood":             "<2-6 word phrase>",
  "narrative":        "<2-sentence narrative summary>",
  "snapshot_md":      "<the snapshot table reproduced in markdown>",
  "macro_section":    "<BAMLH0A0HYM2 + T10Y2Y regime analysis, 2-3 sentences>",
  "analysis_para1":   "<Global tape → Credit Spreads, 3-5 sentences with specific numbers>",
  "analysis_para2":   "<Mechanical Catalyst — the dominant Why, 3-5 sentences>",
  "analysis_para3":   "<Mechanical Plan — actionable price levels, entry triggers, risk thresholds, 3-5 sentences>",
  "technical_signal": "<technical signal per instruction above>",
  "ticker_intel": {{
    "a_grade": [
      {{"ticker": "TICKER1", "company": "Full Company Name", "reason": "1-sentence conviction thesis"}},
      {{"ticker": "TICKER2", "company": "Full Company Name", "reason": "1-sentence conviction thesis"}}
    ],
    "c_grade": [
      {{"ticker": "TICKER3", "company": "Full Company Name", "reason": "1-sentence recycled/avoid thesis"}}
    ]
  }}
}}"""

    try:
        from google import genai
        from google.genai import types as _gt
        client = genai.Client(api_key=GEMINI_API_KEY)

        # Enable thinking mode on high-volatility days (VIX > 20) for deeper analysis
        vix_val = None
        try:
            import yfinance as _yf
            _vix = _yf.Ticker("^VIX").history(period="1d")
            if not _vix.empty:
                vix_val = float(_vix["Close"].iloc[-1])
        except Exception:
            pass

        cfg = None
        if vix_val and vix_val > 20:
            log.info(f"VIX={vix_val:.1f} > 20 — enabling thinking mode for market brief")
            cfg = _gt.GenerateContentConfig(
                thinking_config=_gt.ThinkingConfig(thinking_budget=2048)
            )

        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt, config=cfg)
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?", "", raw).rstrip("`").strip()
        return json.loads(raw)
    except Exception as e:
        log.warning(f"Gemini failed: {e}")
        return {"error": str(e)}


async def generate_brief(
    phase: Literal["PRE", "POST"],
    pulse: dict,
    regime: dict,
    recycled_tickers: set,
    gappers: list,
    now_et: datetime,
) -> dict:
    """
    Async entry point for brief generation.
    phase: 'PRE' for pre-market, 'POST' for post-market.
    """
    return await asyncio.to_thread(
        _generate_brief_sync,
        phase, pulse, regime, recycled_tickers, gappers, now_et,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM — MarkdownV2
# ═══════════════════════════════════════════════════════════════════════════════

_MD2 = re.compile(r"([_*\[\]()~`>#+=|{}.!\-\\])")


def _esc(text: str) -> str:
    """Escape a string for Telegram MarkdownV2."""
    return _MD2.sub(r"\\\1", str(text))


def _trim(text: str, limit: int) -> str:
    """Trim to `limit` chars at a word boundary, appending '…' if truncated."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    return cut + "…"


def build_telegram_messages(analysis: dict, pulse: dict, regime: dict, phase: str) -> list[str]:
    """
    Build Telegram MarkdownV2 messages split into two parts to stay well under
    the 4096-character limit per message.

    Part 1 — Market Pulse: header + asset snapshot + macro rows + reversal alert
    Part 2 — Analysis:     all AI sections (Macro, Global Tape, Catalyst, Plan,
                           Technical Signal) + Ticker Intel
    """
    assets   = pulse["assets"]
    fred     = pulse["fred"]
    breadth  = pulse["breadth"]
    reversal = regime["reversal"]

    phase_icon  = "🌅" if phase == "PRE" else "🌙"
    phase_label = "Pre-Market" if phase == "PRE" else "Post-Market"

    hy   = fred.get("hy_oas")
    yc   = fred.get("yield_curve")
    s5fi = breadth.get("s5fi")
    mmth = breadth.get("mmth")
    hy_stale = fred.get("hy_oas_stale", False)
    yc_stale = fred.get("yield_curve_stale", False)

    # ── Part 1: Market Pulse ──────────────────────────────────────────────────
    p1: list[str] = [
        f"*{phase_icon} {_esc(phase_label.upper())} BRIEF*",
        f"*{_esc(analysis.get('title', '').replace(f'{phase_label} Brief: ', ''))}* "
        f"{analysis.get('mood_emoji', '')}",
        f"_{_esc(analysis.get('mood', ''))}_",
        f"_{_esc(_trim(analysis.get('narrative') or '', 300))}_",
        "",
    ]

    # Asset snapshot
    for sym, label in SNAPSHOT_ASSETS:
        d = assets.get(sym)
        if not d:
            continue
        chg  = d.get("change_pct")
        icon = "🟢" if (chg or 0) >= 0 else "🔴"
        chg_s = f"{chg:+.2f}%" if chg is not None else "—"
        p1.append(f"{icon} *{_esc(label)}* `{d['price']:.2f}` `{chg_s}`")
    p1.append("")

    # FRED + breadth rows (with ⚠️ cached warning)
    if hy is not None:
        r_icon   = {"Complacent": "🟢", "Yellow Flag": "🟡", "Stress": "🔴"}.get(regime["credit_status"], "⚪")
        stale_tag = " ⚠️ _cached_" if hy_stale else ""
        p1.append(f"{r_icon} *HY Spread:* `{hy:.2f}%`{stale_tag} — *{_esc(regime['credit_status'])}*")

    if yc is not None:
        sign      = "+" if yc >= 0 else ""
        stale_tag = " ⚠️ _cached_" if yc_stale else ""
        p1.append(
            f"📐 *Yield Curve \\(T10Y2Y\\):* `{sign}{yc:.3f}`{stale_tag} — {_esc(regime['yc_status'])}"
        )

    if s5fi is not None:
        b_icon = "🔥" if s5fi < 10 else "⚠️" if s5fi < 20 else "📊"
        parts  = [f"S&P 500 stocks above SMA50 `{s5fi:.1f}%`"]
        if mmth is not None:
            parts.append(f"S&P stocks above SMA200 `{mmth:.1f}%`")
        p1.append(f"{b_icon} *Breadth:* " + " \\| ".join(parts))

    p1.append("")

    # Reversal alert
    if reversal.get("signal_detected"):
        p1 += [
            "🚨 *\\[REVERSAL SIGNAL DETECTED\\]*",
            _esc(reversal.get("signal_description", "")),
            "",
        ]

    # ── Part 2: Analysis ──────────────────────────────────────────────────────
    p2: list[str] = []

    if analysis.get("macro_section"):
        p2 += [f"*{phase_icon} {_esc(phase_label.upper())} ANALYSIS*", ""]
        p2 += ["*📊 Macro:*", _esc(_trim(analysis["macro_section"], 550)), ""]

    if analysis.get("analysis_para1"):
        p2 += ["*🌐 Global Tape:*", _esc(_trim(analysis["analysis_para1"], 500)), ""]

    if analysis.get("analysis_para2"):
        p2 += ["*⚙️ Mechanical Catalyst:*", _esc(_trim(analysis["analysis_para2"], 550)), ""]

    if analysis.get("analysis_para3"):
        p2 += ["*🗺️ Mechanical Plan:*", _esc(_trim(analysis["analysis_para3"], 550)), ""]

    if analysis.get("technical_signal"):
        p2 += ["*🔍 Technical Signal:*", _esc(_trim(analysis["technical_signal"], 380)), ""]

    # Ticker intel
    ti     = analysis.get("ticker_intel", {})
    a_list = ti.get("a_grade", [])
    c_list = ti.get("c_grade", [])
    if a_list or c_list:
        p2.append("*🎯 Ticker Intel:*")
        for t in a_list[:2]:
            p2.append(
                f"  ✅ `{t.get('ticker','')}` *{_esc(t.get('company','')[:40])}* "
                f"— {_esc(_trim(str(t.get('reason','')), 220))}"
            )
        for t in c_list[:1]:
            p2.append(
                f"  ❌ `{t.get('ticker','')}` *{_esc(t.get('company','')[:40])}* "
                f"— {_esc(_trim(str(t.get('reason','')), 220))}"
            )

    messages = ["\n".join(p1)[:4090]]
    if p2:
        messages.append("\n".join(p2)[:4090])
    return messages


async def send_telegram(messages: "str | list[str]") -> bool:
    """Send one or more messages to all configured Telegram chat IDs."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        log.info("Telegram: not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing)")
        return False
    if isinstance(messages, str):
        messages = [messages]
    chat_ids = [cid.strip() for cid in TELEGRAM_CHAT.split(",")]
    ok = False
    async with httpx.AsyncClient() as client:
        for chat_id in chat_ids:
            for i, text in enumerate(messages):
                try:
                    r = await client.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                        json={
                            "chat_id":                  chat_id,
                            "text":                     text,
                            "parse_mode":               "MarkdownV2",
                            "disable_web_page_preview": True,
                        },
                        timeout=15,
                    )
                    r.raise_for_status()
                    log.info(f"Telegram: sent msg {i+1}/{len(messages)} to {chat_id} ✓")
                    ok = True
                    if i < len(messages) - 1:
                        await asyncio.sleep(0.5)  # avoid Telegram flood limits
                except Exception as e:
                    log.warning(f"Telegram msg {i+1} failed for {chat_id}: {e}")
    return ok


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE — JSON + Optional PostgreSQL
# ═══════════════════════════════════════════════════════════════════════════════

def _save_json(phase: str, pulse: dict, regime: dict, analysis: dict, now_et: datetime) -> None:
    """
    Save full snapshot to public/market_intelligence.json (latest run only).
    Append compact entry to public/market_briefs.json (rolling history, max 60).
    """
    # Strip private keys before persisting
    clean_assets = {
        sym: {k: v for k, v in d.items() if not k.startswith("_")}
        for sym, d in pulse["assets"].items() if d
    }

    payload = {
        "generated_at": now_et.strftime("%Y-%m-%d %H:%M ET"),
        "phase":        phase,
        "session":      "Pre-Market" if phase == "PRE" else "Post-Market",
        "assets":       clean_assets,
        "fred":         pulse["fred"],
        "breadth":      pulse["breadth"],
        "regime":       regime,
        "analysis":     analysis,
    }

    # Full snapshot — latest run
    intel_path = Path("public/market_intelligence.json")
    intel_path.parent.mkdir(parents=True, exist_ok=True)
    intel_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"Saved → {intel_path}")

    # Rolling history
    BRIEFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        entries = (
            json.loads(BRIEFS_FILE.read_text(encoding="utf-8"))
            if BRIEFS_FILE.exists() else []
        )
        if not isinstance(entries, list):
            entries = []
    except Exception:
        entries = []

    spy     = clean_assets.get("SPY") or {}
    vix     = clean_assets.get("^VIX") or {}
    ti      = analysis.get("ticker_intel", {})
    compact = {
        "generated_at":          payload["generated_at"],
        "phase":                 phase,
        "session":               payload["session"],
        "mood":                  analysis.get("mood", ""),
        "spy_price":             spy.get("price"),
        "spy_change_pct":        spy.get("change_pct"),
        "vix":                   vix.get("price"),
        "hy_oas":                pulse["fred"].get("hy_oas"),
        "yield_curve":           pulse["fred"].get("yield_curve"),
        "credit_status":         regime.get("credit_status"),
        "s5fi":                  pulse["breadth"].get("s5fi"),
        "mmth":                  pulse["breadth"].get("mmth"),
        "generational_buy_zone": regime.get("generational_buy_zone", False),
        "reversal":              regime["reversal"].get("signal_detected", False),
        "reversal_description":  regime["reversal"].get("signal_description", ""),
        "ticker_intel":          ti,
    }

    entries.insert(0, compact)
    BRIEFS_FILE.write_text(
        json.dumps(entries[:60], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info(f"Saved → {BRIEFS_FILE} ({min(len(entries), 60)} entries)")


async def _save_postgres(phase: str, pulse: dict, analysis: dict, now_et: datetime) -> None:
    """
    Optional: save raw JSON + generated markdown to a PostgreSQL `market_briefs` table.
    Requires DATABASE_URL env var and `asyncpg` package (`pip install asyncpg`).
    Table is created automatically on first run.
    """
    if not DATABASE_URL:
        return
    try:
        import asyncpg  # type: ignore[import]
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS market_briefs (
                    id            SERIAL PRIMARY KEY,
                    generated_at  TIMESTAMPTZ NOT NULL,
                    phase         TEXT NOT NULL,
                    raw_json      JSONB,
                    brief_md      TEXT
                )
            """)
            brief_md = (
                f"# {analysis.get('title', '')}\n\n"
                f"**Mood:** {analysis.get('mood_emoji', '')} {analysis.get('mood', '')}\n\n"
                f"{analysis.get('narrative', '')}\n\n"
                f"## Macro\n{analysis.get('macro_section', '')}\n\n"
                f"## Analysis\n\n"
                f"{analysis.get('analysis_para1', '')}\n\n"
                f"{analysis.get('analysis_para2', '')}\n\n"
                f"## Technical Signal\n{analysis.get('technical_signal', '')}\n"
            )
            raw_json = json.dumps({
                "assets":  {
                    sym: {k: v for k, v in d.items() if not k.startswith("_")}
                    for sym, d in pulse["assets"].items() if d
                },
                "fred":    pulse["fred"],
                "breadth": pulse["breadth"],
                "analysis": analysis,
            })
            await conn.execute(
                """INSERT INTO market_briefs (generated_at, phase, raw_json, brief_md)
                   VALUES ($1, $2, $3::jsonb, $4)""",
                now_et.astimezone(timezone.utc), phase, raw_json, brief_md,
            )
            log.info("PostgreSQL: market_brief saved ✓")
        finally:
            await conn.close()
    except ImportError:
        log.warning("PostgreSQL: asyncpg not installed — pip install asyncpg")
    except Exception as e:
        log.warning(f"PostgreSQL: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

async def run_brief(phase: Literal["PRE", "POST"]) -> None:
    """Execute the full dual-phase briefing pipeline."""
    now_et      = datetime.now(ET)
    phase_label = "Pre-Market" if phase == "PRE" else "Post-Market"
    log.info(f"Market Briefing Engine — {now_et.strftime('%Y-%m-%d %H:%M ET')} [{phase_label}]")

    # [1] Fetch all market data concurrently (< 10s target)
    pulse = await fetch_market_pulse()

    # [2] Tag market state / reversal signals
    regime = tag_market_state(pulse)
    if regime["reversal"]["signal_detected"]:
        log.info(f"🚨 REVERSAL: {regime['reversal']['signal_description']}")

    # [3] Recycled news check + today's gappers
    recycled_tickers = load_recent_tickers(hours=48)
    gappers          = load_todays_gappers()
    if recycled_tickers:
        log.info(f"Recycled tickers (48h): {', '.join(sorted(recycled_tickers))}")

    # [4] Generate Gemini brief
    log.info(f"Generating {phase_label} brief via Gemini…")
    analysis = await generate_brief(phase, pulse, regime, recycled_tickers, gappers, now_et)
    if analysis.get("error"):
        log.error(f"Gemini error: {analysis['error']}")

    # [5] Build and send Telegram message (MarkdownV2)
    tg_msgs = build_telegram_messages(analysis, pulse, regime, phase)
    await send_telegram(tg_msgs)

    # [6] Persist (JSON always; PostgreSQL if DATABASE_URL is set)
    _save_json(phase, pulse, regime, analysis, now_et)
    await _save_postgres(phase, pulse, analysis, now_et)

    log.info("Done.")


def _detect_phase() -> Literal["PRE", "POST"]:
    """Auto-detect phase from current ET hour."""
    return "PRE" if datetime.now(ET).hour < 12 else "POST"


if __name__ == "__main__":
    phase_arg = (sys.argv[1].upper() if len(sys.argv) > 1 else "auto").upper()
    if phase_arg == "AUTO":
        phase_arg = _detect_phase()
    if phase_arg not in ("PRE", "POST"):
        print("Usage: python market_briefing_engine.py [pre|post|auto]")
        sys.exit(1)
    asyncio.run(run_brief(phase_arg))  # type: ignore[arg-type]
