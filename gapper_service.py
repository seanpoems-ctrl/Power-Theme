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
                col("premarket_change") >= 3,
                col("premarket_volume") >= 50000,
                col("market_cap_basic") >= 5e8,
                col("average_volume_10d_calc") >= 100000,
                col("close") >= 2,
            )
            .order_by("premarket_change", ascending=False)
            .limit(100)
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
            # Minimum avg $ vol: $5M (UI filter handles stricter thresholds)
            if avg_dvol < 5_000_000:
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
# Ticker Fundamentals: ADR% + Last Earnings Date (yfinance)
# ──────────────────────────────────────────────────────────────

def fetch_ticker_fundamentals(ticker: str) -> dict:
    """Fetch ADR%(20d) and last earnings date in one yfinance call."""
    result = {"adr_pct": None, "last_earnings_date": None}
    try:
        import yfinance as yf
        from datetime import date as _date
        t = yf.Ticker(ticker)

        # ADR% — avg of (High-Low)/Close over last 20 sessions
        hist = t.history(period="25d", interval="1d", auto_adjust=True)
        if len(hist) >= 10:
            adr = ((hist["High"] - hist["Low"]) / hist["Close"] * 100).tail(20).mean()
            result["adr_pct"] = round(float(adr), 2)

        # Last earnings date
        try:
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                today = _date.today()
                past  = ed[ed.index.date < today]
                if not past.empty:
                    result["last_earnings_date"] = past.index[0].strftime("%Y-%m-%d")
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"  yfinance fundamentals failed for {ticker}: {e}")
    return result


# ──────────────────────────────────────────────────────────────
# Hard Gates
# ──────────────────────────────────────────────────────────────

_HIGH_IMPACT_CATS = {"Earnings", "New Contract/Partnership", "FDA"}

def _compute_gates(stock: dict) -> tuple[int, dict, bool]:
    """
    Evaluate 5 hard gates against a stock dict.
    rs_52w defaults to True (pass) when not present in TV data.
    Returns (gates_passed, gates_detail, meets_all_gates).
    """
    gates = {
        "gate_rs":         stock.get("rs_52w") is None or stock.get("rs_52w", 0) > 85,
        "gate_price":      (stock.get("price") or 0) > 12,
        "gate_dollar_vol": (stock.get("avg_dollar_vol") or 0) > 100_000_000,
        "gate_mkt_cap":    (stock.get("mkt_cap") or 0) > 2_000_000_000,
        "gate_adr":        (stock.get("adr_pct") or 0) >= 4.0,
    }
    passed = sum(gates.values())
    return passed, gates, passed == 5


def _compute_tier(grade: str, category: str, meets_all_gates: bool, hypothesis: str) -> tuple[str, str]:
    """
    Map grade + context to a tier label.
    Priority: T1 → T2 → Fail → T3.
    """
    if grade in ("A+", "A") and category in _HIGH_IMPACT_CATS:
        return "T1", "Major Catalyst"
    high_conviction = "High Conviction" in hypothesis or grade == "A+"
    if grade in ("A", "B") and high_conviction:
        return "T2", "Strong Catalyst"
    if grade == "C" and not meets_all_gates:
        return "Fail", "Excluded"
    return "T3", "Minor Catalyst"


def _apply_technical_floor(analysis: dict, avg_dollar_vol: float, adr_pct: float | None) -> tuple[dict, str]:
    """Enforce the Hard Technical Floor on grade and return (updated_analysis, technical_status)."""
    dvol_m  = (avg_dollar_vol or 0) / 1_000_000
    adr     = adr_pct or 0.0
    grade   = analysis.get("grade", "C")

    failures = []
    if dvol_m < 100:
        failures.append(f"Avg $Vol ${dvol_m:.0f}M < $100M")
    if adr < 4:
        failures.append(f"ADR {adr:.1f}% < 4%")

    technical_status = "Pass" if not failures else "Fail (" + ", ".join(failures) + ")"

    # Hard grade cap
    if dvol_m < 100 or adr < 2:
        analysis["grade"] = "C"
    elif adr < 4 and grade in ("A+", "A"):
        analysis["grade"] = "B"

    return analysis, technical_status


# ──────────────────────────────────────────────────────────────
# Google News RSS + Finviz News
# ──────────────────────────────────────────────────────────────

def _fetch_google_news(ticker: str, cutoff, limit: int = 5) -> list[dict]:
    """Fetch up to `limit` headlines from Google News RSS within the last 24h."""
    try:
        from xml.etree import ElementTree as ET_xml
        from email.utils import parsedate_to_datetime
        url = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET_xml.fromstring(resp.content)
        results = []
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            link  = item.findtext("link", "")
            pub_date_str = item.findtext("pubDate", "")
            if not title:
                continue
            try:
                pub_dt = parsedate_to_datetime(pub_date_str)
                if pub_dt < cutoff:
                    continue
                date_label = pub_dt.strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                continue
            results.append({"title": title, "date": date_label, "source": "Google News", "url": link})
            if len(results) >= limit:
                break
        return results
    except Exception as e:
        logger.warning(f"  Google News fetch failed for {ticker}: {e}")
        return []


def _fetch_finviz_news(ticker: str, cutoff, limit: int = 5) -> list[dict]:
    """Fetch up to `limit` headlines from Finviz quote page news table within the last 24h."""
    try:
        from bs4 import BeautifulSoup
        url = f"https://finviz.com/quote.ashx?t={ticker}&p=d"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", id="news-table")
        if not table:
            return []
        results = []
        last_date = None
        today = datetime.now(timezone.utc).date()
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            date_str = cells[0].get_text(strip=True)
            title_tag = cells[1].find("a")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            # Finviz date cell: either "Mar-20-26 07:30AM" or just "07:30AM" (same day)
            if len(date_str) > 8:
                last_date = date_str
            full_date_str = last_date or date_str
            try:
                from datetime import timedelta
                import re
                # Parse "Mar-20-26 07:30AM" or "Mar-20-2026 07:30AM"
                match = re.match(r"(\w{3}-\d{2}-\d{2,4})\s+(\d{1,2}:\d{2}(?:AM|PM))", full_date_str)
                if not match:
                    continue
                date_part, time_part = match.group(1), match.group(2)
                year_part = date_part.split("-")[2]
                if len(year_part) == 2:
                    date_part = date_part[:-2] + "20" + year_part
                pub_dt = datetime.strptime(f"{date_part} {time_part}", "%b-%d-%Y %I:%M%p")
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
                date_label = pub_dt.strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                continue
            link = title_tag.get("href", "") if title_tag else ""
            results.append({"title": title, "date": date_label, "source": "Finviz", "url": link})
            if len(results) >= limit:
                break
        return results
    except Exception as e:
        logger.warning(f"  Finviz news fetch failed for {ticker}: {e}")
        return []


def verify_catalyst_accuracy(
    ticker: str,
    category: str,
    reasoning: str,
    google_headlines: list[dict],
    finviz_headlines: list[dict],
) -> dict:
    """Second Gemini call: Skeptical Auditor cross-checks facts across two independent sources."""
    fallback = {
        "status": "Unconfirmed",
        "confidence_score": 50,
        "sources_consulted": ["Finviz", "Google News"],
        "primary_claim": reasoning,
        "discrepancy_note": "",
    }
    if not GEMINI_API_KEY:
        return fallback

    def fmt(items):
        return "\n".join(f"  [{i+1}] ({h['source']}) {h['title']}" for i, h in enumerate(items)) or "  (none)"

    prompt = f"""You are a Skeptical Auditor fact-checking pre-market news for {ticker}.

PRIMARY CATALYST CLAIM (from initial analysis):
Category: {category}
Summary: {reasoning}

SOURCE A — Finviz News:
{fmt(finviz_headlines)}

SOURCE B — Google News:
{fmt(google_headlines)}

Your job:
1. Identify the single most specific factual claim (a dollar amount, percentage, date, approval decision, etc.) in the catalyst summary.
2. Check whether BOTH sources confirm this claim with matching specifics. Exact number agreement is required for "Verified".
3. If the news mentions a specific number (e.g. "$1.6T deal" or "beat by 22%"), look carefully — is it consistent across sources, or could it be an old headline, typo, or exaggeration?
4. If only ONE source mentions the claim or sources give conflicting numbers, mark as Discrepancy.
5. If neither source contains enough detail to confirm the claim, mark as Unconfirmed.

Respond ONLY with this JSON (no extra text):
{{"status": "Verified|Discrepancy|Unconfirmed", "confidence_score": <0-100>, "sources_consulted": ["Finviz", "Google News"], "primary_claim": "<the specific claim being verified>", "discrepancy_note": "<only populated when status=Discrepancy, e.g. Source A says $200M, Source B says $20M>"}}"""

    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
        result = json.loads(text)
        status = result.get("status", "Unconfirmed")
        if status not in ("Verified", "Discrepancy", "Unconfirmed"):
            status = "Unconfirmed"
        return {
            "status": status,
            "confidence_score": max(0, min(100, int(result.get("confidence_score", 50)))),
            "sources_consulted": result.get("sources_consulted", ["Finviz", "Google News"]),
            "primary_claim": result.get("primary_claim", reasoning),
            "discrepancy_note": result.get("discrepancy_note", ""),
        }
    except Exception as e:
        logger.warning(f"  Verification failed for {ticker}: {e}")
        return fallback


def fetch_news_headlines(ticker: str) -> list[dict]:
    """Fetch news headlines from the last 24h via Google News RSS + Finviz, deduped and sorted."""
    import datetime as dt
    cutoff = datetime.now(timezone.utc) - dt.timedelta(hours=24)
    google = _fetch_google_news(ticker, cutoff, limit=5)
    finviz = _fetch_finviz_news(ticker, cutoff, limit=5)
    # Merge, deduplicate by title similarity, sort newest first
    seen = set()
    merged = []
    for item in google + finviz:
        key = item["title"][:60].lower()
        if key not in seen:
            seen.add(key)
            merged.append(item)
    merged.sort(key=lambda x: x["date"], reverse=True)
    return merged[:8]


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


def analyze_with_gemini(
    ticker: str,
    headlines: list[str],
    rvol: float,
    last_earnings_date: str | None = None,
    avg_dollar_vol: float = 0,
    adr_pct: float | None = None,
) -> dict:
    """Use Gemini 2.5 Flash — Momentum Catalyst Intelligence with Hard Technical Floor."""
    if not GEMINI_API_KEY:
        return _fallback_analysis(ticker, headlines, rvol)

    dvol_m = (avg_dollar_vol or 0) / 1_000_000
    adr    = adr_pct or 0.0

    if not headlines:
        return {
            "category":        "Others",
            "theme":           "Technical / Flow",
            "reasoning":       "No immediate fundamental catalyst found; price action likely driven by technical breakout or institutional flow.",
            "hypothesis":      "Caution (Gap & Trap Risk) — Avoid open chase; wait for 15-min base confirmation.",
            "conviction":      25,
            "grade":           "C",
            "finviz_theme":    "—",
            "analysis_detail": "Catalyst: Unknown | Impact: Speculative. Significant price move on no news suggests Low Float squeeze or technical stop-running. High risk of Gap and Trap without fundamental backing.",
            "analysis_details": "• **What Happened**\nNo news catalyst identified within the last 24 hours. The gap is likely technical or flow-driven.\n\n• **Key Consideration**\nLow Float squeezes and overnight program flows can create sizable gaps with no fundamental backing. These are typically Gap and Trap setups — the stock often fades to fill the gap by end of day.",
            "peer_tickers":    [],
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
        prompt = f"""You are a Senior Momentum Equity Analyst. Today is {today_str}.{earnings_note}

Technical context for {ticker}:
  Avg $ Vol (20d): ${dvol_m:.0f}M  |  ADR% (20d): {adr:.1f}%  |  RVOL: {rvol:.1f}x

News headlines (last 24h):
{headlines_text}

═══ STEP 1: CATALYST VERIFICATION ═══
Before grading, verify a specific catalyst occurred in the last 24h.

UNKNOWN CATALYST RULE — If no specific fundamental news found:
  category: "Others"  |  theme: "Technical / Flow"  |  grade: "C"
  reasoning: "No immediate fundamental catalyst found; likely technical breakout or institutional flow."
  analysis_detail: "Catalyst: Unknown | Impact: Speculative. Significant move on no news suggests Low Float squeeze or technical stop-running. High risk of Gap and Trap without fundamental backing."

SYMPATHY MOVE RULE — If ticker moves because a sector leader (e.g. NVDA) reported news:
  theme: "Sector Sympathy"  |  grade: "B" or "C"
  reasoning: "Moving in sympathy with [Leader] following [Event]."
  analysis_detail: "Catalyst: Sector Tailwinds | Impact: Secondary. No company-specific news; move correlated to broader [Industry] trend."

DO NOT invent a story. DO NOT use "Market Volatility" as reasoning.

═══ STEP 2: CATEGORY ═══
Choose exactly one:
- Earnings | Upgrade | FDA | Thematic Narratives | Government Policy | New Contract/Partnership | Institutional Buying | Insider Buying | Others
{earnings_note}

═══ STEP 3: GRADE RUBRIC — STRICT HIERARCHY ═══
Apply BOTH technical and news quality. The Hard Technical Floor is informational here — Python will enforce caps.

- A+ (Institutional Apex): News = structural change ($1B+ contract, Tier-1 partnership like NVDA/Meta, FDA Approval for $5B+ TAM, massive Beat+Raise)
- A  (High Conviction):    News = Earnings Beat+Raise, significant product launch, major analyst re-rating
- B  (Exploitable):        News is incremental (Price Target hike, minor contract, unclear policy impact)
- C  (Avoid / Noise):      Sympathy move, vague rumor, low-impact headline, Technical/Flow, unknown catalyst

═══ STEP 4: OUTPUT FORMAT ═══
{ANALYSIS_FORMAT_INSTRUCTIONS}

FINVIZ INDUSTRY THEME (use most specific):
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
Real Estate | REITs | Infrastructure | Others

PEER TICKERS: After your analysis, add a JSON key "peer_tickers": a list of 2-3 ticker symbols in the same industry/theme that could see sympathy moves. These should be real tickers with RS > 80 that are NOT {ticker}.

Respond in this exact JSON format only (no extra text):
{{"category": "<category>", "theme": "<specific catalyst name, e.g. 'Beat & Raise', 'New Contracts', 'Sector Sympathy', 'Technical / Flow'>", "reasoning": "<1 sentence mechanical trigger>", "grade": "<A+|A|B|C>", "finviz_theme": "<industry>", "analysis_detail": "Catalyst: [news facts 2-3 sentences] | Impact: [quantify shift, e.g. adds X% to revenue, de-risks pipeline]", "analysis_details": "<detailed multi-section analysis>", "peer_tickers": ["TICK1", "TICK2"]}}"""

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
        theme            = result.get("theme", category)
        reasoning        = result.get("reasoning", "")
        grade            = result.get("grade", "B")
        if grade not in ("A+", "A", "B", "C"):
            grade = "B"
        finviz_theme     = result.get("finviz_theme", "—")
        analysis_detail  = result.get("analysis_detail", f"Catalyst: {reasoning} | Impact: See analysis.")
        analysis_details = result.get("analysis_details", reasoning)
        raw_peers        = result.get("peer_tickers", [])
        peer_tickers     = [str(p).upper().strip() for p in raw_peers if p and str(p).upper() != ticker][:3]

        hypothesis_label, strategy = HYPOTHESIS_RULES.get(category, HYPOTHESIS_RULES["Others"])
        base_conviction = {"High Conviction (Gap & Go)": 80, "Medium Conviction (RS Hold)": 60,
                           "Caution (Fade Candidate)": 35, "High Risk (Volatility Trap)": 25}.get(hypothesis_label, 50)
        conviction = min(99, int(base_conviction + (rvol - 2) * 3))

        return {
            "category":        category,
            "theme":           theme,
            "reasoning":       reasoning,
            "hypothesis":      f"{hypothesis_label} — {strategy}",
            "conviction":      conviction,
            "grade":           grade,
            "finviz_theme":    finviz_theme,
            "analysis_detail": analysis_detail,
            "analysis_details": analysis_details,
            "peer_tickers":    peer_tickers,
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
    rsn = titles[0] if titles else "No catalyst identified."
    return {
        "category":        cat,
        "theme":           cat,
        "reasoning":       rsn,
        "hypothesis":      f"{label} — {strategy}",
        "conviction":      50,
        "grade":           "B",
        "finviz_theme":    "—",
        "analysis_detail": f"Catalyst: {rsn} | Impact: See analysis.",
        "analysis_details": rsn,
        "peer_tickers":    [],
    }


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def _build_ibkr_scanner() -> list[dict]:
    """
    Mirror table: fetch pre-market movers from IBKR and apply gate logic.
    Returns [] when IBKR is unavailable or on any error.
    """
    try:
        import ibkr_client
        if not ibkr_client.IS_LIVE:
            return []
        raw = ibkr_client.get_premarket_scanner() or []
        results = []
        for item in raw:
            ticker = (item.get("ticker") or "").upper()
            if not ticker:
                continue
            stock = {
                "ticker":        ticker,
                "price":         item.get("last") or 0.0,
                "change_pct":    item.get("change_pct"),
                "volume":        item.get("volume"),
                "rs_placeholder": item.get("rs_placeholder"),
            }
            gates_passed, gates_detail, meets_all = _compute_gates(stock)
            results.append({
                **stock,
                "gates_passed":   gates_passed,
                "gates_detail":   gates_detail,
                "meets_all_gates": meets_all,
            })
        logger.info(f"  IBKR scanner: {len(results)} tickers")
        return results
    except Exception as e:
        logger.warning(f"  IBKR scanner mirror failed: {e}")
        return []


def _load_earnings_today() -> list[dict]:
    """
    Read public/earnings_calendar.json and return today's earnings list.
    Returns [] when file is absent or unreadable.
    """
    try:
        ec_path = Path("public/earnings_calendar.json")
        if not ec_path.exists():
            return []
        data = json.loads(ec_path.read_text(encoding="utf-8"))
        return data.get("today", [])
    except Exception as e:
        logger.warning(f"  Could not load earnings_calendar.json: {e}")
        return []


def main():
    now_et = datetime.now(ET)
    logger.info(f"Pre-Market Gapper Scanner — {now_et.strftime('%Y-%m-%d %H:%M ET')}")

    # ── Earnings strip (read before the slow per-ticker loop) ────────────────
    earnings_today = _load_earnings_today()
    logger.info(f"  Earnings today: {len(earnings_today)} events")

    logger.info("Fetching gappers from TradingView...")
    gappers = fetch_gappers()[:25]
    logger.info(f"  Found {len(gappers)} gappers")

    output = []
    import datetime as dt
    for stock in gappers:
        ticker = stock["ticker"]
        logger.info(f"  Analyzing {ticker} (gap={stock['gap_pct']}% rvol={stock['rvol']}x)...")

        # Finviz fundamentals (float, short interest, daily %)
        fv = fetch_finviz_data(ticker)

        # News headlines — keep sources separate for verification
        cutoff = datetime.now(timezone.utc) - dt.timedelta(hours=24)
        google_headlines = _fetch_google_news(ticker, cutoff, limit=5)
        finviz_headlines = _fetch_finviz_news(ticker, cutoff, limit=5)
        seen = set()
        headlines = []
        for h in google_headlines + finviz_headlines:
            key = h["title"][:60].lower()
            if key not in seen:
                seen.add(key)
                headlines.append(h)
        headlines.sort(key=lambda x: x["date"], reverse=True)
        headlines = headlines[:8]

        # ADR% + last earnings date (single yfinance call)
        fundamentals       = fetch_ticker_fundamentals(ticker)
        last_earnings_date = fundamentals["last_earnings_date"]
        adr_pct            = fundamentals["adr_pct"]
        stock["adr_pct"]   = adr_pct

        # Hard gates (adr_pct now available)
        gates_passed, gates_detail, meets_all_gates = _compute_gates(stock)

        # AI analysis — Momentum Catalyst Intelligence
        analysis = analyze_with_gemini(
            ticker, headlines, stock["rvol"], last_earnings_date,
            avg_dollar_vol=stock.get("avg_dollar_vol", 0),
            adr_pct=adr_pct,
        )

        # Hard Technical Floor — enforce grade cap + compute technical_status
        analysis, technical_status = _apply_technical_floor(
            analysis, stock.get("avg_dollar_vol", 0), adr_pct
        )

        # Tier label
        tier, tier_label = _compute_tier(
            analysis.get("grade", "C"),
            analysis.get("category", "Others"),
            meets_all_gates,
            analysis.get("hypothesis", ""),
        )

        # Fact-check verification (Skeptical Auditor — second Gemini call)
        logger.info(f"  Verifying catalyst for {ticker}...")
        verification = verify_catalyst_accuracy(
            ticker,
            analysis.get("category", "Others"),
            analysis.get("reasoning", ""),
            google_headlines,
            finviz_headlines,
        )

        time.sleep(1)  # rate limit
        output.append({
            **stock,
            **analysis,
            "headlines":        [{"title": h["title"], "source": h.get("source",""), "url": h.get("url","")} for h in headlines[:5]],
            "float_shares":     fv.get("float_shares"),
            "short_float":      fv.get("short_float"),
            "daily_pct":        fv.get("daily_pct"),
            "industry":         analysis.get("finviz_theme", "—"),
            "technical_status": technical_status,
            "verification":     verification,
            "gates_passed":     gates_passed,
            "gates_detail":     gates_detail,
            "meets_all_gates":  meets_all_gates,
            "tier":             tier,
            "tier_label":       tier_label,
        })

    # ── IBKR mirror table ────────────────────────────────────────────────────
    ibkr_scanner = _build_ibkr_scanner()

    result = {
        "scan_time":     now_et.strftime("%Y-%m-%d %H:%M ET"),
        "earnings_today": earnings_today,
        "gappers":       output,
        "ibkr_scanner":  ibkr_scanner,
    }

    out_path = Path("public/gapper_data.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Done! {len(output)} gappers → {out_path}")


if __name__ == "__main__":
    main()
