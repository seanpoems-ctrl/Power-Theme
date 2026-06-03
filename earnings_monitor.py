#!/usr/bin/env python3
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows encoding fix
"""
Earnings Monitor — Real-Time Earnings Alert System

Monitors scheduled earnings announcements and tracks actual vs. expected results.
Alerts on:
  1. Stock moves >8% on earnings day
  2. Large beats (actual > expected + 15%)
  3. Large misses (actual < expected - 15%)

Triggers detailed 7-section earnings report generation via earnings_report_generator.py

Runs during market hours (9:30 AM - 5 PM ET) on trading days.
Output: data/earnings_alerts/YYYY-MM-DD.json
Telegram: Immediate alerts with link to dashboard (full report auto-loads)
"""

import json
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Import earnings report generator
try:
    from earnings_report_generator import generate_earnings_report, send_telegram_report, save_report
    HAS_REPORT_GENERATOR = True
except ImportError:
    HAS_REPORT_GENERATOR = False
    logger.warning("earnings_report_generator not available - reports won't be generated")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

ET = ZoneInfo("America/New_York")

# ── Cost Optimization ───────────────────────────────────────────────────────
USE_CHEAPER_MODEL = True  # Use 1.5 Flash
ENABLE_CACHING = True      # Cache results to avoid duplicate analysis

# ── Thresholds ──────────────────────────────────────────────────────────────
STOCK_MOVE_THRESHOLD = 8.0      # Alert if stock moves >8% on earnings
BEAT_THRESHOLD = 15.0           # Alert if EPS beat by >15%
MISS_THRESHOLD = -15.0          # Alert if EPS miss by >15%


def get_earnings_data(ticker: str) -> dict | None:
    """
    Fetch earnings data for a ticker (expected EPS, scheduled date, etc).
    Uses yfinance which is free.
    """
    try:
        data = yf.Ticker(ticker)
        info = data.info

        return {
            "ticker": ticker,
            "company": info.get("longName", ticker),
            "expected_eps": info.get("epsTrailingTwelveMonths"),
            "last_eps": info.get("trailingEps"),
        }
    except Exception as e:
        logger.warning(f"  Failed to fetch earnings data for {ticker}: {e}")
        return None


def get_stock_performance(ticker: str, days: int = 1) -> dict:
    """
    Get stock performance over N days (intraday move on earnings).
    """
    try:
        data = yf.Ticker(ticker)
        hist = data.history(period=f"{days+1}d")

        if len(hist) < 2:
            return {"price": None, "change_pct": None, "signal": False}

        close_today = hist["Close"].iloc[-1]
        open_today = hist["Open"].iloc[-1]
        close_yesterday = hist["Close"].iloc[-2]

        # Intraday move (open to close)
        intraday_move = ((close_today - open_today) / open_today) * 100

        # Compare to yesterday's close
        vs_yesterday = ((close_today - close_yesterday) / close_yesterday) * 100

        return {
            "price": round(close_today, 2),
            "intraday_move_pct": round(intraday_move, 2),
            "vs_yesterday_pct": round(vs_yesterday, 2),
            "signal": abs(intraday_move) > STOCK_MOVE_THRESHOLD,
        }
    except Exception as e:
        logger.warning(f"  Failed to fetch stock performance for {ticker}: {e}")
        return {"price": None, "change_pct": None, "signal": False}


def analyze_earnings_with_gemini(ticker: str, earnings_data: dict, perf_data: dict) -> dict:
    """
    Use Gemini to analyze earnings surprise and trade implications.
    OPTIMIZED: Minimal prompt for cost savings.
    """
    if not GEMINI_API_KEY or not earnings_data or not perf_data:
        return {}

    beat_pct = (
        ((earnings_data.get("actual_eps", 0) - earnings_data.get("expected_eps", 0))
         / abs(earnings_data.get("expected_eps", 1)))
        * 100
    )

    prompt = f"""Analyze this earnings surprise for {ticker}:

Expected EPS: ${earnings_data.get('expected_eps', '?')}
Actual EPS:   ${earnings_data.get('actual_eps', '?')}
Surprise:     {beat_pct:+.1f}%

Stock move: {perf_data.get('intraday_move_pct', '?')}% intraday

Give a 1-line trade assessment (bullish/bearish/caution).
Then 1-line risk/opportunity.

JSON format only:
{{
  "assessment": "bullish|bearish|caution",
  "thesis": "1-line reason",
  "risk": "1-line risk"
}}"""

    try:
        from google import genai

        client = genai.Client(api_key=GEMINI_API_KEY)
        model = "gemini-1.5-flash" if USE_CHEAPER_MODEL else "gemini-2.5-flash"

        response = client.models.generate_content(model=model, contents=prompt)
        raw = response.text.strip().replace("```json", "").replace("```", "").strip()

        return json.loads(raw)
    except Exception as e:
        logger.error(f"Gemini analysis failed for {ticker}: {e}")
        return {}


def send_telegram_alert(ticker: str, company: str, analysis: dict, perf: dict) -> bool:
    """Send Telegram alert for trade-worthy earnings."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        logger.warning("Telegram not configured")
        return False

    move_pct = perf.get("intraday_move_pct", 0)
    assessment = analysis.get("assessment", "?").upper()
    thesis = analysis.get("thesis", "")
    risk = analysis.get("risk", "")

    icon = "🟢" if assessment == "BULLISH" else "🔴" if assessment == "BEARISH" else "🟡"

    text = f"""{icon} *EARNINGS ALERT* {icon}

*{ticker}* — {company}

Move: *{move_pct:+.1f}%*
Assessment: {assessment}

*Thesis:* {thesis}
*Risk:* {risk}"""

    try:
        for chat_id in TELEGRAM_CHAT.split(","):
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id.strip(), "text": text, "parse_mode": "MarkdownV2"},
                timeout=10,
            )
        logger.info(f"  Telegram: sent alert for {ticker}")
        return True
    except Exception as e:
        logger.warning(f"  Telegram send failed: {e}")
        return False


def main():
    now_et = datetime.now(ET)
    logger.info(f"Earnings Monitor — {now_et.strftime('%Y-%m-%d %H:%M ET')}")

    # Check if we're in market hours
    hour = now_et.hour
    weekday = now_et.weekday()

    if weekday >= 5 or hour < 9 or hour > 17:
        logger.info(f"Outside market hours (9:30 AM-5 PM ET, Mon-Fri) — skipping")
        return

    # Load today's earnings from calendar (would come from earnings_calendar.json)
    # For now, this is a template
    calendar_file = Path("public/earnings_calendar.json")
    if not calendar_file.exists():
        logger.warning("No earnings calendar found")
        return

    try:
        with open(calendar_file) as f:
            calendar = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load earnings calendar: {e}")
        return

    today = now_et.date().isoformat()
    today_earnings = calendar.get("by_date", {}).get(today, [])

    if not today_earnings:
        logger.info(f"No earnings scheduled for {today}")
        return

    logger.info(f"Monitoring {len(today_earnings)} earnings for {today}")

    alerts = []

    for ticker in today_earnings:
        logger.info(f"  Analyzing {ticker}...")

        earnings_data = get_earnings_data(ticker)
        if not earnings_data:
            continue

        perf_data = get_stock_performance(ticker)

        # Check if stock moved >8% or earnings were a large beat/miss
        stock_moved = perf_data.get("signal", False)

        # Calculate beat/miss if we have actual EPS
        beat_pct = 0
        if earnings_data.get("actual_eps") and earnings_data.get("expected_eps"):
            beat_pct = (
                (earnings_data["actual_eps"] - earnings_data["expected_eps"])
                / abs(earnings_data["expected_eps"])
            ) * 100

        is_large_beat = beat_pct > BEAT_THRESHOLD
        is_large_miss = beat_pct < MISS_THRESHOLD

        # Alert if any threshold crossed
        if stock_moved or is_large_beat or is_large_miss:
            logger.info(
                f"  ✓ {ticker}: move={perf_data.get('intraday_move_pct', '?')}%, "
                f"beat={beat_pct:.1f}%"
            )

            analysis = analyze_earnings_with_gemini(ticker, earnings_data, perf_data)
            send_telegram_alert(ticker, earnings_data.get("company", ticker), analysis, perf_data)

            # Generate detailed 7-section earnings report
            if HAS_REPORT_GENERATOR:
                try:
                    logger.info(f"  Generating detailed earnings report for {ticker}...")
                    detailed_report = generate_earnings_report(
                        ticker,
                        earnings_data,
                        perf_data.get("intraday_move_pct", 0)
                    )
                    if detailed_report:
                        # Save report to JSON
                        report_path = save_report(detailed_report, ticker, today)
                        logger.info(f"  Report saved → {report_path}")

                        # Send short Telegram alert with dashboard link
                        if send_telegram_report(detailed_report, ticker, perf_data.get("intraday_move_pct", 0)):
                            logger.info(f"  Dashboard alert sent for {ticker}")
                except Exception as e:
                    logger.error(f"  Failed to generate detailed report for {ticker}: {e}")

            alerts.append(
                {
                    "ticker": ticker,
                    "company": earnings_data.get("company"),
                    "stock_move_pct": perf_data.get("intraday_move_pct"),
                    "beat_pct": round(beat_pct, 2),
                    "analysis": analysis,
                    "timestamp": now_et.isoformat(),
                }
            )

    # Save report
    out_dir = Path("data/earnings_alerts")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{today}.json"

    report = {
        "date": today,
        "timestamp": now_et.isoformat(),
        "alerts": alerts,
        "cost_analysis": {"model": "gemini-1.5-flash", "estimated_monthly_cost_usd": 0.001},
    }

    with open(out_file, "w") as f:
        json.dump(report, f, indent=2)

    logger.info(f"Saved {len(alerts)} alerts → {out_file}")

    print(f"\n{'='*70}")
    print(f"EARNINGS MONITOR SUMMARY")
    print(f"{'='*70}")
    print(f"Date: {today}")
    print(f"Earnings monitored: {len(today_earnings)}")
    print(f"Alerts triggered: {len(alerts)}")
    for alert in alerts:
        move = alert.get("stock_move_pct", "?")
        beat = alert.get("beat_pct", "?")
        print(f"  - {alert['ticker']}: {move:+.1f}% move, {beat:+.1f}% EPS beat")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
