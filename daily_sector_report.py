#!/usr/bin/env python3
"""
Daily Sector Report — Post-Market Buy Signal Detection

Runs after market close (4:30 PM ET) on trading days.
Identifies top 5 performing sectors via Gemini, checks if Semiconductors/Tech are included,
verifies TSM & NVDA daily gains > 2%.

Output: data/daily_sector_report/YYYY-MM-DD.json
Optional: Telegram notification on buy signal
"""

import json
import os
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

ET = ZoneInfo("America/New_York")

# ── Cost Optimization ───────────────────────────────────────────────────────
USE_CHEAPER_MODEL = True  # Use 1.5 Flash instead of 1.5 Pro (much cheaper)
ENABLE_CACHING = True  # Cache results to avoid duplicate analysis on retries


def get_sector_performance() -> str:
    """
    Get today's sector performance summary (simple market data, no API cost).
    Returns a brief text summary for Gemini analysis.
    """
    try:
        # Fetch major sector ETFs (free via yfinance)
        sectors = {
            "Technology": "XLK",
            "Semiconductors": "SMH",
            "Healthcare": "XLV",
            "Financials": "XLF",
            "Energy": "XLE",
            "Industrials": "XLI",
            "Consumer Discretionary": "XLY",
            "Materials": "XLB",
            "Real Estate": "XLRE",
            "Utilities": "XLU",
            "Consumer Staples": "XLP",
        }

        today = datetime.now(ET).date().isoformat()
        perf = {}

        for name, ticker in sectors.items():
            try:
                data = yf.Ticker(ticker)
                hist = data.history(period="2d")
                if len(hist) >= 2:
                    close_today = hist["Close"].iloc[-1]
                    close_yest = hist["Close"].iloc[-2]
                    pct = ((close_today - close_yest) / close_yest) * 100
                    perf[name] = round(pct, 2)
            except Exception as e:
                logger.warning(f"  Failed to fetch {name} ({ticker}): {e}")

        return "\n".join([f"  {name}: {pct:+.2f}%" for name, pct in sorted(perf.items(), key=lambda x: x[1], reverse=True)])

    except Exception as e:
        logger.error(f"Error fetching sector performance: {e}")
        return ""


def analyze_sectors_with_gemini(sector_data: str) -> list[dict]:
    """
    Use Gemini 1.5 Flash to identify top 5 sectors.
    OPTIMIZED: Minimal prompt, focuses on pure ranking.
    """
    if not GEMINI_API_KEY or not sector_data:
        return []

    prompt = f"""Rank today's top 5 performing sectors by return percentage.

Sector performance (% change today):
{sector_data}

Return ONLY a JSON array of top 5. Example:
[
  {{"sector": "Semiconductors", "return": 2.5}},
  {{"sector": "Technology", "return": 1.8}},
  ...
]

No markdown, no explanation. JSON only."""

    try:
        from google import genai

        client = genai.Client(api_key=GEMINI_API_KEY)

        # OPTIMIZATION: Use 1.5 Flash (cheapest model, ~$0.075/1M input)
        model = "gemini-1.5-flash" if USE_CHEAPER_MODEL else "gemini-2.5-flash"

        response = client.models.generate_content(model=model, contents=prompt)
        raw = response.text.strip().replace("```json", "").replace("```", "").strip()

        sectors = json.loads(raw)
        if isinstance(sectors, list):
            return sectors
    except Exception as e:
        logger.error(f"Gemini analysis failed: {e}")

    return []


def check_tsm_nvda_gains() -> dict:
    """
    Check TSM & NVDA daily % gains (free, no API cost).
    Returns dict with ticker, today's gain %, buy signal.
    """
    signals = {}

    for ticker in ["TSM", "NVDA"]:
        try:
            data = yf.Ticker(ticker)
            hist = data.history(period="2d")

            if len(hist) >= 2:
                close_today = hist["Close"].iloc[-1]
                open_today = hist["Open"].iloc[-1]
                pct_gain = ((close_today - open_today) / open_today) * 100

                signals[ticker] = {
                    "price": round(close_today, 2),
                    "gain_pct": round(pct_gain, 2),
                    "signal": pct_gain > 2.0,
                }
            else:
                signals[ticker] = {"price": None, "gain_pct": None, "signal": False}

        except Exception as e:
            logger.warning(f"  Failed to fetch {ticker}: {e}")
            signals[ticker] = {"price": None, "gain_pct": None, "signal": False}

    return signals


def load_cache(today: str) -> dict | None:
    """Load cached report for today if it exists."""
    if not ENABLE_CACHING:
        return None

    cache_dir = Path(f"data/daily_sector_report")
    cache_file = cache_dir / f"{today}.json"

    if cache_file.exists():
        try:
            with open(cache_file) as f:
                data = json.load(f)
                if data.get("timestamp", "").startswith(today):
                    logger.info(f"  Using cached report from {cache_file}")
                    return data
        except Exception:
            pass

    return None


def save_report(report: dict, today: str) -> None:
    """Save report to data/daily_sector_report/YYYY-MM-DD.json"""
    out_dir = Path("data/daily_sector_report")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / f"{today}.json"
    with open(out_file, "w") as f:
        json.dump(report, f, indent=2)

    logger.info(f"  Saved → {out_file}")


def send_telegram_signal(report: dict) -> None:
    """Send Telegram alert if buy signal detected."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        logger.warning("  Telegram not configured — skipping notification")
        return

    tsm_signal = report.get("tsm", {}).get("signal", False)
    nvda_signal = report.get("nvda", {}).get("signal", False)
    buy_signal = tsm_signal and nvda_signal

    if not buy_signal:
        logger.info(f"  No buy signal (TSM: {tsm_signal}, NVDA: {nvda_signal})")
        return

    top_sectors = ", ".join([s.get("sector", "?") for s in report.get("top_sectors", [])[:3]])
    tsm_gain = report.get("tsm", {}).get("gain_pct", "?")
    nvda_gain = report.get("nvda", {}).get("gain_pct", "?")

    text = f"""🎯 *BUY SIGNAL DETECTED* 🎯

Top sectors: {top_sectors}

TSM: *{tsm_gain}%* ✓
NVDA: *{nvda_gain}%* ✓

符合買入訊號 ✅"""

    try:
        import requests

        for chat_id in TELEGRAM_CHAT.split(","):
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id.strip(), "text": text, "parse_mode": "MarkdownV2"},
                timeout=10,
            )
        logger.info(f"  Telegram: sent buy signal alert")
    except Exception as e:
        logger.warning(f"  Telegram send failed: {e}")


def main():
    now_et = datetime.now(ET)
    today = now_et.date().isoformat()

    logger.info(f"Daily Sector Report — {now_et.strftime('%Y-%m-%d %H:%M ET')}")

    # Check cache first
    if cached := load_cache(today):
        logger.info("Using cached report")
        send_telegram_signal(cached)
        return

    logger.info("Fetching sector performance…")
    sector_data = get_sector_performance()

    logger.info("Analyzing with Gemini 1.5 Flash (cost: ~$0.001 per run)…")
    top_sectors = analyze_sectors_with_gemini(sector_data)

    logger.info("Checking TSM & NVDA daily gains…")
    stock_signals = check_tsm_nvda_gains()

    # Detect semiconductor/tech in top 5
    semi_tech_detected = any(
        keyword in s.get("sector", "").lower() for keyword in ["semiconductor", "technology", "semi", "chip"]
        for s in top_sectors
    )

    buy_signal = (
        semi_tech_detected
        and stock_signals.get("TSM", {}).get("signal", False)
        and stock_signals.get("NVDA", {}).get("signal", False)
    )

    report = {
        "date": today,
        "timestamp": now_et.isoformat(),
        "top_sectors": top_sectors,
        "semi_tech_detected": semi_tech_detected,
        "tsm": stock_signals.get("TSM", {}),
        "nvda": stock_signals.get("NVDA", {}),
        "buy_signal": buy_signal,
        "cost_analysis": {"model": "gemini-1.5-flash", "estimated_monthly_cost_usd": 0.02},
    }

    # Save report
    save_report(report, today)

    # Print summary
    print(f"\n{'='*70}")
    print(f"DAILY SECTOR REPORT SUMMARY")
    print(f"{'='*70}")
    print(f"Top sectors: {', '.join([s.get('sector', '?') for s in top_sectors[:3]])}")
    print(f"Semiconductor/Tech detected: {'Yes ✓' if semi_tech_detected else 'No'}")
    print(f"TSM gain: {stock_signals.get('TSM', {}).get('gain_pct', '?')}%")
    print(f"NVDA gain: {stock_signals.get('NVDA', {}).get('gain_pct', '?')}%")
    print(f"Buy signal: {'✅ YES — 符合買入訊號' if buy_signal else '⏸ Watching (觀望中)'}")
    print(f"{'='*70}\n")

    # Send Telegram if signal
    if buy_signal:
        send_telegram_signal(report)


if __name__ == "__main__":
    main()
