"""
deal_filter.py — spot stocks that are pinned under a pending cash takeover offer
=================================================================================
Stocks like BRZE (Salesforce, $47.50 cash) or ADT (Thrive, $10 cash) jump 30-50 % the day a deal is announced and then
sit within ~1 % of the offer until it closes. They top every momentum sort (1M +79 %) but are not tradeable setups.

TradingView has no "pending acquisition" flag, so the screener builder detects them itself:

  1. PATTERN (daily bars, Yahoo): the latest close-to-close jump of >= 15 % inside the last 30 sessions, after which
     every session moved <= 1.5 % and the whole stretch stayed within a 3 % band.
  2. NEWS (Google News RSS): a recent headline for the company says it is "to be acquired" / "take-private" / "per
     share in cash" ... A quiet gap-up after earnings has no such headline, so it is not flagged.
  3. PERSISTENCE: once flagged, a stock stays flagged (the 14-30 day windows above roll off) until it makes a >= 5 %
     one-day move - a broken deal or a rival bid - or leaves the screener. The caller carries the flag between runs.
  4. SEEDS: MANUAL_DEALS covers deals the pattern cannot see yet (announced yesterday) or news that failed to load. A
     seed is only used for tickers never evaluated before (not in `deals` and not in `cleared`), so a deal that later
     breaks is not re-flagged on the next run.

State lives in public/pending_deals.json ({"deals": {ticker: {note, since, source}}, "cleared": [...]}); the screener
builder updates it, and the other builders (leadership, focus list, pullback, universe, stage, breadth) only read it via
pending_deal_tickers() / drop_pending() to leave these stocks out of their lists.

Everything here is best-effort: any network failure just means no NEW flags this run, never a crash.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ticker -> note. Only a starting point: see "SEEDS" above.
MANUAL_DEALS: dict[str, str] = {
    "BRZE": "Salesforce, $47.50 cash per share (announced 2026-10-20)",
    "ADT":  "Thrive Holdings, $10.00 cash per share (announced 2026-10-29)",
}

JUMP_MIN = 0.15          # day-over-day gain that marks the announcement
JUMP_LOOKBACK = 120      # sessions to look back for it (deals stay pending for months)
POST_MAX_MOVE = 0.025    # largest daily move allowed after the jump (spreads move on regulatory news)
POST_MAX_BAND = 0.06     # max close / min close since the jump
BREAK_MOVE_PCT = 5.0     # a persisted flag is dropped on a one-day move this big

# Target-side wording only ("to acquire X" would also match the BUYER, so it is deliberately absent).
_DEAL_RE = re.compile(
    r"to be (?:acquired|bought|purchased|taken private)|agrees? to be acquired|agreed to be acquired|"
    r"take[- ]private|go(?:ing)? private|per share in cash|in cash per share|cash per share|all[- ]cash (?:deal|offer|transaction)|"
    r"buyout|definitive (?:merger )?agreement to be|to be acquired by|take-?over bid|tender offer",
    re.IGNORECASE,
)


def jump_then_pinned(closes: list[float]) -> Optional[dict]:
    """closes: daily closes, oldest -> newest. Returns {"jump_pct", "sessions_after"} when the latest big jump in the
    window is followed only by tiny moves, else None. Pure function (tested)."""
    c = [float(x) for x in closes if x is not None and float(x) > 0]
    if len(c) < 6:
        return None
    rets = [c[i] / c[i - 1] - 1 for i in range(1, len(c))]   # rets[i-1] = move into c[i]
    start = max(1, len(c) - JUMP_LOOKBACK)
    jump_idx = None
    for i in range(len(c) - 1, start - 1, -1):               # latest jump first
        if rets[i - 1] >= JUMP_MIN:
            jump_idx = i
            break
    if jump_idx is None:
        return None
    post = c[jump_idx:]
    if any(abs(post[k] / post[k - 1] - 1) > POST_MAX_MOVE for k in range(1, len(post))):
        return None
    if max(post) / min(post) - 1 > POST_MAX_BAND:
        return None
    return {"jump_pct": round(rets[jump_idx - 1] * 100, 1), "sessions_after": len(post) - 1}


def _search_name(company: str) -> str:
    """'Silence Therapeutics Plc - ADR' -> 'Silence Therapeutics'; 'Pacira BioSciences, Inc.' -> 'Pacira BioSciences'."""
    name = (company or "").split(" - ")[0]
    return re.sub(r"\b(?:inc|corp|corporation|co|company|ltd|plc|holdings|n\.?v|s\.?a|ag|class [a-z])\b\.?,?|\ba/s\b", "", name, flags=re.I).strip(" ,.")


def has_deal_headline(company: str, timeout: int = 10) -> Optional[bool]:
    """True/False = headline found / not found; None = could not check (network error)."""
    try:
        import requests
        from urllib.parse import quote
        from xml.etree import ElementTree as ET
        name = _search_name(company)
        if not name:
            return None
        q = quote(f'"{name}" acquired OR "take private" OR "per share" when:45d')
        url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        titles = [(it.findtext("title") or "") for it in ET.fromstring(r.content).iter("item")][:25]
        first = name.split()[0].lower()
        return any(_DEAL_RE.search(t) and first in t.lower() for t in titles)
    except Exception as e:                                    # noqa: BLE001 — best effort
        logger.warning("deal headline check failed for %s: %s", company, e)
        return None


def _candidates(stocks: list[dict], everything: bool = False) -> list[dict]:
    """Prefilter on the TradingView fields so only a modest number of tickers need daily bars. 3M / 6M performance is
    included because a deal stays pinned for months: after a month the 1M figure no longer shows the jump (PTC)."""
    if everything:            # short lists (e.g. the breadth scanners): check every row
        return list(stocks)
    out = []
    for s in stocks:
        big = max(s.get("perf_1w") or 0, s.get("perf_1m") or 0, s.get("perf_3m") or 0, s.get("perf_6m") or 0)
        if big >= 15 and abs(s.get("perf_1d") or 0) <= 2.5:
            out.append(s)
    return out


def _download_closes(tickers: list[str]) -> dict[str, list[float]]:
    try:
        import yfinance as yf
        yf_syms = [t.replace(".", "-") for t in tickers]
        df = yf.download(yf_syms, period="9mo", interval="1d", auto_adjust=False,
                         progress=False, threads=True, group_by="ticker")
    except Exception as e:                                    # noqa: BLE001
        logger.warning("deal filter: yfinance download failed: %s", e)
        return {}
    out: dict[str, list[float]] = {}
    for t, y in zip(tickers, yf_syms):
        try:
            col = df[y]["Close"] if len(tickers) > 1 else df["Close"]
            vals = [float(v) for v in col.dropna().tolist()]
            if vals:
                out[t] = vals
        except Exception:                                     # noqa: BLE001
            continue
    return out


REGISTRY_PATH = Path(__file__).resolve().parent / "public" / "pending_deals.json"
PRUNE_DAYS = 400         # a deal registered longer ago than this is dropped (closed deals delist; breaks are cleared)


def load_registry(path: Path | None = None) -> dict:
    """{"deals": {ticker: {"note", "since"}}, "cleared": [ticker, ...]} — empty when the file is missing/corrupt."""
    try:
        d = json.loads((path or REGISTRY_PATH).read_text(encoding="utf-8"))
        return {"deals": dict(d.get("deals") or {}), "cleared": list(d.get("cleared") or [])}
    except Exception:                                         # noqa: BLE001
        return {"deals": {}, "cleared": []}


def save_registry(reg: dict, path: Path | None = None) -> None:
    out = {"updated": date.today().isoformat(), "deals": dict(sorted(reg["deals"].items())),
           "cleared": sorted(set(reg["cleared"]))}
    (path or REGISTRY_PATH).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def pending_deal_tickers(path: Path | None = None) -> dict[str, str]:
    """ticker -> note for every registered pending deal. What the other builders use to drop these stocks."""
    return {t: (v or {}).get("note") or "Pending cash deal" for t, v in load_registry(path)["deals"].items()}


def drop_pending(rows: list[dict], key: str = "ticker", deals: dict[str, str] | None = None) -> tuple[list[dict], list[str]]:
    """(rows without pending-deal tickers, sorted tickers that were removed). `deals` defaults to the registry."""
    deals = pending_deal_tickers() if deals is None else deals
    kept, hidden = [], []
    for r in rows:
        (hidden if r.get(key) in deals else kept).append(r)
    return kept, sorted({r[key] for r in hidden})


def update_registry(stocks: list[dict], reg: dict, today: Optional[str] = None, check_all: bool = False) -> dict:
    """Runs the full detection over one universe (`stocks`: dicts with ticker/company/perf_1d?/perf_1w?/perf_1m?) and
    returns the updated registry (not saved). Rows of `stocks` are NOT modified."""
    today = today or date.today().isoformat()
    deals = dict(reg["deals"])
    cleared = set(reg["cleared"])
    by_t = {s["ticker"]: s for s in stocks}

    # persistence: a flagged stock that makes a >= 5 % one-day move (deal broke / rival bid) is cleared. A stock that is
    # simply not in THIS universe keeps its flag (another builder's pool may be wider).
    for t in list(deals):
        row = by_t.get(t)
        if row is not None and abs(row.get("perf_1d") or 0) >= BREAK_MOVE_PCT:
            del deals[t]
            cleared.add(t)
            logger.info("Pending deal cleared (%.1f%% move): %s", row.get("perf_1d"), t)

    # seeds: only for tickers never evaluated before
    for t, note in MANUAL_DEALS.items():
        if t not in deals and t not in cleared:
            deals[t] = {"note": note, "since": today, "source": "manual"}

    # new detections
    cands = [s for s in _candidates(stocks, check_all) if s["ticker"] not in deals]
    if cands:
        closes = _download_closes([s["ticker"] for s in cands])
        for s in cands:
            hit = jump_then_pinned(closes.get(s["ticker"], []))
            if hit and has_deal_headline(s.get("company", "")) is True:
                deals[s["ticker"]] = {
                    "note": f"Pending cash deal (auto-detected: +{hit['jump_pct']}% jump, flat for {hit['sessions_after']} sessions, deal headline)",
                    "since": today, "source": "auto"}
                cleared.discard(s["ticker"])
                logger.info("Pending deal detected: %s", s["ticker"])

    cutoff = date.fromisoformat(today).toordinal() - PRUNE_DAYS
    deals = {t: v for t, v in deals.items() if date.fromisoformat((v or {}).get("since", today)).toordinal() >= cutoff}
    return {"deals": deals, "cleared": sorted(cleared)}


def apply_pending_deals(stocks: list[dict], path: Path | None = None) -> int:
    """Screener entry point: updates + saves the registry from this universe and sets `pending_deal` / `deal_note` on
    every row of `stocks` (in place). Returns how many rows are flagged."""
    reg = update_registry(stocks, load_registry(path))
    save_registry(reg, path)
    for s in stocks:
        v = reg["deals"].get(s["ticker"])
        s["pending_deal"] = v is not None
        s["deal_note"] = (v or {}).get("note")
    return sum(1 for s in stocks if s["pending_deal"])


def hide_pending_in(rows: list[dict], key: str = "ticker", widen: bool = False, check_all: bool = False) -> tuple[list[dict], list[str]]:
    """Convenience for builders: optionally runs detection over `rows` first (`widen=True`, for pools wider than the
    screener's top 500), then returns (rows without pending deals, hidden tickers). Never raises."""
    try:
        if widen:
            reg = update_registry([{**r, "ticker": r[key]} for r in rows], load_registry(), check_all=check_all)
            save_registry(reg)
        return drop_pending(rows, key)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("pending-deal filter failed (non-fatal): %s", e)
        return rows, []
