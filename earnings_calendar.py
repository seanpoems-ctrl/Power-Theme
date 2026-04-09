"""
earnings_calendar.py — Earnings Calendar Aggregator

Sources (in priority order):
  1. IBKR  — ibkr_client.get_earnings_calendar()  (next 7 days, most authoritative)
  2. yfinance — Ticker.calendar for top-20 RS stocks from thematic_data.json
  3. Finviz  — https://finviz.com/calendar.ashx  (today's earnings, BMO/AMC, EPS est.)

Outputs public/earnings_calendar.json.
"""

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ET           = ZoneInfo("America/New_York")
TODAY_ET     = datetime.now(ET).date()
WEEK_END     = TODAY_ET + timedelta(days=7)
OUTPUT_PATH  = Path("public/earnings_calendar.json")
THEMATIC_JSON = Path("public/thematic_data.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finviz.com/",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _safe_eps(raw) -> float | None:
    """Parse an EPS estimate string or number to float; return None on failure."""
    if raw is None:
        return None
    try:
        return round(float(str(raw).replace(",", "").strip()), 2)
    except (ValueError, TypeError):
        return None


def _normalise_time(raw: str) -> str:
    """
    Collapse Finviz/IBKR/yfinance time labels to 'BMO', 'AMC', or ''.
    Examples:
      'Before Open'   → 'BMO'
      'After Hours'   → 'AMC'
      'After Close'   → 'AMC'
      'BMO'           → 'BMO'
      '--' / ''       → ''
    """
    s = (raw or "").strip().lower()
    if not s or s in ("-", "--", "n/a", "unknown"):
        return ""
    if any(x in s for x in ("before", "bmo", "morning")):
        return "BMO"
    if any(x in s for x in ("after", "amc", "close", "hour")):
        return "AMC"
    return ""


def _parse_date(raw) -> date | None:
    """Try several common date formats; return a date object or None."""
    if raw is None:
        return None
    if isinstance(raw, (date, datetime)):
        return raw.date() if isinstance(raw, datetime) else raw
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _top20_tickers() -> list[str]:
    """Return top-20 tickers by RS score from thematic_data.json."""
    if not THEMATIC_JSON.exists():
        logger.warning("thematic_data.json not found — yfinance fallback will use empty list")
        return []
    try:
        data = json.loads(THEMATIC_JSON.read_text(encoding="utf-8"))
        all_stocks = [
            s
            for th in data.get("themes", [])
            for sub in th.get("subthemes", [])
            for s in sub.get("stocks", [])
        ]
        # Deduplicate, sort by RS descending
        seen: set[str] = set()
        unique: list[dict] = []
        for s in all_stocks:
            t = s.get("ticker", "")
            if t and t not in seen:
                seen.add(t)
                unique.append(s)
        unique.sort(key=lambda x: x.get("rs_52w", 0), reverse=True)
        tickers = [s["ticker"] for s in unique[:20]]
        logger.info("Top-20 tickers from thematic_data: %s", tickers)
        return tickers
    except Exception as exc:
        logger.error("Failed to read thematic_data.json: %s", exc)
        return []


# ─── Source 1: IBKR ───────────────────────────────────────────────────────────

def _fetch_ibkr() -> list[dict]:
    """
    Call ibkr_client.get_earnings_calendar().
    Returns normalised list of {ticker, date, time_of_day, eps_estimate, source}.
    """
    try:
        import ibkr_client
        if not ibkr_client.IS_LIVE:
            return []
        raw = ibkr_client.get_earnings_calendar() or []
        results = []
        for item in raw:
            d = _parse_date(item.get("date"))
            if d is None or d > WEEK_END:
                continue
            results.append({
                "ticker":       str(item.get("ticker", "")).upper(),
                "date":         d.isoformat(),
                "time_of_day":  _normalise_time(item.get("time_of_day", "")),
                "eps_estimate": _safe_eps(item.get("eps_estimate")),
                "source":       "ibkr",
            })
        logger.info("IBKR earnings: %d entries", len(results))
        return results
    except Exception as exc:
        logger.error("IBKR earnings fetch failed: %s", exc)
        return []


# ─── Source 2: yfinance ───────────────────────────────────────────────────────

def _fetch_yfinance(tickers: list[str]) -> list[dict]:
    """
    For each ticker call yf.Ticker(t).calendar and extract the next earnings date.
    Returns normalised list of {ticker, date, time_of_day, eps_estimate, source}.
    """
    if not tickers:
        return []
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — skipping yfinance source")
        return []

    results = []
    for ticker in tickers:
        try:
            cal = yf.Ticker(ticker).calendar
            if cal is None:
                continue

            # yfinance ≥0.2 returns a dict; older versions a DataFrame
            if isinstance(cal, dict):
                raw_dates = cal.get("Earnings Date")
                if not raw_dates:
                    continue
                # May be a list or a single value
                if not isinstance(raw_dates, list):
                    raw_dates = [raw_dates]
                next_date = None
                for rd in raw_dates:
                    d = _parse_date(rd)
                    if d and d >= TODAY_ET:
                        next_date = d
                        break
                eps = _safe_eps(cal.get("Earnings Average") or cal.get("Earnings High"))
            else:
                # DataFrame — index contains earnings dates
                import pandas as pd
                if cal.empty:
                    continue
                future = [
                    idx for idx in cal.index
                    if hasattr(idx, "date") and idx.date() >= TODAY_ET
                ]
                if not future:
                    continue
                next_date = min(future).date()
                eps_col = next(
                    (c for c in cal.columns if "average" in c.lower() or "estimate" in c.lower()),
                    None,
                )
                eps = _safe_eps(cal[eps_col].iloc[0]) if eps_col else None

            if next_date is None or next_date > WEEK_END:
                continue

            results.append({
                "ticker":       ticker.upper(),
                "date":         next_date.isoformat(),
                "time_of_day":  "",   # yfinance calendar doesn't carry BMO/AMC
                "eps_estimate": eps,
                "source":       "yfinance",
            })
            time.sleep(0.3)  # light rate-limiting
        except Exception as exc:
            logger.warning("yfinance calendar failed for %s: %s", ticker, exc)

    logger.info("yfinance earnings: %d entries", len(results))
    return results


# ─── Source 3: Finviz calendar ────────────────────────────────────────────────

_MONTH_MAP = {m: i for i, m in enumerate(
    ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"], start=1
)}


def _parse_finviz_date(text: str) -> date | None:
    """
    Parse a Finviz earnings calendar date header, e.g.:
      'Apr 09, 26'  →  2026-04-09
      'Apr 09, 2026' → 2026-04-09
    """
    text = text.strip()
    m = re.match(r"(\w{3})\s+(\d{1,2}),\s+(\d{2,4})$", text)
    if not m:
        return None
    mon_str, day_str, yr_str = m.group(1), m.group(2), m.group(3)
    mon = _MONTH_MAP.get(mon_str)
    if mon is None:
        return None
    yr = int(yr_str)
    if yr < 100:
        yr += 2000
    try:
        return date(yr, mon, int(day_str))
    except ValueError:
        return None


def _fetch_finviz_calendar() -> list[dict]:
    """
    Scrape https://finviz.com/calendar.ashx for today's and upcoming earnings.

    Finviz calendar table structure (simplified):
      <tr class="table-top-s">  ← date header row, first <td> has the date text
      <tr class="styled-row-..."> ← ticker rows under that date

    Ticker row columns (0-indexed):
      0: blank/icon  1: ticker  2: company  3: time (Before Open / After Close)
      4: EPS estimate  5–N: other columns
    """
    results = []
    try:
        resp = requests.get(
            "https://finviz.com/calendar.ashx",
            headers=HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Finviz calendar fetch failed: %s", exc)
        return results

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        # The earnings table sits inside the main content area.
        # Look for all <tr> rows; date headers span multiple columns.
        current_date: date | None = None

        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue

            row_text = row.get_text(" ", strip=True)

            # ── Date header detection ──────────────────────────────────────
            # Finviz date rows typically have colspan on the first td
            # OR the row text matches a date pattern like "Apr 09, 26"
            first_td = cells[0]
            colspan = int(first_td.get("colspan", 1))
            if colspan > 3 or (len(cells) <= 3 and re.search(r"\w{3}\s+\d{1,2},\s+\d{2,4}", row_text)):
                parsed = _parse_finviz_date(first_td.get_text(strip=True))
                if parsed:
                    current_date = parsed
                continue

            if current_date is None:
                continue
            # Only keep entries within the next 7 days
            if current_date > WEEK_END:
                continue

            # ── Ticker row ────────────────────────────────────────────────
            # Need at least: ticker (col 1), time (col 3), eps (col 4)
            if len(cells) < 3:
                continue

            ticker_text = cells[1].get_text(strip=True).upper() if len(cells) > 1 else ""
            # Skip rows that don't look like a ticker (e.g. "AAPL", not "Company Name")
            if not re.match(r"^[A-Z]{1,5}$", ticker_text):
                continue

            time_text = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            eps_text  = cells[4].get_text(strip=True) if len(cells) > 4 else ""

            results.append({
                "ticker":       ticker_text,
                "date":         current_date.isoformat(),
                "time_of_day":  _normalise_time(time_text),
                "eps_estimate": _safe_eps(eps_text) if eps_text not in ("-", "", "--") else None,
                "source":       "finviz",
            })

        logger.info("Finviz calendar: %d entries (dates %s to %s)", len(results), TODAY_ET, WEEK_END)
    except Exception as exc:
        logger.error("Finviz calendar parse failed: %s", exc)

    return results


# ─── Merge & deduplicate ──────────────────────────────────────────────────────

def _merge(ibkr: list[dict], yf_data: list[dict], fv: list[dict]) -> dict[str, dict]:
    """
    Build a {ticker → record} dict.
    Priority: IBKR > yfinance > Finviz.
    IBKR wins entirely; yfinance and Finviz can fill in missing fields
    (e.g. BMO/AMC from Finviz when yfinance is the date source).
    """
    merged: dict[str, dict] = {}

    def _upsert(item: dict) -> None:
        t = item["ticker"]
        if t not in merged:
            merged[t] = item
            return
        existing = merged[t]
        # If incoming source is higher-priority, replace entirely
        priority = {"ibkr": 3, "yfinance": 2, "finviz": 1}
        if priority[item["source"]] > priority[existing["source"]]:
            merged[t] = item
            return
        # Otherwise only fill in missing sub-fields
        if not existing.get("time_of_day") and item.get("time_of_day"):
            existing["time_of_day"] = item["time_of_day"]
        if existing.get("eps_estimate") is None and item.get("eps_estimate") is not None:
            existing["eps_estimate"] = item["eps_estimate"]

    for item in ibkr:
        _upsert(item)
    for item in yf_data:
        _upsert(item)
    for item in fv:
        _upsert(item)

    return merged


# ─── Main ────────────────────────────────────────────────────────────────────

def run() -> dict:
    import ibkr_client

    data_source = ibkr_client.get_data_source()
    logger.info("Data source: %s | today ET: %s", data_source, TODAY_ET)

    # ── Fetch all three sources ──────────────────────────────────────────────
    ibkr_data = _fetch_ibkr()

    top20     = _top20_tickers()
    yf_data   = _fetch_yfinance(top20)

    fv_data   = _fetch_finviz_calendar()

    # ── Merge ────────────────────────────────────────────────────────────────
    merged = _merge(ibkr_data, yf_data, fv_data)
    logger.info("Merged: %d unique tickers", len(merged))

    # ── Split into today / upcoming ──────────────────────────────────────────
    today_str = TODAY_ET.isoformat()
    today_out:    list[dict] = []
    upcoming_out: list[dict] = []

    for record in sorted(merged.values(), key=lambda r: (r["date"], r["ticker"])):
        d = record["date"]
        if d == today_str:
            today_out.append({
                "ticker":       record["ticker"],
                "time_of_day":  record.get("time_of_day", ""),
                "eps_estimate": record.get("eps_estimate"),
                "source":       record.get("source", ""),
            })
        else:
            upcoming_out.append({
                "ticker":       record["ticker"],
                "date":         d,
                "time_of_day":  record.get("time_of_day", ""),
                "eps_estimate": record.get("eps_estimate"),
            })

    ibkr_client.disconnect()

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "data_source":  data_source,
        "today":        today_out,
        "upcoming":     upcoming_out,
    }


def main() -> None:
    result = run()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "Done — %d today, %d upcoming → %s",
        len(result["today"]),
        len(result["upcoming"]),
        OUTPUT_PATH,
    )


if __name__ == "__main__":
    main()
