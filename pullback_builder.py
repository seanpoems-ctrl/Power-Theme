from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
pullback_builder.py — "Pullback Watchlist" (Stocks → Pullback Watchlist), after Martin Luk's pullback-buy method
==================================================================================================================
Martin Luk (2025 US Investing Championship stock division, +969.8 %) describes his classic setup like this (tweet by @OtherSideBJ, 2026-10-12):
  1. a strong stock has already made an up-leg ("先有一轮上涨")
  2. it comes BACK to support on the daily chart ("回到支撑附近" — the moving averages / prior base)
  3. buy when it turns back up — the daily chart finds the support, the minute chart gives the trigger ("日线找支撑，分钟图等触发")
  4. hold the stop (so the entry sits right next to it) and scale out as it strengthens ("守住止损，走强后分批卖出")

This builder turns steps 1-2 (and a DAILY proxy of step 3) into numbers for every stock on the dashboard's four lists:
  F  Focus List            public/focus_list.json              every ticker on any scan
  U  Universe              public/screener_stocks.json         ADR > 5 %, avg $ vol > $500M, avg vol > 1M  (same defaults as the Universe page)
  L  Liquid Leaders        public/leadership.json  leaders     top-20 per 1M/3M/6M/1Y
  S  Super Liquid Leaders  public/leadership.json  super_leaders

The minute-chart trigger (step 3) can't be computed from daily data — `status` = "Trigger" is only the daily version (a green day closing above the prior high, or an EMA10
reclaim). Entries are still taken on the intraday chart. Step 4 is a planning aid: `stop` is the lowest low of the last 5 sessions (the pullback's swing low)
and `risk_adr` = stop distance ÷ ADR% (≤ 1 means the stop is within one day's normal range).

All the tunable cut-offs live in the dashboard (editable there); this file only measures. Output: public/pullback_watchlist.json
"""
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
PUB = ROOT / "public"
OUT = PUB / "pullback_watchlist.json"
ET = ZoneInfo("America/New_York")

# Universe-page defaults (src/App.js UNIVERSE_DEFAULTS)
UNIV_ADR, UNIV_DVOL, UNIV_VOL = 5.0, 500e6, 1e6
EXCLUDE = {"CTVA"}       # known-bad data (unadjusted spin-off), same as the Universe page
MIN_BARS = 120
LEG_WINDOW = 60          # look back this many sessions for the swing high
LEG_BASE_WINDOW = 60     # ... and this many sessions before that high for the swing low it launched from


def _load(name):
    try:
        return json.loads((PUB / name).read_text(encoding="utf-8"))
    except Exception:               # noqa: BLE001
        return None


def collect_sources() -> dict[str, set[str]]:
    """{ticker: {"F","U","L","S"}}"""
    src: dict[str, set[str]] = {}

    def add(t, code):
        if t and isinstance(t, str) and t not in EXCLUDE:
            src.setdefault(t, set()).add(code)

    fl = _load("focus_list.json") or {}

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("ticker"), str):
                add(o["ticker"], "F")
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for key in ("scan_result", "scans"):
        walk(fl.get(key))
    sc = _load("screener_stocks.json")
    for s in (sc.get("stocks", []) if isinstance(sc, dict) else sc or []):
        if (s.get("adr_pct") or 0) > UNIV_ADR and (s.get("avg_dollar_volume") or 0) > UNIV_DVOL and (s.get("avg_volume") or 0) > UNIV_VOL:
            add(s["ticker"], "U")
    ld = _load("leadership.json") or {}
    for rows in (ld.get("leaders") or {}).values():
        for r in rows:
            add(r["ticker"], "L")
    for rows in (ld.get("super_leaders") or {}).values():
        for r in rows:
            add(r["ticker"], "S")
    return src


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def metrics(d) -> dict | None:
    """Daily-bar metrics for one ticker (d = DataFrame with Open/High/Low/Close/Volume, oldest first)."""
    import pandas as pd
    d = d.dropna(subset=["Open", "High", "Low", "Close"])
    if len(d) < MIN_BARS:
        return None
    o, h, l, c, v = d["Open"], d["High"], d["Low"], d["Close"], d["Volume"].fillna(0)
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    ema10 = c.ewm(span=10, adjust=False).mean()
    ema21 = c.ewm(span=21, adjust=False).mean()
    sma50 = c.rolling(50).mean()
    sma200 = c.rolling(200).mean()
    i = len(d) - 1
    close, a = float(c.iloc[i]), float(atr.iloc[i])
    if not (close > 0 and a > 0):
        return None
    adr = float(((h / l).rolling(20).mean().iloc[i] - 1) * 100)

    # step 1 — the up-leg: swing high of the last LEG_WINDOW sessions and the swing low it launched from
    win_h = h.iloc[-LEG_WINDOW:]
    hi_pos = len(d) - LEG_WINDOW + int(win_h.values.argmax())
    hi = float(h.iloc[hi_pos])
    base_from = max(0, hi_pos - LEG_BASE_WINDOW)
    lo_before = float(l.iloc[base_from:hi_pos + 1].min())
    leg_pct = (hi / lo_before - 1) * 100 if lo_before > 0 else None
    pullback_pct = (hi - close) / hi * 100
    days_since_high = i - hi_pos

    # step 2 — support: EMA21 and SMA50 (and EMA10 for the shallow ones); distance in ATRs (+ = price above the level)
    d10 = (close - float(ema10.iloc[i])) / a
    d21 = (close - float(ema21.iloc[i])) / a
    d50 = (close - float(sma50.iloc[i])) / a if not math.isnan(sma50.iloc[i]) else None
    cands = [("EMA21", d21)] + ([("SMA50", d50)] if d50 is not None else [])
    sup_name, sup_dist = min(cands, key=lambda x: abs(x[1]))

    # step 3 (daily proxy) — turn-up day / EMA10 reclaim / coil
    turn_up = bool(close > float(o.iloc[i]) and close > float(h.iloc[i - 1]))     # green day that closes above the prior day's high
    reclaim10 = bool(close > float(ema10.iloc[i]) and float(c.iloc[i - 1]) <= float(ema10.iloc[i - 1]))
    inside = bool(float(h.iloc[i]) <= float(h.iloc[i - 1]) and float(l.iloc[i]) >= float(l.iloc[i - 1]))
    rng3 = (float(h.iloc[-3:].max()) - float(l.iloc[-3:].min())) / close * 100
    tight = bool(rng3 < adr * 0.8)
    vol50 = float(v.iloc[-53:-3].mean()) or None
    vol_dry = round(float(v.iloc[-3:].mean()) / vol50, 2) if vol50 else None     # < 1 = volume drying up on the pullback

    # step 4 — planning stop
    stop = float(l.iloc[-5:].min())
    risk_pct = (close - stop) / close * 100

    def ret(n):
        return round((close / float(c.iloc[-n - 1]) - 1) * 100, 1) if len(c) > n else None

    return {
        "close": round(close, 2), "atr": round(a, 2), "adr_pct": round(adr, 2),
        "leg_pct": round(leg_pct, 1) if leg_pct is not None else None, "pullback_pct": round(pullback_pct, 1), "days_since_high": int(days_since_high),
        "d_ema10": round(d10, 2), "d_ema21": round(d21, 2), "d_sma50": round(d50, 2) if d50 is not None else None,
        "support": sup_name, "support_dist_atr": round(sup_dist, 2),
        "above_sma50": bool(close >= float(sma50.iloc[i])) if not math.isnan(sma50.iloc[i]) else None,
        "above_sma200": bool(close >= float(sma200.iloc[i])) if not math.isnan(sma200.iloc[i]) else None,
        "ema21_above_sma50": bool(float(ema21.iloc[i]) > float(sma50.iloc[i])) if not math.isnan(sma50.iloc[i]) else None,
        "turn_up": turn_up, "reclaim_ema10": reclaim10, "inside_bar": inside, "tight": tight, "vol_dry": vol_dry,
        "stop": round(stop, 2), "risk_pct": round(risk_pct, 2), "risk_adr": round(risk_pct / adr, 2) if adr else None,
        "ret_1m": ret(21), "ret_3m": ret(63), "ret_6m": ret(126),
        "chg_1d": round((close / float(prev_c.iloc[i]) - 1) * 100, 2),
    }


def build() -> dict | None:
    import yfinance as yf
    src = collect_sources()
    tickers = sorted(src)
    logger.info("Candidates: %d (F %d · U %d · L %d · S %d)", len(tickers),
                *[sum(1 for s in src.values() if k in s) for k in "FULS"])
    if len(tickers) < 20:
        logger.error("Too few candidates - refusing to publish")
        return None
    yt = [t.replace(".", "-") for t in tickers]
    hist = yf.download(yt, period="1y", interval="1d", group_by="ticker", progress=False, auto_adjust=False, threads=True)

    meta = {}
    for s in (_load("screener_stocks.json") or {}).get("stocks", []):
        meta[s["ticker"]] = s
    for rows in ((_load("leadership.json") or {}).get("leaders") or {}).values():
        for r in rows:
            meta.setdefault(r["ticker"], {"company": r.get("company"), "industry": r.get("industry"), "avg_dollar_volume": r.get("avg_dollar_volume"),
                                          "extension": r.get("extension")})
    for fr in [_load("focus_list.json") or {}]:
        def walk(o):
            if isinstance(o, dict):
                if isinstance(o.get("ticker"), str):
                    m = meta.setdefault(o["ticker"], {})
                    for k in ("industry", "avg_dollar_volume", "extension"):
                        if m.get(k) is None and o.get(k) is not None:
                            m[k] = o[k]
                for x in o.values():
                    walk(x)
            elif isinstance(o, list):
                for x in o:
                    walk(x)
        walk(fr.get("scan_result"))

    rows = []
    for t, y in zip(tickers, yt):
        try:
            d = (hist[y] if len(yt) > 1 else hist)
            m = metrics(d)
        except Exception:           # noqa: BLE001 - one bad ticker must not sink the run
            m = None
        if not m:
            continue
        info = meta.get(t, {})
        rows.append({"ticker": t, "sources": "".join(k for k in "FULS" if k in src[t]), "company": info.get("company"), "industry": info.get("industry"),
                     "avg_dollar_volume": _num(info.get("avg_dollar_volume")), "extension": _num(info.get("extension")), "rs_score": info.get("rs_score"),
                     "market_cap_b": _num(info.get("market_cap_b")), **m})
    logger.info("Metrics for %d / %d tickers", len(rows), len(tickers))
    if len(rows) < 20:
        logger.error("Only %d rows - refusing to publish", len(rows))
        return None
    rows.sort(key=lambda r: r["ticker"])
    now = datetime.now(ET)
    return {"generated_at": now.strftime("%Y-%m-%d %H:%M ET"), "date": now.strftime("%Y-%m-%d"), "count": len(rows),
            "sources": {k: sum(1 for r in rows if k in r["sources"]) for k in "FULS"}, "rows": rows}


def main() -> None:
    payload = build()
    if not payload:
        logger.error("Pullback build failed - leaving the existing file untouched")
        return
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    logger.info("Written %s (%d rows, %.0f KB)", OUT, payload["count"], OUT.stat().st_size / 1024)
    # quick sanity summary with the dashboard's default rules
    ok = [r for r in payload["rows"] if (r["adr_pct"] or 0) >= 4 and (r["leg_pct"] or 0) >= 25 and r["above_sma50"] and 4 <= r["pullback_pct"] <= 30
          and -1 <= r["support_dist_atr"] <= 1.5]
    print(f"{len(ok)} pass the default rules:", ", ".join(r["ticker"] for r in ok[:40]))


if __name__ == "__main__":
    main()
