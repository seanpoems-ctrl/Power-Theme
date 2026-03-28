#!/usr/bin/env python3
from __future__ import annotations
"""
Emergency News Monitor — High-Impact Breaking News Alerts

Runs every 5 min on weekdays via GitHub Actions.
Sources: CNBC, Yahoo Finance, Finviz

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

ALERT_THRESHOLD = 9    # Gemini grade >= 9 triggers alert
HOURS_LOOKBACK  = 2    # Only consider headlines from last N hours
MAX_ALERTS      = 6    # Keep at most N alerts in rolling store
ALERT_TTL_HOURS = 12   # Expire alerts older than N hours

RSS_FEEDS = [
    ("CNBC",        "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("CNBC Markets","https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("Yahoo Finance","https://finance.yahoo.com/news/rssindex"),
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
                pub_time = None
                if pubdate_el is not None and pubdate_el.text:
                    try:
                        pub_dt = parsedate_to_datetime(pubdate_el.text)
                        age_h  = (now_utc - pub_dt.astimezone(timezone.utc)).total_seconds() / 3600
                        if age_h > HOURS_LOOKBACK:
                            continue
                        pub_time = pub_dt.astimezone(ET_TZ).isoformat()
                    except Exception:
                        pass

                headlines.append({"title": title, "source": source, "pub_time": pub_time})
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


# ── Finviz News Scraper ──────────────────────────────────────────────────────

def fetch_finviz_headlines() -> list[dict]:
    """Scrape market news from Finviz news page (no RSS available)."""
    from bs4 import BeautifulSoup
    now_utc = datetime.now(timezone.utc)
    headlines = []

    try:
        r = requests.get(
            "https://finviz.com/news.ashx",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        r.raise_for_status()
        soup  = BeautifulSoup(r.content, "html.parser")
        table = soup.find("table", id="news-table")
        if not table:
            print("  Finviz: news-table not found")
            return []

        current_date = None
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            time_text = cells[0].get_text(strip=True)
            # Full date rows look like "Mar-24-26 07:23AM"; time-only rows "07:23AM"
            if re.match(r"[A-Z][a-z]{2}-\d{2}-\d{2}", time_text):
                current_date = time_text[:9]      # "Mar-24-26"
                time_part    = time_text[10:].strip()
            else:
                time_part    = time_text

            # Recency filter
            pub_time = None
            if current_date and time_part:
                try:
                    pub_dt = datetime.strptime(
                        f"{current_date} {time_part}", "%b-%d-%y %I:%M%p"
                    ).replace(tzinfo=ET_TZ)
                    if (now_utc - pub_dt.astimezone(timezone.utc)).total_seconds() / 3600 > HOURS_LOOKBACK:
                        continue
                    pub_time = pub_dt.isoformat()
                except Exception:
                    pass

            a_tag = cells[1].find("a")
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            if title:
                headlines.append({"title": title, "source": "Finviz", "pub_time": pub_time})

    except Exception as e:
        print(f"  Finviz: {e}")

    print(f"  Finviz: {len(headlines)} headline(s) (last {HOURS_LOOKBACK}h)")
    return headlines


# ── Gemini Grading ──────────────────────────────────────────────────────────

ALLOWED_SOURCES = {"CNBC", "CNBC Markets", "Yahoo Finance", "Finviz"}


def grade_with_gemini(headlines: list[dict]) -> list[dict]:
    if not GEMINI_API_KEY or not headlines:
        return []

    # Map normalized headline → original feed source so we can restore it after Gemini
    source_map = {h["title"]: h["source"] for h in headlines}

    headlines_text = "\n".join(
        f"{i+1}. [{h['source']}] {h['title']}"
        for i, h in enumerate(headlines[:30])
    )

    prompt = f"""You are a senior macro trader. Your job is to flag only the most extreme, \
market-moving headlines. Be brutally strict.

Grade each headline 1–10 for IMMEDIATE broad market impact:

GRADE 9–10 (alert-worthy — include these):
  - Fed emergency action / surprise rate hike or cut outside scheduled FOMC
  - Declared war, military strikes between major nations (US, China, Russia, Iran, Israel, NATO)
  - Nuclear threat escalation (confirmed, not speculative)
  - President / head of state announces attack, invasion, or military operation
  - Major country sovereign default or IMF bailout (G20 level)
  - Surprise tariff announcement affecting entire sectors (e.g. 25% on all semiconductors)
  - Stock market circuit breaker triggered
  - Major central bank emergency meeting / policy reversal
  - Catastrophic natural disaster disrupting global supply chain

GRADE 8 (include only if truly sector-wide, not company-specific):
  - FOMC decision that surprises consensus by ≥25bps
  - G7/G20 coordinated sanctions on a major economy
  - Confirmed ceasefire collapse in an active war zone
  - Major country election result that reverses geopolitical alignment

AUTOMATICALLY GRADE 7 OR BELOW — DO NOT INCLUDE:
  - Any individual company earnings, upgrades, downgrades, or price targets
  - "Concerns about", "fears of", "expected to", speculative/opinion headlines
  - Oil price moves unless caused by a declared supply disruption
  - Economic data releases (CPI, PPI, jobs) unless they cause an emergency Fed response
  - Any headline about a single stock, ETF, or sector fund

Headlines:
{headlines_text}

Return ONLY a valid JSON array. Include ONLY headlines graded 8+.
For each use EXACTLY the source label shown in brackets:
{{
  "headline": "exact headline text",
  "source": "source label from brackets",
  "grade": 9,
  "analysis": "2–3 sentences: macro context and why this matters globally",
  "impact": "2–3 sentences: which asset classes, sectors move and in which direction"
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

        result = []
        for a in alerts:
            if not isinstance(a, dict) or a.get("grade", 0) < ALERT_THRESHOLD:
                continue
            # Restore original feed source (overrides anything Gemini may have derived)
            original_source = source_map.get(a.get("headline", ""), a.get("source", ""))
            a["source"] = original_source
            # Drop alerts whose feed source is not in our approved list
            if a["source"] not in ALLOWED_SOURCES:
                print(f"  Filtered non-approved source: {a['source']}")
                continue
            result.append(a)
        return result
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
    return {"alerts": [], "seen_keys": []}


def _headline_key(text: str) -> str:
    """Normalize a headline to a dedup key (strip punctuation, lowercase, first 100 chars)."""
    import string
    t = text.lower()[:100]
    t = "".join(c for c in t if c not in string.punctuation)
    return " ".join(t.split())


def is_seen(headline: str, existing: dict) -> bool:
    """True if this headline was already processed in a recent run."""
    known = {s["k"] for s in existing.get("seen_keys", [])}
    return _headline_key(headline) in known


def is_duplicate_alert(headline: str, existing: dict) -> bool:
    """True if this headline already exists as an active alert."""
    known = {_headline_key(a.get("headline", "")) for a in existing.get("alerts", [])}
    return _headline_key(headline) in known


def expire_seen_keys(seen: list[dict]) -> list[dict]:
    """Expire seen keys older than 48 hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    result = []
    for s in seen:
        try:
            if datetime.fromisoformat(s["ts"]).astimezone(timezone.utc) >= cutoff:
                result.append(s)
        except Exception:
            result.append(s)
    return result


def expire_old_alerts(alerts: list[dict]) -> list[dict]:
    now_utc   = datetime.now(timezone.utc)
    cutoff    = now_utc - timedelta(hours=ALERT_TTL_HOURS)
    today_et  = datetime.now(ET_TZ).date()
    fresh     = []
    for a in alerts:
        ts = a.get("timestamp")
        if not ts:
            fresh.append(a)
            continue
        try:
            dt     = datetime.fromisoformat(ts)
            dt_utc = dt.astimezone(timezone.utc)
            dt_et  = dt.astimezone(ET_TZ)
            # Expire if older than TTL OR from a previous ET calendar day
            if dt_utc >= cutoff and dt_et.date() == today_et:
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

    chat_ids = [cid.strip() for cid in TELEGRAM_CHAT.split(",")]
    success = False
    for chat_id in chat_ids:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "MarkdownV2", "disable_web_page_preview": True},
                timeout=15,
            )
            r.raise_for_status()
            print(f"  Telegram: sent to {chat_id} — Grade {grade}")
            success = True
        except Exception as e:
            print(f"  Telegram failed for {chat_id}: {e}")
    return success


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    now_et = datetime.now(ET_TZ)
    print(f"Emergency News Monitor — {now_et.strftime('%Y-%m-%d %H:%M ET')}")

    existing = load_existing()

    print("  [1/2] Fetching headlines…")
    headlines  = fetch_headlines()          # CNBC + Yahoo Finance RSS
    headlines += fetch_finviz_headlines()   # Finviz scrape

    # Deduplicate across sources
    seen_titles, deduped = set(), []
    for h in headlines:
        key = h["title"][:60].lower()
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(h)
    headlines = deduped

    # ── Dedup: skip anything already processed in last 6 hours ──────────────
    ts_now = datetime.now(timezone.utc).isoformat()
    fresh_headlines = [h for h in headlines if not is_seen(h["title"], existing)]
    skipped = len(headlines) - len(fresh_headlines)
    if skipped:
        print(f"  Dedup: skipped {skipped} already-seen headline(s)")

    # Mark all fetched headlines as seen (regardless of grade)
    new_seen = [{"k": _headline_key(h["title"]), "ts": ts_now} for h in fresh_headlines]
    all_seen = expire_seen_keys(existing.get("seen_keys", []) + new_seen)

    new_alerts: list[dict] = []
    if fresh_headlines:
        print(f"  [2/2] Grading {len(fresh_headlines)} new headline(s) with Gemini…")
        candidates = grade_with_gemini(fresh_headlines)
        # Also skip if already an active alert
        new_alerts = [
            a for a in candidates
            if not is_duplicate_alert(a.get("headline", ""), existing)
        ]
    else:
        print("  [2/2] All headlines already seen — skipping Gemini")

    # Timestamp and send Telegram for each new alert
    ts = now_et.isoformat()
    pub_time_map = {h["title"]: h.get("pub_time") for h in deduped}
    for a in new_alerts:
        a["timestamp"] = ts
        a["pub_time"] = pub_time_map.get(a.get("headline", ""))
        send_telegram(a)

    # Merge, expire old, cap
    merged = new_alerts + expire_old_alerts(existing.get("alerts", []))
    merged = merged[:MAX_ALERTS]

    result = {
        "last_checked": now_et.strftime("%B %d, %Y (%H:%M EST)"),
        "has_alert":    len(merged) > 0,
        "alerts":       merged,
        "seen_keys":    all_seen,   # persisted for cross-run dedup
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
