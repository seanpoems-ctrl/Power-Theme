"""
scraper.py — 美股強勢主題篩選器數據爬蟲 v2
從 Finviz 抓取各主題股票資料 + 多時間段 Performance
產出 public/thematic_data.json
"""

import json
import time
import random
import logging
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─── 主題定義 ───
THEMES = {
    "AI / Machine Learning": {
        "tickers": ["NVDA", "PLTR", "SMCI", "AI", "SOUN", "BBAI", "UPST", "PATH", "ARM", "AVGO"],
        "pure_plays": ["NVDA", "PLTR", "AI", "SOUN"],
    },
    "Cloud / SaaS": {
        "tickers": ["NOW", "CRM", "DDOG", "NET", "SNOW", "MDB", "CRWD", "ZS", "PANW"],
        "pure_plays": ["NOW", "DDOG", "SNOW", "NET"],
    },
    "Cybersecurity": {
        "tickers": ["CRWD", "PANW", "FTNT", "ZS", "S", "CYBR", "OKTA", "TENB", "QLYS"],
        "pure_plays": ["CRWD", "PANW", "FTNT", "ZS"],
    },
    "Semiconductor": {
        "tickers": ["NVDA", "AVGO", "TSM", "AMD", "AMAT", "KLAC", "LRCX", "MRVL", "ON", "ASML"],
        "pure_plays": ["NVDA", "AVGO", "TSM", "AMD"],
    },
    "Nuclear / Energy": {
        "tickers": ["VST", "CEG", "TLN", "NRG", "SMR", "LEU", "CCJ", "UEC", "DNN", "OKLO"],
        "pure_plays": ["CEG", "TLN", "SMR", "OKLO"],
    },
    "Quantum Computing": {
        "tickers": ["IONQ", "RGTI", "QBTS", "QUBT", "ARQQ"],
        "pure_plays": ["IONQ", "RGTI", "QBTS"],
    },
    "Defense / Aerospace": {
        "tickers": ["LMT", "RTX", "NOC", "GD", "LHX", "PLTR", "KTOS", "RKLB", "LUNR", "RDW"],
        "pure_plays": ["LMT", "NOC", "KTOS", "RKLB"],
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# Finviz snapshot table 中的 performance 欄位對應（欄位名會改版，需與頁面一致）
PERF_MAP = {
    "Perf Day":       "perf_1d",    # 今日 (替代 Change)
    "Perf Week":      "perf_1w",
    "Perf Month":     "perf_1m",
    "Perf Quarter":   "perf_3m",    # 舊名曾為 Perf Quart
    "Perf Half Y":    "perf_6m",    # 舊名曾為 Perf Half
    "Perf YTD":       "perf_ytd",   # 用於計算 RS
}


US_MARKET_HOLIDAYS_2026 = {
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 19),  # MLK Day
    date(2026, 2, 16),  # Presidents' Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),   # Independence Day (observed)
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
}


def is_trading_day(d: date | None = None) -> bool:
    if d is None:
        d = date.today()
    return d.weekday() < 5 and d not in US_MARKET_HOLIDAYS_2026


def last_trading_date(d: date | None = None) -> date:
    if d is None:
        d = date.today()
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def parse_pct(s: str) -> float | None:
    """解析百分比字串 e.g. '3.21%' -> 3.21"""
    if not s or s == "-":
        return None
    try:
        return float(s.replace("%", "").replace(",", "").strip())
    except ValueError:
        return None


def parse_int(s: str) -> int:
    """解析數字字串 e.g. '58,000,000' -> 58000000"""
    if not s or s == "-":
        return 0
    try:
        return int(float(s.replace(",", "").strip()))
    except ValueError:
        return 0


def get_company_name(soup) -> str:
    """從 Finviz 頁面抽取公司名稱"""
    # 嘗試常見選擇器
    for sel in ["a.tab-link", "h1"]:
        tag = soup.select_one(sel)
        if tag and tag.get_text(strip=True):
            return tag.get_text(strip=True)
    title = soup.find("title")
    if title:
        parts = title.get_text().split("|")
        if len(parts) >= 2:
            return parts[1].strip()
    return ""


def fetch_finviz_data(ticker: str) -> dict | None:
    """
    從 Finviz 個股頁面抓取所有需要的數據：
    Price, Volume, ATR, Change%, 以及所有 Performance 欄位
    """
    url = f"https://finviz.com/quote.ashx?t={ticker}&ty=c&p=d&b=1"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"  ✗ Failed to fetch {ticker}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="snapshot-table2")
    if not table:
        logger.warning(f"  ✗ No snapshot table for {ticker}")
        return None

    # 解析所有 key-value pairs
    snap = {}
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        for i in range(0, len(cells) - 1, 2):
            k = cells[i].get_text(strip=True)
            v = cells[i + 1].get_text(strip=True)
            snap[k] = v

    try:
        price = float(snap.get("Price", "0").replace(",", ""))
        if price <= 0:
            return None

        volume = parse_int(snap.get("Volume", "0"))
        # Finviz 欄位為「ATR (14)」，舊版或部分頁面可能仍為「ATR」
        atr_s = (snap.get("ATR (14)") or snap.get("ATR") or "0").strip()
        try:
            atr = float(atr_s.replace(",", "") or "0")
        except ValueError:
            atr = 0.0
        # ADR% ≈ ATR(14) 佔股價比例（與常見「日均波動區間」同量級）
        adr_pct = round((atr / price) * 100, 2) if price > 0 and atr > 0 else 0.0
        dollar_volume = round(price * volume)

        result = {
            "ticker": ticker,
            "company": get_company_name(soup),
            "price": round(price, 2),
            "change_pct": parse_pct(snap.get("Change", "0%")) or 0,
            "volume": volume,
            "dollar_volume": dollar_volume,
            "adr_pct": adr_pct,
        }

        # 抓取所有 Performance 欄位
        for finviz_key, json_key in PERF_MAP.items():
            result[json_key] = parse_pct(snap.get(finviz_key, ""))

        original_change = result["change_pct"]

        if result.get("perf_1d") is not None:
            result["change_pct"] = result["perf_1d"]

        # 非交易日 Perf Day 會是 null 或 0，回退到 Change 欄位（最後交易日漲跌）
        if not is_trading_day():
            if result.get("perf_1d") is None or result["perf_1d"] == 0:
                result["perf_1d"] = original_change
                result["change_pct"] = original_change

        return result

    except (ValueError, TypeError) as e:
        logger.warning(f"  ✗ Parse error for {ticker}: {e}")
        return None


def fetch_sparkline(ticker: str) -> list[float]:
    """
    最近約 10 個交易日的收盤價（給前端畫走勢圖）。
    優先用 Stooq（Yahoo 常 429）；失敗再試 Yahoo。
    """
    sym = f"{ticker.lower()}.us"
    try:
        resp = requests.get(
            f"https://stooq.com/q/d/l/?s={sym}&i=d",
            headers=HEADERS,
            timeout=12,
        )
        resp.raise_for_status()
        lines = [ln.strip() for ln in resp.text.splitlines() if ln.strip()]
        if len(lines) < 2:
            return []
        # CSV: Date,Open,High,Low,Close,Volume
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
        pass
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        resp = requests.get(
            url,
            params={"range": "10d", "interval": "1d"},
            headers=HEADERS,
            timeout=10,
        )
        data = resp.json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        return [round(c, 2) for c in closes if c is not None]
    except Exception:
        return []


def compute_rs(stocks: list[dict]) -> None:
    """
    計算 52 週相對強度 (1-99)
    使用 YTD performance 做百分位排名
    """
    perfs = [(i, s.get("perf_ytd") or 0) for i, s in enumerate(stocks)]
    perfs.sort(key=lambda x: x[1])
    n = len(perfs)
    for rank, (idx, _) in enumerate(perfs):
        stocks[idx]["rs_52w"] = max(1, min(99, int((rank / max(n - 1, 1)) * 98) + 1))


def build_data() -> dict:
    """主流程：抓取所有數據、計算 RS、組裝 JSON"""
    logger.info("=" * 50)
    logger.info("Starting Thematic Scanner Build")
    if not is_trading_day():
        ltd = last_trading_date()
        logger.info(f"⚠ Non-trading day — 1D data will use last session ({ltd.isoformat()})")
    logger.info("=" * 50)

    # 收集所有 unique tickers
    all_tickers = set()
    for cfg in THEMES.values():
        all_tickers.update(cfg["tickers"])
    logger.info(f"Total unique tickers: {len(all_tickers)}")

    # 逐一抓取
    cache: dict[str, dict] = {}
    for i, ticker in enumerate(sorted(all_tickers), 1):
        logger.info(f"  [{i}/{len(all_tickers)}] {ticker}...")
        data = fetch_finviz_data(ticker)
        if data:
            data["sparkline"] = fetch_sparkline(ticker)
            cache[ticker] = data
            logger.info(f"    ✓ ${data['price']}  1D:{data.get('perf_1d')}%  1W:{data.get('perf_1w')}%  1M:{data.get('perf_1m')}%")
        else:
            logger.warning(f"    ✗ Skipped")
        time.sleep(random.uniform(1.2, 2.8))

    # 計算 RS（全局排名）
    all_stocks = list(cache.values())
    compute_rs(all_stocks)
    logger.info(f"Computed RS for {len(all_stocks)} stocks")

    # 組裝主題
    themes = []
    for name, cfg in THEMES.items():
        stocks = []
        for t in cfg["tickers"]:
            if t not in cache:
                continue
            s = cache[t].copy()
            s["pure_play"] = t in cfg["pure_plays"]
            s.pop("perf_ytd", None)  # 移除中間計算欄位
            stocks.append(s)

        stocks.sort(key=lambda x: x["rs_52w"], reverse=True)
        if stocks:
            themes.append({"name": name, "stocks": stocks})

    # 主題按平均 RS 排序
    themes.sort(
        key=lambda t: sum(s["rs_52w"] for s in t["stocks"]) / len(t["stocks"]),
        reverse=True,
    )

    updated = date.today() if is_trading_day() else last_trading_date()

    return {
        "last_updated": updated.isoformat(),
        "themes": themes,
    }


def main():
    output = build_data()

    out_path = Path("public/thematic_data.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    total = sum(len(t["stocks"]) for t in output["themes"])
    logger.info("=" * 50)
    logger.info(f"Done! {len(output['themes'])} themes · {total} entries")
    logger.info(f"Output → {out_path}")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()