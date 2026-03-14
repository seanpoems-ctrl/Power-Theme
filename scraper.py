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

TOP_THEMES = 10
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

        try:
            h52 = float((snap.get("52W High") or "0").replace(",", "").strip())
        except ValueError:
            h52 = 0.0
        try:
            l52 = float((snap.get("52W Low") or "0").replace(",", "").strip())
        except ValueError:
            l52 = 0.0
        avg_vol = parse_vol(snap.get("Avg Volume") or "0")
        dist_52w_high = round((price / h52 - 1) * 100, 2) if h52 > 0 else None
        rvol = round(volume / avg_vol, 2) if avg_vol > 0 else None

        result = {
            "ticker": ticker, "company": company, "price": round(price, 2),
            "change_pct": parse_pct(snap.get("Change", "0%")) or 0,
            "volume": volume, "dollar_volume": round(price * volume), "adr_pct": adr_pct,
            "52w_high": round(h52, 2) if h52 > 0 else None,
            "52w_low": round(l52, 2) if l52 > 0 else None,
            "avg_volume": avg_vol,
            "dist_52w_high": dist_52w_high,
            "rvol": rvol,
        }
        for fk, jk in PERF_MAP.items():
            result[jk] = parse_pct(snap.get(fk, ""))

        original_change = result["change_pct"]
        if result.get("perf_1d") is not None:
            result["change_pct"] = result["perf_1d"]
        if not is_trading_day():
            if result.get("perf_1d") is None or result["perf_1d"] == 0:
                result["perf_1d"] = original_change
                result["change_pct"] = original_change
        return result
    except (ValueError, TypeError):
        return None


def fetch_sparkline(ticker: str) -> list[float]:
    sym = f"{ticker.lower()}.us"
    try:
        resp = requests.get(f"https://stooq.com/q/d/l/?s={sym}&i=d", headers=HEADERS, timeout=12)
        resp.raise_for_status()
        lines = [ln.strip() for ln in resp.text.splitlines() if ln.strip()]
        if len(lines) < 2:
            return []
        out: list[float] = []
        for ln in lines[1:]:
            parts = ln.split(",")
            if len(parts) >= 5:
                try:
                    out.append(round(float(parts[4]), 2))
                except ValueError:
                    continue
        return out[-10:] if len(out) >= 2 else []
    except Exception:
        return []


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

    # ── Step 4: Compute RS across ALL stocks ──
    all_stocks_flat = []
    for th in output_themes:
        for sub in th.get("subthemes", []):
            all_stocks_flat.extend(sub["stocks"])

    if all_stocks_flat:
        perfs = [(i, s.get("perf_6m") or s.get("perf_3m") or 0) for i, s in enumerate(all_stocks_flat)]
        perfs.sort(key=lambda x: x[1])
        n = len(perfs)
        for rank, (idx, _) in enumerate(perfs):
            all_stocks_flat[idx]["rs_52w"] = max(1, min(99, int((rank / max(n - 1, 1)) * 98) + 1))

    for th in output_themes:
        for sub in th.get("subthemes", []):
            sub["stocks"].sort(key=lambda s: s.get("rs_52w", 0), reverse=True)

    updated = date.today() if is_trading_day() else last_trading_date()
    return {"last_updated": updated.isoformat(), "themes": output_themes}


def _fetch_details(picks: list[dict], cache: dict) -> list[dict]:
    result = []
    for s in picks:
        t = s["ticker"]
        if t not in cache:
            logger.info(f"      {t}...")
            detail = fetch_stock_detail(t)
            if detail:
                detail["sparkline"] = fetch_sparkline(t)
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
