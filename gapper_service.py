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
                "market_cap_basic", "average_volume_10d_calc", "relative_volume_intraday|5"
            )
            .where(
                col("premarket_change") >= 5,
                col("premarket_volume") >= 200000,
                col("market_cap_basic") >= 2e9,
                col("average_volume_10d_calc") >= 500000,
                col("close") >= 5,
            )
            .order_by("premarket_change", ascending=False)
            .limit(40)
            .get_scanner_data()
        )
        if df is None or df.empty:
            return []
        results = []
        for _, row in df.iterrows():
            rvol     = float(row.get("relative_volume_intraday|5") or 0)
            price    = round(float(row.get("close") or 0), 2)
            avg_vol  = int(row.get("average_volume_10d_calc") or 0)
            avg_dvol = round(price * avg_vol)
            # Avg $ Vol ≥ $50M filter
            if avg_dvol < 50_000_000:
                continue
            results.append({
                "ticker":       str(row.get("name", "")),
                "price":        price,
                "gap_pct":      round(float(row.get("premarket_change") or 0), 2),
                "pm_volume":    int(row.get("premarket_volume") or 0),
                "avg_vol_10d":  avg_vol,
                "avg_dollar_vol": avg_dvol,
                "mkt_cap":      int(row.get("market_cap_basic") or 0),
                "rvol":         round(rvol, 2),
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
# Last Earnings Date (yfinance)
# ──────────────────────────────────────────────────────────────

def fetch_last_earnings_date(ticker: str) -> str | None:
    """Return the most recent past earnings date as YYYY-MM-DD, or None if unavailable."""
    try:
        import yfinance as yf
        from datetime import date as _date
        t = yf.Ticker(ticker)
        ed = t.earnings_dates
        if ed is None or ed.empty:
            return None
        today = _date.today()
        past = ed[ed.index.date < today]
        if past.empty:
            return None
        return past.index[0].strftime("%Y-%m-%d")
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────
# Google News RSS
# ──────────────────────────────────────────────────────────────

def fetch_news_headlines(ticker: str) -> list[dict]:
    """Fetch news headlines from the last 24 hours for a ticker via Google News RSS.
    Returns list of {title, date} dicts so Gemini knows when each article was published."""
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
                date_label = pub_dt.strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                continue  # Skip articles with unparseable dates to avoid stale news
            headlines.append({"title": title, "date": date_label})
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
Return analysis_details as 2-3 structured sections using EXACTLY this format:

• **[Section Title]**
[Body: 2-4 sentences. Bold important numbers and outcome words using **word** — e.g. **beat** EPS by **22%**, guidance **raised** to **$7.2B**, stock **declined** below support.]

• **[Section Title 2]**
[Body text...]

Section titles to use by category:
- Earnings: "The 'Beats' (Surprise Factor)" | "The Growth (Momentum)" | "The Guide"
- Upgrade: "The Firm & Rating" | "The Thesis"
- FDA: "The Drug" | "The Significance" | "Risk Profile"
- Thematic Narratives: "The Narrative" | "Explosiveness"
- Government Policy: "The Policy" | "Direct Impact"
- New Contract/Partnership: "Impact" | "Strategic Value"
- Institutional Buying / Insider Buying: "The Buyer" | "Conviction Signal"
- Others: "What Happened" | "Key Consideration"

Rules: No emojis. No markdown headers. Start every section with • **Title** on its own line then the body on the next line. Separate sections with a blank line.
"""


def analyze_with_gemini(ticker: str, headlines: list[str], rvol: float, last_earnings_date: str | None = None) -> dict:
    """Use Gemini 2.5 Flash to categorize catalyst, assign grade, and generate detailed analysis."""
    if not GEMINI_API_KEY:
        return _fallback_analysis(ticker, headlines, rvol)
    if not headlines:
        return {
            "category": "Others", "reasoning": "No news found.",
            "hypothesis": "Monitor price action at open.", "conviction": 30,
            "grade": "C", "finviz_theme": "—",
            "analysis_details": "No news catalyst identified.\n\n⚡ KEY CONSIDERATION\nMonitor price action at open for directional bias.",
        }
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        headlines_text = "\n".join(f"- [{h['date']}] {h['title']}" if isinstance(h, dict) else f"- {h}" for h in headlines)
        earnings_note = ""
        if last_earnings_date:
            from datetime import date as _date, timedelta as _td
            last_dt = _date.fromisoformat(last_earnings_date)
            days_ago = (_date.today() - last_dt).days
            if days_ago > 5:
                earnings_note = f"\nIMPORTANT: {ticker}'s last earnings report was on {last_earnings_date} ({days_ago} days ago). Do NOT classify as Earnings — that is too old to be today's catalyst."
            else:
                earnings_note = f"\nNote: {ticker}'s last earnings report was on {last_earnings_date} ({days_ago} days ago) — recent enough to be a valid Earnings catalyst."
        prompt = f"""You are an institutional trading analyst. Today's date is {today_str}.{earnings_note}
Analyze these news headlines for {ticker} that were published in the last 24 hours (publication date shown in brackets):

{headlines_text}

Classify the PRIMARY catalyst and provide a detailed, structured analysis.

CATEGORIES (choose exactly one):
- Earnings: Company reported quarterly/annual results within the last 5 days. Check the IMPORTANT note above about the actual earnings date before choosing this category.
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

FINVIZ HEATMAP THEME — classify {ticker} into one of these Finviz themes heatmap categories based on the company's business and the news. Use the most specific match:
AI Compute | AI Cloud | AI Models | AI Data & Analytics | AI Enterprise Software | AI Networking | AI Security | AI Edge & IoT | AI Robotics | AI Applications | AI Ads & Search | AI Energy | AI AGI |
Semiconductors - Compute | Semiconductors - Memory | Semiconductors - Analog | Semiconductors - Wireless | Semiconductors - Foundries | Semiconductors - Design Tools | Semiconductors - Lithography | Semiconductors - Packaging |
Cloud Hyperscalers | Cloud Data Centers | Cloud Databases | Cloud DevOps | Cloud Security | Cloud Hybrid | Cloud Multi-cloud | Cloud SaaS |
Cybersecurity - Zero Trust | Cybersecurity - Endpoint | Cybersecurity - Network | Cybersecurity - Cloud | Cybersecurity - Identity/IAM | Cybersecurity - Threat Ops |
Fintech - Payments | Fintech - Neobanks | Fintech - Lending | Fintech - Trading | Fintech - Blockchain/Crypto |
Clean Energy - Solar | Clean Energy - Wind | Clean Energy - Grid | Clean Energy - Nuclear | Clean Energy - Hydrogen |
Electric Vehicles | EV Batteries | EV Charging |
Biotech - Oncology | Biotech - Rare Disease | Biotech - Gene Therapy | Biotech - Immunology |
Pharma - Large Cap | Medical Devices | Digital Health |
Defense & Aerospace | Space | Drones |
Consumer - E-Commerce | Consumer - Streaming | Consumer - Social Media | Consumer - Gaming |
Energy - Oil & Gas | Energy - LNG | Materials - Metals & Mining |
Real Estate | REITs | Infrastructure |
Others

Respond in this exact JSON format only, no extra text:
{{"category": "<category>", "reasoning": "<1-2 sentence quick summary>", "grade": "<A+|A|B|C>", "finviz_theme": "<most specific matching theme from the list above>", "analysis_details": "<detailed structured analysis>"}}"""

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
        finviz_theme = result.get("finviz_theme", "—")
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
            "finviz_theme":    finviz_theme,
            "analysis_details": analysis_details,
        }
    except Exception as e:
        logger.warning(f"  Gemini failed for {ticker}: {e}")
        return _fallback_analysis(ticker, headlines, rvol)


def _fallback_analysis(ticker: str, headlines: list, rvol: float) -> dict:
    """Rule-based fallback if Gemini unavailable."""
    titles = [h["title"] if isinstance(h, dict) else h for h in headlines]
    text = " ".join(titles).lower()
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
        "reasoning":       titles[0] if titles else "No catalyst identified.",
        "hypothesis":      f"{label} — {strategy}",
        "conviction":      50,
        "grade":           "B",
        "finviz_theme":    "—",
        "analysis_details": titles[0] if titles else "No catalyst identified.",
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

        # Finviz fundamentals (float, short interest, daily %)
        fv = fetch_finviz_data(ticker)

        # News headlines
        headlines = fetch_news_headlines(ticker)

        # Last earnings date (to prevent misclassification of old earnings news)
        last_earnings_date = fetch_last_earnings_date(ticker)

        # AI analysis (includes finviz_theme classification)
        analysis = analyze_with_gemini(ticker, headlines, stock["rvol"], last_earnings_date)

        time.sleep(1)  # rate limit
        output.append({
            **stock,
            **analysis,
            "headlines":    [h["title"] if isinstance(h, dict) else h for h in headlines[:3]],
            "float_shares": fv.get("float_shares"),
            "short_float":  fv.get("short_float"),
            "daily_pct":    fv.get("daily_pct"),
            "industry":     analysis.get("finviz_theme", "—"),
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
