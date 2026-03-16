"""
gapper_service.py — Pre-Market Gapper Intelligence Scanner
Scans for gap-up stocks 08:00–09:29 AM ET using TradingView Screener
Categorizes catalysts and generates trade hypotheses via Gemini 2.5 Flash
Outputs public/gapper_data.json
"""

import json
import os
import re
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
# Finviz Theme Map (heatmap-based ticker → theme)
# ──────────────────────────────────────────────────────────────

def fetch_finviz_theme_map() -> dict:
    """Scrape Finviz themes heatmap to build {ticker: theme_name} mapping."""
    ticker_theme = {}
    try:
        url = "https://finviz.com/map.ashx?t=themes"
        resp = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://finviz.com/",
        })
        resp.raise_for_status()
        html = resp.text

        # Finviz embeds map data as a JS variable:
        # var d=[{t:"NVDA",n:"NVIDIA",v:5.2,g:"Artificial Intelligence"}, ...]
        patterns = [
            r'var\s+d\s*=\s*(\[.+?\])\s*;',
            r'var\s+mapData\s*=\s*(\[.+?\])\s*;',
        ]
        raw = None
        for pattern in patterns:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                raw = m.group(1)
                break

        if raw:
            # Normalize JS unquoted keys to valid JSON
            fixed = re.sub(r'(?<=[{,\[]\s*)([a-zA-Z_]\w*)\s*:', r'"\1":', raw)
            try:
                items = json.loads(fixed)
                for item in items:
                    t = item.get("t") or item.get("ticker", "")
                    g = item.get("g") or item.get("group", "") or item.get("theme", "")
                    if t and g:
                        ticker_theme[t] = g
                logger.info(f"  Finviz theme map: {len(ticker_theme)} tickers mapped")
            except json.JSONDecodeError as je:
                logger.warning(f"  Finviz theme map JSON parse failed: {je}")
        else:
            logger.warning("  Finviz theme map: data pattern not found in page HTML")
    except Exception as e:
        logger.warning(f"  Finviz theme map fetch failed: {e}")
    return ticker_theme


# ──────────────────────────────────────────────────────────────
# Finviz Fundamentals (float, short interest, daily %)
# ──────────────────────────────────────────────────────────────

def fetch_finviz_data(ticker: str) -> dict:
    """Fetch Float, Short Interest %, and Daily % from Finviz quote page."""
    result = {"float_shares": None, "short_float": None, "daily_pct": None}
    try:
        from bs4 import BeautifulSoup
        url = f"https://finviz.com/quote.ashx?t={ticker}&ty=c&p=d&b=1"
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", class_="snapshot-table2")
        if not table:
            return result
        cells = table.find_all("td")
        for i in range(0, len(cells) - 1, 2):
            label = cells[i].get_text(strip=True)
            value = cells[i + 1].get_text(strip=True)
            if label in ("Shs Float", "Float"):
                result["float_shares"] = value
            elif label == "Short Float":
                result["short_float"] = value
            elif label == "Change":
                try:
                    result["daily_pct"] = float(value.replace("%", "").replace("+", ""))
                except ValueError:
                    result["daily_pct"] = None
        return result
    except Exception as e:
        logger.warning(f"  Finviz data failed for {ticker}: {e}")
        return result


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

ANALYSIS_FORMAT_INSTRUCTIONS = """
For analysis_details, use this exact format based on category:

Earnings:
"📊 THE BEAT\\nEPS: [actual vs estimate + surprise %]\\nRevenue: [actual vs estimate + surprise %]\\n\\n📈 THE GROWTH\\nYoY EPS Growth: [%]\\nYoY Revenue Growth: [%]\\n\\n🎯 GUIDANCE\\n[Raised/Cut/Maintained — new outlook vs prior]\\n\\n⚡ SURPRISE FACTOR\\n[Assessment of beat magnitude and management tone]"

Upgrade:
"🏦 THE FIRM\\n[Analyst firm name]\\n\\n📊 RATING CHANGE\\n[From → To]\\n\\n🎯 PRICE TARGET\\nOld PT: $[X] → New PT: $[Y] ([+Z% implied upside])\\n\\n💡 KEY THESIS\\n[Why analyst upgraded — 2-3 key reasons]"

FDA:
"💊 THE DRUG\\n[Drug/device name — indication/disease area]\\n\\n🔬 TRIAL PHASE\\n[Phase X — approval/rejection/data readout type]\\n\\n📊 MARKET SIZE\\n[Patient population, addressable market estimate]\\n\\n⚡ RISK PROFILE\\n[Approval context, historical base rate for this type, typical stock behavior]"

Thematic Narratives:
"🌊 THE NARRATIVE\\n[Specific theme/trend driving the move]\\n\\n🎯 WHY THIS STOCK\\n[Why this company is a primary beneficiary]\\n\\n📈 TAILWIND DURATION\\n[Short-term catalyst vs structural multi-year trend]\\n\\n⚡ KEY RISK\\n[What could invalidate or fade the thesis]"

Government Policy:
"📋 THE POLICY\\n[Specific regulation/contract/policy change]\\n\\n🎯 DIRECT IMPACT\\n[Quantifiable benefit or headwind for this company]\\n\\n🏛️ DURATION\\n[One-time event vs ongoing structural benefit]\\n\\n⚡ POLITICAL RISK\\n[Implementation risk or reversal probability]"

New Contract/Partnership:
"🤝 THE DEAL\\n[Counterparty name — deal type]\\n\\n💰 DEAL VALUE\\n[Size disclosed or strategic significance]\\n\\n📈 REVENUE IMPACT\\n[Estimated % impact on annual revenue, expected timeline]\\n\\n⚡ COMPETITIVE ANGLE\\n[How this shifts the competitive landscape]"

Institutional Buying / Insider Buying:
"🏛️ WHO BOUGHT\\n[Fund name or insider name + title]\\n\\n💰 POSITION SIZE\\n[Shares/dollar value, % of float if disclosed]\\n\\n📊 CONVICTION SIGNAL\\n[Context: new position vs add, size vs normal activity]\\n\\n⚡ INTERPRETATION\\n[What this buying signals about the stock's near-term outlook]"

Others:
"📰 WHAT HAPPENED\\n[Clear factual summary of the news]\\n\\n📊 WHY THE GAP\\n[Market's likely interpretation]\\n\\n⚡ KEY CONSIDERATION\\n[Most important factor for trading this gap — technicals, short interest, catalyst durability]"
"""


def analyze_with_gemini(ticker: str, headlines: list[str], rvol: float) -> dict:
    """Use Gemini 2.5 Flash to categorize catalyst, assign grade, and generate detailed analysis."""
    if not GEMINI_API_KEY:
        return _fallback_analysis(ticker, headlines, rvol)
    if not headlines:
        return {
            "category": "Others", "reasoning": "No news found.",
            "hypothesis": "Monitor price action at open.", "conviction": 30,
            "grade": "C", "analysis_details": "No news catalyst identified.\n\n⚡ KEY CONSIDERATION\nMonitor price action at open for directional bias.",
        }
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)

        headlines_text = "\n".join(f"- {h}" for h in headlines)
        prompt = f"""You are an institutional trading analyst. Analyze these news headlines for {ticker}:

{headlines_text}

Classify the PRIMARY catalyst and provide a detailed, structured analysis.

CATEGORIES (choose exactly one):
- Earnings: Company reported quarterly/annual results (EPS/revenue beat or miss)
- Upgrade: Analyst raised rating or price target
- FDA: FDA approval, rejection, clinical trial result, or drug news
- Thematic Narratives: Sector rotation, macro theme, industry trend
- Government Policy: Regulation, tariff, government contract, policy change
- New Contract/Partnership: New deal, partnership, or major customer win
- Institutional Buying: Hedge funds/mutual funds disclosed new or increased positions
- Insider Buying: Company executives/directors purchased shares
- Others: Technical breakout, short squeeze, or anything not fitting above

GRADE RUBRIC (based on catalyst quality, clarity, and fundamental backing):
- A+: Crystal-clear high-quality catalyst with strong fundamentals (massive earnings beat + guidance raise, or landmark FDA approval)
- A: Clear catalyst with good fundamental support (solid earnings beat, major contract win, significant policy tailwind)
- B: Moderate catalyst or mixed signals (small beat, minor upgrade, unclear policy impact)
- C: Weak or unclear catalyst (small analyst upgrade, speculative theme, no confirmed news)

{ANALYSIS_FORMAT_INSTRUCTIONS}

Respond in this exact JSON format only, no extra text:
{{"category": "<category>", "reasoning": "<1-2 sentence quick summary>", "grade": "<A+|A|B|C>", "analysis_details": "<detailed structured analysis>"}}"""

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
        grade = result.get("grade", "B")
        if grade not in ("A+", "A", "B", "C"):
            grade = "B"
        analysis_details = result.get("analysis_details", reasoning)

        hypothesis_label, strategy = HYPOTHESIS_RULES.get(category, HYPOTHESIS_RULES["Others"])
        base_conviction = {"High Conviction (Gap & Go)": 80, "Medium Conviction (RS Hold)": 60,
                           "Caution (Fade Candidate)": 35, "High Risk (Volatility Trap)": 25}.get(hypothesis_label, 50)
        conviction = min(99, int(base_conviction + (rvol - 2) * 3))

        return {
            "category":        category,
            "reasoning":       reasoning,
            "hypothesis":      f"{hypothesis_label} — {strategy}",
            "conviction":      conviction,
            "grade":           grade,
            "analysis_details": analysis_details,
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
        "category":        cat,
        "reasoning":       headlines[0] if headlines else "No catalyst identified.",
        "hypothesis":      f"{label} — {strategy}",
        "conviction":      50,
        "grade":           "B",
        "analysis_details": headlines[0] if headlines else "No catalyst identified.",
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

    logger.info("Building Finviz theme map...")
    theme_map = fetch_finviz_theme_map()

    output = []
    for stock in gappers:
        ticker = stock["ticker"]
        logger.info(f"  Analyzing {ticker} (gap={stock['gap_pct']}% rvol={stock['rvol']}x)...")

        # Finviz fundamentals (float, short interest, daily %)
        fv = fetch_finviz_data(ticker)

        # News headlines
        headlines = fetch_news_headlines(ticker)

        # AI analysis
        analysis = analyze_with_gemini(ticker, headlines, stock["rvol"])

        # Theme from heatmap
        industry = theme_map.get(ticker, "—")

        time.sleep(1)  # rate limit
        output.append({
            **stock,
            **analysis,
            "headlines":    headlines[:3],
            "float_shares": fv.get("float_shares"),
            "short_float":  fv.get("short_float"),
            "daily_pct":    fv.get("daily_pct"),
            "industry":     industry,
        })

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
