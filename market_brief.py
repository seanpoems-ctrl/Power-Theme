#!/usr/bin/env python3
"""
Market Brief Generator
Fetches RSS news + yfinance data → Gemini analysis → public/market_brief.json
Runs twice daily: 8:00 AM ET (pre-market) and 5:00 PM ET (post-market)
"""

import json
import os
from dotenv import load_dotenv
load_dotenv()
import requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

ET_TZ = ZoneInfo("America/New_York")

RSS_FEEDS = [
    ("CNBC",             "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("WSJ Markets",      "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews"),
    ("FT Markets",       "https://www.ft.com/markets?format=rss"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MarketBriefBot/1.0)"}


def fetch_rss(name: str, url: str, max_articles: int = 8) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    articles = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            pub_str = item.findtext("pubDate", "")
            if not title:
                continue
            try:
                pub_dt = parsedate_to_datetime(pub_str)
                if pub_dt < cutoff:
                    continue
                date_label = pub_dt.strftime("%b %d %H:%M UTC")
            except Exception:
                date_label = "recent"
            articles.append({"source": name, "title": title, "date": date_label})
            if len(articles) >= max_articles:
                break
    except Exception as e:
        print(f"  RSS fetch failed [{name}]: {e}")
    return articles


def fetch_market_data() -> dict:
    """Fetch VIX, SPY, QQQ, 10Y yield via yfinance"""
    try:
        import yfinance as yf
        tickers = yf.download(
            ["^VIX", "SPY", "QQQ", "^TNX"],
            period="2d", interval="1d", progress=False, auto_adjust=True
        )
        closes = tickers["Close"].iloc[-1]
        prev   = tickers["Close"].iloc[-2]
        def chg(sym):
            try:
                return round(((closes[sym] - prev[sym]) / prev[sym]) * 100, 2)
            except Exception:
                return None
        return {
            "vix":       round(float(closes["^VIX"]), 2),
            "spy":       round(float(closes["SPY"]), 2),
            "spy_chg":   chg("SPY"),
            "qqq":       round(float(closes["QQQ"]), 2),
            "qqq_chg":   chg("QQQ"),
            "yield_10y": round(float(closes["^TNX"]), 2),
        }
    except Exception as e:
        print(f"  Market data fetch failed: {e}")
        return {}


def analyze_with_gemini(articles: list[dict], market_data: dict, session: str) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY not set"}

    headlines_text = "\n".join(
        f"[{a['source']} | {a['date']}] {a['title']}" for a in articles
    )

    mkt_text = ""
    if market_data:
        mkt_text = f"""
Current Market Data:
- VIX: {market_data.get('vix', 'N/A')} (Fear gauge; >20 = elevated)
- SPY: ${market_data.get('spy', 'N/A')} ({market_data.get('spy_chg', 'N/A')}% change)
- QQQ: ${market_data.get('qqq', 'N/A')} ({market_data.get('qqq_chg', 'N/A')}% change)
- 10Y Treasury Yield: {market_data.get('yield_10y', 'N/A')}%
"""

    prompt = f"""You are a senior institutional equity strategist writing a {session} market brief for US swing traders.

{mkt_text}

News headlines from the last 24 hours:
{headlines_text}

Generate a structured market brief in JSON format. Return ONLY valid JSON, no markdown.

{{
  "session": "{session}",
  "sentiment": "Bullish" | "Neutral" | "Bearish",
  "sentiment_reason": "one sentence why",
  "macro_news": [
    {{
      "title": "section title (e.g. Fed Policy, VIX & Volatility, Geopolitics)",
      "summary": "2-3 sentences with key numbers bolded using **number** markdown syntax"
    }}
  ],
  "sector_themes": [
    {{
      "theme": "sector name",
      "reason": "1-2 sentences why it's in focus today"
    }}
  ],
  "key_levels": {{
    "spy_support": "price level to watch",
    "catalyst_today": "main event/data driving price action today"
  }}
}}

Rules:
- macro_news: exactly 3 items covering Fed/rates, VIX/volatility, and geopolitics or economic data
- sector_themes: exactly 2 most discussed sectors
- Be specific with numbers (%, price levels, dates)
- Sentiment must reflect actual data, not default to Neutral
"""

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        raw = response.text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"  Gemini analysis failed: {e}")
        return {"error": str(e)}


def main():
    now_et = datetime.now(ET_TZ)
    hour = now_et.hour
    session = "Pre-Market" if hour < 12 else "Post-Market"

    print(f"Market Brief — {now_et.strftime('%Y-%m-%d %H:%M ET')} [{session}]")

    # Fetch all RSS feeds
    all_articles = []
    for name, url in RSS_FEEDS:
        print(f"  Fetching {name}...")
        all_articles.extend(fetch_rss(name, url))

    print(f"  Total articles: {len(all_articles)}")

    # Fetch market data
    print("  Fetching market data...")
    market_data = fetch_market_data()

    # Gemini analysis
    print("  Running Gemini analysis...")
    analysis = analyze_with_gemini(all_articles, market_data, session)

    # Build output
    result = {
        "generated_at": now_et.strftime("%Y-%m-%d %H:%M ET"),
        "session": session,
        "market_data": market_data,
        "brief": analysis,
        "article_count": len(all_articles),
    }

    out_path = Path("public/market_brief.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Done → {out_path}")


if __name__ == "__main__":
    main()
