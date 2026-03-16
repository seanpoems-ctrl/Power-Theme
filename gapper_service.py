"""
gapper_service.py — Pre-Market Gapper Intelligence Scanner
Scans for gap-up stocks 08:00–09:29 AM ET using TradingView Screener
Categorizes catalysts and generates trade hypotheses via Gemini 1.5 Flash
Outputs public/gapper_data.json
"""

import json
import os
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
ET = ZoneInfo("America/New_York")


# ──────────────────────────────────────────────────────────────
# TradingView Screener
# ──────────────────────────────────────────────────────────────

def fetch_gappers() -> list[dict]:
    """Fetch pre-market gap-up stocks from TradingView screener."""
    try:
        from tradingview_screener import Query, col
        (_, df) = (
            Query()
            .select(
                "name", "close", "premarket_change", "premarket_volume",
                "market_cap_basic", "average_volume_30d_calc", "relative_volume_intraday|5"
            )
            .where(
                col("premarket_change") >= 5,
                col("premarket_volume") >= 200000,
                col("market_cap_basic") >= 2e9,
                col("average_volume_30d_calc") >= 500000,
                col("close") >= 5,
                col("close") <= 200,
            )
            .order_by("premarket_change", ascending=False)
            .limit(20)
            .get_scanner_data()
        )
        if df is None or df.empty:
            return []
        results = []
        for _, row in df.iterrows():
            rvol = float(row.get("relative_volume_intraday|5") or 0)
            results.append({
                "ticker":      str(row.get("name", "")),
                "price":       round(float(row.get("close") or 0), 2),
                "gap_pct":     round(float(row.get("premarket_change") or 0), 2),
                "pm_volume":   int(row.get("premarket_volume") or 0),
                "avg_vol_30d": int(row.get("average_volume_30d_calc") or 0),
                "mkt_cap":     int(row.get("market_cap_basic") or 0),
                "rvol":        round(rvol, 2),
            })
        return results
    except Exception as e:
        logger.error(f"TradingView screener failed: {e}")
        return []


# ──────────────────────────────────────────────────────────────
# Google News RSS
# ──────────────────────────────────────────────────────────────

def fetch_news_headlines(ticker: str) -> list[str]:
    """Fetch news headlines from the last 24 hours for a ticker via Google News RSS."""
    try:
        from xml.etree import ElementTree as ET_xml
        from email.utils import parsedate_to_datetime
        url = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET_xml.fromstring(resp.content)
        cutoff = datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=24)
        headlines = []
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            pub_date_str = item.findtext("pubDate", "")
            if not title:
                continue
            try:
                pub_dt = parsedate_to_datetime(pub_date_str)
                if pub_dt < cutoff:
                    continue
            except Exception:
                pass  # If date can't be parsed, include it anyway
            headlines.append(title)
            if len(headlines) >= 5:
                break
        return headlines
    except Exception as e:
        logger.warning(f"  News fetch failed for {ticker}: {e}")
        return []


# ──────────────────────────────────────────────────────────────
# Gemini Analysis
# ──────────────────────────────────────────────────────────────

CATEGORIES = ["Earnings", "Upgrade", "FDA", "Thematic Narratives", "Government Policy", "New Contract/Partnership", "Institutional Buying", "Insider Buying", "Others"]

HYPOTHESIS_RULES = {
    "Earnings":                 ("High Conviction (Gap & Go)",  "Watch for 5-min ORB above PM High."),
    "New Contract/Partnership": ("High Conviction (Gap & Go)",  "Watch for 5-min ORB above PM High."),
    "Thematic Narratives":      ("Medium Conviction (RS Hold)", "Look for dip-buy at 9-EMA/VWAP."),
    "Government Policy":        ("Medium Conviction (RS Hold)", "Look for dip-buy at 9-EMA/VWAP."),
    "Institutional Buying":     ("Medium Conviction (RS Hold)", "Large fund accumulation; watch for continuation."),
    "Insider Buying":           ("Medium Conviction (RS Hold)", "Insider conviction signal; look for base breakout."),
    "Upgrade":                  ("Caution (Fade Candidate)",    "Low institutional conviction; likely gap-fill."),
    "FDA":                      ("High Risk (Volatility Trap)", "Expect 2nd-half mean reversion; avoid open chase."),
    "Others":                   ("Medium Conviction (RS Hold)", "Monitor price action at open."),
}


def analyze_with_gemini(ticker: str, headlines: list[str], rvol: float) -> dict:
    """Use Gemini 1.5 Flash to categorize catalyst and generate trade hypothesis."""
    if not GEMINI_API_KEY:
        return _fallback_analysis(ticker, headlines, rvol)
    if not headlines:
        return {
            "category": "Others", "reasoning": "No news found.",
            "hypothesis": "Monitor price action at open.", "conviction": 30,
        }
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)

        headlines_text = "\n".join(f"- {h}" for h in headlines)
        prompt = f"""You are an institutional trading analyst. Analyze these news headlines for {ticker}:

{headlines_text}

Classify the PRIMARY catalyst into exactly ONE category using these strict definitions:
- Earnings: Company reported quarterly/annual earnings results (beat/miss EPS or revenue)
- Upgrade: Analyst raised rating or price target
- FDA: FDA approval, rejection, clinical trial result, or drug news
- Thematic Narratives: Sector rotation, macro theme, industry trend
- Government Policy: Regulation, tariff, government contract, policy change
- New Contract/Partnership: Company announced new deal, partnership, or major customer win
- Institutional Buying: Hedge funds, mutual funds, or large institutions disclosed new or increased positions
- Insider Buying: Company executives, directors, or insiders purchased shares
- Others: Technical breakout, short squeeze, or anything not fitting above

Important: Only use "Earnings" if the company ACTUALLY reported earnings results. Analyst upgrades are "Upgrade". Fund purchases are "Institutional Buying". Executive purchases are "Insider Buying".

Respond in this exact JSON format only, no extra text:
{{"category": "<category>", "reasoning": "<1-2 sentence explanation>"}}"""

        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        category = result.get("category", "Others")
        if category not in CATEGORIES:
            category = "Others"
        reasoning = result.get("reasoning", "")

        hypothesis_label, strategy = HYPOTHESIS_RULES.get(category, HYPOTHESIS_RULES["Others"])
        # Adjust conviction by RVOL
        base_conviction = {"High Conviction (Gap & Go)": 80, "Medium Conviction (RS Hold)": 60,
                           "Caution (Fade Candidate)": 35, "High Risk (Volatility Trap)": 25}.get(hypothesis_label, 50)
        conviction = min(99, int(base_conviction + (rvol - 2) * 3))

        return {
            "category":   category,
            "reasoning":  reasoning,
            "hypothesis": f"{hypothesis_label} — {strategy}",
            "conviction": conviction,
        }
    except Exception as e:
        logger.warning(f"  Gemini failed for {ticker}: {e}")
        return _fallback_analysis(ticker, headlines, rvol)


def _fallback_analysis(ticker: str, headlines: list[str], rvol: float) -> dict:
    """Rule-based fallback if Gemini unavailable."""
    text = " ".join(headlines).lower()
    if any(w in text for w in ["earnings", "beat", "revenue", "eps"]):
        cat = "Earnings"
    elif any(w in text for w in ["fda", "clinical", "trial", "drug", "approval"]):
        cat = "FDA"
    elif any(w in text for w in ["upgrade", "price target", "analyst"]):
        cat = "Upgrade"
    elif any(w in text for w in ["contract", "partnership", "deal", "agreement"]):
        cat = "New Contract/Partnership"
    elif any(w in text for w in ["policy", "government", "regulation", "tariff"]):
        cat = "Government Policy"
    else:
        cat = "Others"
    label, strategy = HYPOTHESIS_RULES.get(cat, HYPOTHESIS_RULES["Others"])
    return {
        "category":   cat,
        "reasoning":  headlines[0] if headlines else "No catalyst identified.",
        "hypothesis": f"{label} — {strategy}",
        "conviction": 50,
    }


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def main():
    now_et = datetime.now(ET)
    logger.info(f"Pre-Market Gapper Scanner — {now_et.strftime('%Y-%m-%d %H:%M ET')}")

    logger.info("Fetching gappers from TradingView...")
    gappers = fetch_gappers()
    logger.info(f"  Found {len(gappers)} gappers")

    output = []
    for stock in gappers:
        ticker = stock["ticker"]
        logger.info(f"  Analyzing {ticker} (gap={stock['gap_pct']}% rvol={stock['rvol']}x)...")
        headlines = fetch_news_headlines(ticker)
        analysis = analyze_with_gemini(ticker, headlines, stock["rvol"])
        time.sleep(1)  # rate limit
        output.append({**stock, **analysis, "headlines": headlines[:3]})

    result = {
        "scan_time": now_et.strftime("%Y-%m-%d %H:%M ET"),
        "gappers":   output,
    }

    out_path = Path("public/gapper_data.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Done! {len(output)} gappers → {out_path}")


if __name__ == "__main__":
    main()
