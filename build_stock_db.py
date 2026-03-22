"""
build_stock_db.py — One-time builder for comprehensive US stock database.
Scrapes all US stocks from Finviz screener (sector, industry, theme).
Merges with existing scanner data (subtheme).
Output: public/stock_db.json
Run: python3 build_stock_db.py
"""

import json
import time
import logging
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from scraper import INDUSTRY_TO_THEME, INDUSTRY_TO_SUBTHEME, TICKER_THEME_OVERRIDE, HEADERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def parse_finviz_v111(soup) -> list[dict]:
    """Parse Finviz screener v=111: header is rows[1], data starts rows[2]."""
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if len(rows) < 3:
            continue
        # Row[0] is a combined blob, Row[1] is actual header
        headers = [td.get_text(strip=True) for td in rows[1].find_all(["td", "th"])]
        if "Ticker" not in headers:
            continue
        idx = {h: i for i, h in enumerate(headers)}
        stocks = []
        for row in rows[2:]:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 5:
                continue
            ticker   = cells[idx.get("Ticker", 1)]
            company  = cells[idx.get("Company", 2)]
            sector   = cells[idx.get("Sector", 3)]
            industry = cells[idx.get("Industry", 4)]
            if not ticker or ticker in ("Ticker", "No."):
                continue
            stocks.append({"ticker": ticker, "company": company, "sector": sector, "industry": industry})
        if stocks:
            return stocks
    return []


def fetch_all_finviz_stocks() -> list[dict]:
    """Scrape all US stocks from Finviz screener v=111."""
    base_url = "https://finviz.com/screener.ashx?v=111&r={offset}"
    stocks = []
    offset = 1
    while True:
        url = base_url.format(offset=offset)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            page_stocks = parse_finviz_v111(soup)
            if not page_stocks:
                logger.info(f"  offset {offset}: no rows, stopping.")
                break
            stocks.extend(page_stocks)
            logger.info(f"  offset {offset}: +{len(page_stocks)} (total {len(stocks)})")
            if len(page_stocks) < 20:
                break
            offset += 20
            time.sleep(0.35)
        except Exception as e:
            logger.warning(f"  Error at offset {offset}: {e}")
            break
    return stocks


def load_scanner_subthemes() -> dict:
    """Load scanner data to get subtheme mappings per ticker."""
    path = Path("public/thematic_data.json")
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    mapping = {}
    for theme in data.get("themes", []):
        theme_name = theme.get("name", "")
        for sub in theme.get("subthemes", []):
            sub_name = sub.get("name", "")
            for stock in sub.get("stocks", []):
                ticker = stock.get("ticker", "")
                if ticker:
                    mapping[ticker] = {"theme": theme_name, "subtheme": sub_name}
    return mapping


def build():
    logger.info("Fetching all US stocks from Finviz screener (v=111)...")
    stocks = fetch_all_finviz_stocks()
    logger.info(f"Total from Finviz: {len(stocks)}")

    logger.info("Loading scanner subtheme data...")
    scanner_map = load_scanner_subthemes()

    logger.info("Building stock database...")
    db = []
    seen = set()
    for s in stocks:
        industry = s.get("industry", "")
        derived_theme = INDUSTRY_TO_THEME.get(industry, "")
        scanner = scanner_map.get(s["ticker"], {})
        derived_subtheme = INDUSTRY_TO_SUBTHEME.get(industry, "")
        override = TICKER_THEME_OVERRIDE.get(s["ticker"])
        db.append({
            "ticker":   s["ticker"],
            "company":  s["company"],
            "sector":   s["sector"],
            "industry": industry,
            "theme":    override[0] if override else (scanner.get("theme") or derived_theme),
            "subtheme": override[1] if override else (scanner.get("subtheme") or derived_subtheme),
        })
        seen.add(s["ticker"])

    # Merge SEC all_tickers.json — fill in any tickers not found in Finviz (ADRs, foreign-listed, etc.)
    sec_path = Path("public/all_tickers.json")
    if sec_path.exists():
        sec_tickers = json.loads(sec_path.read_text(encoding="utf-8"))
        for s in sec_tickers:
            t = s.get("ticker", "")
            if not t or t in seen:
                continue
            scanner = scanner_map.get(t, {})
            override = TICKER_THEME_OVERRIDE.get(t)
            db.append({
                "ticker":   t,
                "company":  s.get("company", ""),
                "sector":   "",
                "industry": "",
                "theme":    override[0] if override else scanner.get("theme", ""),
                "subtheme": override[1] if override else scanner.get("subtheme", ""),
            })
            seen.add(t)
        logger.info(f"  After SEC merge: {len(db)} total tickers")

    # Patch from gapper_data.json — fill sector/industry/theme/subtheme for any gapper ticker
    # that still has empty fields (foreign ADRs not covered by Finviz or SEC data)
    gapper_path = Path("public/gapper_data.json")
    if gapper_path.exists():
        gapper_data = json.loads(gapper_path.read_text(encoding="utf-8"))
        db_map = {entry["ticker"]: entry for entry in db}
        patched = 0
        for g in gapper_data.get("gappers", []):
            t = g.get("ticker", "")
            if not t:
                continue
            entry = db_map.get(t)
            if not entry:
                continue
            # Only patch fields that are empty
            if not entry.get("sector") and not entry.get("industry"):
                industry = g.get("industry", "")
                if industry and industry != "—":
                    entry["industry"] = industry
                    entry["theme"] = entry["theme"] or INDUSTRY_TO_THEME.get(industry, g.get("finviz_theme", ""))
                    entry["subtheme"] = entry["subtheme"] or INDUSTRY_TO_SUBTHEME.get(industry, "")
                    patched += 1
            elif not entry.get("theme"):
                industry = entry.get("industry", "")
                entry["theme"] = INDUSTRY_TO_THEME.get(industry, g.get("finviz_theme", ""))
                entry["subtheme"] = entry["subtheme"] or INDUSTRY_TO_SUBTHEME.get(industry, "")
                patched += 1
            elif not entry.get("subtheme"):
                industry = entry.get("industry", "")
                entry["subtheme"] = INDUSTRY_TO_SUBTHEME.get(industry, "")
                patched += 1
        logger.info(f"  Gapper patch: {patched} tickers updated from gapper_data.json")

    out_path = Path("public/stock_db.json")
    out_path.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Done! {len(db)} stocks → {out_path}")

    # Spot check
    blte = next((s for s in db if s["ticker"] == "BLTE"), None)
    if blte:
        logger.info(f"BLTE check: {blte}")


if __name__ == "__main__":
    build()
