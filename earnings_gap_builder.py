from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
earnings_gap_builder.py — "Gap-Ups" list (Stocks -> Earnings Gaps)
====================================================================
Stocks that GAPPED UP at least MIN_GAP % at the open, per earnings season, each with a short Catalyst Summary - both the ones that gapped on an earnings
report ("earnings", kind e) and the ones that gapped with NO earnings report that day ("other", kind o: upgrade, FDA, contract, M&A, sector move ...).

  * Universe = the Theme Leaderboard's screening rules, applied to public/industry_stocks.json (every US stock >= $500M): ADR >= MIN_ADR % and avg $ volume
    >= MIN_DV - so no thin or slow names.  (Current ADR / $ volume, not the values on the gap day.)
  * Seasons are contiguous periods that start with each reporting window:  Jan 10 -> Apr 7 (Q4 results), Apr 8 -> Jul 7 (Q1), Jul 8 -> Oct 7 (Q2),
    Oct 8 -> Jan 9 (Q3).  The newest N_SEASONS are kept; the current one is "in progress" and recomputed every night, a finished one is frozen ~10 days
    after it ends (its events are copied from the previous file).
  * Gap = open of the day / previous close - 1.  A gap is an EARNINGS gap when that day is the reaction day of a report: the report date for a before-open
    report, the NEXT trading day for an after-close report (either, when the time is unknown).  Report dates come from public/earnings_history.json, else
    yfinance (earnings_history_builder.fetch_one).  Every other >= MIN_GAP % gap-up is kind "o".
  * Catalyst Summary = Gemini (gemini-2.5-flash, same key as gapper_service.py) over the dated Google News headlines around the day (+ EPS surprise and the
    gap numbers for earnings); 1-2 sentences.  No key / no headlines / an error -> a data-only fallback line.  AI summaries are never regenerated.

The page joins the CURRENT columns (ADR, Avg $ Vol, Mkt Cap, Extension, Intra / 1D / 1W / 1M / 3M) from industry_stocks.json in the browser, so the
file only stores what is specific to the gap.

Output: public/earnings_gaps.json
  { generated_at, min_gap, seasons: [ { key, label, start, end, in_progress, events: [ {k, t, n, ed, tod, d, g, day, rv, sur, c, cat, src} ] } ] }
    k = e (earnings) / o (other), ed = report date (= d for kind o), tod = b / a / "", d = gap day, g = gap %, day = close vs previous close % on the gap day,
    rv = gap-day volume / 50-day avg, sur = EPS surprise % (e only), c = close on the gap day, cat = catalyst summary, src = ai / data.
Usage:  python earnings_gap_builder.py [--full] [--no-ai]
"""
import json
import logging
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
PUB = ROOT / "public"
OUT = PUB / "earnings_gaps.json"
MIN_GAP = 5.0            # % open vs previous close
MIN_ADR = 4.0            # Theme Leaderboard "Highest-ADR stocks" defaults: ADR >= 4 % ...
MIN_DV = 20e6            # ... and avg $ volume >= $20M
N_SEASONS = 4
FREEZE_AFTER_DAYS = 10
BATCH_AI = 10
UA = {"User-Agent": "Mozilla/5.0"}
GEMINI_MODEL = "gemini-2.5-flash"
# (first month, first day, label of the quarter whose results are reported)
STARTS = [(1, 10, "Q4"), (4, 8, "Q1"), (7, 8, "Q2"), (10, 8, "Q3")]
MONTH = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def seasons_for(today: date) -> list[dict]:
    starts = []
    for yr in (today.year - 1, today.year, today.year + 1):
        for m, d, q in STARTS:
            starts.append((date(yr, m, d), yr, q))
    starts.sort()
    out = []
    for i, (s, yr, q) in enumerate(starts[:-1]):
        e = starts[i + 1][0] - timedelta(days=1)
        if s <= today:
            rep_year = yr - 1 if q == "Q4" else yr          # Q4 results (reported Jan-Mar) are the previous year's
            out.append({"key": f"{yr}-{q}", "label": f"{q} {rep_year} results · {MONTH[s.month]} {s.day} – {MONTH[e.month]} {e.day}", "start": s, "end": e, "in_progress": today <= e})
    return sorted(out, key=lambda x: x["start"], reverse=True)[:N_SEASONS]


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:       # noqa: BLE001
        return None


def universe() -> dict[str, dict]:
    d = load_json(PUB / "industry_stocks.json") or {}
    uni: dict[str, dict] = {}
    for ind, rows in (d.get("industries") or {}).items():
        for r in rows:
            if (r.get("dv") or 0) >= MIN_DV and (r.get("adr") or 0) >= MIN_ADR and r["t"] not in uni:
                uni[r["t"]] = {"n": r.get("n") or "", "ind": ind}
    return uni


def download(tickers: list[str], start: date):
    import pandas as pd
    import yfinance as yf
    frames = {}
    syms = {t: t.replace(".", "-") for t in tickers}
    inv = {v: k for k, v in syms.items()}
    items = list(syms.values())
    for i in range(0, len(items), 250):
        chunk = items[i:i + 250]
        try:
            df = yf.download(chunk, start=start.isoformat(), interval="1d", auto_adjust=False, group_by="ticker", threads=True, progress=False)
        except Exception as exc:        # noqa: BLE001
            logger.warning("download batch failed: %s", exc)
            continue
        for sym in chunk:
            try:
                sub = df[sym] if isinstance(df.columns, pd.MultiIndex) else df
                sub = sub[["Open", "Close", "Volume"]].dropna(subset=["Open", "Close"])
                if len(sub) > 60:
                    frames[inv[sym]] = sub
            except Exception:           # noqa: BLE001
                continue
        logger.info("prices %d / %d", min(i + 250, len(items)), len(items))
    return frames


def candidates(frames: dict, windows: list[dict]) -> list[tuple]:
    """(ticker, gap day, gap %, day %, rvol, close) for every day inside a season that opened >= MIN_GAP % above the previous close."""
    out = []
    for t, df in frames.items():
        idx = df.index
        o, c, v = df["Open"].to_numpy(float), df["Close"].to_numpy(float), df["Volume"].to_numpy(float)
        for i in range(1, len(df)):
            d = idx[i].date()
            if not any(w["start"] <= d <= w["end"] for w in windows):
                continue
            if c[i - 1] <= 0:
                continue
            gap = (o[i] / c[i - 1] - 1) * 100
            if gap < MIN_GAP:
                continue
            base = v[max(0, i - 50):i]
            base = base[base > 0]
            rv = float(v[i] / base.mean()) if len(base) >= 20 and v[i] > 0 else None
            out.append((t, d, round(gap, 2), round((c[i] / c[i - 1] - 1) * 100, 2), None if rv is None else round(rv, 1), round(float(c[i]), 2)))
    return out


def earnings_records(tickers: list[str]) -> dict[str, list]:
    """{ticker: [[date, tod, surprise], ...]} - the stored history where it exists, yfinance for the rest."""
    from earnings_history_builder import fetch_one
    hist = (load_json(PUB / "earnings_history.json") or {}).get("tickers", {})
    recs = {t: hist[t].get("e", []) for t in tickers if t in hist}
    todo = [t for t in tickers if t not in recs]
    logger.info("earnings dates: %d from earnings_history.json, %d via yfinance", len(recs), len(todo))
    today = date.today()

    def work(t):
        r = fetch_one(t, today)
        time.sleep(0.15)
        return t, r
    with ThreadPoolExecutor(max_workers=4) as ex:
        for t, r in ex.map(work, todo):
            if r is not None:
                recs[t] = r.get("e", [])
    return recs


def match_earnings(t: str, d: date, recs: list, frames: dict):
    """The report whose reaction day is d: before-open -> same day, after-close -> next trading day, unknown -> either."""
    idx = [x.date() for x in frames[t].index]
    pos = {x: i for i, x in enumerate(idx)}
    for ed, tod, sur in recs:
        try:
            e = date.fromisoformat(ed)
        except ValueError:
            continue
        nxt = None
        if e in pos and pos[e] + 1 < len(idx):
            nxt = idx[pos[e] + 1]
        elif e not in pos:
            nxt = next((x for x in idx if x > e), None)
        ok = (d == e) if tod == "b" else (d == nxt) if tod == "a" else (d == e or d == nxt)
        if ok:
            return ed, tod, sur
    return None


# ── Catalyst summary ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def headlines(ticker: str, ed: str, earnings: bool) -> list[str]:
    e = date.fromisoformat(ed)
    topic = "stock earnings" if earnings else "stock"
    q = f"{ticker} {topic} after:{(e - timedelta(days=1)).isoformat()} before:{(e + timedelta(days=3 if not earnings else 4)).isoformat()}"
    try:
        r = requests.get(f"https://news.google.com/rss/search?q={quote(q)}&hl=en-US&gl=US&ceid=US:en", headers=UA, timeout=20)
        root = ET.fromstring(r.content)
    except Exception:       # noqa: BLE001
        return []
    seen, out = set(), []
    for it in root.findall(".//item"):
        title = re.sub(r"\s+-\s+[^-]{2,40}$", "", (it.findtext("title") or "").strip())
        key = title[:60].lower()
        if title and key not in seen:
            seen.add(key)
            out.append(title)
        if len(out) >= 6:
            break
    return out


def fallback(ev: dict) -> str:
    rv = f" on {ev['rv']:.1f}x volume" if ev.get("rv") else ""
    if ev["k"] == "e":
        sur = ev.get("sur")
        head = f"EPS {'beat' if sur > 0 else 'miss' if sur < 0 else 'in line'}{f' {abs(sur):.1f}%' if sur else ''}; " if sur is not None else ""
        return f"{head}gapped {ev['g']:+.1f}%{rv} after earnings."
    return f"Gapped {ev['g']:+.1f}%{rv}; no earnings report that day and no catalyst found."


def gemini_summaries(batch: list[dict], heads: dict[str, list[str]]) -> dict[str, str]:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        return {}
    from google import genai
    from google.genai import types as gt
    lines = []
    for i, ev in enumerate(batch):
        hs = heads.get(ev["t"] + "|" + ev["ed"]) or []
        kind = "earnings report" if ev["k"] == "e" else "NO earnings report"
        sur_txt = "; EPS surprise %+.1f%%" % ev["sur"] if ev.get("sur") is not None else ""
        lines.append(f"[{i}] {ev['t']} ({ev['n'] or 'n/a'}) — {kind}, {ev['d']}; gapped {ev['g']:+.1f}% at the open, closed {ev['day']:+.1f}% on the day{sur_txt}\n"
                     "    headlines: " + (" | ".join(hs) if hs else "(none found)"))
    prompt = ("You are an equity analyst. For each stock below write a CATALYST SUMMARY of why it gapped up: for an earnings report say what drove it "
              "(beat / raise / guidance / segment growth / AI demand ...); when there was NO earnings report say what the news was (analyst upgrade, FDA, "
              "contract / partnership, M&A, sector or macro move, product news ...). 1-2 sentences, max 45 words, plain text (no markdown, no emojis), concrete. "
              "Use ONLY facts in the headlines and the numbers given; never invent figures. If the headlines give no reason, say so briefly "
              "(e.g. 'No clear driver in the headlines').\n\n"
              + "\n".join(lines) + "\n\nReturn ONLY a JSON array: [{\"i\": <number in brackets>, \"summary\": \"...\"}, ...] with one entry per stock.")
    try:
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt, config=gt.GenerateContentConfig(
            response_mime_type="application/json", thinking_config=gt.ThinkingConfig(thinking_budget=0)))
        arr = json.loads(resp.text)
    except Exception as exc:        # noqa: BLE001
        logger.warning("Gemini batch failed: %s", exc)
        return {}
    out = {}
    for x in arr if isinstance(arr, list) else []:
        try:
            ev = batch[int(x["i"])]
            s = str(x["summary"]).strip()
            if s:
                out[ev["t"] + "|" + ev["ed"]] = s
        except Exception:           # noqa: BLE001
            continue
    return out


def add_catalysts(events: list[dict], use_ai: bool) -> None:
    need = [e for e in events if not e.get("cat") or (e.get("src") != "ai" and use_ai)]
    if not need:
        return
    logger.info("catalyst summaries needed: %d (AI %s)", len(need), "on" if use_ai else "off")
    heads: dict[str, list[str]] = {}
    if use_ai:
        def work(e):
            time.sleep(0.2)
            return e["t"] + "|" + e["ed"], headlines(e["t"], e["ed"], e["k"] == "e")
        with ThreadPoolExecutor(max_workers=5) as ex:
            for k, h in ex.map(work, need):
                heads[k] = h
    done = {}
    if use_ai:
        for i in range(0, len(need), BATCH_AI):
            done.update(gemini_summaries(need[i:i + BATCH_AI], heads))
            time.sleep(0.5)
    for e in need:
        k = e["t"] + "|" + e["ed"]
        if k in done:
            e["cat"], e["src"] = done[k], "ai"
        elif not e.get("cat"):
            e["cat"], e["src"] = fallback(e), "data"
    logger.info("AI summaries: %d, data-only fallbacks: %d", sum(1 for e in need if e["src"] == "ai"), sum(1 for e in need if e["src"] != "ai"))


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    full, use_ai = "--full" in sys.argv, "--no-ai" not in sys.argv
    today = date.today()
    prev = load_json(OUT) or {}
    prev_by_key = {s["key"]: s for s in prev.get("seasons", [])}
    seasons = seasons_for(today)
    redo = [s for s in seasons if full or s["key"] not in prev_by_key or s["in_progress"] or (today - s["end"]).days <= FREEZE_AFTER_DAYS]
    logger.info("seasons: %s | recomputing: %s", [s["key"] for s in seasons], [s["key"] for s in redo])
    uni = universe()
    logger.info("universe: %d stocks with ADR >= %.0f%% and avg $ vol >= $%dM", len(uni), MIN_ADR, MIN_DV // 1_000_000)
    new_events: dict[str, list[dict]] = {s["key"]: [] for s in redo}
    if redo and uni:
        start = min(s["start"] for s in redo) - timedelta(days=100)
        frames = download(sorted(uni), start)
        logger.info("price history for %d stocks", len(frames))
        if len(frames) < 0.6 * len(uni):
            logger.error("only %d of %d price histories - not publishing", len(frames), len(uni))
            return
        cands = candidates(frames, redo)
        logger.info("gap-up days >= %.0f%% in the seasons: %d (%d tickers)", MIN_GAP, len(cands), len({c[0] for c in cands}))
        recs = earnings_records(sorted({c[0] for c in cands}))
        # an AI summary already written for the same stock + day (from any earlier file, earnings or not) is kept
        prev_events = {e["t"] + "|" + e["ed"]: e for s in prev.get("seasons", []) for e in s.get("events", [])}
        n_e = n_o = 0
        for t, d, gap, day, rv, close in cands:
            m = match_earnings(t, d, recs.get(t, []), frames) if t in recs else None
            ev = {"k": "e" if m else "o", "t": t, "n": uni[t]["n"], "ed": m[0] if m else d.isoformat(), "tod": m[1] if m else "", "d": d.isoformat(),
                  "g": gap, "day": day, "rv": rv, "sur": m[2] if m else None, "c": close}
            old = prev_events.get(ev["t"] + "|" + ev["ed"])
            if old and old.get("cat") and old.get("src") == "ai" and old.get("k", "e") == ev["k"]:
                ev["cat"], ev["src"] = old["cat"], "ai"
            for s in redo:
                if s["start"] <= d <= s["end"]:
                    new_events[s["key"]].append(ev)
                    n_e += ev["k"] == "e"
                    n_o += ev["k"] == "o"
                    break
        logger.info("events: %d earnings, %d non-earnings", n_e, n_o)
    out_seasons = []
    for s in seasons:
        events = new_events[s["key"]] if s["key"] in new_events else prev_by_key[s["key"]].get("events", [])
        if s["key"] in new_events:
            add_catalysts(events, use_ai)
        events.sort(key=lambda e: -e["g"])
        out_seasons.append({"key": s["key"], "label": s["label"], "start": s["start"].isoformat(), "end": s["end"].isoformat(), "in_progress": s["in_progress"], "events": events})
        logger.info("%s: %d events (%d earnings)", s["key"], len(events), sum(1 for e in events if e.get("k", "e") == "e"))
    if not any(s["events"] for s in out_seasons):
        logger.error("no events - leaving the existing file untouched")
        return
    payload = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "min_gap": MIN_GAP, "seasons": out_seasons}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    logger.info("Wrote %s (%.0f KB)", OUT.name, OUT.stat().st_size / 1024)


if __name__ == "__main__":
    main()
