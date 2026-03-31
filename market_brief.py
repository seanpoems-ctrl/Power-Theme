#!/usr/bin/env python3
from __future__ import annotations
"""
Market Brief Generator — Omni-Market Intelligence format

Runs at 07:30 ET (Pre-Market) and 16:30 ET (Post-Market).

Data:
  - S&P 500 (^GSPC), Nasdaq 100 (^NDX), VIX (^VIX), 10Y Yield (^TNX): yfinance
  - BAMLH0A0HYM2 HY credit spread: FRED free CSV
  - S5FI / MMTH approximation: TradingView Screener
  - Nikkei 225, DAX, FTSE 100: yfinance
  - SPY 200-day SMA (for Undercut & Reclaim): yfinance

Output: public/market_brief.json
Telegram: MarkdownV2 (if TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID set)
"""

import json
import os
import re
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


# ── Helpers ────────────────────────────────────────────────────────────────────

def _dl(sym: str, period: str = "5d"):
    """yfinance download with flattened MultiIndex columns."""
    import pandas as pd
    df = yf.download(sym, period=period, interval="1d",
                     progress=False, auto_adjust=True)
    df.dropna(inplace=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


# ── Data Fetching ──────────────────────────────────────────────────────────────

def fetch_indices() -> dict:
    """S&P 500, Nasdaq 100, VIX, 10Y Yield — price, change, labels."""
    specs = [
        ("sp500",   "^GSPC", "S&P 500"),
        ("nasdaq",  "^NDX",  "Nasdaq 100"),
        ("vix",     "^VIX",  "VIX"),
        ("yield10", "^TNX",  "10Y Yield"),
    ]
    result = {}
    for key, sym, label in specs:
        try:
            df = _dl(sym)
            if len(df) < 2:
                result[key] = None
                continue
            c, p = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
            # TNX is quoted ×10 in some feeds; normalise to actual %
            if key == "yield10" and c > 20:
                c /= 10
                p /= 10
            result[key] = {
                "label":      label,
                "price":      round(c, 2),
                "change":     round(c - p, 2),
                "change_pct": round((c - p) / p * 100, 2) if key not in ("vix", "yield10") else None,
            }
        except Exception as e:
            print(f"  fetch_indices [{sym}]: {e}")
            result[key] = None
    return result


def fetch_credit_spread() -> dict | None:
    """BAMLH0A0HYM2 from FRED — free CSV, no API key."""
    try:
        r = requests.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2",
            timeout=15, headers={"User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
        for line in reversed(r.text.strip().split("\n")[1:]):
            parts = line.strip().split(",")
            if len(parts) == 2 and parts[1] not in (".", ""):
                try:
                    return {"date": parts[0], "value": float(parts[1])}
                except ValueError:
                    continue
    except Exception as e:
        print(f"  fetch_credit_spread: {e}")
    return None


def fetch_breadth() -> dict:
    """S5FI + MMTH approximation via TradingView Screener (large-cap US proxy)."""
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
            return {}
        sma50  = next((c for c in df.columns if c in ("SMA50",  "simple_moving_average_50")),  None)
        sma200 = next((c for c in df.columns if c in ("SMA200", "simple_moving_average_200")), None)
        if not sma50 or not sma200:
            return {}
        df = df.dropna(subset=["close", sma50, sma200])
        if df.empty:
            return {}
        n = len(df)
        s5fi = float(round(float((df["close"] > df[sma50]).sum())  / n * 100, 1))
        mmth = float(round(float((df["close"] > df[sma200]).sum()) / n * 100, 1))
        print(f"  breadth: {n} stocks  S5FI={s5fi}%  MMTH={mmth}%")
        return {"s5fi": s5fi, "mmth": mmth}
    except Exception as e:
        print(f"  fetch_breadth: {e}")
        return {}


def fetch_global() -> dict:
    """Nikkei 225, DAX, FTSE 100 — change%."""
    result = {}
    for key, sym, label in [
        ("nikkei", "^N225",  "Nikkei 225"),
        ("dax",    "^GDAXI", "DAX"),
        ("ftse",   "^FTSE",  "FTSE 100"),
    ]:
        try:
            df = _dl(sym)
            if len(df) < 2:
                result[key] = None
                continue
            c, p = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
            result[key] = {"label": label, "price": round(c, 2),
                           "change_pct": round((c - p) / p * 100, 2)}
        except Exception as e:
            print(f"  fetch_global [{sym}]: {e}")
            result[key] = None
    return result


def fetch_spy_ma200() -> float | None:
    try:
        df = _dl("SPY", period="300d")
        if len(df) >= 200:
            return round(float(df["Close"].rolling(200).mean().iloc[-1]), 2)
    except Exception as e:
        print(f"  fetch_spy_ma200: {e}")
    return None


# ── Signal Logic ───────────────────────────────────────────────────────────────

def vix_zone(v: float) -> str:
    if v < 15:  return "Calm"
    if v < 20:  return "Normal"
    if v < 25:  return "Elevated"
    if v < 30:  return "Anxiety Zone"
    if v < 35:  return "Fear"
    return "Extreme Fear"


def yield_trend(chg: float) -> str:
    if chg >  0.03: return "Rising"
    if chg < -0.03: return "Falling"
    return "Stable"


def credit_regime(spread: float) -> str:
    if spread < 3.5: return "Complacent"
    if spread < 4.5: return "Yellow Flag"
    return "Stress"


def breadth_status_label(s5fi: float | None) -> str:
    if s5fi is None:   return ""
    if s5fi < 10:      return "GENERATIONAL BUY ZONE"
    if s5fi < 15:      return "BREADTH FLUSH ACTIVE"
    if s5fi < 25:      return "FLUSH WATCH"
    return ""


def detect_reversal(indices: dict, s5fi: float | None, spy_ma200: float | None) -> dict:
    """Candle pattern detection — only when S5FI < 15."""
    signals = {"signal_detected": False, "description": ""}
    if s5fi is None or s5fi >= 15:
        return signals

    # SPY proxy: use sp500 data
    sp = indices.get("sp500")
    if not sp:
        return signals

    # We don't have OHLC in the basic fetch; skip candle detection
    # Undercut & Reclaim check using close vs MA200
    if spy_ma200 and sp.get("price"):
        close = sp["price"]
        low_proxy = close * 0.995  # approximate — no intraday low in index data
        if low_proxy < spy_ma200 < close:
            signals["signal_detected"] = True
            signals["description"] = f"Undercut & Reclaim of SPY 200DMA ({spy_ma200:.2f}) [S5FI={s5fi:.1f}%]"

    if s5fi < 10 and not signals["signal_detected"]:
        signals["signal_detected"] = True
        signals["description"] = f"GENERATIONAL BUY ZONE (S5FI={s5fi:.1f}%)"

    return signals


# ── Gemini Analysis ────────────────────────────────────────────────────────────

def fetch_top_stocks_from_scanner() -> list[dict]:
    """讀取 thematic_data.json，篩選出最強的候選股票清單給 Gemini 參考"""
    try:
        p = Path("public/thematic_data.json")
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        candidates = []
        for theme in data.get("themes", []):
            theme_name = theme.get("name", "")
            for sub in theme.get("subthemes", []):
                for stock in sub.get("stocks", []):
                    rs = stock.get("rs_52w") or 0
                    perf_1m = stock.get("perf_1m") or 0
                    dollar_vol = stock.get("dollar_volume") or 0
                    adr = stock.get("adr_pct") or 0
                    if rs >= 80 and perf_1m >= 10 and dollar_vol >= 80_000_000 and adr >= 4:
                        candidates.append({
                            "ticker": stock.get("ticker"),
                            "company": stock.get("company", ""),
                            "theme": theme_name,
                            "rs_52w": rs,
                            "perf_1m": perf_1m,
                            "perf_3m": stock.get("perf_3m") or 0,
                            "adr_pct": adr,
                        })
        # 按 RS 排序，最多給 Gemini 15 個候選
        candidates.sort(key=lambda x: x["rs_52w"], reverse=True)
        return candidates[:15]
    except Exception as e:
        print(f"  fetch_top_stocks_from_scanner: {e}")
        return []


def analyze_with_gemini(context: dict, session: str, now_et: datetime) -> dict:
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not set"}

    candidates = fetch_top_stocks_from_scanner()

    idx     = context["indices"]
    credit  = context["credit"]
    breadth = context["breadth"]
    gl      = context["global"]
    rev     = context["reversal"]

    sp  = idx.get("sp500")  or {}
    ndx = idx.get("nasdaq") or {}
    vix = idx.get("vix")    or {}
    y10 = idx.get("yield10")or {}

    def _p(d, key="price", decimals=2):
        v = d.get(key)
        return f"{v:.{decimals}f}" if v is not None else "N/A"

    s5fi = breadth.get("s5fi")
    mmth = breadth.get("mmth")
    spread = credit.get("value")
    regime = credit_regime(spread) if spread else "Unknown"

    rev_note = (f"\n⚠️  REVERSAL SIGNAL: {rev['description']}"
                if rev.get("signal_detected") else "")

    tech_instruction = (
        f'MUST begin with "[🔥 REVERSAL SIGNAL DETECTED] {rev["description"]} —" '
        "then add your mechanical assessment."
        if rev.get("signal_detected")
        else "If no reversal detected, state 'No reversal pattern confirmed yet.' with brief context."
    )

    def _gl_str(key):
        g = gl.get(key)
        if not g or g.get("change_pct") is None:
            return "N/A"
        return f"{g['change_pct']:+.2f}%"

    prompt = f"""You are writing the {session} Market Intelligence Brief for {now_et.strftime('%B %d, %Y (%H:%M %Z)')}.

Market data:
  S&P 500:     {_p(sp)} ({sp.get('change_pct', 0):+.2f}% today)
  Nasdaq 100:  {_p(ndx)} ({ndx.get('change_pct', 0):+.2f}%)
  VIX:         {_p(vix)} — {vix_zone(vix['price']) if vix.get('price') else 'N/A'}
  10Y Yield:   {_p(y10)}% ({yield_trend(y10.get('change', 0))})
  BAML HY Spread: {f"{spread:.2f}%" if spread else "N/A"} → {regime} regime
  S5FI (% stocks > 50DMA):  {f"{s5fi:.1f}%" if s5fi is not None else "N/A"}
  MMTH (% stocks > 200DMA): {f"{mmth:.1f}%" if mmth is not None else "N/A"}
  Nikkei 225: {_gl_str("nikkei")} | DAX: {_gl_str("dax")} | FTSE 100: {_gl_str("ftse")}{rev_note}
"""

    if candidates:
        cand_text = "\n".join(
            f"  {c['ticker']} ({c['company']}) | Theme: {c['theme']} | "
            f"RS={c['rs_52w']} | 1M={c['perf_1m']:+.1f}% | 3M={c['perf_3m']:+.1f}% | ADR={c['adr_pct']:.1f}%"
            for c in candidates
        )
        prompt += f"""Stock candidates from Thematic Scanner (pre-filtered: RS≥80, 1M≥+10%, AvgDolVol≥$80M, ADR≥4%):
{cand_text}
"""

    prompt += f"""
Output ONLY valid JSON (no markdown wrapping):
{{
  "mood": "2-6 word market mood — e.g. 'Risk-Off / Global Weakness' or 'Capitulation / Distribution'",
  "analysis_para1": "1 paragraph connecting global tape (Nikkei/DAX/FTSE) to US credit and breadth",
  "analysis_para2": "1 paragraph: the 'Mechanical Plan' — specific price levels, signals, and action to watch",
  "technical_signal": "{tech_instruction}",
  "ticker_intel": [
    {{"ticker": "TICK1", "company": "Full Company Name", "grade": "A+", "reason": "1-sentence thesis"}},
    {{"ticker": "TICK2", "company": "Full Company Name", "grade": "A",  "reason": "1-sentence thesis"}},
    {{"ticker": "TICK3", "company": "Full Company Name", "grade": "C",  "reason": "1-sentence avoid thesis"}}
  ]
}}

Rules:
  mood: captures today's market character in a phrase
  analysis_para1: 3-5 sentences, connect global → credit → US breadth, cite specific numbers
  analysis_para2: 3-5 sentences, the actionable mechanical plan with key price levels
  technical_signal: {tech_instruction}
  ticker_intel: pick exactly 2 conviction longs (grade A+/A/A-) + 1 avoid (grade C/D/F)
  FROM THE CANDIDATES LIST ABOVE ONLY. Do not invent tickers not on the list.
  For the avoid: pick the weakest candidate or one showing distribution.
  reason: 1 sentence citing specific RS, 1M return, and why it fits current market regime.
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

def _esc(t: str) -> str:
    return _MD2.sub(r"\\\1", str(t))


def build_telegram_message(result: dict) -> str:
    session    = result.get("session", "")
    gen_at     = result.get("generated_at", "")
    snap       = result.get("global_snapshot", [])
    macro      = result.get("macro_breadth", {})
    rev        = result.get("reversal_signals", {})
    ana        = result.get("analysis", {})
    mood       = result.get("mood", "")

    session_emoji = {"Pre-Market": "🌅", "Market Hours": "☀️", "Post-Market": "🌙"}.get(session, "📊")

    lines = [
        f"*{session_emoji} {_esc(session.upper())} BRIEF*",
        f"_{_esc(gen_at)}_",
        "",
    ]

    if mood:
        lines += [f"*Mood:* {_esc(mood)}", ""]

    # Snapshot row per row
    for row in snap:
        label = row.get("label", "")
        price = row.get("price")
        chg   = row.get("change")
        pct   = row.get("change_pct")
        lbl   = row.get("zone_label") or row.get("trend_label") or ""
        arrow = ("🟢" if (chg or 0) >= 0 else "🔴") if chg is not None else "⚪"
        price_str = f"{price:.2f}" if price else "—"
        chg_str   = f"{chg:+.2f}" if chg is not None else ""
        pct_str   = f"{pct:+.2f}%" if pct is not None else lbl
        lines.append(f"{arrow} *{_esc(label)}* `{price_str}` `{chg_str}` `{pct_str}`")

    lines.append("")

    # Macro
    spread = macro.get("credit_spread")
    regime = macro.get("credit_regime", "")
    s5fi   = macro.get("s5fi")
    mmth   = macro.get("mmth")
    status = macro.get("breadth_status", "")

    if spread:
        regime_icon = {"Complacent": "🟢", "Yellow Flag": "🟡", "Stress": "🔴"}.get(regime, "⚪")
        lines.append(f"{regime_icon} *HY Spread:* `{spread:.2f}%` — *{_esc(regime)}*")

    if s5fi is not None:
        bicon = "🔥" if s5fi < 10 else "⚠️" if s5fi < 20 else "📊"
        parts = [f"S5FI `{s5fi:.1f}%`"]
        if mmth is not None:
            parts.append(f"MMTH `{mmth:.1f}%`")
        lines.append(f"{bicon} *Breadth:* " + " \\| ".join(parts))

    if status:
        lines.append(f"📌 *Status:* \\[{_esc(status)}\\]")

    if rev.get("signal_detected"):
        lines += ["", f"🔥 *\\[REVERSAL SIGNAL DETECTED\\]*",
                  _esc(rev.get("description", ""))]

    lines.append("")

    if ana.get("analysis_para1"):
        lines += [f"*📊 Analysis:*", _esc(ana["analysis_para1"][:500]), ""]
    if ana.get("analysis_para2"):
        lines += [f"*⚙️ Mechanical Plan:*", _esc(ana["analysis_para2"][:500]), ""]

    tickers = ana.get("ticker_intel", [])
    if tickers:
        lines.append("*🎯 Ticker Intel:*")
        for t in tickers:
            grade = t.get("grade", "")
            g_icon = "✅" if grade.startswith("A") else "⚠️" if grade.startswith("B") else "❌"
            lines.append(
                f"  {g_icon} `{t.get('ticker','')}` *{_esc(t.get('company',''))}* "
                f"Grade {_esc(grade)}: {_esc(str(t.get('reason',''))[:140])}"
            )

    return "\n".join(line for line in lines if line is not None)[:4090]


def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print("  Telegram: not configured — skipping")
        return False
    chat_ids = [cid.strip() for cid in TELEGRAM_CHAT.split(",")]
    ok = False
    for chat_id in chat_ids:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text,
                      "parse_mode": "MarkdownV2", "disable_web_page_preview": True},
                timeout=15,
            )
            r.raise_for_status()
            print(f"  Telegram: sent to {chat_id} ✓")
            ok = True
        except Exception as e:
            print(f"  Telegram failed for {chat_id}: {e}")
    return ok


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    now_et  = datetime.now(ET_TZ)
    hour    = now_et.hour
    session = "Pre-Market" if hour < 9 else "Post-Market" if hour >= 16 else "Market Hours"
    print(f"Market Brief — {now_et.strftime('%Y-%m-%d %H:%M ET')} [{session}]")

    print("  [1/4] Fetching indices (S&P 500 / Nasdaq / VIX / 10Y)…")
    indices = fetch_indices()

    print("  [2/4] Fetching credit spread (FRED)…")
    credit = fetch_credit_spread()   # {"date": ..., "value": ...}

    print("  [3/4] Fetching market breadth (TradingView)…")
    breadth_data = fetch_breadth()   # {"s5fi": ..., "mmth": ...}

    print("  [4/4] Fetching global indices…")
    global_data = fetch_global()

    # Labels
    sp500  = indices.get("sp500")  or {}
    nasdaq = indices.get("nasdaq") or {}
    vix_d  = indices.get("vix")    or {}
    y10_d  = indices.get("yield10")or {}

    spread_val  = credit.get("value") if credit else None
    s5fi        = breadth_data.get("s5fi")
    mmth        = breadth_data.get("mmth")
    status_lbl  = breadth_status_label(s5fi)

    # SPY 200MA for reversal
    spy_ma200 = None
    if s5fi is not None and s5fi < 25:
        print("  Fetching SPY MA200 for reversal check…")
        spy_ma200 = fetch_spy_ma200()

    reversal = detect_reversal(indices, s5fi, spy_ma200)

    # Build global snapshot rows
    global_snapshot = []
    for key, price_label in [
        ("sp500",   sp500.get("label",   "S&P 500")),
        ("nasdaq",  nasdaq.get("label",  "Nasdaq 100")),
        ("vix",     vix_d.get("label",   "VIX")),
        ("yield10", y10_d.get("label",   "10Y Yield")),
    ]:
        d = indices.get(key) or {}
        row = {
            "label":     d.get("label", price_label),
            "price":     d.get("price"),
            "change":    d.get("change"),
            "change_pct": d.get("change_pct"),
        }
        if key == "vix" and d.get("price"):
            row["zone_label"] = vix_zone(d["price"])
        if key == "yield10" and d.get("change") is not None:
            row["trend_label"] = yield_trend(d["change"])
        global_snapshot.append(row)

    macro_breadth = {
        "credit_spread":  spread_val,
        "credit_date":    credit.get("date") if credit else None,
        "credit_regime":  credit_regime(spread_val) if spread_val else None,
        "s5fi":           s5fi,
        "mmth":           mmth,
        "breadth_flush":  bool(s5fi is not None and s5fi < 15),
        "generational":   bool(s5fi is not None and s5fi < 10),
        "breadth_status": status_lbl,
    }

    context = {
        "indices":  indices,
        "credit":   {"value": spread_val, "date": credit.get("date") if credit else None},
        "breadth":  breadth_data,
        "global":   global_data,
        "reversal": reversal,
    }

    print("  Running Gemini…")
    analysis = analyze_with_gemini(context, session, now_et)

    result = {
        "generated_at":    now_et.strftime("%B %d, %Y (%H:%M %Z)"),
        "session":         session,
        "global_snapshot": global_snapshot,
        "global_indices":  global_data,
        "macro_breadth":   macro_breadth,
        "reversal_signals": reversal,
        "mood":            analysis.get("mood", ""),
        "analysis": {
            "analysis_para1":   analysis.get("analysis_para1", ""),
            "analysis_para2":   analysis.get("analysis_para2", ""),
            "technical_signal": analysis.get("technical_signal", ""),
            "ticker_intel":     analysis.get("ticker_intel", []),
        },
    }
    if analysis.get("error"):
        result["error"] = analysis["error"]

    out = Path("public/market_brief.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved → {out}")

    tg_msg = build_telegram_message(result)
    send_telegram(tg_msg)
    print("  Done.")


if __name__ == "__main__":
    main()
