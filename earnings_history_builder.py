from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
earnings_history_builder.py — past earnings dates (+ EPS surprise) for the "E" markers under the dashboard's charts
===================================================================================================================
Browsers can't read Yahoo's earnings history (CORS) and Finnhub's free tier only returns upcoming dates, so this builder keeps a
compact history for every stock the dashboard shows, via yfinance `Ticker.get_earnings_dates`:

    public/earnings_history.json
    { "generated_at": "...", "tickers": { "HPE": { "f": "2026-10-08", "n": "2026-12-03",
                                                   "e": [["2026-09-02", "a", 18.41], ["2026-06-01", "a", 48.03], ...] } } }

  e = [date, time-of-day ("b" before open / "a" after close / "" unknown), EPS surprise % or null]  — newest first, last ~16 reports
  f = day the ticker was fetched, n = next scheduled report (so a refresh happens once that date has passed)

Dividends and splits are NOT built here — the browser fetches them live from Finance Query when a chart opens.

Incremental: only tickers that are missing, older than REFRESH_DAYS, or whose next report date has passed since they were fetched are
re-requested, at most MAX_PER_RUN per run (the first run fills the file; later runs touch ~20/day).

Universe = screener_stocks + thematic_data stocks + leadership leaders + focus list — every stock that can be charted from a table.
"""
import json
import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
PUB = ROOT / "public"
OUT = PUB / "earnings_history.json"
KEEP_PAST = 16
REFRESH_DAYS = 30
MAX_PER_RUN = int(os.environ.get("EARNINGS_HISTORY_MAX", "600"))
WORKERS = 4


def _load(name: str):
    try:
        return json.loads((PUB / name).read_text(encoding="utf-8"))
    except Exception:               # noqa: BLE001
        return None


def universe() -> list[str]:
    t: set[str] = set()
    sc = _load("screener_stocks.json")
    for s in (sc.get("stocks", []) if isinstance(sc, dict) else sc or []):
        t.add(s["ticker"])
    th = _load("thematic_data.json") or {}
    for theme in th.get("themes", []):
        for sub in theme.get("subthemes", []):
            for s in sub.get("stocks", []):
                t.add(s["ticker"])
    ld = _load("leadership.json") or {}
    for key in ("leaders", "super_leaders"):
        for rows in (ld.get(key) or {}).values():
            for s in rows:
                t.add(s["ticker"])
    fl = _load("focus_list.json") or {}

    def walk(o):
        if isinstance(o, dict):
            if "ticker" in o and isinstance(o["ticker"], str):
                t.add(o["ticker"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(fl)
    return sorted(x for x in t if x and x.replace(".", "").replace("-", "").isalnum())


def fetch_one(tk: str, today: date) -> dict | None:
    import yfinance as yf
    try:
        df = yf.Ticker(tk.replace(".", "-")).get_earnings_dates(limit=KEEP_PAST + 6)
    except Exception as exc:        # noqa: BLE001
        logger.debug("%s: %s", tk, exc)
        return None
    if df is None or len(df) == 0:
        return {"f": today.isoformat(), "n": None, "e": []}
    past, nxt = [], None
    for ts, row in df.iterrows():
        try:
            d = ts.date()
        except Exception:           # noqa: BLE001
            continue
        hr = getattr(ts, "hour", 0)
        tod = "b" if 0 < hr < 12 else "a" if hr >= 16 else ""
        sp = row.get("Surprise(%)")
        sp = None if sp is None or (isinstance(sp, float) and math.isnan(sp)) else round(float(sp), 2)
        if d > today:
            if nxt is None or d < nxt:
                nxt = d
        else:
            past.append((d, tod, sp))
    past.sort(key=lambda x: x[0], reverse=True)
    return {"f": today.isoformat(), "n": nxt.isoformat() if nxt else None,
            "e": [[d.isoformat(), tod, sp] for d, tod, sp in past[:KEEP_PAST]]}


def needs_refresh(c: dict | None, today: date) -> bool:
    if not c:
        return True
    try:
        f = date.fromisoformat(c["f"])
        if (today - f).days > REFRESH_DAYS:
            return True
        n = c.get("n")
        if n and date.fromisoformat(n) < today and f <= date.fromisoformat(n) + timedelta(days=2):
            return True
    except Exception:               # noqa: BLE001
        return True
    return False


def main() -> None:
    today = datetime.now(timezone(timedelta(hours=-5))).date()
    old = (_load("earnings_history.json") or {}).get("tickers", {})
    tickers = universe()
    todo = [t for t in tickers if needs_refresh(old.get(t), today)]
    todo.sort(key=lambda t: (t in old, old.get(t, {}).get("f", "")))        # never-fetched first, then oldest
    todo = todo[:MAX_PER_RUN]
    logger.info("Universe %d tickers, %d cached, refreshing %d", len(tickers), len(old), len(todo))
    new: dict[str, dict] = {}
    t0 = time.time()

    def work(tk):
        r = fetch_one(tk, today)
        time.sleep(0.15)
        return tk, r
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for tk, r in ex.map(work, todo):
            if r is not None:
                new[tk] = r
    logger.info("Fetched %d / %d in %.0fs", len(new), len(todo), time.time() - t0)

    merged = {t: v for t, v in old.items() if t in set(tickers)}
    merged.update(new)
    if len(merged) < 50:
        logger.error("Only %d tickers - refusing to publish", len(merged))
        return
    payload = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "tickers": dict(sorted(merged.items()))}
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if OUT.exists():
        prev = json.loads(OUT.read_text(encoding="utf-8"))
        if prev.get("tickers") == payload["tickers"]:
            logger.info("No change - leaving file untouched")
            return
    OUT.write_text(text, encoding="utf-8")
    logger.info("Written %s (%d tickers, %.0f KB)", OUT, len(merged), len(text) / 1024)


if __name__ == "__main__":
    main()
