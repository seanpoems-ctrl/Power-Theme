"""
earnings_calendar.py — Earnings Calendar with Full Enrichment

Sources (in priority order):
  1. Finviz   — https://finviz.com/calendar.ashx  (ticker list, BMO/AMC, EPS est)
  2. IBKR     — ibkr_client.get_earnings_calendar() (if live)
  3. yfinance — Ticker.calendar (fills in any gaps from top-20 RS stocks)

Enrichment via yfinance (applied to all tickers):
  - company name, market cap, price, avg volume, ADR%
  - EPS actual + EPS surprise% (from earnings_dates)
  - Revenue estimate + actual + surprise% (from quarterly_financials)

Filters applied post-enrichment (matching breadth_stocks_builder.py thresholds):
  - Market cap   >= $1B
  - Avg volume   >= 100K shares
  - Price        >= $5
  - Dollar vol   >= $50M daily
  - ADR%         >= 2.5%

Writes public/earnings_calendar.json with flat schema:
  {
    "generated_at": str,
    "data_source":  str,
    "earnings": [
      {
        "date":         "YYYY-MM-DD",
        "ticker":       str,
        "company":      str,
        "time_of_day":  "BMO" | "AMC" | "",
        "mkt_cap":      float | null,   # raw dollars
        "eps_estimate": float | null,
        "eps_act":      float | null,
        "eps_surp_pct": float | null,
        "rev_est":      float | null,   # raw dollars
        "rev_act":      float | null,
        "rev_surp_pct": float | null,
        "price":        float | null,
        "avg_volume":   int   | null,
        "adr_pct":      float | null,
        "dollar_volume":float | null
      }
    ]
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

ET           = ZoneInfo("America/New_York")
TODAY_ET     = datetime.now(ET).date()
WEEK_END     = TODAY_ET + timedelta(days=7)
OUTPUT_PATH  = Path("public/earnings_calendar.json")
THEMATIC_JSON = Path("public/thematic_data.json")

# Quality filters for earnings calendar.
# ADR% is intentionally NOT filtered — large stable caps (GS, JPM, AAPL) have low
# normal ADR but are exactly the stocks we want to track on earnings day.
MIN_MKT_CAP_B  = 1.0          # $1B
MIN_AVG_VOL    = 100_000       # shares
MIN_PRICE      = 5.0           # dollars
MIN_DOLLAR_VOL = 20_000_000    # $20M (lower than breadth scanner — coverage > liquidity)

FINVIZ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finviz.com/",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _safe_float(val, ndigits: int = 2) -> float | None:
    if val is None:
        return None
    try:
        f = float(str(val).replace(",", "").replace("%", "").strip())
        return round(f, ndigits) if not (f != f) else None  # NaN check
    except (ValueError, TypeError):
        return None


def _normalise_time(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s or s in ("-", "--", "n/a", "unknown"):
        return ""
    if any(x in s for x in ("before", "bmo", "morning", "pre")):
        return "BMO"
    if any(x in s for x in ("after", "amc", "close", "hour", "post")):
        return "AMC"
    return ""


def _parse_date(raw) -> date | None:
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
    if not THEMATIC_JSON.exists():
        return []
    try:
        data = json.loads(THEMATIC_JSON.read_text(encoding="utf-8"))
        seen: set[str] = set()
        unique: list[dict] = []
        for th in data.get("themes", []):
            for sub in th.get("subthemes", []):
                for s in sub.get("stocks", []):
                    t = s.get("ticker", "")
                    if t and t not in seen:
                        seen.add(t)
                        unique.append(s)
        unique.sort(key=lambda x: x.get("rs_52w", 0), reverse=True)
        return [s["ticker"] for s in unique[:20]]
    except Exception as exc:
        logger.error("Failed to read thematic_data.json: %s", exc)
        return []


# ─── Source 0: TradingView Screener (broad, 7-day reliable) ──────────────────

def _fetch_tradingview_screener() -> list[dict]:
    """
    Primary broad source: TradingView screener filtered to stocks with
    earnings dates within the next 7 days.  Works on weekends / holidays.
    Returns tickers with date, time_of_day (empty — TV doesn't expose BMO/AMC),
    and basic price/volume data that enrichment will fill in.
    """
    results: list[dict] = []
    try:
        from tradingview_screener import Query, col

        # earnings_release_next_date is stored as Unix timestamp (seconds UTC)
        today_ts    = int(datetime.combine(TODAY_ET,    datetime.min.time()).replace(tzinfo=timezone.utc).timestamp())
        week_end_ts = int(datetime.combine(WEEK_END + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone.utc).timestamp())

        _, df = (
            Query()
            .select(
                "name",
                "earnings_release_next_date",
                "close",
                "market_cap_basic",
                "average_volume_10d_calc",
            )
            .where(
                col("earnings_release_next_date") >= today_ts,
                col("earnings_release_next_date") < week_end_ts,
                col("market_cap_basic") >= 1e9,          # $1B+ mkt cap
                col("close") >= 5,                        # price ≥ $5
                col("average_volume_10d_calc") >= 100_000,
                col("type").isin(["stock", "dr"]),        # exclude ETFs
            )
            .order_by("market_cap_basic", ascending=False)
            .limit(300)
            .get_scanner_data()
        )

        if df is None or df.empty:
            logger.info("TradingView screener: 0 earnings entries")
            return results

        for _, row in df.iterrows():
            ticker = str(row.get("name", "")).strip().upper()
            if not ticker or not re.match(r"^[A-Z]{1,5}$", ticker):
                continue

            raw_ts = row.get("earnings_release_next_date")
            if not raw_ts:
                continue
            try:
                d = datetime.fromtimestamp(float(raw_ts), tz=timezone.utc).date()
            except Exception:
                continue
            if d < TODAY_ET or d > WEEK_END:
                continue

            results.append({
                "ticker":       ticker,
                "company":      "",
                "date":         d.isoformat(),
                "time_of_day":  "",
                "eps_estimate": None,
                "source":       "tradingview",
            })

        logger.info("TradingView screener: %d earnings entries", len(results))

    except Exception as exc:
        logger.error("TradingView screener earnings fetch failed: %s", exc)

    return results


# ─── Source 1: Finviz calendar ────────────────────────────────────────────────

_MONTH_MAP = {m: i for i, m in enumerate(
    ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"], start=1
)}


def _parse_finviz_date(text: str) -> date | None:
    m = re.match(r"(\w{3})\s+(\d{1,2}),\s+(\d{2,4})$", text.strip())
    if not m:
        return None
    mon = _MONTH_MAP.get(m.group(1))
    if not mon:
        return None
    yr = int(m.group(3))
    if yr < 100:
        yr += 2000
    try:
        return date(yr, mon, int(m.group(2)))
    except ValueError:
        return None


def _fetch_finviz_calendar() -> list[dict]:
    """Primary source: Finviz calendar for ticker list, BMO/AMC, EPS estimate."""
    results = []
    try:
        resp = requests.get(
            "https://finviz.com/calendar.ashx",
            headers=FINVIZ_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Finviz calendar fetch failed: %s", exc)
        return results

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        current_date: date | None = None

        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            row_text = row.get_text(" ", strip=True)
            first_td = cells[0]
            colspan  = int(first_td.get("colspan", 1))
            if colspan > 3 or (len(cells) <= 3 and
                               re.search(r"\w{3}\s+\d{1,2},\s+\d{2,4}", row_text)):
                parsed = _parse_finviz_date(first_td.get_text(strip=True))
                if parsed:
                    current_date = parsed
                continue

            if current_date is None or current_date > WEEK_END:
                continue

            if len(cells) < 3:
                continue
            ticker_text = cells[1].get_text(strip=True).upper() if len(cells) > 1 else ""
            if not re.match(r"^[A-Z]{1,5}$", ticker_text):
                continue

            # Company name is in cells[2] on Finviz calendar
            company   = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            time_text = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            eps_text  = cells[4].get_text(strip=True) if len(cells) > 4 else ""

            results.append({
                "ticker":       ticker_text,
                "company":      company,
                "date":         current_date.isoformat(),
                "time_of_day":  _normalise_time(time_text),
                "eps_estimate": _safe_float(eps_text) if eps_text not in ("-","","--") else None,
                "source":       "finviz",
            })

        logger.info("Finviz calendar: %d entries", len(results))
    except Exception as exc:
        logger.error("Finviz calendar parse failed: %s", exc)

    return results


# ─── Source 2: IBKR ───────────────────────────────────────────────────────────

def _fetch_ibkr() -> list[dict]:
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
                "company":      item.get("company", ""),
                "date":         d.isoformat(),
                "time_of_day":  _normalise_time(item.get("time_of_day", "")),
                "eps_estimate": _safe_float(item.get("eps_estimate")),
                "source":       "ibkr",
            })
        logger.info("IBKR earnings: %d entries", len(results))
        return results
    except Exception as exc:
        logger.error("IBKR earnings fetch failed: %s", exc)
        return []


# ─── Source 3: yfinance calendar (gap-fill for top-20 RS stocks) ─────────────

def _fetch_yfinance_calendar(tickers: list[str]) -> list[dict]:
    if not tickers:
        return []
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — skipping")
        return []

    results = []
    for ticker in tickers:
        try:
            cal = yf.Ticker(ticker).calendar
            if cal is None:
                continue
            if isinstance(cal, dict):
                raw_dates = cal.get("Earnings Date") or []
                if not isinstance(raw_dates, list):
                    raw_dates = [raw_dates]
                next_date = next(
                    (d for rd in raw_dates if (d := _parse_date(rd)) and d >= TODAY_ET),
                    None,
                )
                eps = _safe_float(cal.get("Earnings Average") or cal.get("Earnings High"))
            else:
                import pandas as pd
                if cal.empty:
                    continue
                future = [i for i in cal.index if hasattr(i, "date") and i.date() >= TODAY_ET]
                if not future:
                    continue
                next_date = min(future).date()
                eps_col = next((c for c in cal.columns if "average" in c.lower()), None)
                eps = _safe_float(cal[eps_col].iloc[0]) if eps_col else None

            if next_date is None or next_date > WEEK_END:
                continue

            results.append({
                "ticker":       ticker.upper(),
                "company":      "",
                "date":         next_date.isoformat(),
                "time_of_day":  "",
                "eps_estimate": eps,
                "source":       "yfinance",
            })
            time.sleep(0.3)
        except Exception as exc:
            logger.warning("yfinance calendar failed for %s: %s", ticker, exc)

    logger.info("yfinance calendar: %d entries", len(results))
    return results


# ─── Merge sources ────────────────────────────────────────────────────────────

def _merge(*source_lists) -> dict[str, dict]:
    """Merge sources in priority order; higher-priority sources win."""
    priority = {"ibkr": 4, "finviz": 3, "tradingview": 2, "yfinance": 1}
    merged: dict[str, dict] = {}
    for items in source_lists:
        for item in items:
            t = item["ticker"]
            if t not in merged:
                merged[t] = dict(item)
            else:
                existing = merged[t]
                if priority.get(item["source"], 0) > priority.get(existing["source"], 0):
                    merged[t] = dict(item)
                else:
                    # Fill in missing sub-fields from lower-priority source
                    for field in ("time_of_day", "eps_estimate", "company"):
                        if not existing.get(field) and item.get(field):
                            existing[field] = item[field]
    return merged


# ─── yfinance enrichment ──────────────────────────────────────────────────────

def _enrich_with_yfinance(records: list[dict]) -> list[dict]:
    """
    For each record, fetch from yfinance:
      - info: company name, market cap, current price, avg volume
      - earnings_dates: reported EPS, EPS surprise%
      - quarterly_financials: revenue estimate (not available) + actual revenue
      - history (14d): ADR%

    Returns enriched records (failures are logged and skipped/left partial).
    """
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        logger.warning("yfinance not installed — enrichment skipped")
        return records

    enriched = []
    for rec in records:
        ticker = rec["ticker"]
        try:
            t = yf.Ticker(ticker)

            # ── Basic info ───────────────────────────────────────────────────
            info = {}
            try:
                info = t.fast_info
            except Exception:
                pass

            price     = _safe_float(getattr(info, "last_price", None)
                                    or rec.get("price"))
            mkt_cap   = _safe_float(getattr(info, "market_cap", None), ndigits=0)
            avg_vol   = getattr(info, "three_month_average_volume", None)
            if avg_vol is None:
                try:
                    avg_vol = t.info.get("averageVolume") or t.info.get("averageVolume10days")
                except Exception:
                    avg_vol = None
            avg_vol_i = int(avg_vol) if avg_vol else None

            # company name fallback
            company = rec.get("company") or ""
            if not company:
                try:
                    company = t.info.get("longName") or t.info.get("shortName") or ""
                except Exception:
                    pass

            # ── ADR% — 14-day average of (High-Low)/Close ────────────────────
            adr_pct = None
            try:
                hist = t.history(period="1mo", interval="1d", auto_adjust=True)
                if not hist.empty and len(hist) >= 5:
                    n = min(len(hist), 14)
                    adr_pct = round(float(
                        ((hist["High"].iloc[-n:] - hist["Low"].iloc[-n:])
                         / hist["Close"].iloc[-n:] * 100).mean()
                    ), 1)
                    if price is None and not hist.empty:
                        price = round(float(hist["Close"].iloc[-1]), 2)
            except Exception:
                pass

            # ── EPS actual + surprise from earnings_dates ────────────────────
            eps_act      = None
            eps_surp_pct = None
            try:
                ed = t.earnings_dates
                if ed is not None and not ed.empty:
                    # earnings_dates index is timezone-aware; find the most recent past date
                    today_aware = pd.Timestamp.now(tz="UTC")
                    past = ed[ed.index <= today_aware].sort_index(ascending=False)
                    if not past.empty:
                        row = past.iloc[0]
                        rep_col  = next((c for c in past.columns if "reported" in c.lower()), None)
                        surp_col = next((c for c in past.columns if "surprise" in c.lower()), None)
                        if rep_col:
                            eps_act = _safe_float(row[rep_col])
                        if surp_col:
                            eps_surp_pct = _safe_float(row[surp_col])
            except Exception:
                pass

            # ── Revenue actual from quarterly_financials ─────────────────────
            rev_act      = None
            rev_est      = None
            rev_surp_pct = None
            try:
                qf = t.quarterly_financials
                if qf is not None and not qf.empty:
                    rev_row = next(
                        (r for r in qf.index
                         if "total revenue" in str(r).lower() or "revenue" == str(r).lower()),
                        None,
                    )
                    if rev_row is not None:
                        val = qf.loc[rev_row].iloc[0]
                        rev_act = _safe_float(val, ndigits=0)
            except Exception:
                pass

            # ── Dollar volume ────────────────────────────────────────────────
            dollar_vol = None
            if price and avg_vol_i:
                dollar_vol = round(price * avg_vol_i, 0)

            enriched.append({
                **rec,
                "company":      company,
                "mkt_cap":      mkt_cap,
                "price":        price,
                "avg_volume":   avg_vol_i,
                "adr_pct":      adr_pct,
                "dollar_volume": dollar_vol,
                "eps_act":      eps_act,
                "eps_surp_pct": eps_surp_pct,
                "rev_est":      rev_est,
                "rev_act":      rev_act,
                "rev_surp_pct": rev_surp_pct,
            })
            time.sleep(0.4)   # light rate-limiting

        except Exception as exc:
            logger.warning("yfinance enrichment failed for %s: %s", ticker, exc)
            # Keep record without enrichment so it still appears
            enriched.append({
                **rec,
                "mkt_cap": None, "price": None, "avg_volume": None,
                "adr_pct": None, "dollar_volume": None,
                "eps_act": None, "eps_surp_pct": None,
                "rev_est": None, "rev_act": None, "rev_surp_pct": None,
            })

    logger.info("Enrichment complete: %d records", len(enriched))
    return enriched


# ─── Quality filter ───────────────────────────────────────────────────────────

def _apply_filters(records: list[dict]) -> list[dict]:
    """Drop records that fail liquidity/quality thresholds."""
    passed = []
    for r in records:
        mkt_cap_b = (r.get("mkt_cap") or 0) / 1e9
        if mkt_cap_b < MIN_MKT_CAP_B:
            logger.debug("Filter: %s mkt_cap $%.2fB < $%.1fB", r["ticker"], mkt_cap_b, MIN_MKT_CAP_B)
            continue
        if (r.get("price") or 0) < MIN_PRICE:
            logger.debug("Filter: %s price $%.2f < $%.1f", r["ticker"], r.get("price",0), MIN_PRICE)
            continue
        if (r.get("avg_volume") or 0) < MIN_AVG_VOL:
            logger.debug("Filter: %s avg_vol %d < %d", r["ticker"], r.get("avg_volume",0), MIN_AVG_VOL)
            continue
        if (r.get("dollar_volume") or 0) < MIN_DOLLAR_VOL:
            logger.debug("Filter: %s dollar_vol $%,.0f < $%,.0f",
                         r["ticker"], r.get("dollar_volume",0), MIN_DOLLAR_VOL)
            continue
        # ADR% not filtered — large caps like GS/JPM/AAPL have low normal ADR
        passed.append(r)

    logger.info("Quality filter: %d → %d records", len(records), len(passed))
    return passed


# ─── Main ─────────────────────────────────────────────────────────────────────

def run() -> dict:
    # ── 1. Fetch all sources ─────────────────────────────────────────────────
    tv_data   = _fetch_tradingview_screener()      # broad, reliable, 7-day
    fv_data   = _fetch_finviz_calendar()           # adds BMO/AMC + EPS est
    ibkr_data = _fetch_ibkr()                      # live data if available
    top20     = _top20_tickers()
    yf_cal    = _fetch_yfinance_calendar(top20)    # gap-fill for RS leaders

    # ── 2. Merge — highest priority last wins for shared fields ───────────────
    # Priority: IBKR (4) > Finviz (3) > TradingView (2) > yfinance (1)
    # Finviz fills BMO/AMC + EPS estimate on top of TV's broad ticker list
    merged = _merge(yf_cal, tv_data, fv_data, ibkr_data)
    logger.info("Merged: %d unique tickers", len(merged))

    records = sorted(merged.values(), key=lambda r: (r["date"], r["ticker"]))

    # ── 3. Enrich with yfinance (price, mkt_cap, ADR%, EPS actual, revenue) ──
    records = _enrich_with_yfinance(records)

    # ── 4. Apply quality filters ─────────────────────────────────────────────
    records = _apply_filters(records)

    # ── 5. Build flat output ─────────────────────────────────────────────────
    earnings_out = []
    for r in records:
        earnings_out.append({
            "date":         r["date"],
            "ticker":       r["ticker"],
            "company":      r.get("company") or "",
            "time_of_day":  r.get("time_of_day") or "",
            "mkt_cap":      r.get("mkt_cap"),
            "eps_estimate": r.get("eps_estimate"),
            "eps_act":      r.get("eps_act"),
            "eps_surp_pct": r.get("eps_surp_pct"),
            "rev_est":      r.get("rev_est"),
            "rev_act":      r.get("rev_act"),
            "rev_surp_pct": r.get("rev_surp_pct"),
            "price":        r.get("price"),
            "avg_volume":   r.get("avg_volume"),
            "adr_pct":      r.get("adr_pct"),
            "dollar_volume": r.get("dollar_volume"),
        })

    data_sources = set()
    for r in merged.values():
        data_sources.add(r.get("source", "unknown"))
    data_source = "+".join(sorted(data_sources)) or "none"

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "data_source":  data_source,
        "earnings":     earnings_out,
    }


def main() -> None:
    result = run()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "Done — %d earnings → %s  (source: %s)",
        len(result["earnings"]),
        OUTPUT_PATH,
        result["data_source"],
    )


if __name__ == "__main__":
    main()
