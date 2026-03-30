#!/usr/bin/env python3
from __future__ import annotations
"""
Omni-Market Intelligence & Reversal Engine

Data sources:
  - SPY / QQQ / VIX OHLC + 200-day SMA: yfinance
  - BAMLH0A0HYM2 (ICE BofA HY spread):   FRED free CSV endpoint
  - S5FI / MMTH approximation:            TradingView Screener (SMA50/SMA200 count)
  - Nikkei 225 / DAX / FTSE 100:          yfinance

Outputs:
  - public/market_intelligence.json
  - Telegram MarkdownV2 message  (requires TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)

Credit regimes:
  < 3.5%  → Complacent
  3.5–4.5 → Yellow Flag
  > 4.5%  → Stress

Reversal detection (only when S5FI < 15):
  - Hammer candle
  - Bullish Engulfing
  - Undercut & Reclaim (SPY vs 200DMA)
  S5FI < 10 → GENERATIONAL BUY ZONE
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

ET_TZ          = ZoneInfo("America/New_York")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")


# ── Data Fetching ──────────────────────────────────────────────────────────────

def _dl(sym: str, period: str = "5d") -> object:
    """yfinance download with flattened columns."""
    import pandas as pd
    df = yf.download(sym, period=period, interval="1d",
                     progress=False, auto_adjust=True)
    df.dropna(inplace=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def fetch_market_ohlc() -> dict:
    """SPY, QQQ, VIX — current price, change%, 2-day OHLC."""
    result: dict = {}
    for key, sym in [("spy", "SPY"), ("qqq", "QQQ"), ("vix", "^VIX")]:
        try:
            df = _dl(sym)
            if len(df) < 2:
                result[key] = None
                continue
            t, p = df.iloc[-1], df.iloc[-2]
            tc, pc = float(t["Close"]), float(p["Close"])
            result[key] = {
                "price":      round(tc, 2),
                "open":       round(float(t["Open"]), 2),
                "high":       round(float(t["High"]), 2),
                "low":        round(float(t["Low"]),  2),
                "close":      round(tc, 2),
                "prev_open":  round(float(p["Open"]), 2),
                "prev_close": round(pc, 2),
                "change_pct": round((tc - pc) / pc * 100, 2),
            }
        except Exception as e:
            print(f"  fetch_market_ohlc [{sym}]: {e}")
            result[key] = None
    return result


def fetch_spy_ma200() -> float | None:
    """SPY 200-day SMA — for Undercut & Reclaim detection."""
    try:
        df = _dl("SPY", period="300d")
        if len(df) >= 200:
            return round(float(df["Close"].rolling(200).mean().iloc[-1]), 2)
    except Exception as e:
        print(f"  fetch_spy_ma200: {e}")
    return None


def fetch_credit_spread() -> dict | None:
    """
    BAMLH0A0HYM2 from FRED — free CSV, no API key required.
    Retries once on failure; falls back to last cached value from
    public/market_intelligence.json when live fetch fails.
    """
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2"
    for attempt in range(2):
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            for line in reversed(r.text.strip().split("\n")[1:]):
                parts = line.strip().split(",")
                if len(parts) == 2 and parts[1] not in (".", ""):
                    try:
                        return {"date": parts[0], "value": float(parts[1]), "stale": False}
                    except ValueError:
                        continue
        except Exception as e:
            print(f"  fetch_credit_spread attempt {attempt + 1}: {e}")
            if attempt == 0:
                time.sleep(3)

    # Fallback: load last known value from previously committed market_intelligence.json
    try:
        p = Path("public/market_intelligence.json")
        if p.exists():
            prev = json.loads(p.read_text(encoding="utf-8"))
            v = (prev.get("credit") or {}).get("baml_hy") or \
                (prev.get("fred")   or {}).get("hy_oas")
            if v is not None:
                print(f"  fetch_credit_spread: using cached value {v:.2f}%")
                return {"date": "cached", "value": float(v), "stale": True}
    except Exception as e:
        print(f"  fetch_credit_spread cache fallback: {e}")
    return None


def fetch_breadth() -> dict:
    """Approximate S5FI + MMTH via TradingView Screener for large-cap US stocks."""
    try:
        from tradingview_screener import Query, col
        _, df = (
            Query()
            .select("name", "close", "SMA50", "SMA200")
            .where(
                col("exchange").isin(["NYSE", "NASDAQ"]),
                col("type") == "stock",
                col("market_cap_basic") > 5e8,
                col("average_volume_10d_calc") > 300000,
            )
            .limit(600)
            .get_scanner_data()
        )
        if df is None or df.empty:
            return {"s5fi": None, "mmth": None}

        sma50  = next((c for c in df.columns if c in ("SMA50",  "simple_moving_average_50")),  None)
        sma200 = next((c for c in df.columns if c in ("SMA200", "simple_moving_average_200")), None)
        if sma50 is None or sma200 is None:
            print("  fetch_breadth: SMA columns not found in screener response")
            return {"s5fi": None, "mmth": None}

        df = df.dropna(subset=["close", sma50, sma200])
        if df.empty:
            return {"s5fi": None, "mmth": None}

        n = len(df)
        s5fi = round((df["close"] > df[sma50]).sum()  / n * 100, 1)
        mmth = round((df["close"] > df[sma200]).sum() / n * 100, 1)
        print(f"  fetch_breadth: {n} stocks — S5FI={s5fi}% MMTH={mmth}%")
        return {"s5fi": s5fi, "mmth": mmth}
    except Exception as e:
        print(f"  fetch_breadth: {e}")
        return {"s5fi": None, "mmth": None}


def fetch_global_indices() -> dict:
    """Nikkei 225, DAX, FTSE 100 — last close change%."""
    result: dict = {}
    for key, sym in [("nikkei", "^N225"), ("dax", "^GDAXI"), ("ftse", "^FTSE")]:
        try:
            df = _dl(sym)
            if len(df) < 2:
                result[key] = None
                continue
            c, p = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
            result[key] = {
                "price":      round(c, 2),
                "change_pct": round((c - p) / p * 100, 2),
            }
        except Exception as e:
            print(f"  fetch_global [{sym}]: {e}")
            result[key] = None
    return result


# ── Signal Logic ───────────────────────────────────────────────────────────────

def classify_credit_regime(spread: float | None) -> dict:
    if spread is None:
        return {"regime": "Unknown", "color": "gray"}
    if spread < 3.5:
        return {"regime": "Complacent", "color": "green"}
    if spread < 4.5:
        return {"regime": "Yellow Flag", "color": "yellow"}
    return {"regime": "Stress", "color": "red"}


def _detect_candles(today: dict, prev: dict) -> dict:
    """Hammer + Bullish Engulfing from OHLC dicts."""
    o, h, l, c  = today["open"], today.get("high", today["close"]), today.get("low", today["close"]), today["close"]
    po, pc       = prev["open"], prev["close"]

    body       = abs(c - o) or 0.0001
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)

    hammer    = bool(lower_wick >= 2 * body and upper_wick <= body)
    engulfing = bool(c > o and pc < po and o <= pc and c >= po)

    return {"hammer": hammer, "bullish_engulfing": engulfing}


def compute_reversal_signals(indices: dict, breadth: dict,
                             spy_ma200: float | None) -> dict:
    s5fi = breadth.get("s5fi")
    signals = {
        "spy": {"hammer": False, "bullish_engulfing": False, "undercut_reclaim": False},
        "qqq": {"hammer": False, "bullish_engulfing": False, "undercut_reclaim": False},
        "signal_detected": False,
        "signal_description": "",
    }

    if s5fi is None or s5fi >= 15:
        return signals

    descriptions: list[str] = []
    for key in ("spy", "qqq"):
        ohlc = indices.get(key)
        if not ohlc:
            continue
        today = {k: ohlc[k] for k in ("open", "high", "low", "close")}
        prev  = {"open": ohlc["prev_open"], "close": ohlc["prev_close"]}
        patterns = _detect_candles(today, prev)
        signals[key].update(patterns)

        # Undercut & Reclaim — SPY only (vs 200DMA)
        if key == "spy" and spy_ma200:
            signals["spy"]["undercut_reclaim"] = bool(
                today.get("low", today["close"]) < spy_ma200 < today["close"]
            )

        detected = [p for p, v in signals[key].items() if v]
        if detected:
            name_map = {
                "hammer":           "Hammer",
                "bullish_engulfing":"Bullish Engulfing",
                "undercut_reclaim": "Undercut & Reclaim",
            }
            descriptions.append(f"{key.upper()} {' + '.join(name_map[p] for p in detected)}")

    if breadth.get("generational_buy_zone"):
        descriptions.insert(0, f"GENERATIONAL BUY ZONE (S5FI={s5fi:.1f}%)")

    if descriptions:
        signals["signal_detected"] = True
        signals["signal_description"] = " | ".join(descriptions) + f"  [S5FI={s5fi:.1f}%]"

    return signals


# ── Gemini Analysis ────────────────────────────────────────────────────────────

def analyze_with_gemini(market_data: dict, session: str) -> dict:
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not set"}

    idx     = market_data.get("indices", {})
    credit  = market_data.get("credit",  {})
    breadth = market_data.get("breadth", {})
    gl      = market_data.get("global",  {})
    rev     = market_data.get("reversal_signals", {})

    def _fi(key: str) -> str:
        d = idx.get(key)
        if not d:
            return "N/A"
        return f"${d['price']:.2f} ({d['change_pct']:+.2f}%)"

    def _gl(key: str) -> str:
        g = gl.get(key)
        return f"{g['change_pct']:+.2f}%" if g and g.get("change_pct") is not None else "N/A"

    spread = credit.get("baml_hy")
    s5fi   = breadth.get("s5fi")
    mmth   = breadth.get("mmth")

    reversal_note = ""
    if rev.get("signal_detected"):
        reversal_note = f"\n⚠️  REVERSAL SIGNAL ACTIVE: {rev['signal_description']}"

    technical_signal_instruction = (
        f'MUST begin with exactly "[🔥 REVERSAL SIGNAL DETECTED] {rev["signal_description"]} —" '
        f"then add your brief technical assessment."
        if rev.get("signal_detected")
        else "State 'No reversal pattern detected.' then add brief technical context on the current setup."
    )

    prompt = f"""You are a senior market strategist writing the {session} Market Intelligence Brief.

Current market data:
  SPY:  {_fi("spy")}
  QQQ:  {_fi("qqq")}
  VIX:  {_fi("vix")}
  BAML HY Spread (BAMLH0A0HYM2): {f"{spread:.2f}%" if spread else "N/A"} → {credit.get("regime", "Unknown")} regime
  S5FI (% stocks > 50DMA):  {f"{s5fi:.1f}%" if s5fi is not None else "N/A"}
  MMTH (% stocks > 200DMA): {f"{mmth:.1f}%" if mmth is not None else "N/A"}
  Nikkei 225: {_gl("nikkei")} | DAX: {_gl("dax")} | FTSE 100: {_gl("ftse")}{reversal_note}

Generate the brief with these fields:
  snapshot_md      – Markdown table (Index | Price | Change) for SPY/QQQ/VIX/HY Spread/S5FI/MMTH/Nikkei/DAX/FTSE
  macro_section    – 2-3 sentences on the credit regime and what BAMLH0A0HYM2 implies for risk appetite
  analysis_para1   – 1 paragraph connecting global tape (Nikkei/DAX/FTSE) to US credit spreads
  analysis_para2   – 1 paragraph identifying the single most important "Mechanical Catalyst" for today's session
  technical_signal – {technical_signal_instruction}
  ticker_intel     – 2 A-Grade (high conviction) + 1 C-Grade (avoid) — well-known S&P 500 names

Output ONLY a single valid JSON object, no markdown wrapping:
{{
  "snapshot_md": "...",
  "macro_section": "...",
  "analysis_para1": "...",
  "analysis_para2": "...",
  "technical_signal": "...",
  "ticker_intel": {{
    "a_grade": [
      {{"ticker": "TICKER1", "reason": "1-sentence conviction thesis"}},
      {{"ticker": "TICKER2", "reason": "1-sentence conviction thesis"}}
    ],
    "c_grade": [
      {{"ticker": "TICKER3", "reason": "1-sentence avoid thesis"}}
    ]
  }}
}}
"""

    try:
        from google import genai
        client   = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt
        )
        raw = response.text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"  Gemini failed: {e}")
        return {"error": str(e)}


# ── Telegram ───────────────────────────────────────────────────────────────────

_MD2 = re.compile(r"([_*\[\]()~`>#+=|{}.!\-\\])")

def _esc(text: str) -> str:
    """Escape text for Telegram MarkdownV2."""
    return _MD2.sub(r"\\\1", str(text))


def build_telegram_message(result: dict) -> str:
    idx     = result.get("indices", {})
    credit  = result.get("credit",  {})
    breadth = result.get("breadth", {})
    gl      = result.get("global",  {})
    rev     = result.get("reversal_signals", {})
    ana     = result.get("analysis", {})

    spy = idx.get("spy") or {}
    qqq = idx.get("qqq") or {}
    vix = idx.get("vix") or {}

    def arrow(chg: float | None) -> str:
        return "🟢" if (chg or 0) >= 0 else "🔴"

    def fchg(v: float | None) -> str:
        if v is None:
            return "—"
        return f"{v:+.2f}%"

    lines: list[str] = [
        f"*🧠 Market Intelligence — {_esc(result.get('session', ''))}*",
        f"_{_esc(result.get('generated_at', ''))}_",
        "",
    ]

    for label, d in [("SPY", spy), ("QQQ", qqq)]:
        if d.get("price"):
            lines.append(f"{arrow(d.get('change_pct'))} *{label}* `${d['price']:.2f}` `{fchg(d.get('change_pct'))}`")
    if vix.get("price"):
        lines += [f"⚡ *VIX* `{vix['price']:.2f}` `{fchg(vix.get('change_pct'))}`", ""]

    regime_icon = {"Complacent": "🟢", "Yellow Flag": "🟡", "Stress": "🔴"}.get(
        credit.get("regime", ""), "⚪"
    )
    if credit.get("baml_hy") is not None:
        lines.append(
            f"{regime_icon} *HY Spread:* `{credit['baml_hy']:.2f}%` — *{_esc(credit.get('regime', ''))}*"
        )

    s5fi = breadth.get("s5fi")
    mmth = breadth.get("mmth")
    if s5fi is not None:
        icon = "🔥" if s5fi < 10 else "⚠️" if s5fi < 20 else "📊"
        breadth_parts = [f"S5FI `{s5fi:.1f}%`"]
        if mmth is not None:
            breadth_parts.append(f"MMTH `{mmth:.1f}%`")
        lines.append(f"{icon} *Breadth:* " + " \\| ".join(breadth_parts))

    global_parts: list[str] = []
    for gkey, glabel in [("nikkei", "Nikkei"), ("dax", "DAX"), ("ftse", "FTSE")]:
        g = gl.get(gkey)
        if g and g.get("change_pct") is not None:
            global_parts.append(f"{glabel} `{fchg(g['change_pct'])}`")
    if global_parts:
        lines += ["", "🌏 " + " \\| ".join(global_parts)]

    lines.append("")

    if rev.get("signal_detected"):
        lines += [
            "🔥 *\\[REVERSAL SIGNAL DETECTED\\]*",
            _esc(rev.get("signal_description", "")),
            "",
        ]

    if ana.get("macro_section"):
        lines += ["*📊 Macro:*", _esc(ana["macro_section"][:600]), ""]

    if ana.get("analysis_para2"):
        lines += ["*⚙️ Mechanical Catalyst:*", _esc(ana["analysis_para2"][:500]), ""]

    tickers = ana.get("ticker_intel", {})
    a_list  = tickers.get("a_grade", [])
    c_list  = tickers.get("c_grade", [])
    if a_list or c_list:
        lines.append("*🎯 Ticker Intel:*")
        for t in a_list[:2]:
            lines.append(f"  ✅ `{t.get('ticker', '')}` {_esc(str(t.get('reason', ''))[:140])}")
        for t in c_list[:1]:
            lines.append(f"  ❌ `{t.get('ticker', '')}` {_esc(str(t.get('reason', ''))[:140])}")

    # Cap at Telegram's 4096-char limit
    msg = "\n".join(line for line in lines if line is not None)
    return msg[:4090]


def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print("  Telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not configured — skipping")
        return False
    chat_ids = [cid.strip() for cid in TELEGRAM_CHAT.split(",")]
    ok = False
    for chat_id in chat_ids:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "MarkdownV2",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            r.raise_for_status()
            print(f"  Telegram: sent to {chat_id} ✓")
            ok = True
        except Exception as e:
            print(f"  Telegram failed for {chat_id}: {e}")
    return ok


# ── Briefs DB ──────────────────────────────────────────────────────────────────

def _append_briefs_db(result: dict) -> None:
    """Append a compact summary to public/market_briefs.json (newest-first, max 30)."""
    path = Path("public/market_briefs.json")
    try:
        entries: list = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(entries, list):
            entries = []
    except Exception:
        entries = []

    idx     = result.get("indices", {})
    spy     = idx.get("spy") or {}
    vix     = idx.get("vix") or {}
    credit  = result.get("credit", {})
    breadth = result.get("breadth", {})
    rev     = result.get("reversal_signals", {})
    ana     = result.get("analysis") or {}

    compact = {
        "generated_at":          result.get("generated_at", ""),
        "session":               result.get("session", ""),
        "spy_price":             spy.get("price"),
        "spy_change_pct":        spy.get("change_pct"),
        "vix":                   vix.get("price"),
        "baml_hy":               credit.get("baml_hy"),
        "credit_regime":         credit.get("regime"),
        "s5fi":                  breadth.get("s5fi"),
        "mmth":                  breadth.get("mmth"),
        "generational_buy_zone": breadth.get("generational_buy_zone", False),
        "reversal":              rev.get("signal_detected", False),
        "reversal_description":  rev.get("signal_description", ""),
    }

    entries.insert(0, compact)
    path.write_text(
        json.dumps(entries[:30], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  Saved → {path} ({min(len(entries), 30)} entries)")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    now_et  = datetime.now(ET_TZ)
    hour    = now_et.hour
    session = "Pre-Market" if hour < 9 else "Post-Market" if hour >= 16 else "Market Hours"
    print(f"Market Intelligence Engine — {now_et.strftime('%Y-%m-%d %H:%M ET')} [{session}]")

    print("  [1/5] Fetching market OHLC (SPY / QQQ / VIX)…")
    indices = fetch_market_ohlc()

    print("  [2/5] Fetching credit spread (FRED BAMLH0A0HYM2)…")
    raw_credit = fetch_credit_spread()
    spread_val = raw_credit.get("value") if raw_credit else None
    credit = {
        "baml_hy": spread_val,
        "date":    raw_credit.get("date") if raw_credit else None,
        **classify_credit_regime(spread_val),
    }

    print("  [3/5] Fetching market breadth (TradingView Screener)…")
    breadth = fetch_breadth()
    s5fi = breadth.get("s5fi")
    breadth["breadth_flush"]         = bool(s5fi is not None and s5fi < 15)
    breadth["generational_buy_zone"] = bool(s5fi is not None and s5fi < 10)

    print("  [4/5] Fetching global indices (Nikkei / DAX / FTSE)…")
    global_idx = fetch_global_indices()

    spy_ma200: float | None = None
    if s5fi is not None and s5fi < 15:
        print("       ⚠ Breadth flush detected — fetching SPY MA200…")
        spy_ma200 = fetch_spy_ma200()

    reversal_signals = compute_reversal_signals(indices, breadth, spy_ma200)
    if reversal_signals["signal_detected"]:
        print(f"  🔥 REVERSAL: {reversal_signals['signal_description']}")

    market_data = {
        "indices":          indices,
        "credit":           credit,
        "breadth":          breadth,
        "global":           global_idx,
        "reversal_signals": reversal_signals,
    }

    print("  [5/5] Running Gemini analysis…")
    analysis = analyze_with_gemini(market_data, session)

    result = {
        "generated_at": now_et.strftime("%Y-%m-%d %H:%M ET"),
        "session":      session,
        **market_data,
        "analysis":     analysis,
    }

    out = Path("public/market_intelligence.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved → {out}")

    _append_briefs_db(result)

    tg_msg = build_telegram_message(result)
    send_telegram(tg_msg)
    print("  Done.")


if __name__ == "__main__":
    main()
