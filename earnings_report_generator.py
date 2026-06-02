#!/usr/bin/env python3
"""
Hedge Fund Earnings Report Generator

Generates comprehensive hedge fund-style earnings analysis reports
covering all 7 critical sections:

1. Key Actuals vs. Estimates
2. Guidance vs. Estimates
3. Growth & Margins
4. Call Highlights
5. Ongoing Concerns
6. Risks & Opportunities
7. Special Notes

Triggered on earnings with >8% move or >15% beat/miss.
Output: Detailed JSON + Telegram alert
Cost: ~$0.0002 per earnings (minimal Gemini call)
"""

import json
import os
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

ET = ZoneInfo("America/New_York")


def generate_earnings_report(ticker: str, earnings_data: dict, stock_move: float) -> dict:
    """
    Generate comprehensive 7-section hedge fund earnings report.
    Uses Gemini to synthesize all key metrics and analysis.
    """
    if not GEMINI_API_KEY:
        return {}

    prompt = f"""You are a hedge fund analyst. Generate a concise earnings report for {ticker}.

ACTUALS:
- Expected EPS: ${earnings_data.get('expected_eps', '?')}
- Actual EPS: ${earnings_data.get('actual_eps', '?')}
- Expected Revenue: ${earnings_data.get('expected_revenue', '?')}M
- Actual Revenue: ${earnings_data.get('actual_revenue', '?')}M
- Stock Move: {stock_move:+.1f}%

GUIDANCE:
- Prior Year Revenue Growth: {earnings_data.get('prior_yoy_growth', '?')}%
- Guidance Revenue Growth: {earnings_data.get('guidance_growth', '?')}%
- Prior Year Operating Margin: {earnings_data.get('prior_margin', '?')}%

Generate this EXACT JSON structure (be concise, 1-2 lines per section):

{{
  "company": "{ticker}",
  "quarter": "Q? FY?",
  "beat_miss": "beat|miss|inline",
  "stock_reaction": "{stock_move:+.1f}%",

  "section_1_actuals": {{
    "revenue_beat": "Actual $X vs Est $Y → Beat/Miss by Z%",
    "eps_beat": "Actual $X vs Est $Y → Beat/Miss by Z%",
    "key_kpis": "Highlight 1 key operational metric"
  }},

  "section_2_guidance": {{
    "revenue_guidance": "New guidance $X vs consensus $Y",
    "eps_guidance": "New EPS guidance vs consensus",
    "forward_signal": "What guidance tells us about trajectory"
  }},

  "section_3_growth_margins": {{
    "revenue_growth": "YoY growth X%, QoQ growth Y%",
    "margin_trend": "Operating margin Z% vs prior Y%",
    "profitability": "Operating leverage improving/deteriorating"
  }},

  "section_4_call_highlights": {{
    "main_themes": "1-2 key topics management emphasized",
    "mgmt_tone": "Optimistic/cautious/defensive",
    "analyst_questions": "What analysts focused on"
  }},

  "section_5_concerns": {{
    "prior_issues": "Previously raised concern #1 - status: resolved/ongoing/worsened",
    "new_headwinds": "Any NEW concerns mentioned this quarter"
  }},

  "section_6_risks_opportunities": {{
    "new_risks": "Risk #1: Description",
    "opportunities": "Opportunity #1: upside catalyst",
    "peer_context": "How this compares to peers (GitHub, Atlassian, etc)"
  }},

  "section_7_special_notes": {{
    "accounting_changes": "Any accounting, audit, or restatement issues?",
    "insider_activity": "Any material insider buying/selling?",
    "analyst_reaction": "Street consensus post-call"
  }},

  "rating": "BUY|HOLD|SELL|REDUCE",
  "price_target": "$XXX (implied X% upside/downside)"
}}

Be data-driven. Use actual metrics where available. Highlight what matters for traders."""

    try:
        from google import genai

        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt
        )

        raw = response.text.strip().replace("```json", "").replace("```", "").strip()
        report = json.loads(raw)
        return report

    except Exception as e:
        logger.error(f"Report generation failed: {e}")
        return {}


def send_telegram_report(report: dict, ticker: str, stock_move: float = 0) -> bool:
    """Send short earnings alert to Telegram with link to dashboard."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return False

    try:
        # Short summary + link to dashboard
        beat_miss = report.get('beat_miss', '?').upper()
        rating = report.get('rating', '?')
        reaction = report.get('stock_reaction', '?')

        # Build the dashboard link
        dashboard_url = "https://seanpoems.github.io/Power-Theme/"

        text = f"""📊 *EARNINGS ALERT* — {ticker.upper()}

*{beat_miss}* | Reaction: *{reaction}*
Rating: *{rating}* | Target: {report.get('price_target', '?')}

🔗 Full analysis in dashboard:
{dashboard_url}

_Tap "📊 Earnings Report" tab for full 7-section breakdown_"""

        for chat_id in TELEGRAM_CHAT.split(","):
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id.strip(), "text": text, "parse_mode": "MarkdownV2"},
                timeout=15,
            )
        return True
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
        return False


def save_report(report: dict, ticker: str, date: str) -> Path:
    """Save comprehensive report to JSON and update manifest in public/ for dashboard access."""
    out_dir = Path("public/earnings_reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / f"{ticker}_{date}_report.json"

    report_with_metadata = {
        "ticker": ticker,
        "report_date": date,
        "generated_at": datetime.now(ET).isoformat(),
        "report": report,
    }

    with open(out_file, "w") as f:
        json.dump(report_with_metadata, f, indent=2)

    # Update manifest.json to help dashboard load reports
    manifest_file = out_dir / "manifest.json"
    manifest = {}

    # Load existing manifest
    if manifest_file.exists():
        try:
            with open(manifest_file) as f:
                manifest = json.load(f)
        except:
            manifest = {}

    # Add today's report to manifest
    if date not in manifest:
        manifest[date] = []

    report_filename = f"{ticker}_{date}_report.json"
    if report_filename not in manifest[date]:
        manifest[date].append(report_filename)

    # Save updated manifest
    with open(manifest_file, "w") as f:
        json.dump(manifest, f, indent=2)

    logger.info(f"Manifest updated with {report_filename}")

    return out_file


def main():
    """
    Example usage — would be called from earnings_monitor.py
    when earnings trigger (>8% move or >15% beat/miss).
    """
    today = datetime.now(ET).date().isoformat()

    # Example: GTLB earnings
    earnings_data = {
        "expected_eps": 0.18,
        "actual_eps": 0.21,
        "expected_revenue": 158,
        "actual_revenue": 165,
        "prior_yoy_growth": 28,
        "guidance_growth": 25,
        "prior_margin": 12.5,
    }

    stock_move = 8.5  # 8.5% gap up
    ticker = "GTLB"

    logger.info(f"Generating comprehensive earnings report for {ticker}...")

    report = generate_earnings_report(ticker, earnings_data, stock_move)

    if report:
        # Save report
        report_path = save_report(report, ticker, today)
        logger.info(f"Report saved → {report_path}")

        # Send to Telegram
        if send_telegram_report(report, ticker):
            logger.info(f"Telegram alert sent for {ticker}")

        # Print summary
        print(f"\n{'='*70}")
        print(f"EARNINGS REPORT: {ticker}")
        print(f"{'='*70}")
        print(f"Beat/Miss: {report.get('beat_miss', '?').upper()}")
        print(f"Stock Reaction: {report.get('stock_reaction', '?')}")
        print(f"Rating: {report.get('rating', '?')}")
        print(f"Price Target: {report.get('price_target', '?')}")
        print(f"{'='*70}\n")

        # Show full report
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
