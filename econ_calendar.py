"""
econ_calendar.py — USD Economic Event Calendar

Sources (attempted in order):
  1. IBKR — reqNewsProviders + reqHistoricalNews filtered to macro event types
  2. ForexFactory — https://www.forexfactory.com/calendar (HTML scrape)
  3. FairEconomy JSON — https://nfs.faireconomy.media/ff_calendar_thisweek.json
     (fallback when ForexFactory blocks)

Only USD events are kept. Writes public/econ_calendar.json.
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

ET          = ZoneInfo("America/New_York")
NOW_ET      = datetime.now(ET)
TODAY_ET    = NOW_ET.date()
WEEK_END    = TODAY_ET + timedelta(days=7)
OUTPUT_PATH = Path("public/econ_calendar.json")

# ForexFactory blocks plain Python UA; mimic a real browser closely.
FF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.forexfactory.com/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Cache-Control": "max-age=0",
}

# IBKR macro keyword filter — headlines containing these strings are kept.
MACRO_KEYWORDS = {
    "cpi", "ppi", "nfp", "nonfarm", "non-farm", "gdp", "fomc", "fed", "federal reserve",
    "unemployment", "jobless", "payroll", "inflation", "pce", "retail sales",
    "ism", "pmi", "industrial production", "housing starts", "building permits",
    "consumer confidence", "consumer sentiment", "durable goods", "trade balance",
    "treasury", "yield", "rate decision", "interest rate", "jobs report",
    "core inflation", "balance of trade",
}


# ─── Shared normalisation ─────────────────────────────────────────────────────

def _normalise_impact(raw: str) -> str:
    s = (raw or "").strip().lower()
    if "high" in s or "red" in s:
        return "High"
    if "medium" in s or "orange" in s or "moderate" in s:
        return "Medium"
    if "low" in s or "yellow" in s:
        return "Low"
    return "Low"


def _clean(val: str | None) -> str | None:
    """Strip whitespace/dashes; return None when empty."""
    if val is None:
        return None
    v = val.strip()
    return v if v and v not in ("-", "--", "N/A", "n/a") else None


def _to_et_str(dt: datetime) -> str:
    """Convert an aware datetime to 'YYYY-MM-DD HH:MM ET' string."""
    local = dt.astimezone(ET)
    return local.strftime("%Y-%m-%d %H:%M ET")


# ─── Source 1: IBKR ───────────────────────────────────────────────────────────

def _fetch_ibkr() -> list[dict]:
    """
    Use reqNewsProviders to confirm macro-capable providers are available,
    then reqHistoricalNews for macro keywords over the next 7 days.
    Returns normalised event list or [] on any failure.
    """
    try:
        import ibkr_client
        if not ibkr_client.IS_LIVE or ibkr_client._ib is None:
            return []

        ib = ibkr_client._ib

        # Confirm at least one news provider is available
        try:
            providers = ib.reqNewsProviders()
            provider_codes = "+".join(p.code for p in providers) if providers else "BRFG+DJNL"
        except Exception:
            provider_codes = "BRFG+DJNL"

        # Use a broad market contract (SPY) as the anchor for macro news
        from ib_insync import Stock
        spy = Stock("SPY", "ARCA", "USD")
        ib.qualifyContracts(spy)

        start_str = NOW_ET.strftime("%Y-%m-%d %H:%M:%S")
        end_str   = (NOW_ET + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

        articles = ib.reqHistoricalNews(
            spy.conId,
            providerCodes=provider_codes,
            startDateTime=start_str,
            endDateTime=end_str,
            totalResults=200,
        )

        results = []
        seen: set[str] = set()
        for article in (articles or []):
            headline = (article.headline or "").lower()
            # Keep only headlines that look like macro events
            if not any(kw in headline for kw in MACRO_KEYWORDS):
                continue
            key = headline[:80]
            if key in seen:
                continue
            seen.add(key)

            try:
                # article.time is usually "YYYY-MM-DD HH:MM:SS" UTC
                dt_utc = datetime.strptime(article.time, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
            except (ValueError, AttributeError):
                continue

            if not (TODAY_ET <= dt_utc.astimezone(ET).date() <= WEEK_END):
                continue

            results.append({
                "datetime_et": _to_et_str(dt_utc),
                "event_name":  article.headline.strip(),
                "impact":      "Medium",     # IBKR news doesn't carry impact level
                "forecast":    None,
                "previous":    None,
                "currency":    "USD",
            })

        logger.info("IBKR macro events: %d", len(results))
        return results

    except Exception as exc:
        logger.warning("IBKR econ fetch failed: %s", exc)
        return []


# ─── Source 2: ForexFactory HTML ─────────────────────────────────────────────

def _parse_ff_time(time_text: str, current_date: date) -> str | None:
    """
    Parse ForexFactory time cell text such as '8:30am', '12:00pm', 'All Day',
    'Tentative'. Returns 'YYYY-MM-DD HH:MM ET' or None.
    """
    t = time_text.strip().lower()
    if not t or t in ("all day", "tentative", ""):
        return f"{current_date.isoformat()} 00:00 ET"
    m = re.match(r"(\d{1,2}):(\d{2})(am|pm)", t)
    if not m:
        return None
    h, mi, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    if ampm == "pm" and h != 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    try:
        dt_naive = datetime(current_date.year, current_date.month, current_date.day, h, mi)
        dt_et    = dt_naive.replace(tzinfo=ET)
        return _to_et_str(dt_et)
    except ValueError:
        return None


def _fetch_forexfactory() -> list[dict]:
    """
    Scrape https://www.forexfactory.com/calendar for this week's USD events.

    FF calendar table structure (one <tr> per event):
      .calendar__cell.calendar__date     → date (only present on first row of a day)
      .calendar__cell.calendar__time     → time text  e.g. '8:30am'
      .calendar__cell.calendar__currency → currency   e.g. 'USD'
      .calendar__cell.calendar__impact   → <span class="impact-..."> impact level
      .calendar__cell.calendar__event    → event name span.calendar__event-title
      .calendar__cell.calendar__forecast → forecast text
      .calendar__cell.calendar__previous → previous text
    """
    results: list[dict] = []
    try:
        resp = requests.get(
            "https://www.forexfactory.com/calendar",
            headers=FF_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("ForexFactory fetch failed (%s) — will try JSON fallback", exc)
        return results

    # Quick block-detection: FF sometimes serves a challenge page
    if len(resp.text) < 5000 or "calendar__row" not in resp.text:
        logger.warning("ForexFactory returned a non-calendar page — will try JSON fallback")
        return results

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        current_date: date | None = None
        current_time: str | None = None  # carry forward within a day

        for row in soup.select("tr.calendar__row"):
            # ── Date cell (only on the first row of each calendar day) ──────
            date_cell = row.select_one("td.calendar__date")
            if date_cell:
                raw_date = date_cell.get_text(" ", strip=True)
                # FF date format: "Wed Apr 9" — we add the current year
                m = re.search(r"(\w{3})\s+(\d{1,2})", raw_date)
                if m:
                    months = {
                        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
                    }
                    mon = months.get(m.group(1))
                    day = int(m.group(2))
                    if mon:
                        yr  = TODAY_ET.year
                        # Handle year-wrap (e.g. Dec→Jan)
                        candidate = date(yr, mon, day)
                        if candidate < TODAY_ET - timedelta(days=3):
                            candidate = date(yr + 1, mon, day)
                        current_date = candidate
                        current_time = None  # reset time carry for new day

            if current_date is None:
                continue
            if current_date > WEEK_END:
                continue

            # ── Currency filter ──────────────────────────────────────────────
            currency_cell = row.select_one("td.calendar__currency")
            if not currency_cell:
                continue
            currency = currency_cell.get_text(strip=True).upper()
            if currency != "USD":
                continue

            # ── Time ────────────────────────────────────────────────────────
            time_cell = row.select_one("td.calendar__time")
            time_text = time_cell.get_text(strip=True) if time_cell else ""
            if time_text:
                current_time = _parse_ff_time(time_text, current_date)
            datetime_et = current_time or f"{current_date.isoformat()} 00:00 ET"

            # ── Impact ───────────────────────────────────────────────────────
            impact_cell = row.select_one("td.calendar__impact")
            impact_raw  = ""
            if impact_cell:
                span = impact_cell.select_one("span[class]")
                impact_raw = " ".join(span.get("class", [])) if span else ""
            impact = _normalise_impact(impact_raw)

            # ── Event name ───────────────────────────────────────────────────
            event_cell  = row.select_one("td.calendar__event")
            event_title = ""
            if event_cell:
                title_span = event_cell.select_one(".calendar__event-title")
                event_title = (title_span or event_cell).get_text(strip=True)
            if not event_title:
                continue

            # ── Forecast / Previous ──────────────────────────────────────────
            forecast_cell  = row.select_one("td.calendar__forecast")
            previous_cell  = row.select_one("td.calendar__previous")
            forecast = _clean(forecast_cell.get_text(strip=True) if forecast_cell else None)
            previous = _clean(previous_cell.get_text(strip=True) if previous_cell else None)

            results.append({
                "datetime_et": datetime_et,
                "event_name":  event_title,
                "impact":      impact,
                "forecast":    forecast,
                "previous":    previous,
                "currency":    "USD",
            })

        logger.info("ForexFactory: %d USD events", len(results))
    except Exception as exc:
        logger.error("ForexFactory parse error: %s", exc)

    return results


# ─── Source 3: FairEconomy JSON fallback ─────────────────────────────────────

_FF_IMPACT_MAP = {
    "High":   "High",
    "Medium": "Medium",
    "Low":    "Low",
    "Holiday": "Low",
    "Non-Economic": "Low",
}

_FF_MONTHS = {
    "01": 1, "02": 2, "03": 3, "04": 4, "05": 5, "06": 6,
    "07": 7, "08": 8, "09": 9, "10": 10, "11": 11, "12": 12,
}


def _parse_faireconomy_dt(date_str: str, time_str: str) -> str | None:
    """
    FairEconomy date: '04-09-2026' (MM-DD-YYYY)
    FairEconomy time: '8:30am' or 'All Day' or 'Tentative'
    Returns 'YYYY-MM-DD HH:MM ET' string or None.
    """
    try:
        parts = date_str.split("-")
        if len(parts) != 3:
            return None
        mon, day, yr = int(parts[0]), int(parts[1]), int(parts[2])
        d = date(yr, mon, day)
        return _parse_ff_time(time_str, d)
    except (ValueError, IndexError):
        return None


def _fetch_faireconomy() -> list[dict]:
    """
    Fetch https://nfs.faireconomy.media/ff_calendar_thisweek.json
    and filter to USD events within the next 7 days.

    JSON schema (array of objects):
      { "title", "country", "date", "time", "impact", "forecast", "previous" }
    """
    results: list[dict] = []
    try:
        resp = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        events = resp.json()
        if not isinstance(events, list):
            logger.warning("FairEconomy: unexpected JSON structure")
            return results
    except Exception as exc:
        logger.error("FairEconomy fetch failed: %s", exc)
        return results

    for ev in events:
        # Currency filter
        if (ev.get("country") or "").upper() != "USD":
            continue

        datetime_et = _parse_faireconomy_dt(ev.get("date", ""), ev.get("time", ""))
        if datetime_et is None:
            continue

        # Date range filter — parse back from the ET string
        try:
            ev_date = date.fromisoformat(datetime_et[:10])
        except ValueError:
            continue
        if not (TODAY_ET <= ev_date <= WEEK_END):
            continue

        impact_raw = ev.get("impact") or ""
        results.append({
            "datetime_et": datetime_et,
            "event_name":  (ev.get("title") or "").strip(),
            "impact":      _FF_IMPACT_MAP.get(impact_raw, _normalise_impact(impact_raw)),
            "forecast":    _clean(ev.get("forecast")),
            "previous":    _clean(ev.get("previous")),
            "currency":    "USD",
        })

    logger.info("FairEconomy: %d USD events", len(results))
    return results


# ─── Deduplication ────────────────────────────────────────────────────────────

def _dedup_and_sort(events: list[dict]) -> list[dict]:
    """
    Deduplicate by (datetime_et, normalised event_name).
    Earlier sources (IBKR) take precedence; sort by datetime_et ascending.
    """
    seen: set[tuple] = set()
    out:  list[dict] = []
    for ev in events:
        key = (ev["datetime_et"], re.sub(r"\s+", " ", ev["event_name"].lower().strip()))
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    out.sort(key=lambda e: e["datetime_et"])
    return out


# ─── Main ────────────────────────────────────────────────────────────────────

def run() -> dict:
    import ibkr_client

    all_events: list[dict] = []

    # ── 1. IBKR ──────────────────────────────────────────────────────────────
    ibkr_events = _fetch_ibkr()
    all_events.extend(ibkr_events)
    logger.info("After IBKR: %d events", len(all_events))

    # ── 2. ForexFactory HTML ─────────────────────────────────────────────────
    ff_events = _fetch_forexfactory()
    if ff_events:
        all_events.extend(ff_events)
        logger.info("After ForexFactory: %d events total", len(all_events))
    else:
        # ── 3. FairEconomy JSON fallback ──────────────────────────────────
        logger.info("ForexFactory returned nothing — trying FairEconomy JSON")
        fe_events = _fetch_faireconomy()
        all_events.extend(fe_events)
        logger.info("After FairEconomy: %d events total", len(all_events))

    # ── Deduplicate & sort ────────────────────────────────────────────────────
    events = _dedup_and_sort(all_events)

    # ── Determine data_source label ───────────────────────────────────────────
    if ibkr_events:
        data_source = "ibkr"
        if ff_events:
            data_source = "ibkr+forexfactory"
        elif all_events:
            data_source = "ibkr+faireconomy"
    elif ff_events:
        data_source = "forexfactory"
    elif all_events:
        data_source = "faireconomy"
    else:
        data_source = "none"

    ibkr_client.disconnect()

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "data_source":  data_source,
        "events":       events,
    }


def main() -> None:
    result = run()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "Done — %d USD events → %s  (source: %s)",
        len(result["events"]),
        OUTPUT_PATH,
        result["data_source"],
    )


if __name__ == "__main__":
    main()
