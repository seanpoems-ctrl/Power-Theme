"""
breadth_monitor.py — Stockbee Market Monitor data fetcher for Power Theme.

Fetches the published Stockbee Market Monitor Google Sheet and writes the
parsed breadth data to public/breadth_monitor.json for the React frontend.

Run:
    python breadth_monitor.py

Output:
    public/breadth_monitor.json
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

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
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    data = fetch_breadth_monitor()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

    if data["ok"]:
        logger.info("Wrote %s (%d rows)", OUTPUT_PATH, len(data["rows"]))
    else:
        logger.warning("Wrote error payload to %s: %s", OUTPUT_PATH, data["detail"])
        sys.exit(1)


if __name__ == "__main__":
    main()
