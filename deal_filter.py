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
     seed is only used while the previous output has no `pending_deal` field for that ticker, so a deal that later
     breaks is not re-flagged on the next run.

Everything here is best-effort: any network failure just means no NEW flags this run, never a crash.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ticker -> note. Only a starting point: see "SEEDS" above.
MANUAL_DEALS: dict[str, str] = {
    "BRZE": "Salesforce, $47.50 cash per share (announced 2026-10-20)",
    "ADT":  "Thrive Holdings, $10.00 cash per share (announced 2026-10-29)",
}

JUMP_MIN = 0.15          # day-over-day gain that marks the announcement
JUMP_LOOKBACK = 30       # sessions to look back for it
POST_MAX_MOVE = 0.015    # largest daily move allowed after the jump
POST_MAX_BAND = 0.03     # max close / min close since the jump
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


def has_deal_headline(company: str, timeout: int = 10) -> Optional[bool]:
    """True/False = headline found / not found; None = could not check (network error)."""
    try:
        import requests
        from urllib.parse import quote
        from xml.etree import ElementTree as ET
        name = re.sub(r"\b(inc|corp|corporation|co|company|ltd|plc|holdings|class [a-z])\b\.?,?", "", company or "", flags=re.I).strip(" ,.")
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


def _candidates(stocks: list[dict]) -> list[dict]:
    """Cheap prefilter on the TradingView fields so only a handful of tickers need daily bars."""
    out = []
    for s in stocks:
        big = max(s.get("perf_1w") or 0, s.get("perf_1m") or 0)
        if big >= 15 and abs(s.get("perf_1d") or 0) <= 2.5:
            out.append(s)
    return out


def _download_closes(tickers: list[str]) -> dict[str, list[float]]:
    try:
        import yfinance as yf
        yf_syms = [t.replace(".", "-") for t in tickers]
        df = yf.download(yf_syms, period="3mo", interval="1d", auto_adjust=False,
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


def apply_pending_deals(stocks: list[dict], previous: dict[str, dict] | None) -> int:
    """Sets `pending_deal` (bool) and `deal_note` on every stock in place. `previous` = last run's rows by ticker.
    Returns the number flagged."""
    previous = previous or {}
    flagged: dict[str, str] = {}

    # 3. persistence + 4. seeds
    for s in stocks:
        t = s["ticker"]
        prev = previous.get(t)
        broke = abs(s.get("perf_1d") or 0) >= BREAK_MOVE_PCT
        if prev is not None and "pending_deal" in prev:
            if prev.get("pending_deal") and not broke:
                flagged[t] = prev.get("deal_note") or "Pending cash deal"
        elif t in MANUAL_DEALS and not broke:
            flagged[t] = MANUAL_DEALS[t]

    # 1 + 2: new detections among the not-yet-flagged candidates
    cands = [s for s in _candidates(stocks) if s["ticker"] not in flagged]
    if cands:
        closes = _download_closes([s["ticker"] for s in cands])
        for s in cands:
            hit = jump_then_pinned(closes.get(s["ticker"], []))
            if not hit:
                continue
            if has_deal_headline(s.get("company", "")) is True:
                flagged[s["ticker"]] = (f"Pending cash deal (auto-detected: +{hit['jump_pct']}% jump, "
                                        f"flat for {hit['sessions_after']} sessions, deal headline)")
                logger.info("Pending deal detected: %s %s", s["ticker"], flagged[s["ticker"]])

    for s in stocks:
        s["pending_deal"] = s["ticker"] in flagged
        s["deal_note"] = flagged.get(s["ticker"])
    return len(flagged)
