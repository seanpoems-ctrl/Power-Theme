#!/usr/bin/env python3
from __future__ import annotations
"""
Emergency News Monitor — High-Impact Breaking News Alerts

Runs every 30 min on weekdays via GitHub Actions.
Sources: CNBC, Yahoo Finance, MarketWatch RSS + Trump X/TruthSocial posts

Logic:
  1. Fetch headlines from the last 2 hours
  2. Grade each 1-10 via Gemini for market impact
  3. Alert only if grade >= 8
  4. Rolling store: keep last 6 alerts, expire after 12 hours

Output: public/breaking_news.json
Telegram: Immediate push per new alert
"""

import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

ET_TZ           = ZoneInfo("America/New_York")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT   = os.getenv("TELEGRAM_CHAT_ID", "")

ALERT_THRESHOLD = 8    # Gemini grade >= 8 triggers alert
HOURS_LOOKBACK  = 2    # Only consider headlines from last N hours
MAX_ALERTS      = 6    # Keep at most N alerts in rolling store
ALERT_TTL_HOURS = 12   # Expire alerts older than N hours

RSS_FEEDS = [
    ("CNBC",        "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("CNBC Markets","https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("Yahoo Finance","https://finance.yahoo.com/news/rssindex"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
]

# Nitter instances to try for Trump's X feed (tried in order, first success wins)
NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
    "https://nitter.kavin.rocks",
    "https://nitter.cz",
    "https://nitter.net",
    "https://nitter.it",
]

# Google News RSS queries targeting Trump market-moving statements
TRUMP_GNEWS_QUERIES = [
    "Trump tariff",
    "Trump military strike",
    "Trump sanctions",
    "Trump federal reserve",
    "Trump announces",
    "Trump threatens",
]


# ── RSS Fetch ───────────────────────────────────────────────────────────────

def fetch_headlines() -> list[dict]:
    from email.utils import parsedate_to_datetime
    headlines = []
    now_utc = datetime.now(timezone.utc)

    for source, url in RSS_FEEDS:
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            root = ET.fromstring(r.content)

            for item in root.findall(".//item")[:15]:
                title_el   = item.find("title")
                pubdate_el = item.find("pubDate")
                if title_el is None:
                    continue
                title = (title_el.text or "").strip()
                if not title:
                    continue

                # Recency filter
                if pubdate_el is not None and pubdate_el.text:
                    try:
                        pub_dt = parsedate_to_datetime(pubdate_el.text)
                        age_h  = (now_utc - pub_dt.astimezone(timezone.utc)).total_seconds() / 3600
                        if age_h > HOURS_LOOKBACK:
                            continue
                    except Exception:
                        pass

                headlines.append({"title": title, "source": source})
        except Exception as e:
            print(f"  RSS [{source}]: {e}")

    # Deduplicate
    seen, unique = set(), []
    for h in headlines:
        key = h["title"][:60].lower()
        if key not in seen:
            seen.add(key)
            unique.append(h)

    print(f"  Fetched {len(unique)} unique headlines (last {HOURS_LOOKBACK}h)")
    return unique


# ── Trump / X Posts ─────────────────────────────────────────────────────────

def fetch_trump_posts() -> list[dict]:
    """Fetch Trump's X posts via Nitter RSS, falling back to Truth Social RSS."""
    from email.utils import parsedate_to_datetime
    now_utc = datetime.now(timezone.utc)
    posts   = []

    def _parse_feed(content: bytes, source_label: str) -> list[dict]:
        found = []
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return found
        for item in root.findall(".//item")[:15]:
            title_el   = item.find("title")
            pubdate_el = item.find("pubDate")
            if title_el is None:
                continue
            text = (title_el.text or "").strip()
            if not text or text.lower().startswith("rt by"):
                continue  # skip retweets
            # Strip "R to @handle: " reply prefix from nitter titles
            text = re.sub(r"^R to @\w+:\s*", "", text).strip()
            if not text:
                continue
            # Recency filter
            if pubdate_el is not None and pubdate_el.text:
                try:
                    pub_dt = parsedate_to_datetime(pubdate_el.text)
                    age_h  = (now_utc - pub_dt.astimezone(timezone.utc)).total_seconds() / 3600
                    if age_h > HOURS_LOOKBACK:
                        continue
                except Exception:
                    pass
            found.append({"title": text, "source": source_label})
        return found

    # 1. Try nitter instances
    for base in NITTER_INSTANCES:
        url = f"{base}/realDonaldTrump/rss"
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                posts = _parse_feed(r.content, "Trump/X")
                if posts:
                    print(f"  Trump/X: {len(posts)} recent posts via {base}")
                    return posts
        except Exception:
            continue

    # 2. Fallback: Truth Social RSS
    try:
        r = requests.get(
            "https://truthsocial.com/@realDonaldTrump.rss",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code == 200:
            posts = _parse_feed(r.content, "Trump/TruthSocial")
            if posts:
                print(f"  Trump/TruthSocial: {len(posts)} recent posts")
    except Exception as e:
        print(f"  Trump/TruthSocial: {e}")

    # 3. Fallback: Google News RSS search for Trump statements
    if not posts:
        posts = _fetch_trump_gnews()

    if not posts:
        print("  Trump: no posts retrieved (all sources failed or nothing recent)")
    return posts


def _fetch_trump_gnews() -> list[dict]:
    """Google News RSS search for Trump market-moving statements."""
    from email.utils import parsedate_to_datetime
    now_utc = datetime.now(timezone.utc)
    found   = []
    seen    = set()

    for query in TRUMP_GNEWS_QUERIES:
        url = (
            "https://news.google.com/rss/search"
            f"?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
        )
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:8]:
                title_el   = item.find("title")
                pubdate_el = item.find("pubDate")
                if title_el is None:
                    continue
                title = (title_el.text or "").strip()
                if not title:
                    continue
                key = title[:60].lower()
                if key in seen:
                    continue
                seen.add(key)
                # Recency filter
                if pubdate_el is not None and pubdate_el.text:
                    try:
                        pub_dt = parsedate_to_datetime(pubdate_el.text)
                        age_h  = (now_utc - pub_dt.astimezone(timezone.utc)).total_seconds() / 3600
                        if age_h > HOURS_LOOKBACK:
                            continue
                    except Exception:
                        pass
                found.append({"title": title, "source": "Trump/GoogleNews"})
        except Exception as e:
            print(f"  Trump GNews [{query}]: {e}")

    if found:
        print(f"  Trump/GoogleNews: {len(found)} recent articles")
    return found


# ── Gemini Grading ──────────────────────────────────────────────────────────

def grade_with_gemini(headlines: list[dict]) -> list[dict]:
    if not GEMINI_API_KEY or not headlines:
        return []

    headlines_text = "\n".join(
        f"{i+1}. [{h['source']}] {h['title']}"
        for i, h in enumerate(headlines[:30])
    )

    prompt = f"""You are a financial news analyst for a professional trading desk.

Review these recent financial headlines and social media posts. Grade each 1-10 for IMMEDIATE MARKET IMPACT:
- 9-10: Market-moving event — Fed emergency action, geopolitical crisis, circuit breakers, major bankruptcy,
        Trump announcing NEW tariffs / military strikes / sanctions / Fed firing threats
- 8:    High-impact — significant policy shift, major earnings shock, sector-wide catalyst,
        Trump threatening tariffs / commenting on war / market or trade statements
- 7 and below: Routine news, old tariff rehash, general commentary — OMIT from output

IMPORTANT: Posts from Trump/X or Trump/TruthSocial about war, tariffs, sanctions, or the Federal Reserve
are almost always grade 8-10 due to direct presidential market impact. Grade them aggressively.

Headlines:
{headlines_text}

Return ONLY a valid JSON array. Include ONLY headlines graded 8+.
For each qualifying headline:
{{
  "headline": "exact headline text",
  "source": "source name",
  "grade": 9,
  "analysis": "2-3 sentences on market and macro context — what this means for the broader market",
  "impact": "2-3 sentences on direct trading implications — which sectors, stocks, or asset classes are affected and how to position"
}}

If NO headlines score 8+, return: []
Output ONLY the JSON array. No markdown, no explanation."""

    try:
        from google import genai
        client   = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt
        )
        raw    = response.text.strip().replace("```json", "").replace("```", "").strip()
        alerts = json.loads(raw)
        if not isinstance(alerts, list):
            return []
        return [a for a in alerts if isinstance(a, dict) and a.get("grade", 0) >= ALERT_THRESHOLD]
    except Exception as e:
        print(f"  Gemini grading failed: {e}")
        return []


# ── State Management ────────────────────────────────────────────────────────

def load_existing() -> dict:
    path = Path("public/breaking_news.json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"alerts": []}


def is_duplicate(headline: str, existing: dict) -> bool:
    known = {a.get("headline", "")[:60].lower() for a in existing.get("alerts", [])}
    return headline[:60].lower() in known


def expire_old_alerts(alerts: list[dict]) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ALERT_TTL_HOURS)
    fresh  = []
    for a in alerts:
        ts = a.get("timestamp")
        if not ts:
            fresh.append(a)
            continue
        try:
            dt = datetime.fromisoformat(ts)
            if dt.astimezone(timezone.utc) >= cutoff:
                fresh.append(a)
        except Exception:
            fresh.append(a)
    return fresh


# ── Telegram ────────────────────────────────────────────────────────────────

_MD2 = re.compile(r"([_*\[\]()~`>#+=|{}.!\-\\])")

def _esc(t: str) -> str:
    return _MD2.sub(r"\\\1", str(t))


def send_telegram(alert: dict) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print("  Telegram: not configured — skipping")
        return False

    grade    = alert.get("grade", 0)
    headline = alert.get("headline", "")
    source   = alert.get("source", "")
    analysis = alert.get("analysis", "")
    impact   = alert.get("impact", "")

    lines = [
        "🚨 *BREAKING NEWS ALERT* 🚨",
        f"*Grade: {grade}/10*",
        "",
        f"*{_esc(headline.upper())}*",
        f"_{_esc(source)}_",
        "",
    ]
    if analysis:
        lines += ["*Analysis*", _esc(str(analysis)), ""]
    if impact:
        lines += ["*Impact*", _esc(str(impact))]

    text = "\n".join(lines)[:4090]
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": text,
                  "parse_mode": "MarkdownV2", "disable_web_page_preview": True},
            timeout=15,
        )
        r.raise_for_status()
        print(f"  Telegram: sent — Grade {grade}")
        return True
    except Exception as e:
        print(f"  Telegram failed: {e}")
        return False


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    now_et = datetime.now(ET_TZ)
    print(f"Emergency News Monitor — {now_et.strftime('%Y-%m-%d %H:%M ET')}")

    existing = load_existing()

    print("  [1/3] Fetching RSS headlines…")
    headlines = fetch_headlines()

    print("  [2/3] Fetching Trump/X posts…")
    trump_posts = fetch_trump_posts()
    headlines = trump_posts + headlines  # Trump posts first (higher priority)

    new_alerts: list[dict] = []
    if headlines:
        print(f"  [3/3] Grading {len(headlines)} items with Gemini…")
        candidates = grade_with_gemini(headlines)
        new_alerts = [
            a for a in candidates
            if not is_duplicate(a.get("headline", ""), existing)
        ]
    else:
        print("  [2/2] No recent headlines — skipping Gemini")

    # Timestamp and send Telegram for each new alert
    ts = now_et.isoformat()
    for a in new_alerts:
        a["timestamp"] = ts
        send_telegram(a)

    # Merge, expire old, cap
    merged = new_alerts + expire_old_alerts(existing.get("alerts", []))
    merged = merged[:MAX_ALERTS]

    result = {
        "last_checked": now_et.strftime("%B %d, %Y (%H:%M EST)"),
        "has_alert":    len(merged) > 0,
        "alerts":       merged,
    }

    out = Path("public/breaking_news.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    if new_alerts:
        max_grade = max(a.get("grade", 0) for a in new_alerts)
        print(f"  ⚡ {len(new_alerts)} NEW alert(s) — top grade: {max_grade}/10")
    else:
        print(f"  No new high-impact headlines (threshold: {ALERT_THRESHOLD}+)")
    print(f"  Saved → {out}  ({len(merged)} active alerts)")


if __name__ == "__main__":
    main()
