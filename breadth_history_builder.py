from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
breadth_history_builder.py — long breadth history (2009 → today) for the Breadth Cycle page's scenario engine
==============================================================================================================
breadth_monitor.py only publishes the current year's sheet (~200 rows). The same Stockbee Market Monitor Google Sheet also holds one
tab per year back to 2007 (STOCKBEE_SHEET_GIDS in breadth_monitor.py). This script reads those tabs and writes one compact, column-oriented
history so the dashboard can run an "analog" study: find earlier days that looked like the selected day (breadth + S&P 500 momentum,
trend and volatility) and report how the S&P 500 did over the following 1 / 3 / 6 / 12 months.

Why a header-driven parser: the sheet's column layout changed over the years (2009 and 2010 use different orders, 2013 has an extra
ratio column, 2018-2021 have 27 columns, ...). Columns are matched by header text instead of position. 2007 and 2008 are laid out
horizontally (dates across the top) and are skipped, so history starts 2009-01-02. The 5- and 10-day ratios are NOT read from the sheet
(older tabs lack them): the dashboard recomputes them as sum(up 4%)/sum(down 4%) over 5 / 10 sessions.

S&P 500 closes come from Yahoo (^GSPC, price-only) for every date — the sheet's own S&P column is missing or mis-positioned in several years.
`spx_dates`/`spx` start earlier than the breadth data so the 200-day average is defined from the first breadth row.

Incremental: when public/breadth_long_history.json exists only the current year's tab (plus the previous year in January) is re-read and
merged; pass --full to rebuild every year.

Output: public/breadth_long_history.json
"""
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

import breadth_monitor as bm

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

OUTPUT_PATH = Path(__file__).parent / "public" / "breadth_long_history.json"
FIRST_YEAR = 2009                       # 2007/2008 tabs are horizontal — not parsed
CORE = ("up_4_pct", "down_4_pct", "up_25_q", "down_25_q", "up_13_34d", "down_13_34d")
FIELDS = ("up_4_pct", "down_4_pct", "up_25_q", "down_25_q", "up_25_m", "down_25_m", "up_50_m", "down_50_m", "up_13_34d", "down_13_34d", "t2108")


def classify(header: str) -> str | None:
    """Map a sheet header cell to one of FIELDS (or 'date'); None = ignore."""
    h = re.sub(r"\s+", " ", header.lower()).strip()
    if not h:
        return None
    if h == "date":
        return "date"
    if "t2108" in h:
        return "t2108"
    if "34" in h and "13" in h:
        if "diff" in h or h.endswith("34/13d"):
            return None
        return "down_13_34d" if ("down" in h or "bear" in h) else ("up_13_34d" if ("up" in h or "plus" in h or "bull" in h) else None)
    if "quarter" in h and "25" in h:
        if "ratio" in h:
            return None
        return "down_25_q" if "down" in h else "up_25_q"
    if "month" in h and "50" in h:
        return "down_50_m" if "down" in h else "up_50_m"
    if "month" in h and "25" in h:
        return "down_25_m" if "down" in h else "up_25_m"
    if h in ("50% up", "50% down"):
        return "down_50_m" if "down" in h else "up_50_m"
    if "4%" in h and ("daily" in h or "today" in h):
        return "down_4_pct" if "down" in h else "up_4_pct"
    return None


def parse_sheet(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return []
    rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])] for tr in table.find_all("tr")]
    colmap: dict[int, str] = {}
    for cells in rows:
        if any(c.strip().lower() == "date" for c in cells):
            cand = {i: k for i, c in enumerate(cells) if (k := classify(c))}
            if "date" in cand.values() and len(cand) >= 7:
                colmap = cand
                break
    if not colmap:
        return []
    date_idx = next(i for i, k in colmap.items() if k == "date")
    out = []
    for cells in rows:
        if len(cells) <= date_idx or not bm._DATE_CELL.match(cells[date_idx].strip()):
            continue
        dt = bm._parse_us_date(cells[date_idx])
        # The sheet has typo dates (e.g. 2/22/213, 2/8/2111) — keep only plausible ones.
        if dt is None or not (FIRST_YEAR <= dt.year <= datetime.now().year + 1):
            continue
        rec = {"date": dt.date().isoformat()}
        for i, k in colmap.items():
            if k == "date" or i >= len(cells):
                continue
            if k == "t2108":
                v = bm._num(cells[i])
                rec[k] = v if v is not None and 0 <= v <= 100 else None
            else:
                rec[k] = bm._int(cells[i])
        if all(rec.get(k) is not None for k in CORE):
            out.append(rec)
    return out


def fetch_year(client: httpx.Client, year: int) -> list[dict]:
    gid = bm.STOCKBEE_SHEET_GIDS.get(year)
    if gid is None:
        return []
    r = client.get(bm._sheet_pub_url(gid))
    r.raise_for_status()
    recs = parse_sheet(r.text)
    logger.info("%d: %d rows", year, len(recs))
    return recs


def load_existing() -> dict[str, dict]:
    try:
        d = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        cols = d["cols"]
        return {dt: {k: (cols[k][i] if k in cols else None) for k in FIELDS} for i, dt in enumerate(d["dates"])}
    except Exception:  # noqa: BLE001
        return {}


def fetch_spx(start: str):
    import yfinance as yf
    df = yf.download("^GSPC", start=start, auto_adjust=False, progress=False)
    close = df["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    close = close.dropna()
    return [d.strftime("%Y-%m-%d") for d in close.index], [round(float(v), 2) for v in close.values]


def main() -> None:
    full = "--full" in sys.argv
    by_date = {} if full else load_existing()
    now_year = datetime.now().year
    if by_date:
        years = [now_year] + ([now_year - 1] if datetime.now().month == 1 else [])
    else:
        years = [y for y in sorted(bm.STOCKBEE_SHEET_GIDS) if y >= FIRST_YEAR]
    try:
        with httpx.Client(timeout=45, headers=bm.BROWSER_HEADERS, follow_redirects=True) as client:
            for y in years:
                for rec in fetch_year(client, y):
                    by_date[rec["date"]] = {k: rec.get(k) for k in FIELDS}
    except Exception as exc:  # noqa: BLE001
        logger.error("Sheet fetch failed (%s) - leaving existing file untouched", exc)
        return
    dates = sorted(by_date)
    if len(dates) < 1500:
        logger.error("Only %d rows parsed - refusing to publish", len(dates))
        return
    try:
        spx_dates, spx = fetch_spx("2008-01-01")
    except Exception as exc:  # noqa: BLE001
        logger.error("S&P 500 download failed (%s) - leaving existing file untouched", exc)
        return
    # Typo dates (weekends/holidays) have no S&P close — drop them so every breadth row aligns with a trading session.
    have = set(spx_dates)
    dates = [d for d in dates if d in have]
    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": bm.STOCKBEE_MM_PAGE,
        "dates": dates,
        "cols": {k: [by_date[d].get(k) for d in dates] for k in FIELDS},
        "spx_dates": spx_dates,
        "spx": spx,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    logger.info("Written %s: %d sessions %s -> %s, %d S&P closes (%.0f KB)", OUTPUT_PATH, len(dates), dates[0], dates[-1], len(spx),
                OUTPUT_PATH.stat().st_size / 1024)


if __name__ == "__main__":
    main()
