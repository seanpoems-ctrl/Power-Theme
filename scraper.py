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
# Discover Themes & Sub-themes
# ──────────────────────────────────────────────────────────────

def discover_themes() -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    url = "https://finviz.com/screener.ashx?v=141"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    themes = []
    sel = soup.find("select", {"data-filter": "theme"})
    if sel:
        for opt in sel.find_all("option"):
            v = opt.get("value", "")
            if v:
                themes.append((v, opt.get_text(strip=True)))

    subthemes = []
    sel2 = soup.find("select", {"data-filter": "subtheme"})
    if sel2:
        for opt in sel2.find_all("option"):
            v = opt.get("value", "")
            label = opt.get_text(strip=True)
            if v and " - " in label:
                parent = label.split(" - ", 1)[0].strip()
                subthemes.append((v, label, parent))

    return themes, subthemes


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
            if len(cells) < 17:
                continue
            stocks.append({
                "ticker": cells[1],
                "perf_1w": parse_pct(cells[2]),
                "perf_1m": parse_pct(cells[3]),
                "perf_3m": parse_pct(cells[4]),
                "perf_6m": parse_pct(cells[5]),
                "perf_ytd": parse_pct(cells[6]),
                "avg_volume": parse_vol(cells[12]),
                "price": float(cells[14].replace(",", "") or "0"),
                "change_pct": parse_pct(cells[15]) or 0,
                "volume": parse_vol(cells[16]),
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


# ──────────────────────────────────────────────────────────────
# Build pipeline
# ──────────────────────────────────────────────────────────────

_SUB_PARENT_TO_THEME = {
    "AI": "Artificial Intelligence",
    "Agriculture": "Agriculture & FoodTech",
    "Automation": "Industrial Automation",
    "Autonomous": "Autonomous Systems",
    "Blockchain": "Crypto & Blockchain",
    "Cloud": "Cloud Computing",
    "Comm Agri": "Commodities - Agriculture",
    "Comm Energy": "Commodities - Energy",
    "Comm Metals": "Commodities - Metals",
    "Consumer": "Consumer Goods",
    "Defense": "Defense & Aerospace",
    "EVs": "Electric Vehicles",
    "Education": "Education Technology",
    "Energy Base": "Energy - Traditional",
    "Energy Clean": "Energy - Renewable",
    "Entertainment": "Digital Entertainment",
    "Environmental": "Environmental Sustainability",
    "Healthcare": "Healthcare & Biotech",
    "IoT": "Internet of Things",
    "Longevity": "Aging Population & Longevity",
    "NanoTech": "Nanotechnology",
    "Nutrition": "Healthy Food & Nutrition",
    "Quantum": "Quantum Computing",
    "Real Estate": "Real Estate & REITs",
    "Semis": "Semiconductors",
    "Social": "Social Media",
    "Space": "Space Tech",
    "Telecom": "Telecommunications",
    "Transportation": "Transportation & Logistics",
    "V/A Reality": "Virtual & Augmented Reality",
}


def _sub_parent_to_theme_label(parent: str) -> str:
    return _SUB_PARENT_TO_THEME.get(parent.strip(), parent.strip())


def build_data() -> dict:
    logger.info("=" * 60)
    logger.info("Thematic Scanner v4 — Theme → Sub-theme → Stocks")
    if not is_trading_day():
        logger.info(f"⚠ Non-trading day — using last session ({last_trading_date().isoformat()})")
    logger.info("=" * 60)

    # ── Step 1: Discover ──
    logger.info("Step 1: Discovering themes & sub-themes...")
    themes_list, subthemes_list = discover_themes()
    logger.info(f"  {len(themes_list)} themes, {len(subthemes_list)} sub-themes")
    _sleep()

    # ── Step 2: Score all themes ──
    logger.info("Step 2: Scoring all themes...")
    theme_scores: dict[str, dict] = {}
    for i, (code, label) in enumerate(themes_list, 1):
        logger.info(f"  [{i}/{len(themes_list)}] {label}...")
        stocks = fetch_screener_stocks("theme", code)
        if stocks:
            score = _composite(stocks)
            theme_scores[label] = {"code": code, "label": label, "score": score, "n": len(stocks)}
            logger.info(f"    → {len(stocks)} stocks, score={score:+.2f}")
        _sleep()

    ranked_themes = sorted(theme_scores.values(), key=lambda t: t["score"], reverse=True)
    top_themes = ranked_themes[:TOP_THEMES]
    logger.info(f"\nTop {len(top_themes)} themes:")
    for t in top_themes:
        logger.info(f"  {t['label']:35} score={t['score']:+.2f}")

    # ── Step 3: For each top theme, find & score its sub-themes ──
    logger.info(f"\nStep 3: Scanning sub-themes for top themes...")
    all_detail_cache: dict[str, dict | None] = {}
    output_themes = []

    for theme_info in top_themes:
        theme_label = theme_info["label"]
        logger.info(f"\n{'─'*50}")
        logger.info(f"Theme: {theme_label}")

        matching_subs = [
            (code, label) for code, label, parent in subthemes_list
            if _sub_parent_to_theme_label(parent) == theme_label
        ]

        if not matching_subs:
            logger.info(f"  No sub-themes found, using theme-level stocks")
            stocks = fetch_screener_stocks("theme", theme_info["code"])
            if not stocks:
                continue
            stocks.sort(key=_stock_score, reverse=True)
            picks = stocks[:TOP_STOCKS_PER_SUBTHEME]
            sub_stocks = _fetch_details(picks, all_detail_cache)
            if sub_stocks:
                output_themes.append({
                    "name": theme_label,
                    "subthemes": [{"name": theme_label, "stocks": sub_stocks}]
                })
            continue

        logger.info(f"  {len(matching_subs)} sub-themes found, scoring...")
        sub_scored = []
        for sub_code, sub_label in matching_subs:
            stocks = fetch_screener_stocks("subtheme", sub_code)
            if stocks:
                score = _composite(stocks)
                sub_scored.append({"code": sub_code, "label": sub_label, "stocks": stocks, "score": score})
                logger.info(f"    {sub_label:50} {len(stocks):>3} stocks  score={score:+.2f}")
            _sleep()

        sub_scored.sort(key=lambda x: x["score"], reverse=True)
        top_subs = sub_scored[:TOP_SUBTHEMES_PER_THEME]

        theme_subthemes = []
        for sub in top_subs:
            sub["stocks"].sort(key=_stock_score, reverse=True)
            picks = sub["stocks"][:TOP_STOCKS_PER_SUBTHEME]
            sub_stocks = _fetch_details(picks, all_detail_cache)
            if sub_stocks:
                short_name = sub["label"].split(" - ", 1)[1] if " - " in sub["label"] else sub["label"]
                theme_subthemes.append({"name": short_name, "stocks": sub_stocks})

        if theme_subthemes:
            output_themes.append({"name": theme_label, "subthemes": theme_subthemes})

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
    return {
        "last_updated": updated.isoformat(),
        "themes": output_themes,
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
