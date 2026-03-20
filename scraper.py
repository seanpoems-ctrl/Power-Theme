"""
scraper.py — 美股強勢主題篩選器數據爬蟲 v4
Theme → Sub-theme → Stocks  hierarchical scan from Finviz Screener
產出 public/thematic_data.json
"""

import json
import os
import time
import random
import logging
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import exchange_calendars as xcals

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

CI = os.environ.get("CI") == "true"

TOP_THEMES = 5
TOP_SUBTHEMES_PER_THEME = 5
TOP_STOCKS_PER_SUBTHEME = 10

_NYSE = xcals.get_calendar("XNYS")


def is_trading_day(d: date | None = None) -> bool:
    if d is None:
        d = date.today()
    try:
        return _NYSE.is_session(d.isoformat())
    except Exception:
        return d.weekday() < 5


def last_trading_date(d: date | None = None) -> date:
    if d is None:
        d = date.today()
    try:
        prev = _NYSE.previous_session(d.isoformat())
        return prev.date()
    except Exception:
        d -= timedelta(days=1)
        while not is_trading_day(d):
            d -= timedelta(days=1)
        return d


def parse_pct(s: str) -> float | None:
    if not s or s == "-":
        return None
    try:
        return float(s.replace("%", "").replace(",", "").strip())
    except ValueError:
        return None


def parse_vol(s: str) -> int:
    s = s.strip().upper()
    try:
        if s.endswith("B"):
            return int(float(s[:-1]) * 1e9)
        if s.endswith("M"):
            return int(float(s[:-1]) * 1e6)
        if s.endswith("K"):
            return int(float(s[:-1]) * 1e3)
        return int(float(s.replace(",", "")))
    except ValueError:
        return 0


def _sleep():
    lo, hi = (0.35, 0.7) if CI else (0.8, 1.6)
    time.sleep(random.uniform(lo, hi))


# ──────────────────────────────────────────────────────────────
# Industry → Parent Theme Mapping  (144 Finviz industries → ~20 themes)
# ──────────────────────────────────────────────────────────────

INDUSTRY_TO_THEME = {
    # ── AI & Semiconductors ──
    "Semiconductors": "AI & Semiconductors",
    "Semiconductor Equipment & Materials": "AI & Semiconductors",
    "Electronic Components": "AI & Semiconductors",
    "Scientific & Technical Instruments": "AI & Semiconductors",
    "Computer Hardware": "AI & Semiconductors",
    "Consumer Electronics": "AI & Semiconductors",
    "Communication Equipment": "AI & Semiconductors",
    "Electronics & Computer Distribution": "AI & Semiconductors",

    # ── Software & Cloud ──
    "Software - Application": "Software & Cloud",
    "Software - Infrastructure": "Software & Cloud",
    "Information Technology Services": "Software & Cloud",
    "Internet Content & Information": "Software & Cloud",

    # ── Internet & E-Commerce ──
    "Internet Retail": "Internet & E-Commerce",
    "Electronic Gaming & Multimedia": "Internet & E-Commerce",

    # ── Financials ──
    "Banks - Diversified": "Financials",
    "Banks - Regional": "Financials",
    "Capital Markets": "Financials",
    "Financial Data & Stock Exchanges": "Financials",
    "Asset Management": "Financials",
    "Insurance - Life": "Financials",
    "Insurance - Property & Casualty": "Financials",
    "Insurance - Diversified": "Financials",
    "Insurance - Reinsurance": "Financials",
    "Insurance - Specialty": "Financials",
    "Insurance Brokers": "Financials",
    "Credit Services": "Financials",
    "Financial Conglomerates": "Financials",
    "Mortgage Finance": "Financials",

    # ── Healthcare & Biotech ──
    "Biotechnology": "Healthcare & Biotech",
    "Drug Manufacturers - General": "Healthcare & Biotech",
    "Drug Manufacturers - Specialty & Generic": "Healthcare & Biotech",
    "Medical Devices": "Healthcare & Biotech",
    "Medical Instruments & Supplies": "Healthcare & Biotech",
    "Health Information Services": "Healthcare & Biotech",
    "Healthcare Plans": "Healthcare & Biotech",
    "Diagnostics & Research": "Healthcare & Biotech",
    "Pharmaceutical Retailers": "Healthcare & Biotech",
    "Medical Care Facilities": "Healthcare & Biotech",
    "Medical Distribution": "Healthcare & Biotech",

    # ── Defense & Aerospace ──
    "Aerospace & Defense": "Defense & Aerospace",

    # ── Energy - Oil & Gas ──
    "Oil & Gas E&P": "Energy - Oil & Gas",
    "Oil & Gas Integrated": "Energy - Oil & Gas",
    "Oil & Gas Midstream": "Energy - Oil & Gas",
    "Oil & Gas Refining & Marketing": "Energy - Oil & Gas",
    "Oil & Gas Equipment & Services": "Energy - Oil & Gas",
    "Oil & Gas Drilling": "Energy - Oil & Gas",

    # ── Clean Energy & Utilities ──
    "Solar": "Clean Energy & Utilities",
    "Uranium": "Clean Energy & Utilities",
    "Utilities - Renewable": "Clean Energy & Utilities",
    "Utilities - Regulated Electric": "Clean Energy & Utilities",
    "Utilities - Regulated Gas": "Clean Energy & Utilities",
    "Utilities - Regulated Water": "Clean Energy & Utilities",
    "Utilities - Diversified": "Clean Energy & Utilities",
    "Utilities - Independent Power Producers": "Clean Energy & Utilities",

    # ── Consumer Discretionary ──
    "Specialty Retail": "Consumer Discretionary",
    "Apparel Retail": "Consumer Discretionary",
    "Home Improvement Retail": "Consumer Discretionary",
    "Auto Manufacturers": "Consumer Discretionary",
    "Auto Parts": "Consumer Discretionary",
    "Auto & Truck Dealerships": "Consumer Discretionary",
    "Restaurants": "Consumer Discretionary",
    "Leisure": "Consumer Discretionary",
    "Gambling": "Consumer Discretionary",
    "Resorts & Casinos": "Consumer Discretionary",
    "Travel Services": "Consumer Discretionary",
    "Lodging": "Consumer Discretionary",
    "Luxury Goods": "Consumer Discretionary",
    "Apparel Manufacturing": "Consumer Discretionary",
    "Footwear & Accessories": "Consumer Discretionary",
    "Residential Construction": "Consumer Discretionary",
    "Department Stores": "Consumer Discretionary",
    "Recreational Vehicles": "Consumer Discretionary",
    "Furnishings, Fixtures & Appliances": "Consumer Discretionary",
    "Personal Services": "Consumer Discretionary",
    "Textile Manufacturing": "Consumer Discretionary",

    # ── Consumer Staples ──
    "Household & Personal Products": "Consumer Staples",
    "Packaged Foods": "Consumer Staples",
    "Beverages - Non-Alcoholic": "Consumer Staples",
    "Beverages - Brewers": "Consumer Staples",
    "Beverages - Wineries & Distilleries": "Consumer Staples",
    "Grocery Stores": "Consumer Staples",
    "Discount Stores": "Consumer Staples",
    "Tobacco": "Consumer Staples",
    "Farm Products": "Consumer Staples",
    "Confectioners": "Consumer Staples",
    "Food Distribution": "Consumer Staples",
    "Education & Training Services": "Consumer Staples",

    # ── Industrials ──
    "Railroads": "Industrials",
    "Trucking": "Industrials",
    "Airlines": "Industrials",
    "Airports & Air Services": "Industrials",
    "Marine Shipping": "Industrials",
    "Industrial Distribution": "Industrials",
    "Specialty Industrial Machinery": "Industrials",
    "Farm & Heavy Construction Machinery": "Industrials",
    "Metal Fabrication": "Industrials",
    "Building Products & Equipment": "Industrials",
    "Engineering & Construction": "Industrials",
    "Conglomerates": "Industrials",
    "Rental & Leasing Services": "Industrials",
    "Waste Management": "Industrials",
    "Pollution & Treatment Controls": "Industrials",
    "Electrical Equipment & Parts": "Industrials",
    "Consulting Services": "Industrials",
    "Staffing & Employment Services": "Industrials",
    "Security & Protection Services": "Industrials",
    "Tools & Accessories": "Industrials",
    "Integrated Freight & Logistics": "Industrials",
    "Business Equipment & Supplies": "Industrials",
    "Specialty Business Services": "Industrials",

    # ── Real Estate ──
    "REIT - Diversified": "Real Estate",
    "REIT - Industrial": "Real Estate",
    "REIT - Office": "Real Estate",
    "REIT - Residential": "Real Estate",
    "REIT - Retail": "Real Estate",
    "REIT - Healthcare Facilities": "Real Estate",
    "REIT - Hotel & Motel": "Real Estate",
    "REIT - Mortgage": "Real Estate",
    "REIT - Specialty": "Real Estate",
    "Real Estate Services": "Real Estate",
    "Real Estate - Development": "Real Estate",
    "Real Estate - Diversified": "Real Estate",

    # ── Materials & Mining ──
    "Gold": "Materials & Mining",
    "Silver": "Materials & Mining",
    "Copper": "Materials & Mining",
    "Steel": "Materials & Mining",
    "Aluminum": "Materials & Mining",
    "Other Industrial Metals & Mining": "Materials & Mining",
    "Specialty Chemicals": "Materials & Mining",
    "Chemicals": "Materials & Mining",
    "Agricultural Inputs": "Materials & Mining",
    "Building Materials": "Materials & Mining",
    "Lumber & Wood Production": "Materials & Mining",
    "Paper & Paper Products": "Materials & Mining",
    "Coking Coal": "Materials & Mining",
    "Thermal Coal": "Materials & Mining",
    "Other Precious Metals & Mining": "Materials & Mining",
    "Packaging & Containers": "Materials & Mining",

    # ── Media & Entertainment ──
    "Entertainment": "Media & Entertainment",
    "Publishing": "Media & Entertainment",
    "Broadcasting": "Media & Entertainment",
    "Advertising Agencies": "Media & Entertainment",

    # ── Telecom ──
    "Telecom Services": "Telecom",

    # ── Shell ──
    "Shell Companies": "Other",
}


# ──────────────────────────────────────────────────────────────
# Finviz Themes Map — 40 parent themes from map.ashx?t=themes
# ──────────────────────────────────────────────────────────────

# Node ID prefix → parent theme name (from Finviz themes map JS bundle)
_THEME_MAP_PREFIXES = {
    "energyclean": "Energy Renewable",
    "energybase": "Energy Traditional",
    "commenergy": "Commodities Energy",
    "commmetals": "Commodities Metals",
    "commagri": "Commodities Agriculture",
    "cybersecurity": "Cybersecurity",
    "entertainment": "Digital Entertainment",
    "environmental": "Environmental Sustainability",
    "transportation": "Transportation & Logistics",
    "agriculture": "Agriculture & FoodTech",
    "realestate": "Real Estate & REITs",
    "blockchain": "Crypto & Blockchain",
    "automation": "Industrial Automation",
    "autonomous": "Autonomous Systems",
    "healthcare": "Healthcare & Biotech",
    "longevity": "Aging Population & Longevity",
    "nutrition": "Healthy Food & Nutrition",
    "biometrics": "Biometrics",
    "smarthome": "Smart Home",
    "wearables": "Wearables",
    "education": "Education Technology",
    "ecommerce": "E-commerce",
    "hardware": "Hardware",
    "software": "Software",
    "consumer": "Consumer Goods",
    "robotics": "Robotics",
    "nanotech": "Nanotechnology",
    "quantum": "Quantum Computing",
    "vareality": "Virtual & Augmented Reality",
    "bigdata": "Big Data",
    "defense": "Defense & Aerospace",
    "fintech": "FinTech",
    "telecom": "Telecommunications",
    "social": "Social Media",
    "cloud": "Cloud Computing",
    "space": "Space Tech",
    "semis": "Semiconductors",
    "evs": "Electric Vehicles",
    "iot": "Internet of Things",
    "ai": "Artificial Intelligence",
}

# URL param for each timeframe on Finviz themes map
_THEME_MAP_TIMEFRAMES = {
    "perf_1d": "",            # default (no param) = 1-day
    "perf_1w": "&st=w1",
    "perf_1m": "&st=w4",
    "perf_3m": "&st=w13",
    "perf_6m": "&st=w26",
}


def fetch_themes_map_performance() -> list[dict]:
    """Fetch all ~40 Finviz parent themes with 1D/1W/1M/3M/6M performance.

    Scrapes finviz.com/map.ashx?t=themes for each timeframe (5 HTTP requests).
    Returns list of dicts with name, perf_1d..perf_6m, rs_score, stage2_momentum.
    """
    import re

    base_url = "https://finviz.com/map.ashx?t=themes"
    # {theme_name: {perf_1d: float, ...}}
    theme_data: dict[str, dict[str, float]] = {}

    for perf_key, url_param in _THEME_MAP_TIMEFRAMES.items():
        url = base_url + url_param
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            # Extract nodes JSON from FinvizInitCanvas({..., initialPerf: {"nodes": {...}, ...}})
            m = re.search(r'"nodes":(\{[^}]+\})', r.text)
            if not m:
                logger.warning(f"  Themes map: no nodes found for {perf_key}")
                continue
            nodes = json.loads(m.group(1))

            # Aggregate sub-theme nodes into parent themes
            parent_sums: dict[str, list[float]] = {}
            for node_id, perf_val in nodes.items():
                parent_name = None
                # Match longest prefix first (sorted by length desc)
                for prefix, name in sorted(_THEME_MAP_PREFIXES.items(), key=lambda x: -len(x[0])):
                    if node_id.startswith(prefix):
                        parent_name = name
                        break
                if parent_name is None:
                    continue
                if parent_name not in parent_sums:
                    parent_sums[parent_name] = []
                parent_sums[parent_name].append(perf_val)

            for name, vals in parent_sums.items():
                if name not in theme_data:
                    theme_data[name] = {}
                theme_data[name][perf_key] = round(sum(vals) / len(vals), 3)

            logger.info(f"  Themes map {perf_key}: {len(parent_sums)} themes from {len(nodes)} nodes")
        except Exception as e:
            logger.warning(f"  Themes map fetch failed for {perf_key}: {e}")
        _sleep()

    # Build results with RS score + Stage 2 badge
    perf_keys = ["perf_1d", "perf_1w", "perf_1m", "perf_3m", "perf_6m"]
    results = []
    for name, perfs in theme_data.items():
        # RS Score: 5% 1D + 15% 1W + 25% 1M + 30% 3M + 25% 6M
        rs_score = round(
            perfs.get("perf_1d", 0) * 0.05 +
            perfs.get("perf_1w", 0) * 0.15 +
            perfs.get("perf_1m", 0) * 0.25 +
            perfs.get("perf_3m", 0) * 0.30 +
            perfs.get("perf_6m", 0) * 0.25, 2)
        stage2 = all(perfs.get(k, 0) > 0 for k in perf_keys)
        results.append({
            "name": name,
            "rs_score": rs_score,
            "stage2_momentum": stage2,
            **{k: perfs.get(k, 0) for k in perf_keys},
        })

    results.sort(key=lambda t: t["rs_score"], reverse=True)
    logger.info(f"  Themes map: {len(results)} parent themes with performance data")
    return results


# ──────────────────────────────────────────────────────────────
# Fetch Industry Performance + Industry Codes
# ──────────────────────────────────────────────────────────────

def fetch_industry_performance() -> list[dict]:
    """Fetch performance data for all ~144 Finviz industries (single HTTP request)."""
    url = "https://finviz.com/groups.ashx?g=industry&v=140"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if len(rows) < 5:
            continue
        header = [td.get_text(strip=True) for td in rows[0].find_all(["td", "th"])]
        if "Name" not in header:
            continue
        # v=140 columns: No, Name, PerfW, PerfM, PerfQ, PerfHY, PerfY, PerfYTD, AvgVol, RelVol, Change, Volume
        industries = []
        for row in rows[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 12:
                continue
            name = cells[1]
            parent = INDUSTRY_TO_THEME.get(name)
            if parent is None:
                logger.warning(f"  Unmapped industry: {name}")
                parent = "Other"
            industries.append({
                "name": name,
                "parent_theme": parent,
                "perf_1w": parse_pct(cells[2]),
                "perf_1m": parse_pct(cells[3]),
                "perf_3m": parse_pct(cells[4]),
                "perf_6m": parse_pct(cells[5]),
                "perf_1y": parse_pct(cells[6]),
                "perf_ytd": parse_pct(cells[7]),
                "perf_1d": parse_pct(cells[10]),  # "Change" column = today's %
            })
        logger.info(f"  Fetched {len(industries)} industries from Finviz groups")
        return industries
    return []


def discover_industry_codes() -> dict[str, str]:
    """Return {display_name: filter_code} for all Finviz industries from screener dropdown."""
    url = "https://finviz.com/screener.ashx?v=141"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    sel = soup.find("select", {"data-filter": "ind"})
    codes = {}
    if sel:
        for opt in sel.find_all("option"):
            v = opt.get("value", "")
            if v:
                codes[opt.get_text(strip=True)] = v
    logger.info(f"  Discovered {len(codes)} industry filter codes")
    return codes


def aggregate_theme_performance(industries: list[dict]) -> list[dict]:
    """Group industries by parent theme, compute avg performance + RS score."""
    from collections import defaultdict
    theme_groups = defaultdict(list)
    for ind in industries:
        theme_groups[ind["parent_theme"]].append(ind)

    perf_keys = ["perf_1d", "perf_1w", "perf_1m", "perf_3m", "perf_6m"]
    results = []
    for theme_name, inds in theme_groups.items():
        if theme_name == "Other":
            continue
        avg_perfs = {}
        for k in perf_keys:
            vals = [i[k] for i in inds if i.get(k) is not None]
            avg_perfs[k] = round(sum(vals) / len(vals), 2) if vals else 0

        # Weighted RS Score: 5% 1D + 15% 1W + 25% 1M + 30% 3M + 25% 6M
        rs_score = round(
            avg_perfs.get("perf_1d", 0) * 0.05 +
            avg_perfs.get("perf_1w", 0) * 0.15 +
            avg_perfs.get("perf_1m", 0) * 0.25 +
            avg_perfs.get("perf_3m", 0) * 0.30 +
            avg_perfs.get("perf_6m", 0) * 0.25, 2)

        # Stage 2 Momentum: positive across ALL 5 timeframes
        stage2 = all(avg_perfs.get(k, 0) > 0 for k in perf_keys)

        results.append({
            "name": theme_name,
            "industries": [i["name"] for i in inds],
            "n_industries": len(inds),
            "rs_score": rs_score,
            "stage2_momentum": stage2,
            **avg_perfs,
        })

    results.sort(key=lambda t: t["rs_score"], reverse=True)
    return results


# ──────────────────────────────────────────────────────────────
# Screener table parser
# ──────────────────────────────────────────────────────────────

def _parse_screener_table(soup) -> list[dict]:
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if len(rows) < 2:
            continue
        header = [td.get_text(strip=True) for td in rows[0].find_all(["td", "th"])]
        if "No." not in header or "Ticker" not in header:
            continue
        stocks = []
        for row in rows[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 18:
                continue
            # Finviz v=141 columns (18 total):
            # 0:No 1:Ticker 2:PerfW 3:PerfM 4:PerfQ 5:PerfHY 6:PerfYTD
            # 7:PerfY 8:Perf3Y 9:Perf5Y 10:Perf10Y 11:VolW 12:VolM
            # 13:AvgVol 14:RelVol 15:Price 16:Change 17:Volume
            stocks.append({
                "ticker": cells[1],
                "perf_1w": parse_pct(cells[2]),
                "perf_1m": parse_pct(cells[3]),
                "perf_3m": parse_pct(cells[4]),
                "perf_6m": parse_pct(cells[5]),
                "perf_ytd": parse_pct(cells[6]),
                "avg_volume": parse_vol(cells[13]),
                "price": float(cells[15].replace(",", "").replace("-", "0") or "0"),
                "change_pct": parse_pct(cells[16]) or 0,
                "volume": parse_vol(cells[17]),
            })
        return stocks
    return []


def fetch_screener_stocks(filter_key: str, filter_val: str, max_pages: int = 10) -> list[dict]:
    all_stocks: list[dict] = []
    for page in range(1, max_pages + 1):
        offset = (page - 1) * 20 + 1
        url = f"https://finviz.com/screener.ashx?v=141&f={filter_key}_{filter_val}&r={offset}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"    Screener fail: {e}")
            break
        page_stocks = _parse_screener_table(BeautifulSoup(r.text, "html.parser"))
        all_stocks.extend(page_stocks)
        if len(page_stocks) < 20:
            break
        _sleep()
    return all_stocks


# ──────────────────────────────────────────────────────────────
# Individual stock detail + sparkline
# ──────────────────────────────────────────────────────────────

PERF_MAP = {
    "Perf Day": "perf_1d", "Perf Week": "perf_1w", "Perf Month": "perf_1m",
    "Perf Quarter": "perf_3m", "Perf Half Y": "perf_6m", "Perf YTD": "perf_ytd",
}


def fetch_stock_detail(ticker: str) -> dict | None:
    url = f"https://finviz.com/quote.ashx?t={ticker}&ty=c&p=d&b=1"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="snapshot-table2")
    if not table:
        return None

    snap = {}
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        for i in range(0, len(cells) - 1, 2):
            snap[cells[i].get_text(strip=True)] = cells[i + 1].get_text(strip=True)

    # Sector and Industry are in tab-link anchors with hrefs like f=sec_* and f=ind_*
    sector = ""
    industry = ""
    for a in soup.find_all("a", class_="tab-link"):
        href = a.get("href", "")
        if "f=sec_" in href:
            sector = a.get_text(strip=True)
        elif "f=ind_" in href:
            industry = a.get_text(strip=True)

    try:
        price = float(snap.get("Price", "0").replace(",", ""))
        if price <= 0:
            return None
        volume = parse_vol(snap.get("Volume", "0"))
        atr_s = (snap.get("ATR (14)") or snap.get("ATR") or "0").strip()
        try:
            atr = float(atr_s.replace(",", "") or "0")
        except ValueError:
            atr = 0.0
        adr_pct = round((atr / price) * 100, 2) if price > 0 and atr > 0 else 0.0

        company = ""
        for sel in ["a.tab-link", "h1"]:
            tag = soup.select_one(sel)
            if tag and tag.get_text(strip=True):
                company = tag.get_text(strip=True)
                break

        import re as _re
        def _parse_52w(s):
            # Finviz format: "71.54-1.12%" or "31.03127.97%" — extract leading number only
            m = _re.match(r"^([\d,]+\.\d{2})", (s or "").strip())
            try:
                return float(m.group(1).replace(",", "")) if m else 0.0
            except ValueError:
                return 0.0
        h52 = _parse_52w(snap.get("52W High"))
        l52 = _parse_52w(snap.get("52W Low"))
        avg_vol = parse_vol(snap.get("Avg Volume") or "0")
        dist_52w_high = round((price / h52 - 1) * 100, 2) if h52 > 0 else None
        rvol = round(volume / avg_vol, 2) if avg_vol > 0 else None

        result = {
            "ticker": ticker, "company": company, "price": round(price, 2),
            "change_pct": parse_pct(snap.get("Change", "0%")) or 0,
            "volume": volume, "dollar_volume": round(price * volume),
            "avg_dollar_volume": round(price * avg_vol) if avg_vol > 0 else 0,
            "adr_pct": adr_pct,
            "52w_high": round(h52, 2) if h52 > 0 else None,
            "52w_low": round(l52, 2) if l52 > 0 else None,
            "avg_volume": avg_vol,
            "dist_52w_high": dist_52w_high,
            "rvol": rvol,
            "sector": sector,
            "industry": industry,
        }
        for fk, jk in PERF_MAP.items():
            result[jk] = parse_pct(snap.get(fk, ""))
        for fk, jk in [("SMA20", "sma20_pct"), ("SMA50", "sma50_pct"), ("SMA200", "sma200_pct")]:
            result[jk] = parse_pct(snap.get(fk, "") or "")

        # Finviz snapshot-table2 has no "Perf Day" field; always fall back to Change%
        if result.get("perf_1d") is None:
            result["perf_1d"] = result["change_pct"]
        return result
    except (ValueError, TypeError):
        return None


def fetch_sparkline(ticker: str) -> dict:
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="6mo", interval="1d")
        if hist.empty or len(hist) < 2:
            return {"sparkline": [], "bars_30d": []}
        closes = [round(float(c), 2) for c in hist["Close"].tolist()]
        bars_30d = [
            {"h": round(float(r["High"]), 2), "l": round(float(r["Low"]), 2),
             "c": round(float(r["Close"]), 2), "v": int(r["Volume"])}
            for _, r in hist.tail(30).iterrows()
        ]
        return {"sparkline": closes, "bars_30d": bars_30d}
    except Exception:
        return {"sparkline": [], "bars_30d": []}


# ──────────────────────────────────────────────────────────────
# Market Condition (SPY + QQQ indicators)
# ──────────────────────────────────────────────────────────────

def _ema(closes: list, period: int):
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    val = sum(closes[:period]) / period
    for p in closes[period:]:
        val = p * k + val * (1 - k)
    return val


def _rsi(closes: list, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0.0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0.0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 2)


def _elite_status(price, sma10, sma20, sma50, sma200, rsi, breadth) -> str:
    """Classify index into Strong / Mediocre / Lagging / Weak per Elite Rubric."""
    # Strong: price > SMA10 > SMA20 > SMA50, breadth > 70%, RSI 60–80
    strong_smas = sma10 and sma20 and sma50 and price > sma10 > sma20 > sma50
    if strong_smas and (breadth is None or breadth > 70) and (rsi is None or 60 <= rsi <= 80):
        return "Strong"

    # Weak: price below SMA10, SMA20, SMA50 AND SMA200
    if (sma10 and price < sma10) and (sma20 and price < sma20) and \
       (sma50 and price < sma50) and (sma200 and price < sma200):
        return "Weak"

    # Lagging: price below SMA10, SMA20, SMA50 but still above SMA200, breadth < 40%
    if (sma10 and price < sma10) and (sma20 and price < sma20) and \
       (sma50 and price < sma50) and (sma200 and price > sma200) and \
       (breadth is None or breadth < 40):
        return "Lagging"

    # Mediocre: SMA10 has crossed below SMA20, but price still above SMA50 and SMA200
    if sma10 and sma20 and sma50 and sma200 and \
       sma10 < sma20 and price > sma50 and price > sma200:
        return "Mediocre"

    return "Neutral"


def fetch_market_indicators(ticker: str, breadth: float | None = None) -> dict:
    """Return price action metrics + Elite Regime status for an index ETF."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="1y", interval="1d")
        if hist.empty or len(hist) < 50:
            return {}
        closes = [float(c) for c in hist["Close"].tolist()]
        price = closes[-1]
        sma10  = sum(closes[-10:]) / 10  if len(closes) >= 10  else None
        sma20  = sum(closes[-20:]) / 20  if len(closes) >= 20  else None
        sma50  = sum(closes[-50:]) / 50  if len(closes) >= 50  else None
        sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
        # 200SMA slope: compare current vs 20 trading days ago
        sma200_20d = sum(closes[-220:-20]) / 200 if len(closes) >= 220 else None
        ema10      = _ema(closes, 10)
        ema20      = _ema(closes, 20)
        ema10_prev = _ema(closes[:-1], 10)
        ema20_prev = _ema(closes[:-1], 20)
        rsi14      = _rsi(closes, 14)
        change_pct = round((closes[-1] / closes[-2] - 1) * 100, 2) if len(closes) >= 2 else None
        sma50_pct  = round((price / sma50  - 1) * 100, 2) if sma50  else None
        sma200_pct = round((price / sma200 - 1) * 100, 2) if sma200 else None
        slope_up   = bool(sma200 > sma200_20d) if sma200 and sma200_20d else None

        index_status = _elite_status(price, sma10, sma20, sma50, sma200, rsi14, breadth)

        return {
            "price":             round(price, 2),
            "change_pct":        change_pct,
            "index_status":      index_status,
            "breadth":           breadth,
            "rsi14":             rsi14,
            "sma50_pct":         sma50_pct,
            "sma200_pct":        sma200_pct,
            "sma200_slope_up":   slope_up,
            "ema10_above_ema20": bool(ema10 > ema20) if ema10 and ema20 else None,
            "ema10_ema20_both_down": bool(ema10 < ema10_prev and ema20 < ema20_prev)
                                     if all([ema10, ema10_prev, ema20, ema20_prev]) else None,
        }
    except Exception as e:
        logger.warning(f"  Market indicators for {ticker} failed: {e}")
        return {}


def _market_signal(spy: dict, qqq: dict) -> str:
    def is_red(d):
        broke_200 = (d.get("sma200_pct") or 0) < 0
        ema_cross_down = (d.get("ema10_above_ema20") is False
                          and d.get("ema10_ema20_both_down") is True)
        return broke_200 or ema_cross_down

    if is_red(spy) or is_red(qqq):
        return "red"

    spy_above_50 = (spy.get("sma50_pct") or 0) > 0
    qqq_above_50 = (qqq.get("sma50_pct") or 0) > 0
    spy_200_up   = spy.get("sma200_slope_up") is not False
    qqq_200_up   = qqq.get("sma200_slope_up") is not False

    if spy_above_50 and qqq_above_50 and spy_200_up and qqq_200_up:
        return "green"
    return "yellow"


# ──────────────────────────────────────────────────────────────
# RS Universe (S&P 500)
# ──────────────────────────────────────────────────────────────

def _build_sp500_rs_universe() -> tuple[dict[str, float], float | None]:
    """Download S&P 500 6-month data. Returns (rs_dict, breadth_pct)."""
    try:
        import pandas as pd
        import yfinance as yf
        from io import StringIO
        resp = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers=HEADERS, timeout=15
        )
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text))
        tickers = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
        logger.info(f"  Downloading {len(tickers)} S&P 500 stocks...")
        data = yf.download(tickers, period="6mo", interval="1d", auto_adjust=True, progress=False)
        closes = data["Close"]
        valid = closes.dropna(thresh=int(len(closes) * 0.5), axis=1)
        perf = ((valid.iloc[-1] - valid.iloc[0]) / valid.iloc[0] * 100).dropna()

        # Compute breadth: % of S&P 500 stocks above their 50-day SMA
        above_50 = 0
        total = 0
        for col in valid.columns:
            col_data = valid[col].dropna()
            if len(col_data) >= 50:
                total += 1
                if float(col_data.iloc[-1]) > float(col_data.iloc[-50:].mean()):
                    above_50 += 1
        breadth = round(above_50 / total * 100, 1) if total > 0 else None
        logger.info(f"  S&P 500 breadth (% above SMA50): {breadth}%")
        return perf.to_dict(), breadth
    except Exception as e:
        logger.warning(f"  S&P 500 RS universe failed: {e}")
        return {}, None


def _fetch_nasdaq100_breadth() -> float | None:
    """Compute % of Nasdaq 100 stocks above their 50-day SMA."""
    try:
        import pandas as pd
        import yfinance as yf
        from io import StringIO
        resp = requests.get(
            "https://en.wikipedia.org/wiki/Nasdaq-100",
            headers=HEADERS, timeout=15
        )
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text))
        # Find the table with a 'Ticker' or 'Symbol' column
        tickers = None
        for t in tables:
            cols = [str(c).lower() for c in t.columns]
            if "ticker" in cols:
                tickers = t[t.columns[[i for i, c in enumerate(cols) if c == "ticker"][0]]].tolist()
                break
            if "symbol" in cols:
                tickers = t[t.columns[[i for i, c in enumerate(cols) if c == "symbol"][0]]].tolist()
                break
        if not tickers:
            return None
        tickers = [str(t).replace(".", "-") for t in tickers if str(t) not in ("nan", "")]
        logger.info(f"  Downloading {len(tickers)} Nasdaq-100 stocks for breadth...")
        data = yf.download(tickers, period="3mo", interval="1d", auto_adjust=True, progress=False)
        closes = data["Close"]
        valid = closes.dropna(thresh=int(len(closes) * 0.5), axis=1)
        above_50 = 0
        total = 0
        for col in valid.columns:
            col_data = valid[col].dropna()
            if len(col_data) >= 50:
                total += 1
                if float(col_data.iloc[-1]) > float(col_data.iloc[-50:].mean()):
                    above_50 += 1
        breadth = round(above_50 / total * 100, 1) if total > 0 else None
        logger.info(f"  Nasdaq-100 breadth (% above SMA50): {breadth}%")
        return breadth
    except Exception as e:
        logger.warning(f"  Nasdaq-100 breadth failed: {e}")
        return None


# ──────────────────────────────────────────────────────────────
# Scoring
# ──────────────────────────────────────────────────────────────

def _avg(stocks, key):
    vals = [s.get(key) for s in stocks if s.get(key) is not None]
    return sum(vals) / len(vals) if vals else 0


def _composite(stocks):
    return (_avg(stocks, "perf_1w") * 0.20 + _avg(stocks, "perf_1m") * 0.30
            + _avg(stocks, "perf_3m") * 0.30 + _avg(stocks, "perf_6m") * 0.20)


def _stock_score(s):
    v = 0
    for k, w in [("perf_1w", 0.2), ("perf_1m", 0.3), ("perf_3m", 0.3), ("perf_6m", 0.2)]:
        if s.get(k) is not None:
            v += s[k] * w
    return v


def _composite_ind(ind: dict) -> float:
    """Composite score for a single industry dict (from groups page)."""
    return ((ind.get("perf_1w") or 0) * 0.20 + (ind.get("perf_1m") or 0) * 0.30
            + (ind.get("perf_3m") or 0) * 0.30 + (ind.get("perf_6m") or 0) * 0.20)


# ──────────────────────────────────────────────────────────────
# Build pipeline
# ──────────────────────────────────────────────────────────────

def build_data() -> dict:
    logger.info("=" * 60)
    logger.info("Thematic Scanner v5 — Industry → Parent Theme → Stocks")
    if not is_trading_day():
        logger.info(f"⚠ Non-trading day — using last session ({last_trading_date().isoformat()})")
    logger.info("=" * 60)

    # ── Step 0: Fetch Finviz themes map (40 parent themes, 5 timeframes) ──
    logger.info("Step 0: Fetching Finviz themes map performance...")
    finviz_theme_rankings = fetch_themes_map_performance()
    for t in finviz_theme_rankings[:10]:
        badge = " ★ Stage2" if t["stage2_momentum"] else ""
        logger.info(f"  {t['name']:35} RS={t['rs_score']:+7.2f}  1D={t['perf_1d']:+.2f}%  1W={t['perf_1w']:+.2f}%  1M={t['perf_1m']:+.2f}%  3M={t['perf_3m']:+.2f}%  6M={t['perf_6m']:+.2f}%{badge}")

    # ── Step 1: Fetch all industry performance (single request) ──
    logger.info("Step 1: Fetching industry performance from Finviz groups...")
    industries = fetch_industry_performance()
    if not industries:
        logger.error("  Failed to fetch industry performance!")
        industries = []
    _sleep()

    # ── Step 1b: Discover industry filter codes for stock fetching ──
    logger.info("Step 1b: Discovering industry filter codes...")
    ind_codes = discover_industry_codes()
    _sleep()

    # ── Step 2: Aggregate into parent themes ──
    logger.info("Step 2: Aggregating into parent themes...")
    theme_rankings = aggregate_theme_performance(industries)
    for t in theme_rankings:
        badge = " ★ Stage2" if t["stage2_momentum"] else ""
        logger.info(f"  {t['name']:30} RS={t['rs_score']:+7.2f}  1D={t['perf_1d']:+.1f}%  1W={t['perf_1w']:+.1f}%  1M={t['perf_1m']:+.1f}%  3M={t['perf_3m']:+.1f}%  6M={t['perf_6m']:+.1f}%{badge}")

    # ── Step 3: For top themes, drill into industries → stocks ──
    top_themes = theme_rankings[:TOP_THEMES]
    logger.info(f"\nStep 3: Drilling into top {len(top_themes)} themes for stock details...")
    all_detail_cache: dict[str, dict | None] = {}
    output_themes = []

    for theme in top_themes:
        theme_name = theme["name"]
        logger.info(f"\n{'─'*50}")
        logger.info(f"Theme: {theme_name} ({theme['n_industries']} industries)")

        # Sort industries within this theme by their composite perf
        theme_industries = [i for i in industries if i["parent_theme"] == theme_name]
        theme_industries.sort(key=lambda i: _composite_ind(i), reverse=True)
        top_industries = theme_industries[:TOP_SUBTHEMES_PER_THEME]

        theme_subthemes = []
        for ind in top_industries:
            ind_name = ind["name"]
            ind_code = ind_codes.get(ind_name)
            if not ind_code:
                logger.warning(f"  No filter code for industry: {ind_name}")
                continue

            logger.info(f"  Industry: {ind_name} (code={ind_code})")
            stocks = fetch_screener_stocks("ind", ind_code)
            if not stocks:
                continue
            stocks.sort(key=_stock_score, reverse=True)
            picks = stocks[:TOP_STOCKS_PER_SUBTHEME]
            sub_stocks = _fetch_details(picks, all_detail_cache)
            if sub_stocks:
                theme_subthemes.append({"name": ind_name, "stocks": sub_stocks})

        if theme_subthemes:
            output_themes.append({"name": theme_name, "subthemes": theme_subthemes})

    # ── Step 4: Mark pure_play (appears in only one subtheme across all themes) ──
    ticker_count: dict[str, int] = {}
    for th in output_themes:
        for sub in th.get("subthemes", []):
            for stock in sub["stocks"]:
                ticker_count[stock["ticker"]] = ticker_count.get(stock["ticker"], 0) + 1
    for th in output_themes:
        for sub in th.get("subthemes", []):
            for stock in sub["stocks"]:
                stock["pure_play"] = ticker_count.get(stock["ticker"], 1) == 1

    # ── Step 5: Compute RS vs S&P 500 universe + breadth ──
    logger.info("\nStep 5: Building RS universe from S&P 500...")
    rs_universe, sp500_breadth = _build_sp500_rs_universe()
    logger.info(f"  RS universe: {len(rs_universe)} stocks | S&P 500 breadth: {sp500_breadth}%")

    all_stocks_flat = []
    for th in output_themes:
        for sub in th.get("subthemes", []):
            all_stocks_flat.extend(sub["stocks"])

    if rs_universe and all_stocks_flat:
        sorted_perfs = sorted(rs_universe.values())
        n = len(sorted_perfs)
        for stock in all_stocks_flat:
            perf = stock.get("perf_6m") or stock.get("perf_3m") or 0
            rank = sum(1 for v in sorted_perfs if v <= perf)
            stock["rs_52w"] = max(1, min(99, int((rank / max(n, 1)) * 98) + 1))
    elif all_stocks_flat:
        # Fallback: internal ranking if S&P 500 fetch failed
        perfs = [(i, s.get("perf_6m") or s.get("perf_3m") or 0) for i, s in enumerate(all_stocks_flat)]
        perfs.sort(key=lambda x: x[1])
        n = len(perfs)
        for rank, (idx, _) in enumerate(perfs):
            all_stocks_flat[idx]["rs_52w"] = max(1, min(99, int((rank / max(n - 1, 1)) * 98) + 1))

    for th in output_themes:
        for sub in th.get("subthemes", []):
            sub["stocks"].sort(key=lambda s: s.get("rs_52w", 0), reverse=True)

    # ── Step 6: Fetch SPY benchmark + market condition ──
    logger.info("\nStep 6: Fetching SPY & QQQ market condition...")
    _sleep()
    spy_detail = fetch_stock_detail("SPY")
    spy_benchmarks = {}
    if spy_detail:
        for k in ["perf_1w", "perf_1m", "perf_3m", "perf_6m"]:
            spy_benchmarks[k] = spy_detail.get(k)

    # Fetch Nasdaq-100 breadth for QQQ (S&P 500 reused as proxy for SPY and IWM)
    qqq_breadth = _fetch_nasdaq100_breadth()
    _sleep()

    spy_ind = fetch_market_indicators("SPY", breadth=sp500_breadth)
    _sleep()
    qqq_ind = fetch_market_indicators("QQQ", breadth=qqq_breadth)
    _sleep()
    iwm_ind = fetch_market_indicators("IWM", breadth=sp500_breadth)  # S&P 500 as proxy
    signal = _market_signal(spy_ind, qqq_ind) if spy_ind and qqq_ind else "yellow"
    market_condition = {"signal": signal, "spy": spy_ind, "qqq": qqq_ind, "iwm": iwm_ind}
    logger.info(
        f"  Market signal: {signal} | "
        f"SPY sma50={spy_ind.get('sma50_pct')} status={spy_ind.get('index_status')} breadth={sp500_breadth}% | "
        f"QQQ sma50={qqq_ind.get('sma50_pct')} status={qqq_ind.get('index_status')} breadth={qqq_breadth}%"
    )

    # Fetch VIX from TradingView scanner API
    vix_value = None
    try:
        vix_resp = requests.post(
            "https://scanner.tradingview.com/global/scan",
            json={"symbols": {"tickers": ["CBOE:VIX"]}, "columns": ["close"]},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        vix_resp.raise_for_status()
        rows = vix_resp.json().get("data", [])
        if rows and rows[0].get("d"):
            vix_value = round(float(rows[0]["d"][0]), 2)
            logger.info(f"  VIX (TradingView): {vix_value}")
    except Exception as e:
        logger.warning(f"  VIX TradingView fetch failed: {e}")

    updated = date.today() if is_trading_day() else last_trading_date()

    # Build industry_rankings (raw per-industry data with parent_theme tag)
    industry_rankings = [
        {k: v for k, v in ind.items() if k != "perf_1y"}
        for ind in industries
        if ind.get("parent_theme") != "Other"
    ]

    return {
        "last_updated": updated.isoformat(),
        "themes": output_themes,
        "theme_rankings": theme_rankings,
        "industry_rankings": industry_rankings,
        "finviz_theme_rankings": finviz_theme_rankings,
        "spy_benchmarks": spy_benchmarks,
        "market_condition": market_condition,
        "vix": vix_value,
    }


def fetch_earnings_yf(ticker: str) -> str | None:
    """Return next earnings date as 'MMM DD' string using yfinance, or None."""
    try:
        import yfinance as yf
        from datetime import date as _date
        cal = yf.Ticker(ticker).calendar
        if not cal:
            return None
        dates = cal.get("Earnings Date")
        if not dates:
            return None
        # calendar returns a list of timestamps
        if not isinstance(dates, list):
            dates = [dates]
        today = _date.today()
        upcoming = []
        for d in dates:
            # yfinance returns datetime.date or Timestamp
            d_date = d.date() if hasattr(d, "date") and callable(d.date) else d
            if d_date >= today:
                upcoming.append(d_date)
        if not upcoming:
            return None
        return upcoming[0].strftime("%b %-d")
    except Exception:
        return None


def _fetch_details(picks: list[dict], cache: dict) -> list[dict]:
    result = []
    for s in picks:
        t = s["ticker"]
        if t not in cache:
            logger.info(f"      {t}...")
            detail = fetch_stock_detail(t)
            if detail:
                price_data = fetch_sparkline(t)
                detail["sparkline"] = price_data.get("sparkline", [])
                detail["bars_30d"] = price_data.get("bars_30d", [])
                detail["earnings"] = fetch_earnings_yf(t)
            cache[t] = detail
            _sleep()
        d = cache.get(t)
        if d is None:
            continue
        stock = d.copy()
        stock["pure_play"] = False
        stock.pop("perf_ytd", None)
        result.append(stock)
    return result


def main():
    output = build_data()
    out_path = Path("public/thematic_data.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    total = sum(len(sub["stocks"]) for th in output["themes"] for sub in th.get("subthemes", []))
    subs = sum(len(th.get("subthemes", [])) for th in output["themes"])
    logger.info("=" * 60)
    logger.info(f"Done! {len(output['themes'])} themes · {subs} sub-themes · {total} stocks")
    logger.info(f"Output → {out_path}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
