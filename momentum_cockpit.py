"""
momentum_cockpit.py — Momentum Catalyst Analyst Cockpit
Implements get_ticker_analysis(ticker) integrating Alpaca, Finviz, Google News, and Gemini.

Usage:
    from momentum_cockpit import get_ticker_analysis
    result = get_ticker_analysis("NVDA")

    # Or run as HTTP API:
    python3 momentum_cockpit.py        → serves on http://localhost:5002/analyze/<TICKER>
"""

import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")
ALPACA_API_KEY  = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET   = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = "https://data.alpaca.markets/v2"

# ── Hard Floor Thresholds ────────────────────────────────────────────────────

HARD_FLOOR = {
    "min_price":             2.00,
    "min_avg_vol_10d":     500_000,
    "min_avg_dollar_vol": 100_000_000,   # $100M (20D avg $ vol)
    "min_mkt_cap":        300_000_000,   # $300M
    "min_adr":               4.0,        # ADR(20) %
    "min_pm_vol":          100_000,
}

GRADING_RUBRIC = """
HARD FLOOR QUANTITATIVE FILTER — evaluate these first:
  • Institutional Floor: Min Price $2.00, Min Avg Vol(10D) 500K, Min Avg $Vol(20D) $100M, Market Cap >$300M
  • Volatility Fuel: ADR(20) > 4.0%, Pre-Market Vol > 100K shares

GRADING SCALE (apply after confirming floors):
  A+: Meets ALL floors + ADR > 5% + Structural News ($1B+ Contract, Tier-1 Partnership, landmark FDA approval, massive earnings beat + guidance raise)
  A : Meets ALL floors + ADR > 4% + Strong Catalyst (solid earnings beat/raise, major partnership win, significant policy tailwind)
  B : Meets floors BUT ADR is marginal (4–5%) OR news is incremental (PT hike, minor upgrade, analyst initiation, small beat)
  C : Fails ANY liquidity floor (too thin for institutions) OR catalyst is "Sympathy" (moving on peer news, not own catalyst)

Reasoning rule: state the MECHANICAL TRIGGER in one sentence.
  Good: "Q4 EPS beat by 22%, FY25 guidance raised $0.40 above consensus."
  Bad:  "Stock went up because of positive news."
"""

ANALYSIS_FORMAT = """
Return analysis_detail as a JSON object with exactly two keys:
  "catalyst": 2-3 sentences of specific, factual news (numbers, names, dates). Bold key metrics with **word**.
  "impact":   1-2 sentences of quantitative valuation/revenue impact (e.g., "$2.1B contract vs $800M annual revenue = 2.6x coverage").

Also return analysis_details as a formatted markdown string with 2-3 sections using this format:
• **[Section Title]**
[Body paragraph]

Section titles by category:
- Earnings:              "The 'Beats' (Surprise Factor)" | "The Growth (Momentum)" | "The Guide"
- Upgrade:               "The Firm & Rating"             | "The Thesis"
- FDA:                   "The Drug"                      | "The Significance" | "Risk Profile"
- Thematic Narratives:   "The Narrative"                 | "Explosiveness"
- Government Policy:     "The Policy"                    | "Direct Impact"
- New Contract/Partner:  "Impact"                        | "Strategic Value"
- Institutional/Insider: "The Buyer"                     | "Conviction Signal"
- Others:                "What Happened"                 | "Key Consideration"
"""

CATEGORIES = [
    "Earnings", "Upgrade", "FDA", "Thematic Narratives", "Government Policy",
    "New Contract/Partnership", "Institutional Buying", "Insider Buying", "Others",
]

HYPOTHESIS_RULES = {
    "Earnings":                 ("High Conviction (Gap & Go)",  "Watch for 5-min ORB above PM High."),
    "New Contract/Partnership": ("High Conviction (Gap & Go)",  "Watch for 5-min ORB above PM High."),
    "Thematic Narratives":      ("Medium Conviction (RS Hold)", "Look for dip-buy at 9-EMA/VWAP."),
    "Government Policy":        ("Medium Conviction (RS Hold)", "Look for dip-buy at 9-EMA/VWAP."),
    "Institutional Buying":     ("Medium Conviction (RS Hold)", "Watch for continuation above VWAP."),
    "Insider Buying":           ("Medium Conviction (RS Hold)", "Look for base breakout."),
    "Upgrade":                  ("Caution (Fade Candidate)",    "Low institutional conviction; likely gap-fill."),
    "FDA":                      ("High Risk (Volatility Trap)", "Expect mean reversion; avoid open chase."),
    "Others":                   ("Medium Conviction (RS Hold)", "Monitor price action at open."),
}


# ── Alpaca: ADR + Price Data ─────────────────────────────────────────────────

def fetch_alpaca_bars(ticker: str, days: int = 25) -> dict:
    """Fetch daily bars from Alpaca and compute ADR(20)."""
    if not ALPACA_API_KEY or not ALPACA_SECRET:
        return {}
    try:
        start = (date.today() - timedelta(days=days + 5)).isoformat()
        end   = date.today().isoformat()
        url   = f"{ALPACA_BASE_URL}/stocks/{ticker}/bars"
        resp  = requests.get(url, params={
            "timeframe": "1Day", "start": start, "end": end,
            "limit": days + 5, "feed": "iex",
        }, headers={
            "APCA-API-KEY-ID":     ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET,
        }, timeout=10)
        if resp.status_code in (401, 403):
            logger.warning(f"  Alpaca auth failed for {ticker} — check ALPACA_API_KEY / ALPACA_SECRET_KEY in .env")
            return {}
        resp.raise_for_status()
        bars = resp.json().get("bars", [])
        if len(bars) < 5:
            return {}
        # ADR = avg( (H-L) / midpoint ) * 100 over last 20 bars
        adrs = []
        for bar in bars[-20:]:
            h, l = bar.get("h", 0), bar.get("l", 0)
            if h > 0 and l > 0:
                adrs.append((h - l) / ((h + l) / 2) * 100)
        last = bars[-1]
        return {
            "adr_pct":    round(sum(adrs) / len(adrs), 2) if adrs else None,
            "last_close": round(last.get("c", 0), 2),
            "last_vol":   int(last.get("v", 0)),
        }
    except Exception as e:
        logger.warning(f"  Alpaca bars failed for {ticker}: {e}")
        return {}


# ── Finviz Fundamentals ──────────────────────────────────────────────────────

def fetch_finviz_fundamentals(ticker: str) -> dict:
    """Scrape Finviz quote page for ADR, avg vol, mkt cap, float, short float."""
    result = {
        "adr_pct": None, "avg_vol_10d": None, "avg_dollar_vol": None,
        "mkt_cap": None, "float_shares": None, "short_float": None,
        "daily_pct": None, "sector": None, "industry": None,
    }
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(
            f"https://finviz.com/quote.ashx?t={ticker}&ty=c&p=d&b=1",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", class_="snapshot-table2")
        if not table:
            return result
        cells = table.find_all("td")
        for i in range(0, len(cells) - 1, 2):
            label = cells[i].get_text(strip=True)
            value = cells[i + 1].get_text(strip=True)
            try:
                if label == "ATR":              result["adr_pct"] = float(value)
                elif label in ("Shs Float", "Float"): result["float_shares"] = value
                elif label == "Short Float":    result["short_float"] = value
                elif label == "Change":         result["daily_pct"] = float(value.replace("%", "").replace("+", ""))
                elif label == "Avg Volume":
                    v = value.upper()
                    result["avg_vol_10d"] = int(float(v.replace("M","").replace("K","")) *
                                               (1_000_000 if "M" in v else 1_000 if "K" in v else 1))
                elif label == "Market Cap":
                    v = value.upper()
                    result["mkt_cap"] = int(float(v.replace("B","").replace("M","").replace("T","")) *
                                           (1e12 if "T" in v else 1e9 if "B" in v else 1e6))
                elif label == "Sector":         result["sector"] = value
                elif label == "Industry":       result["industry"] = value
            except (ValueError, TypeError):
                pass
        # avg dollar vol estimate
        if result["avg_vol_10d"] and result["last_close"] if "last_close" in result else True:
            pass  # filled later with price * avg_vol
        return result
    except Exception as e:
        logger.warning(f"  Finviz fundamentals failed for {ticker}: {e}")
        return result


# ── Google News + Finviz News ────────────────────────────────────────────────

def fetch_news(ticker: str) -> list[dict]:
    """Fetch recent headlines from Google News RSS + Finviz, merged and deduped."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    articles = []

    # Google News RSS
    try:
        from xml.etree import ElementTree as ET
        from email.utils import parsedate_to_datetime
        resp = requests.get(
            f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
        )
        root = ET.fromstring(resp.content)
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            pub   = item.findtext("pubDate", "")
            if not title:
                continue
            try:
                dt = parsedate_to_datetime(pub)
                if dt < cutoff:
                    continue
                articles.append({"title": title, "date": dt.strftime("%Y-%m-%d %H:%M UTC"), "source": "Google"})
            except Exception:
                pass
            if len(articles) >= 5:
                break
    except Exception:
        pass

    # Finviz News
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(
            f"https://finviz.com/quote.ashx?t={ticker}&p=d",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=10,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", id="news-table")
        last_date = None
        seen_titles = {a["title"][:60].lower() for a in articles}
        if table:
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                date_str  = cells[0].get_text(strip=True)
                title_tag = cells[1].find("a")
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                if title[:60].lower() in seen_titles:
                    continue
                if len(date_str) > 8:
                    last_date = date_str
                full = last_date or date_str
                try:
                    m = re.match(r"(\w{3}-\d{2}-\d{2,4})\s+(\d{1,2}:\d{2}(?:AM|PM))", full)
                    if not m:
                        continue
                    dp, tp = m.group(1), m.group(2)
                    yp = dp.split("-")[2]
                    if len(yp) == 2:
                        dp = dp[:-2] + "20" + yp
                    dt = datetime.strptime(f"{dp} {tp}", "%b-%d-%Y %I:%M%p").replace(tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue
                    articles.append({"title": title, "date": dt.strftime("%Y-%m-%d %H:%M UTC"), "source": "Finviz"})
                    seen_titles.add(title[:60].lower())
                except Exception:
                    pass
                if len(articles) >= 8:
                    break
    except Exception:
        pass

    articles.sort(key=lambda x: x["date"], reverse=True)
    return articles[:8]


# ── Technical Status (Hard Floor) ────────────────────────────────────────────

def compute_technical_status(price: float, avg_vol_10d: int, avg_dollar_vol: float,
                              mkt_cap: int, pm_vol: int) -> str:
    """Return 'Pass' if all institutional floor criteria are met, else 'Fail'."""
    if price < HARD_FLOOR["min_price"]:             return "Fail"
    if avg_vol_10d < HARD_FLOOR["min_avg_vol_10d"]: return "Fail"
    if avg_dollar_vol < HARD_FLOOR["min_avg_dollar_vol"]: return "Fail"
    if mkt_cap < HARD_FLOOR["min_mkt_cap"]:         return "Fail"
    if pm_vol < HARD_FLOOR["min_pm_vol"]:           return "Fail"
    return "Pass"


# ── Gemini Analysis ──────────────────────────────────────────────────────────

def _run_gemini(ticker: str, headlines: list[dict], stock_ctx: dict) -> dict:
    if not GEMINI_API_KEY:
        return _fallback(ticker, headlines)
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        hl_text = "\n".join(f"- [{h['date']}] {h['title']}" for h in headlines) or "No recent headlines."
        prompt = f"""You are a Senior Momentum Analyst. Today: {date.today().isoformat()}.

Ticker: {ticker}
Price: ${stock_ctx.get('price', '?')}  Gap: +{stock_ctx.get('gap_pct', '?')}%
PM Volume: {stock_ctx.get('pm_vol', '?'):,}  RVOL: {stock_ctx.get('rvol', '?')}x
ADR(20): {stock_ctx.get('adr_pct', 'N/A')}%  Avg Vol(10D): {stock_ctx.get('avg_vol_10d', '?'):,}
Avg $Vol(20D): ${stock_ctx.get('avg_dollar_vol', 0)/1e6:.1f}M  Mkt Cap: ${stock_ctx.get('mkt_cap', 0)/1e9:.2f}B

Recent headlines (last 24h):
{hl_text}

{GRADING_RUBRIC}

CATEGORIES (choose exactly one):
Earnings | Upgrade | FDA | Thematic Narratives | Government Policy | New Contract/Partnership | Institutional Buying | Insider Buying | Others

FINVIZ THEME — choose most specific match:
AI Compute | AI Cloud | AI Models | AI Networking | AI Security | AI Robotics | AI Energy |
Semiconductors - Compute | Semiconductors - Memory | Semiconductors - Analog | Semiconductors - Foundries | Semiconductors - Design Tools |
Cloud Hyperscalers | Cloud SaaS | Cybersecurity - Zero Trust | Cybersecurity - Endpoint | Cybersecurity - Network |
Fintech - Payments | Fintech - Neobanks | Fintech - Lending | Fintech - Trading |
Clean Energy - Solar | Clean Energy - Wind | Clean Energy - Nuclear | Clean Energy - Grid |
Electric Vehicles | EV Batteries | Biotech - Oncology | Biotech - Rare Disease | Biotech - Gene Therapy |
Pharma - Large Cap | Medical Devices | Digital Health | Defense & Aerospace | Space | Drones |
Consumer - E-Commerce | Consumer - Streaming | Consumer - Gaming |
Energy - Oil & Gas | Materials - Metals & Mining | Infrastructure | Others

{ANALYSIS_FORMAT}

Respond ONLY with valid JSON (no markdown fences):
{{
  "category": "<category>",
  "reasoning": "<one mechanical trigger sentence>",
  "grade": "<A+|A|B|C>",
  "theme": "<finviz theme>",
  "analysis_detail": {{
    "catalyst": "<2-3 sentences of specific facts with bold **metrics**>",
    "impact": "<1-2 sentences of quantitative valuation impact>"
  }},
  "analysis_details": "<formatted markdown string with • **Title** sections>"
}}"""

        resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        text = resp.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())

        category = result.get("category", "Others")
        if category not in CATEGORIES:
            category = "Others"
        grade = result.get("grade", "B")
        if grade not in ("A+", "A", "B", "C"):
            grade = "B"
        hyp_label, strategy = HYPOTHESIS_RULES.get(category, HYPOTHESIS_RULES["Others"])
        base_conv = {"High Conviction (Gap & Go)": 80, "Medium Conviction (RS Hold)": 60,
                     "Caution (Fade Candidate)": 35, "High Risk (Volatility Trap)": 25}.get(hyp_label, 50)
        rvol = stock_ctx.get("rvol", 2)
        conviction = min(99, int(base_conv + (rvol - 2) * 3))

        return {
            "category":        category,
            "reasoning":       result.get("reasoning", ""),
            "grade":           grade,
            "theme":           result.get("theme", "Others"),
            "analysis_detail": result.get("analysis_detail", {"catalyst": "", "impact": ""}),
            "analysis_details": result.get("analysis_details", ""),
            "hypothesis":      f"{hyp_label} — {strategy}",
            "conviction":      conviction,
        }
    except Exception as e:
        logger.warning(f"  Gemini failed for {ticker}: {e}")
        return _fallback(ticker, headlines)


def _fallback(ticker: str, headlines: list[dict]) -> dict:
    titles = [h.get("title", h) if isinstance(h, dict) else h for h in headlines]
    text = " ".join(titles).lower()
    if any(w in text for w in ["earnings", "beat", "revenue", "eps"]):         cat = "Earnings"
    elif any(w in text for w in ["fda", "clinical", "approval", "drug"]):       cat = "FDA"
    elif any(w in text for w in ["upgrade", "price target", "analyst"]):        cat = "Upgrade"
    elif any(w in text for w in ["contract", "partnership", "deal"]):           cat = "New Contract/Partnership"
    elif any(w in text for w in ["policy", "government", "tariff"]):            cat = "Government Policy"
    else:                                                                         cat = "Others"
    hyp_label, strategy = HYPOTHESIS_RULES.get(cat, HYPOTHESIS_RULES["Others"])
    reason = titles[0] if titles else "No catalyst identified."
    return {
        "category": cat, "reasoning": reason, "grade": "B", "theme": "Others",
        "analysis_detail": {"catalyst": reason, "impact": "Quantitative impact unavailable."},
        "analysis_details": f"• **What Happened**\n{reason}",
        "hypothesis": f"{hyp_label} — {strategy}", "conviction": 50,
    }


# ── Main Entry Point ─────────────────────────────────────────────────────────

def get_ticker_analysis(ticker: str, gap_pct: float = 0.0, pm_vol: int = 0,
                        rvol: float = 0.0, price: float = 0.0, mkt_cap: int = 0) -> dict:
    """
    Full analysis for a ticker. Returns the Analyst Cockpit JSON schema.
    Pass known values (from TradingView screener) to avoid redundant fetches.
    """
    ticker = ticker.upper().strip()
    logger.info(f"[{ticker}] Starting Analyst Cockpit analysis…")

    # 1. Alpaca bars → ADR(20)
    alpaca = fetch_alpaca_bars(ticker)
    adr_pct = alpaca.get("adr_pct")

    # 2. Finviz fundamentals
    fv = fetch_finviz_fundamentals(ticker)
    if not price and fv.get("last_close"):
        price = fv["last_close"]
    if not mkt_cap and fv.get("mkt_cap"):
        mkt_cap = fv["mkt_cap"]
    avg_vol_10d  = fv.get("avg_vol_10d") or 0
    avg_dvol     = price * avg_vol_10d  # 10D avg $ vol estimate
    if not adr_pct:
        adr_pct = fv.get("adr_pct")

    # 3. News headlines
    headlines = fetch_news(ticker)

    # 4. Technical status (Hard Floor)
    technical_status = compute_technical_status(
        price        = price,
        avg_vol_10d  = avg_vol_10d,
        avg_dollar_vol = avg_dvol,
        mkt_cap      = mkt_cap,
        pm_vol       = pm_vol,
    )

    # 5. Gemini analysis
    stock_ctx = {
        "price": price, "gap_pct": gap_pct, "pm_vol": pm_vol, "rvol": rvol,
        "adr_pct": adr_pct, "avg_vol_10d": avg_vol_10d,
        "avg_dollar_vol": avg_dvol, "mkt_cap": mkt_cap,
    }
    ai = _run_gemini(ticker, headlines, stock_ctx)

    # 6. Industry from Finviz
    industry = fv.get("industry") or ai.get("theme", "—")

    return {
        "ticker":           ticker,
        "price":            price,
        "gap_pct":          gap_pct,
        "pm_volume":        pm_vol,
        "rvol":             rvol,
        "adr_pct":          adr_pct,
        "avg_vol_10d":      avg_vol_10d,
        "avg_dollar_vol":   avg_dvol,
        "mkt_cap":          mkt_cap,
        "float_shares":     fv.get("float_shares"),
        "short_float":      fv.get("short_float"),
        "daily_pct":        fv.get("daily_pct"),
        "industry":         industry,
        "theme":            ai.get("theme", "Others"),
        "finviz_theme":     ai.get("theme", "Others"),
        "grade":            ai["grade"],
        "category":         ai["category"],
        "reasoning":        ai["reasoning"],
        "analysis_detail":  ai["analysis_detail"],
        "analysis_details": ai["analysis_details"],
        "hypothesis":       ai["hypothesis"],
        "conviction":       ai["conviction"],
        "headlines":        [h["title"] if isinstance(h, dict) else h for h in headlines[:3]],
        "technical_status": technical_status,
    }


# ── HTTP API Server ───────────────────────────────────────────────────────────

class AnalystHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/analyze/"):
            ticker = path.split("/analyze/")[1].upper()
            try:
                result = get_ticker_analysis(ticker)
                body = json.dumps(result, ensure_ascii=False, indent=2)
                self.send_response(200)
            except Exception as e:
                body = json.dumps({"error": str(e)})
                self.send_response(500)
        elif path == "/health":
            body = json.dumps({"status": "ok"})
            self.send_response(200)
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode())


if __name__ == "__main__":
    port = 5002
    server = HTTPServer(("localhost", port), AnalystHandler)
    print(f"Analyst Cockpit API running on http://localhost:{port}")
    print(f"  GET /analyze/<TICKER>  → full analysis JSON")
    server.serve_forever()
