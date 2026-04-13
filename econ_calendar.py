"""
econ_calendar.py — USD Economic Event Calendar

Sources (attempted in order):
  1. TradingView — https://economic-calendar.tradingview.com/events  (primary)
  2. ForexFactory — https://www.forexfactory.com/calendar           (fallback)
  3. FairEconomy JSON — https://nfs.faireconomy.media/ff_calendar_thisweek.json
     (fallback when ForexFactory blocks)

Only USD events are kept. Writes public/econ_calendar.json.

Output schema per event:
  {
    "date":     "YYYY-MM-DD",    # ET date
    "time_et":  "HH:MM",         # ET time (24h), null if all-day
    "event":    str,
    "impact":   "High"|"Medium"|"Low",
    "actual":   str | null,       # released value, null if not yet out
    "forecast": str | null,
    "previous": str | null,
    "currency": "USD"
  }
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

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.tradingview.com/",
    "Origin":          "https://www.tradingview.com",
}

FF_HEADERS = {
    **BROWSER_HEADERS,
    "Accept":  "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Referer": "https://www.forexfactory.com/",
    "Origin":  "https://www.forexfactory.com",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Cache-Control":  "max-age=0",
}


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _normalise_impact(raw) -> str:
    """Map TradingView importance int (1/2/3) or string to High/Medium/Low."""
    if isinstance(raw, int):
        return {1: "Low", 2: "Medium", 3: "High"}.get(raw, "Low")
    s = (raw or "").strip().lower()
    if "high" in s or "red" in s or s == "3":
        return "High"
    if "medium" in s or "orange" in s or "moderate" in s or s == "2":
        return "Medium"
    return "Low"


def _clean(val) -> str | None:
    """Strip whitespace/dashes; return None when empty."""
    if val is None:
        return None
    v = str(val).strip()
    return v if v and v not in ("-", "--", "N/A", "n/a", "nan") else None


def _utc_to_et(dt_utc: datetime) -> tuple[str, str]:
    """Return (date_str 'YYYY-MM-DD', time_str 'HH:MM') in ET."""
    local = dt_utc.astimezone(ET)
    return local.strftime("%Y-%m-%d"), local.strftime("%H:%M")


def _normalise_event(raw_date: str, raw_time: str | None, event: str,
                     impact, actual, forecast, previous,
                     currency: str = "USD") -> dict:
    return {
        "date":     raw_date,
        "time_et":  raw_time,
        "event":    event.strip(),
        "impact":   _normalise_impact(impact),
        "actual":   _clean(actual),
        "forecast": _clean(forecast),
        "previous": _clean(previous),
        "currency": currency.upper(),
    }


# ─── Source 1: TradingView economic calendar ──────────────────────────────────

def _fetch_tradingview() -> list[dict]:
    """
    GET https://economic-calendar.tradingview.com/events
        ?from=<ISO UTC>&to=<ISO UTC>&countries=US

    Response JSON: { "status": "ok", "result": [ { ...event fields... } ] }
      or just an array at root level.

    Known field names (observed in the wild):
      title / event_name  → event
      date                → UTC ISO datetime string
      importance          → 1/2/3
      actual              → released value string or null
      forecast / estimate → forecast string or null
      prev_value / previous / prior → previous string or null
      currency            → "USD" etc.
    """
    results: list[dict] = []
    try:
        from_dt = datetime(TODAY_ET.year, TODAY_ET.month, TODAY_ET.day,
                           tzinfo=timezone.utc)
        to_dt   = datetime(WEEK_END.year, WEEK_END.month, WEEK_END.day,
                           23, 59, 59, tzinfo=timezone.utc)

        url = "https://economic-calendar.tradingview.com/events"
        params = {
            "from":      from_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "to":        to_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "countries": "US",
        }
        resp = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=20)
        resp.raise_for_status()
        payload = resp.json()

        # Support both {"result":[...]} and direct list
        events = payload.get("result", payload) if isinstance(payload, dict) else payload
        if not isinstance(events, list):
            logger.warning("TradingView: unexpected JSON structure — %s", type(events))
            return results

        for ev in events:
            # Date — always UTC ISO string e.g. "2026-04-13T12:30:00Z"
            raw_date_str = ev.get("date") or ev.get("datetime") or ""
            if not raw_date_str:
                continue
            try:
                # Strip trailing Z or offset
                clean_dt = raw_date_str.rstrip("Z").split("+")[0].split(".")[0]
                dt_utc = datetime.fromisoformat(clean_dt).replace(tzinfo=timezone.utc)
            except ValueError:
                logger.debug("TradingView: bad date %s", raw_date_str)
                continue

            date_str, time_str = _utc_to_et(dt_utc)

            # Date range gate
            try:
                ev_date = date.fromisoformat(date_str)
            except ValueError:
                continue
            if not (TODAY_ET <= ev_date <= WEEK_END):
                continue

            # Currency filter
            currency = (ev.get("currency") or ev.get("country") or "").upper()
            if currency not in ("USD", "US"):
                continue

            # Field name variations
            event_name = (
                ev.get("title") or ev.get("event_name") or ev.get("name") or ""
            ).strip()
            if not event_name:
                continue

            actual   = ev.get("actual")   or ev.get("actual_value")
            forecast = ev.get("forecast") or ev.get("estimate")
            previous = ev.get("prev_value") or ev.get("previous") or ev.get("prior")
            impact   = ev.get("importance") or ev.get("impact") or "Low"

            results.append(_normalise_event(
                date_str, time_str, event_name,
                impact, actual, forecast, previous, "USD",
            ))

        logger.info("TradingView: %d USD events", len(results))
    except Exception as exc:
        logger.warning("TradingView econ fetch failed: %s", exc)

    return results


# ─── Source 2: ForexFactory HTML ─────────────────────────────────────────────

def _parse_ff_time(time_text: str, current_date: date) -> str | None:
    """Parse ForexFactory time text e.g. '8:30am' → '08:30'. None if all-day."""
    t = time_text.strip().lower()
    if not t or t in ("all day", "tentative", ""):
        return None
    m = re.match(r"(\d{1,2}):(\d{2})(am|pm)", t)
    if not m:
        return None
    h, mi, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    if ampm == "pm" and h != 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}"


def _fetch_forexfactory() -> list[dict]:
    """Scrape ForexFactory calendar for USD events this week."""
    results: list[dict] = []
    try:
        resp = requests.get(
            "https://www.forexfactory.com/calendar",
            headers=FF_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("ForexFactory fetch failed (%s) — will try FairEconomy", exc)
        return results

    if len(resp.text) < 5000 or "calendar__row" not in resp.text:
        logger.warning("ForexFactory: non-calendar page — will try FairEconomy")
        return results

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        current_date: date | None = None
        current_time: str | None = None

        for row in soup.select("tr.calendar__row"):
            date_cell = row.select_one("td.calendar__date")
            if date_cell:
                raw_date = date_cell.get_text(" ", strip=True)
                m = re.search(r"(\w{3})\s+(\d{1,2})", raw_date)
                if m:
                    months = {k: i for i, k in enumerate(
                        ["Jan","Feb","Mar","Apr","May","Jun",
                         "Jul","Aug","Sep","Oct","Nov","Dec"], start=1)}
                    mon = months.get(m.group(1))
                    day = int(m.group(2))
                    if mon:
                        yr  = TODAY_ET.year
                        candidate = date(yr, mon, day)
                        if candidate < TODAY_ET - timedelta(days=3):
                            candidate = date(yr + 1, mon, day)
                        current_date = candidate
                        current_time = None

            if current_date is None or current_date > WEEK_END:
                continue

            currency_cell = row.select_one("td.calendar__currency")
            if not currency_cell:
                continue
            if currency_cell.get_text(strip=True).upper() != "USD":
                continue

            time_cell = row.select_one("td.calendar__time")
            time_text = time_cell.get_text(strip=True) if time_cell else ""
            if time_text:
                current_time = _parse_ff_time(time_text, current_date)

            impact_cell = row.select_one("td.calendar__impact")
            impact_raw  = ""
            if impact_cell:
                span = impact_cell.select_one("span[class]")
                impact_raw = " ".join(span.get("class", [])) if span else ""

            event_cell  = row.select_one("td.calendar__event")
            event_title = ""
            if event_cell:
                title_span = event_cell.select_one(".calendar__event-title")
                event_title = (title_span or event_cell).get_text(strip=True)
            if not event_title:
                continue

            forecast_cell = row.select_one("td.calendar__forecast")
            previous_cell = row.select_one("td.calendar__previous")
            actual_cell   = row.select_one("td.calendar__actual")

            results.append(_normalise_event(
                current_date.isoformat(),
                current_time,
                event_title,
                impact_raw,
                _clean(actual_cell.get_text(strip=True) if actual_cell else None),
                _clean(forecast_cell.get_text(strip=True) if forecast_cell else None),
                _clean(previous_cell.get_text(strip=True) if previous_cell else None),
                "USD",
            ))

        logger.info("ForexFactory: %d USD events", len(results))
    except Exception as exc:
        logger.error("ForexFactory parse error: %s", exc)

    return results


# ─── Source 3: FairEconomy JSON fallback ─────────────────────────────────────

_FF_IMPACT_MAP = {"High": "High", "Medium": "Medium", "Low": "Low",
                  "Holiday": "Low", "Non-Economic": "Low"}


def _parse_faireconomy_dt(date_str: str, time_str: str) -> tuple[str, str | None] | None:
    """FairEconomy date: 'MM-DD-YYYY', time: '8:30am' / 'All Day'. Returns (date_str, time_str)."""
    try:
        parts = date_str.split("-")
        mon, day, yr = int(parts[0]), int(parts[1]), int(parts[2])
        d = date(yr, mon, day)
        t = _parse_ff_time(time_str, d)
        return d.isoformat(), t
    except (ValueError, IndexError):
        return None


def _fetch_faireconomy() -> list[dict]:
    """Fetch https://nfs.faireconomy.media/ff_calendar_thisweek.json as final fallback."""
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
        if (ev.get("country") or "").upper() != "USD":
            continue
        parsed = _parse_faireconomy_dt(ev.get("date", ""), ev.get("time", ""))
        if not parsed:
            continue
        date_str, time_str = parsed
        try:
            if not (TODAY_ET <= date.fromisoformat(date_str) <= WEEK_END):
                continue
        except ValueError:
            continue

        impact_raw = ev.get("impact") or ""
        results.append(_normalise_event(
            date_str, time_str,
            (ev.get("title") or "").strip(),
            _FF_IMPACT_MAP.get(impact_raw, _normalise_impact(impact_raw)),
            _clean(ev.get("actual")),
            _clean(ev.get("forecast")),
            _clean(ev.get("previous")),
            "USD",
        ))

    logger.info("FairEconomy: %d USD events", len(results))
    return results


# ─── Deduplication ────────────────────────────────────────────────────────────

def _dedup_and_sort(events: list[dict]) -> list[dict]:
    """Deduplicate by (date, time_et, normalised event name); sort chronologically."""
    seen: set[tuple] = set()
    out:  list[dict] = []
    for ev in events:
        key = (
            ev.get("date", ""),
            ev.get("time_et") or "",
            re.sub(r"\s+", " ", ev.get("event", "").lower().strip()),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    out.sort(key=lambda e: (e.get("date", ""), e.get("time_et") or ""))
    return out


# ─── Main ─────────────────────────────────────────────────────────────────────

def run() -> dict:
    all_events: list[dict] = []
    data_source = "none"

    # ── 1. TradingView (primary) ──────────────────────────────────────────────
    tv_events = _fetch_tradingview()
    if tv_events:
        all_events.extend(tv_events)
        data_source = "tradingview"
        logger.info("Using TradingView as primary source (%d events)", len(tv_events))
    else:
        logger.info("TradingView returned nothing — trying ForexFactory")

        # ── 2. ForexFactory HTML ──────────────────────────────────────────────
        ff_events = _fetch_forexfactory()
        if ff_events:
            all_events.extend(ff_events)
            data_source = "forexfactory"
            logger.info("Using ForexFactory (%d events)", len(ff_events))
        else:
            # ── 3. FairEconomy JSON (final fallback) ──────────────────────────
            logger.info("ForexFactory returned nothing — trying FairEconomy JSON")
            fe_events = _fetch_faireconomy()
            all_events.extend(fe_events)
            data_source = "faireconomy" if fe_events else "none"
            logger.info("FairEconomy: %d events", len(fe_events))

    events = _dedup_and_sort(all_events)
    logger.info("Total after dedup: %d USD events", len(events))

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
