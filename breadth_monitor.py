from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows encoding fix
"""
breadth_monitor.py — Stockbee Market Monitor data fetcher for Power Theme.

Fetches the published Stockbee Market Monitor Google Sheet and writes the
parsed breadth data to public/breadth_monitor.json for the React frontend.

Run:
    python breadth_monitor.py

Output:
    public/breadth_monitor.json
"""

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

STOCKBEE_MM_PAGE = "https://stockbee.blogspot.com/p/mm.html"
STOCKBEE_SPREADSHEET_ID = "1O6OhS7ciA8zwfycBfGPbP2fWJnR0pn2UUvFZVDP9jpE"

STOCKBEE_SHEET_GIDS: dict[int, str] = {
    2026: "1082103394",
    2025: "780188096",
    2024: "1146204629",
    2023: "632667710",
    2022: "1394777987",
    2021: "1981550515",
    2020: "2093835319",
    2019: "1089581064",
    2018: "280217788",
    2017: "1391207759",
    2016: "233732777",
    2015: "0",
    2014: "1622090416",
    2013: "299051502",
    2012: "2142678713",
    2011: "24026662",
    2010: "1622166415",
    2009: "1397702728",
    2008: "1269494253",
    2007: "908739106",
}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

OUTPUT_PATH = Path(__file__).parent / "public" / "breadth_monitor.json"

_DATE_CELL = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _sheet_pub_url(gid: str) -> str:
    return (
        f"https://docs.google.com/spreadsheets/d/{STOCKBEE_SPREADSHEET_ID}/"
        f"pubhtml/sheet?headers=false&gid={gid}"
    )


def _pick_year_gid() -> tuple[int, str]:
    try:
        from zoneinfo import ZoneInfo
        y = datetime.now(ZoneInfo("America/New_York")).year
    except Exception:
        y = datetime.now().year
    for try_y in (y, y - 1, y - 2, y - 3):
        gid = STOCKBEE_SHEET_GIDS.get(try_y)
        if gid is not None:
            return try_y, gid
    return 2026, STOCKBEE_SHEET_GIDS[2026]


def _parse_us_date(raw: str) -> datetime | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _num(x: str) -> float | None:
    t = (x or "").strip().replace(",", "").replace("%", "")
    if not t or t in ("—", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _int(x: str) -> int | None:
    n = _num(x)
    return None if n is None else int(round(n))


def _row_to_record(cells: list[str]) -> dict[str, Any] | None:
    """
    Parse one Stockbee spreadsheet row into a breadth record.

    Column layout (0-indexed):
      [0]  row #
      [1]  date (M/D/YY or M/D/YYYY)
      [2]  up 4%+
      [3]  dn 4%+
      [4]  5-day ratio
      [5]  10-day ratio
      [6]  up 25% quarterly
      [7]  dn 25% quarterly
      [8]  up 25% monthly
      [9]  dn 25% monthly
      [10] up 50% monthly
      [11] dn 50% monthly
      [12] up 13% 34d
      [13] dn 13% 34d
      --- extended sheet (19+ cols) ---
      [14] 10x ATR extension count
      [15] % above 50 DMA
      [16] Worden universe
      [17] T2108
      [18] S&P
      --- standard sheet (17 cols) ---
      [14] Worden universe
      [15] T2108
      [16] S&P
    """
    if len(cells) < 17:
        return None
    date_raw = cells[1].strip()
    if not _DATE_CELL.match(date_raw):
        return None
    dt = _parse_us_date(date_raw)
    if dt is None:
        return None

    extended = len(cells) >= 19

    if extended:
        atr_10x_ext    = _int(cells[14])
        above_50dma    = _num(cells[15])
        worden         = _int(cells[16])
        t2108_raw      = cells[17].strip().replace("%", "")
        sp             = _num(cells[18])
    else:
        atr_10x_ext    = None
        above_50dma    = None
        worden         = _int(cells[14])
        t2108_raw      = cells[15].strip().replace("%", "")
        sp             = _num(cells[16])

    try:
        t2108: float | None = float(t2108_raw) if t2108_raw else None
    except ValueError:
        t2108 = None

    return {
        "date":          dt.date().isoformat(),
        "date_display":  date_raw,
        "up_4_pct":      _int(cells[2]),
        "down_4_pct":    _int(cells[3]),
        "ratio_5d":      _num(cells[4]),
        "ratio_10d":     _num(cells[5]),
        "up_25_q":       _int(cells[6]),
        "down_25_q":     _int(cells[7]),
        "up_25_m":       _int(cells[8]),
        "down_25_m":     _int(cells[9]),
        "up_50_m":       _int(cells[10]),
        "down_50_m":     _int(cells[11]),
        "up_13_34d":     _int(cells[12]),
        "down_13_34d":   _int(cells[13]),
        "atr_10x_ext":   atr_10x_ext,
        "above_50dma_pct": above_50dma,
        "worden_universe": worden,
        "t2108":         t2108,
        "sp_index":      sp,
    }

# ---------------------------------------------------------------------------
# TradingView screener — compute 10x ATR Ext count and % above 50 DMA
# ---------------------------------------------------------------------------

def _compute_breadth_extras() -> dict[str, Any]:
    """
    Fetch the broad US equity universe from TradingView screener and compute:
      atr_10x_ext    — count of stocks with |change%| > 10 × (ATR / close × 100)
      above_50dma_pct — % of stocks where close > SMA50

    Universe: US stocks & DRs, price ≥ $2, avg 10-day vol ≥ 50K shares.
    Mirrors Stockbee's Worden-T universe (~6 000–7 000 names).
    """
    extras: dict[str, Any] = {"atr_10x_ext": None, "above_50dma_pct": None, "universe_1b": None}
    try:
        from tradingview_screener import Query, col  # type: ignore

        logger.info("Fetching TradingView screener for breadth extras …")
        _, df = (
            Query()
            .select("name", "close", "change", "ATR", "SMA50", "market_cap_basic")
            .where(
                col("close") >= 2,
                col("average_volume_10d_calc") >= 50_000,
                col("type").isin(["stock", "dr"]),
                col("exchange").isin(["NYSE", "NASDAQ", "AMEX", "NYSE ARCA"]),
            )
            .limit(10_000)
            .get_scanner_data()
        )

        if df is None or df.empty:
            logger.warning("TradingView screener returned empty result for breadth extras")
            return extras

        logger.info("TradingView screener returned %d rows for breadth extras", len(df))

        # ── 10x ATR Extension ────────────────────────────────────────────────
        # Formula (3 steps):
        #   1. ATR%             = ATR / Price
        #   2. % Gain from 50MA = (Price − SMA50) / SMA50
        #   3. ATR% Multiple    = % Gain / ATR%
        # Qualifies when |ATR% Multiple| ≥ 10 (both extended-up and extended-down).
        # Restricted to Mkt Cap ≥ $1B.
        df_atr = df.dropna(subset=["close", "ATR", "SMA50", "market_cap_basic"]).copy()
        df_atr = df_atr[(df_atr["close"] > 0) & (df_atr["ATR"] > 0) & (df_atr["SMA50"] > 0)
                        & (df_atr["market_cap_basic"] >= 1_000_000_000)]
        if len(df_atr) > 0:
            df_atr = df_atr.copy()
            df_atr["atr_pct"]       = df_atr["ATR"] / df_atr["close"]
            df_atr["pct_gain_50ma"] = (df_atr["close"] - df_atr["SMA50"]) / df_atr["SMA50"]
            df_atr["atr_ext_val"]   = df_atr["pct_gain_50ma"] / df_atr["atr_pct"]
            df_atr["is_ext"]        = df_atr["atr_ext_val"].abs() >= 10
            extras["atr_10x_ext"]   = int(df_atr["is_ext"].sum())
            logger.info("10x ATR ext count: %d / %d", extras["atr_10x_ext"], len(df_atr))

        # ── % above 50 DMA (Mkt Cap ≥ $1B) ─────────────────────────────────
        df_sma = df.dropna(subset=["close", "SMA50", "market_cap_basic"]).copy()
        df_sma = df_sma[df_sma["market_cap_basic"] >= 1_000_000_000].copy()
        if len(df_sma) > 0:
            above = int((df_sma["close"] > df_sma["SMA50"]).sum())
            extras["above_50dma_pct"] = round(above / len(df_sma) * 100, 1)
            logger.info(">50 DMA: %.1f%% (%d / %d, mkt cap ≥ $1B)",
                        extras["above_50dma_pct"], above, len(df_sma))

        # ── Stock Universe $1B+ — total count of stocks with Mkt Cap ≥ $1B ──
        df_1b = df[df["market_cap_basic"].notna() & (df["market_cap_basic"] >= 1_000_000_000)]
        extras["universe_1b"] = len(df_1b)
        logger.info("Stock universe $1B+: %d", extras["universe_1b"])

    except Exception as exc:
        logger.warning("breadth extras compute failed: %s", exc)

    return extras


# ---------------------------------------------------------------------------
# Fetch & parse
# ---------------------------------------------------------------------------

def fetch_breadth_monitor(*, timeout: float = 45.0) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    year, gid = _pick_year_gid()
    url = _sheet_pub_url(gid)

    logger.info("Fetching Stockbee Market Monitor — year=%d gid=%s", year, gid)

    try:
        with httpx.Client(timeout=timeout, headers=BROWSER_HEADERS, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            html = r.text
    except Exception as exc:
        logger.warning("Sheet fetch failed: %s", exc)
        return {
            "ok": False,
            "rows": [],
            "sheet_year": year,
            "source_url": url,
            "blog_url": STOCKBEE_MM_PAGE,
            "detail": str(exc),
            "fetched_at_utc": now,
        }

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return {
            "ok": False,
            "rows": [],
            "sheet_year": year,
            "source_url": url,
            "blog_url": STOCKBEE_MM_PAGE,
            "detail": "No table found in published sheet HTML.",
            "fetched_at_utc": now,
        }

    rows: list[dict[str, Any]] = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        rec = _row_to_record(cells)
        if rec is not None:
            rows.append(rec)

    rows.sort(key=lambda r: r["date"], reverse=True)
    logger.info("Parsed %d breadth rows", len(rows))

    # Enrich the latest row with live TradingView breadth extras (10x ATR ext, >50 DMA).
    # Only fill fields that the sheet didn't provide (atr_10x_ext / above_50dma_pct are
    # None for most historical rows since Stockbee only added them recently).
    if rows:
        extras = _compute_breadth_extras()
        if extras["atr_10x_ext"] is not None:
            rows[0]["atr_10x_ext"] = extras["atr_10x_ext"]
        if extras["above_50dma_pct"] is not None:
            rows[0]["above_50dma_pct"] = extras["above_50dma_pct"]
        if extras["universe_1b"] is not None:
            rows[0]["universe_1b"] = extras["universe_1b"]

    return {
        "ok": True,
        "rows": rows,
        "sheet_year": year,
        "source_url": url,
        "blog_url": STOCKBEE_MM_PAGE,
        "detail": None,
        "fetched_at_utc": now,
    }


# ---------------------------------------------------------------------------
# Gemini Breadth Analysis — generated once at scrape time, stored in JSON
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")

STOCKBEE_PROMPT_CONTEXT = """
You are analysing market breadth data the way Pradeep Bonde (Stockbee) would read it.
Stockbee's colour thresholds for notable readings:
  - Up 4%+  ≥400 = healthy buying thrust; ≥600 = extreme thrust (very bullish)
  - Down 4%+ ≥200 = distribution pressure; ≥400 = heavy selling (bearish)
  - 5D / 10D Ratio: ≥2.0 = strong bull trend; ≥1.5 = bullish; ≤0.5 = bearish; ≤0.3 = very bearish
  - Up 25% Quarterly: rising count = momentum universe expanding (bullish)
  - Down 25% Quarterly: rising count = momentum breaking down
  - Up 25% Monthly: broad participation; high = bull trend healthy
  - Down 25% Monthly: rising = distribution broadening
  - Up 50% Monthly ≥50 = hot market; ≥100 = frothy / extended (caution signal, not bullish)
  - Down 50% Monthly ≥30 = panic / washout (watch for reversal)
  - Up 13% 34-Day: medium-term momentum; acceleration = stage-2 breakouts firing
  - Down 13% 34-Day: rising = intermediate downtrend widening
  - 10x ATR Ext ≥20 = market getting extended (amber warning); ≥50 = extreme (fade risk)
  - % above 50 DMA <30% = oversold/bearish; >80% = extended/overbought; 40-70% = healthy bull
  - T2108 ≤20 = deeply oversold — historical buy signal; ≥80 = overbought warning
""".strip()


def generate_gemini_breadth_analysis(rows: list[dict]) -> str | None:
    """
    Call Gemini with the last 20 breadth rows and return a Stockbee-style
    4-bullet analysis (TODAY / WEEK / MONTH / REGIME).
    Returns None if GEMINI_API_KEY is missing or the call fails.
    """
    if not GEMINI_API_KEY:
        logger.info("GEMINI_API_KEY not set — skipping breadth analysis")
        return None

    snapshot = [
        {
            "date":       r["date"],
            "up4":        r.get("up_4_pct"),
            "dn4":        r.get("down_4_pct"),
            "r5d":        r.get("ratio_5d"),
            "r10d":       r.get("ratio_10d"),
            "up25q":      r.get("up_25_q"),
            "dn25q":      r.get("down_25_q"),
            "up25m":      r.get("up_25_m"),
            "dn25m":      r.get("down_25_m"),
            "up50m":      r.get("up_50_m"),
            "dn50m":      r.get("down_50_m"),
            "up13":       r.get("up_13_34d"),
            "dn13":       r.get("down_13_34d"),
            "atrExt":     r.get("atr_10x_ext"),
            "above50dma": r.get("above_50dma_pct"),
            "t2108":      r.get("t2108"),
            "sp":         r.get("sp_index"),
        }
        for r in rows[:20]
    ]

    prompt = f"""{STOCKBEE_PROMPT_CONTEXT}

Below is the last 20 trading sessions of Stockbee Market Monitor data (newest row first).
Fields: date | up4 | dn4 | 5d-ratio | 10d-ratio | up25Q | dn25Q | up25M | dn25M | up50M | dn50M | up13_34d | dn13_34d | atrExt | %above50dma | T2108 | S&P

{json.dumps(snapshot)}

Terminology (MANDATORY): write "S&P 500 stocks above SMA50" not "S5FI", "S&P stocks above SMA200" not "MMTH", "Trading Index" not "TRIN", "% above 50 DMA" not "S5FI".

Reply with ONLY the 4 labelled bullets below — no intro, no outro, no markdown headers.
Each bullet: label + em-dash + 2 concise sentences with specific numbers.

TODAY — (today's up4/dn4 absolute counts and ratio; note any threshold crossing per the colour rules above)
WEEK — (5-day trend in the ratio and up25M or up13_34d; is breadth improving, steady, or fading?)
MONTH — (up50M froth level, % above 50dma zone, T2108 reading; what it means for swing traders)
REGIME — (one-word regime label + what Stockbee would watch as the next leading signal)"""

    # Ordered fallback chain: try each model in sequence until one works.
    MODELS = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ]

    try:
        from google import genai  # type: ignore
    except ImportError as exc:
        logger.warning("google-genai not installed: %s", exc)
        return None

    last_exc: Exception | None = None
    for model in MODELS:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )
            text = (response.text or "").strip()
            if text:
                logger.info("Gemini breadth analysis generated via %s (%d chars)", model, len(text))
                return text
            # Empty text from this model — try next
            logger.warning("Gemini model %s returned empty text — trying next", model)
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            # On hard model-not-found / deprecated errors, try the next model immediately.
            # On network/timeout errors, also try next (don't retry same model — we have fallbacks).
            logger.warning("Gemini model %s failed (%s) — trying next fallback", model, exc)

    logger.warning("All Gemini models exhausted. Last error: %s", last_exc)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _load_persisted_extras() -> dict[str, dict]:
    """Return {date_str: {atr_10x_ext, above_50dma_pct}} from the existing JSON, if any."""
    try:
        old = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        result: dict[str, dict] = {}
        for row in old.get("rows", []):
            date = row.get("date")
            if not date:
                continue
            entry: dict = {}
            if row.get("atr_10x_ext") is not None:
                entry["atr_10x_ext"] = row["atr_10x_ext"]
            if row.get("above_50dma_pct") is not None:
                entry["above_50dma_pct"] = row["above_50dma_pct"]
            if row.get("universe_1b") is not None:
                entry["universe_1b"] = row["universe_1b"]
            if entry:
                result[date] = entry
        return result
    except Exception:
        return {}


def main() -> None:
    persisted = _load_persisted_extras()
    data = fetch_breadth_monitor()

    # Merge previously persisted 10x ATR Ext / >50 DMA values back into historical rows
    # so they survive across re-runs (those values come from TradingView, not the sheet).
    if data["ok"]:
        for row in data["rows"]:
            saved = persisted.get(row["date"], {})
            if row.get("atr_10x_ext") is None and "atr_10x_ext" in saved:
                row["atr_10x_ext"] = saved["atr_10x_ext"]
            if row.get("above_50dma_pct") is None and "above_50dma_pct" in saved:
                row["above_50dma_pct"] = saved["above_50dma_pct"]
            if row.get("universe_1b") is None and "universe_1b" in saved:
                row["universe_1b"] = saved["universe_1b"]

    # Generate Gemini Stockbee-style analysis and embed in the payload
    if data["ok"] and data["rows"]:
        analysis = generate_gemini_breadth_analysis(data["rows"])
        data["gemini_analysis"] = analysis  # None if key missing or call failed

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

    if data["ok"]:
        logger.info("Wrote %s (%d rows)", OUTPUT_PATH, len(data["rows"]))
    else:
        logger.warning("Wrote error payload to %s: %s", OUTPUT_PATH, data["detail"])
        sys.exit(1)


if __name__ == "__main__":
    main()
