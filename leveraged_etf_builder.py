from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
leveraged_etf_builder.py — liquid leveraged / inverse ETFs and the stock each one tracks
========================================================================================
Feeds the Watchlist → 🌐 Universe mode: a stock joins the Universe when a leveraged or inverse ETF on it clears the
liquidity floors and a lower ADR bar (default 3%), and the ETF's name is shown beside the stock.

Source: TradingView screener (`leveraged_flag` in Leveraged / Inverse). Every fund above a light floor is written
(avg $ volume ≥ $50M, avg volume ≥ 300K shares) so the dashboard can tune the thresholds without a rebuild; the
page applies its own floors. `underlying` is the tracked *stock* ticker when it can be resolved (SOXL, TQQQ, AGQ... track
indexes / commodities, so theirs is null) and only resolves to stocks that are in public/screener_stocks.json, the list the
Universe is drawn from.

Output: public/leveraged_stock_etfs.json
"""
import json
import logging
import math
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
OUTPUT_PATH = ROOT / "public" / "leveraged_stock_etfs.json"
SCREENER_PATH = ROOT / "public" / "screener_stocks.json"
ET = ZoneInfo("America/New_York")

MIN_DOLLAR_VOLUME = 50_000_000     # light write floor; the page applies its own (default $500M)
MIN_AVG_VOLUME = 300_000

# Uppercase words in fund names that are never the underlying ticker.
_TOKEN_STOP = {"FANG", "ETF", "ETN", "USD", "US", "USA", "AI", "ULTRA", "BULL", "BEAR", "LONG", "SHORT", "DAILY", "INDEX"}
# Funds that name the stock by company ("2X Long NVIDIA Daily ETF") instead of ticker; a few legal names differ.
_NAME_ALIASES = {"spacex": "SPCX", "google": "GOOGL", "alphabet": "GOOGL", "facebook": "META", "bitcoin": None, "ether": None}
_NAME_STOP = {"daily", "target", "etf", "index", "the", "bull", "bear", "short", "inverse", "leveraged", "long", "2x", "3x", "1x", "2", "3"}


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _load_stock_universe() -> tuple[set[str], dict[str, str]]:
    """(tickers, first-word-of-company-name -> ticker) from the Stock Screener list."""
    try:
        d = json.loads(SCREENER_PATH.read_text(encoding="utf-8"))
        stocks = d["stocks"] if isinstance(d, dict) else d
    except Exception as exc:  # noqa: BLE001
        logger.warning("screener_stocks.json unavailable (%s) - no underlyings can be resolved", exc)
        return set(), {}
    tickers = {s["ticker"] for s in stocks if s.get("ticker")}
    names: dict[str, str] = {}
    for st in sorted(stocks, key=lambda x: -(x.get("market_cap_b") or 0)):          # biggest company wins a shared name
        words = re.findall(r"[A-Za-z0-9]+", st.get("company") or "")
        if words and st.get("ticker"):
            names.setdefault(words[0].lower(), st["ticker"])
    return tickers, names


def resolve_underlying(desc: str, tickers: set[str], names: dict[str, str]) -> str | None:
    """Tracked stock ticker from a fund name like 'Direxion Daily MU Bull 2X ETF' / 'Tradr 2X Long SNDK Daily ETF' /
    'T-Rex 2X Inverse NVIDIA Daily Target ETF'; None for index / commodity / crypto funds."""
    desc = desc or ""
    for tok in re.findall(r"\b[A-Z]{2,5}\b", desc):                 # 2+ letters: "S&P" -> "P" and "T-Rex" -> "T" are not tickers
        if tok not in _TOKEN_STOP and tok in tickers:
            return tok
    m = re.search(r"(?:Long|Bull|Short|Inverse|Bear)\s+([A-Za-z][A-Za-z0-9]*)", desc)
    if m:
        key = m.group(1).lower()
        if key in _NAME_STOP or len(key) < 3:      # "S&P 500" -> "S", "T-Rex" -> "T" are not company names
            return None
        hit = _NAME_ALIASES[key] if key in _NAME_ALIASES else names.get(key)
        if hit in tickers:
            return hit
    return None


def build() -> list[dict]:
    try:
        from tradingview_screener import Query, col
    except ImportError:
        logger.error("tradingview_screener not installed")
        return []
    q = (Query().set_markets("america")
         .select("name", "description", "close", "ATR", "average_volume_10d_calc", "leverage_ratio", "leveraged_flag")
         .where(col("leveraged_flag").isin(["Leveraged", "Inverse"]), col("average_volume_10d_calc") > MIN_AVG_VOLUME)
         .order_by("average_volume_10d_calc", ascending=False).limit(2000))
    # tradingview_screener >= 3.2 adds a default `filter2` that excludes ETFs; CI installs the latest version.
    q.query.pop("filter2", None)
    try:
        _, df = q.get_scanner_data()
    except Exception as exc:  # noqa: BLE001
        logger.error("TradingView query failed: %s", exc)
        return []
    logger.info("TradingView: %d leveraged/inverse funds above %dK shares", len(df), MIN_AVG_VOLUME // 1000)

    tickers, names = _load_stock_universe()
    out = []
    for _, r in df.iterrows():
        close, atr, vol = _num(r.get("close")), _num(r.get("ATR")), _num(r.get("average_volume_10d_calc"))
        if not close or not atr or not vol or close <= 0:
            continue
        dvol = close * vol
        if dvol < MIN_DOLLAR_VOLUME:
            continue
        ratio = re.sub(r"[^0-9.]", "", str(r.get("leverage_ratio") or "")) or "1"
        out.append({
            "ticker": str(r["name"]),
            "description": str(r.get("description") or ""),
            "direction": "inverse" if r.get("leveraged_flag") == "Inverse" else "bull",
            "ratio": float(ratio),
            "adr_pct": round(atr / close * 100, 2),
            "avg_dollar_volume": round(dvol),
            "avg_volume": int(vol),
            "underlying": resolve_underlying(str(r.get("description") or ""), tickers, names),
        })
    out.sort(key=lambda e: -e["avg_dollar_volume"])
    return out


def main() -> None:
    etfs = build()
    if not etfs:
        logger.error("No funds - leaving the existing file untouched")
        return
    payload = {"generated_at": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"), "etfs": etfs}
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    single = [e for e in etfs if e["underlying"]]
    logger.info("Written %s: %d funds, %d on a single stock", OUTPUT_PATH, len(etfs), len(single))
    for e in single[:15]:
        print(f"  {e['ticker']:<6} {e['direction']:<7} {e['ratio']:.0f}x  ADR {e['adr_pct']:>5.1f}%  ${e['avg_dollar_volume']/1e6:>6.0f}M  -> {e['underlying']}")


if __name__ == "__main__":
    main()
