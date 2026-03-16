"""
gapper_analyzer.py — Interactive Pre-Market Gapper Analyzer
Run: streamlit run gapper_analyzer.py
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET_xml
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

ET_TZ = ZoneInfo("America/New_York")
HISTORY_FILE = Path("data/gapper_history.json")
HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

CATEGORIES = [
    "Earnings", "Upgrade", "FDA", "Thematic Narratives", "Government Policy",
    "New Contract/Partnership", "Institutional Buying", "Insider Buying", "Others",
]

HYPOTHESIS_RULES = {
    "Earnings":                 ("Gap and Go",      "5-min ORB above PM High"),
    "New Contract/Partnership": ("Gap and Go",      "5-min ORB above PM High"),
    "Thematic Narratives":      ("RS Hold",         "Dip-buy 9-EMA / VWAP"),
    "Government Policy":        ("RS Hold",         "Dip-buy 9-EMA / VWAP"),
    "Institutional Buying":     ("RS Hold",         "Watch for continuation above VWAP"),
    "Insider Buying":           ("RS Hold",         "Look for base breakout"),
    "Upgrade":                  ("Fade Watch",      "Gap-fill risk — wait for reversal"),
    "FDA":                      ("Volatility Trap", "Mean-reversion likely after open"),
    "Others":                   ("RS Hold",         "Monitor price action at open"),
}

CONVICTION_BASE = {
    "Gap and Go": 80, "RS Hold": 62, "Fade Watch": 35, "Volatility Trap": 28,
}

# ─── Page Config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Pre-Market Gapper Analyzer",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CSS ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap');

[data-testid="stApp"] {
  background: #09090b;
  color: #e4e4e7;
  font-family: 'JetBrains Mono', 'Courier New', monospace;
}
[data-testid="stSidebar"] {
  background: #0c0c0f;
  border-right: 1px solid #1c1c24;
}
[data-testid="stSidebar"] > div { padding-top: 1rem; }

/* Sidebar labels */
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stNumberInput label {
  color: #71717a !important; font-size: 10px !important;
  text-transform: uppercase; letter-spacing: 0.08em;
}
[data-testid="stSidebar"] input[type="number"] {
  background: #18181b !important; color: #e4e4e7 !important;
  border: 1px solid #3f3f46 !important; border-radius: 4px !important;
  font-family: monospace !important; font-size: 12px !important;
}
[data-testid="stSidebar"] input[type="number"]:focus {
  border-color: #2563eb !important; box-shadow: 0 0 0 1px #2563eb !important;
}

/* Sidebar buttons */
[data-testid="stSidebar"] .stButton > button {
  background: #18181b; color: #a1a1aa;
  border: 1px solid #3f3f46; border-radius: 4px;
  width: 100%; font-size: 11px; font-family: monospace;
  letter-spacing: 0.06em; font-weight: 600;
  transition: all 0.15s;
}
[data-testid="stSidebar"] .stButton > button:hover {
  background: #27272a; color: #e4e4e7; border-color: #52525b;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"] {
  background: linear-gradient(135deg, #1d4ed8, #2563eb);
  color: #fff; border: none;
  box-shadow: 0 0 14px rgba(37,99,235,0.4);
}
[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {
  background: linear-gradient(135deg, #2563eb, #3b82f6);
  box-shadow: 0 0 20px rgba(37,99,235,0.6);
}

/* Divider */
hr { border-color: #1c1c24 !important; margin: 0.6rem 0 !important; }

/* Main area */
.block-container { padding: 0 1.5rem 2rem !important; max-width: 100% !important; }
h1, h2, h3 { color: #e4e4e7 !important; font-family: monospace !important; }

/* Terminal header */
.term-header {
  background: linear-gradient(90deg, #0c0c0f, #111118);
  border-bottom: 1px solid #1c1c24;
  padding: 14px 0 10px; margin-bottom: 20px;
  display: flex; align-items: center; gap: 10px;
}
.pulse { display: inline-block; width: 8px; height: 8px; background: #22c55e;
  border-radius: 50%; box-shadow: 0 0 7px #22c55e;
  animation: blink 2s infinite; vertical-align: middle; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.3} }

/* Table */
.gapper-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.gapper-table th {
  background: #111118; color: #52525b; font-size: 9px;
  text-transform: uppercase; letter-spacing: 0.1em;
  padding: 8px 12px; border-bottom: 1px solid #1c1c24;
  white-space: nowrap; text-align: left; position: sticky; top: 0; z-index: 1;
}
.gapper-table td {
  padding: 10px 12px; border-bottom: 1px solid #18181b;
  vertical-align: top; color: #d4d4d8;
}
.gapper-table tr:hover td { background: rgba(39,39,42,0.4); }

/* Glow row */
.glow-row td {
  background: rgba(37,99,235,0.04) !important;
  box-shadow: inset 3px 0 0 #2563eb;
}
.glow-row:hover td { background: rgba(37,99,235,0.08) !important; }

/* Ticker */
.ticker-sym { color: #60a5fa; font-weight: 700; font-size: 14px; text-decoration: none; }
.ticker-sym:hover { color: #93c5fd; }
.mkt-cap { color: #52525b; font-size: 10px; margin-top: 3px; }

/* Gap cell */
.gap-up { color: #4ade80; font-weight: 700; font-size: 14px; }
.gap-dn { color: #f87171; font-weight: 700; font-size: 14px; }

/* Volume */
.vol-val { font-weight: 600; }
.rvol-high { color: #60a5fa; }
.rvol-norm { color: #52525b; }

/* Badges */
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 3px;
  font-size: 9px; font-weight: 700; letter-spacing: 0.06em;
  text-transform: uppercase; white-space: nowrap;
}
.b-earnings    { background: rgba(34,197,94,.12);  color: #4ade80; border: 1px solid rgba(34,197,94,.25); }
.b-upgrade     { background: rgba(234,179,8,.12);  color: #fbbf24; border: 1px solid rgba(234,179,8,.25); }
.b-fda         { background: rgba(239,68,68,.12);  color: #f87171; border: 1px solid rgba(239,68,68,.25); }
.b-thematic    { background: rgba(168,85,247,.12); color: #c084fc; border: 1px solid rgba(168,85,247,.25); }
.b-gov         { background: rgba(59,130,246,.12); color: #60a5fa; border: 1px solid rgba(59,130,246,.25); }
.b-contract    { background: rgba(20,184,166,.12); color: #2dd4bf; border: 1px solid rgba(20,184,166,.25); }
.b-insider     { background: rgba(251,146,60,.12); color: #fb923c; border: 1px solid rgba(251,146,60,.25); }
.b-other       { background: rgba(113,113,122,.12);color: #71717a; border: 1px solid rgba(113,113,122,.25); }

/* Conviction */
.conv-wrap { display: flex; align-items: center; gap: 7px; }
.conv-bar  { height: 4px; border-radius: 2px; background: #27272a; width: 70px; }
.conv-fill { height: 4px; border-radius: 2px; }

/* Hypothesis */
.hyp-name { font-weight: 700; font-size: 11px; }
.hyp-detail { font-size: 10px; color: #52525b; margin-top: 3px; }

/* Reasoning */
.reasoning { font-size: 11px; color: #a1a1aa; line-height: 1.55; max-width: 300px; }

/* Empty state */
.empty {
  text-align: center; padding: 90px 0; color: #3f3f46;
}
.empty .e-icon { font-size: 44px; margin-bottom: 14px; }
.empty h3 { font-size: 16px; color: #52525b; margin: 0 0 8px; font-weight: 600; }
.empty p  { font-size: 12px; color: #3f3f46; }

/* Status bar */
.status-bar {
  display: flex; align-items: center; gap: 14px;
  padding: 8px 14px; background: #111118;
  border: 1px solid #1c1c24; border-radius: 6px;
  margin-bottom: 14px; font-size: 11px;
}
.status-bar .count { color: #e4e4e7; font-weight: 700; }
.status-bar .hint  { color: #52525b; }

/* History card */
.hist-meta { font-size: 11px; color: #52525b; margin-bottom: 6px; }
.hist-spy  { color: #60a5fa; }

/* Scrollable table container */
.table-wrap { overflow-x: auto; overflow-y: auto; max-height: 78vh; border-radius: 6px; border: 1px solid #1c1c24; }

/* Streamlit expander */
[data-testid="stExpander"] {
  background: #0f0f12; border: 1px solid #1c1c24 !important;
  border-radius: 6px !important;
}
</style>
""", unsafe_allow_html=True)

# ─── Session State ───────────────────────────────────────────────────────────
for k, v in [("scan_results", []), ("analysis_done", False), ("show_history", False)]:
    if k not in st.session_state:
        st.session_state[k] = v

has_results = len(st.session_state.scan_results) > 0

# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("#### ⬡ COMMAND CENTER")
    st.divider()

    st.markdown("<span style='font-size:10px;color:#52525b;text-transform:uppercase;letter-spacing:.08em'>PRICE RANGE</span>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        min_price = st.number_input("Min $", value=5, min_value=1, step=1, label_visibility="visible")
    with c2:
        max_price = st.number_input("Max $", value=200, min_value=10, step=10)

    min_gap = st.number_input("Min Gap %", value=5.0, min_value=0.5, step=0.5, format="%.1f")

    c1, c2 = st.columns(2)
    with c1:
        min_pm_vol_k = st.number_input("PM Vol (K)", value=200, min_value=10, step=50)
    with c2:
        min_adv_k = st.number_input("ADV 30D (K)", value=500, min_value=50, step=50)

    min_mkt_cap_b = st.number_input("Min Mkt Cap ($B)", value=2.0, min_value=0.1, step=0.5, format="%.1f")

    st.divider()

    load_btn    = st.button("⚡ LOAD GAPPERS",  type="primary",  use_container_width=True)
    analyze_btn = st.button("🤖 ANALYZE (AI)",  use_container_width=True, disabled=not has_results)

    st.divider()

    c1, c2 = st.columns(2)
    with c1:
        save_btn = st.button("💾 SAVE",    use_container_width=True, disabled=not has_results)
    with c2:
        hist_btn = st.button("📂 HISTORY", use_container_width=True)

    if has_results:
        st.divider()
        analyzed = st.session_state.analysis_done
        n = len(st.session_state.scan_results)
        st.markdown(
            f"<div style='font-size:11px;color:#52525b'>"
            f"<b style='color:#e4e4e7'>{n}</b> gappers loaded<br>"
            f"{'<span style=\"color:#22c55e\">✓ AI analyzed</span>' if analyzed else '<span style=\"color:#f59e0b\">⏳ pending analysis</span>'}"
            f"</div>",
            unsafe_allow_html=True,
        )

# ─── Helper Functions ─────────────────────────────────────────────────────────
def fmt_vol(v: int) -> str:
    if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
    if v >= 1_000:     return f"{v/1_000:.0f}K"
    return str(v)

def fmt_cap(v: float) -> str:
    if v >= 1e12: return f"${v/1e12:.1f}T"
    if v >= 1e9:  return f"${v/1e9:.1f}B"
    if v >= 1e6:  return f"${v/1e6:.0f}M"
    return f"${v:.0f}"

def category_badge(cat: str) -> str:
    if not cat: return "—"
    c = cat.lower()
    if   "earn"    in c: cls, lbl = "b-earnings", "EARNINGS"
    elif "upgrade" in c: cls, lbl = "b-upgrade",  "UPGRADE"
    elif "fda"     in c: cls, lbl = "b-fda",       "FDA"
    elif "themat"  in c: cls, lbl = "b-thematic",  "THEMATIC"
    elif "gov"     in c or "policy" in c: cls, lbl = "b-gov",      "GOV/POLICY"
    elif "contract" in c or "partner" in c: cls, lbl = "b-contract", "CONTRACT"
    elif "insider" in c: cls, lbl = "b-insider",  "INSIDER"
    else:                cls, lbl = "b-other",    "OTHERS"
    return f'<span class="badge {cls}">{lbl}</span>'

def hypothesis_html(hyp: str | None, detail: str | None) -> str:
    if not hyp: return '<span style="color:#3f3f46">—</span>'
    h = hyp.lower()
    if   "gap and go"    in h: color, icon = "#4ade80", "🚀"
    elif "rs hold"       in h: color, icon = "#60a5fa", "📊"
    elif "fade"          in h: color, icon = "#f97316", "⚠️"
    elif "volatility"    in h or "trap" in h: color, icon = "#c084fc", "⚡"
    else:                      color, icon = "#a1a1aa", "○"
    d = f'<div class="hyp-detail">{detail}</div>' if detail else ""
    return f'<div class="hyp-name" style="color:{color}">{icon} {hyp}</div>{d}'

def conviction_html(v: int | None) -> str:
    if v is None: return '<span style="color:#3f3f46">—</span>'
    pct = min(100, max(0, v))
    color = "#22c55e" if pct >= 75 else "#f59e0b" if pct >= 50 else "#ef4444"
    return (
        f'<div class="conv-wrap">'
        f'<div class="conv-bar"><div class="conv-fill" style="width:{pct}%;background:{color}"></div></div>'
        f'<span style="font-size:11px;color:{color};font-weight:700">{pct}%</span>'
        f'</div>'
    )

# ─── Data Functions ───────────────────────────────────────────────────────────
def load_gappers(min_price, max_price, min_gap_pct, min_pm_vol, min_mkt_cap_b, min_adv):
    try:
        from tradingview_screener import Query, col
        _, df = (
            Query()
            .select(
                "name", "close", "premarket_change", "premarket_volume",
                "market_cap_basic", "average_volume_30d_calc",
                "relative_volume_intraday|5",
            )
            .where(
                col("premarket_change")       >= min_gap_pct,
                col("premarket_volume")       >= min_pm_vol,
                col("market_cap_basic")       >= min_mkt_cap_b * 1e9,
                col("average_volume_30d_calc") >= min_adv,
                col("close")                  >= min_price,
                col("close")                  <= max_price,
            )
            .order_by("premarket_change", ascending=False)
            .limit(25)
            .get_scanner_data()
        )
        if df is None or df.empty:
            return []
        results = []
        for _, row in df.iterrows():
            avg_vol = int(row.get("average_volume_30d_calc") or 0) or 1
            pm_vol  = int(row.get("premarket_volume") or 0)
            rvol    = round(float(row.get("relative_volume_intraday|5") or 0), 2)
            results.append({
                "ticker":      str(row.get("name", "")),
                "price":       round(float(row.get("close") or 0), 2),
                "gap_pct":     round(float(row.get("premarket_change") or 0), 2),
                "pm_volume":   pm_vol,
                "avg_vol_30d": avg_vol,
                "mkt_cap":     int(row.get("market_cap_basic") or 0),
                "rvol":        rvol,
                # AI fields (empty until analyzed)
                "category": None, "reasoning": None,
                "hypothesis": None, "hypothesis_detail": None, "conviction": None,
            })
        return results
    except Exception as e:
        st.error(f"TradingView scan failed: {e}")
        return []


def fetch_headlines(ticker: str) -> list[str]:
    try:
        url = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET_xml.fromstring(resp.content)
        cutoff = datetime.now(timezone.utc).__class__.now(timezone.utc) - \
                 __import__("datetime").timedelta(hours=24)
        headlines = []
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            pub   = item.findtext("pubDate", "")
            if not title: continue
            try:
                if parsedate_to_datetime(pub) < cutoff: continue
            except Exception:
                pass
            headlines.append(title)
            if len(headlines) >= 6: break
        return headlines
    except Exception:
        return []


def run_gemini_analysis(stock: dict, headlines: list[str]) -> dict:
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        return _rule_based_fallback(stock, headlines)
    try:
        from google import genai
        client = genai.Client(api_key=gemini_key)
        hl_text = "\n".join(f"- {h}" for h in headlines) if headlines else "No recent headlines."
        prompt = f"""You are an institutional pre-market trading analyst.

Ticker: {stock['ticker']}
Pre-market gap: +{stock['gap_pct']}%
PM Volume: {fmt_vol(stock['pm_volume'])}  RVOL: {stock['rvol']}x  Mkt Cap: {fmt_cap(stock['mkt_cap'])}

Recent headlines (last 24h):
{hl_text}

Classify the PRIMARY catalyst. Choose ONE category:
Earnings | Upgrade | FDA | Thematic Narratives | Government Policy | New Contract/Partnership | Institutional Buying | Insider Buying | Others

Rules: "Earnings" = company reported results. "Upgrade" = analyst raised rating/target. "FDA" = drug/trial/approval news. "Institutional Buying" = fund disclosures. "Insider Buying" = executive purchases.

Reply in valid JSON only (no markdown):
{{"category": "<category>", "reasoning": "<1-2 sentence explanation of the catalyst>"}}"""

        resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        raw  = resp.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        parsed   = json.loads(raw.strip())
        category = parsed.get("category", "Others")
        if category not in CATEGORIES:
            category = "Others"
        reasoning = parsed.get("reasoning", "")
        hyp, detail = HYPOTHESIS_RULES.get(category, HYPOTHESIS_RULES["Others"])
        base    = CONVICTION_BASE.get(hyp, 55)
        conv    = min(99, int(base + (stock["rvol"] - 2) * 3))
        return {"category": category, "reasoning": reasoning,
                "hypothesis": hyp, "hypothesis_detail": detail, "conviction": conv}
    except Exception as e:
        return _rule_based_fallback(stock, headlines)


def _rule_based_fallback(stock: dict, headlines: list[str]) -> dict:
    text = " ".join(headlines).lower()
    if   any(w in text for w in ["earnings", "beat", "revenue", "eps", "quarterly"]):
        cat = "Earnings"
    elif any(w in text for w in ["fda", "clinical", "trial", "drug", "approval", "phase"]):
        cat = "FDA"
    elif any(w in text for w in ["upgrade", "price target", "overweight", "analyst"]):
        cat = "Upgrade"
    elif any(w in text for w in ["contract", "partnership", "deal", "agreement", "awarded"]):
        cat = "New Contract/Partnership"
    elif any(w in text for w in ["policy", "government", "regulation", "tariff", "executive order"]):
        cat = "Government Policy"
    else:
        cat = "Others"
    hyp, detail = HYPOTHESIS_RULES.get(cat, HYPOTHESIS_RULES["Others"])
    base = CONVICTION_BASE.get(hyp, 55)
    conv = min(99, int(base + (stock["rvol"] - 2) * 3))
    reason = headlines[0] if headlines else "No catalyst identified from headlines."
    return {"category": cat, "reasoning": reason,
            "hypothesis": hyp, "hypothesis_detail": detail, "conviction": conv}


def analyze_all(tickers_data: list[dict]) -> list[dict]:
    results = [dict(s) for s in tickers_data]
    prog = st.progress(0, text="Starting AI analysis…")
    for i, stock in enumerate(results):
        ticker = stock["ticker"]
        prog.progress((i + 0.3) / len(results), text=f"Fetching news: {ticker}…")
        headlines = fetch_headlines(ticker)
        prog.progress((i + 0.7) / len(results), text=f"Gemini analyzing: {ticker}…")
        ai = run_gemini_analysis(stock, headlines)
        results[i].update(ai)
        results[i]["headlines"] = headlines[:3]
        prog.progress((i + 1) / len(results), text=f"Done: {ticker}")
        time.sleep(0.4)
    prog.empty()
    return results


def save_snapshot(data: list[dict]):
    try:
        import yfinance as yf
        spy_p = round(float(yf.Ticker("SPY").fast_info.get("lastPrice", 0)), 2)
        qqq_p = round(float(yf.Ticker("QQQ").fast_info.get("lastPrice", 0)), 2)
    except Exception:
        spy_p, qqq_p = None, None
    entry = {
        "timestamp": datetime.now(ET_TZ).strftime("%Y-%m-%d %H:%M ET"),
        "spy_price": spy_p, "qqq_price": qqq_p,
        "count": len(data), "tickers": data,
    }
    history = []
    if HISTORY_FILE.exists():
        try: history = json.loads(HISTORY_FILE.read_text())
        except Exception: pass
    history.insert(0, entry)
    HISTORY_FILE.write_text(json.dumps(history[:50], indent=2))
    st.success(f"Snapshot saved — {len(data)} gappers @ {entry['timestamp']}")


# ─── Render Table ─────────────────────────────────────────────────────────────
def render_table(data: list[dict], analyzed: bool):
    rows = ""
    for s in data:
        ticker = s["ticker"]
        glow   = (s.get("rvol", 0) >= 5) or (s.get("conviction") or 0) >= 80
        rc     = "glow-row" if glow else ""
        tv_url = f"https://www.tradingview.com/chart/?symbol={ticker}"

        gap_cls = "gap-up" if s["gap_pct"] >= 0 else "gap-dn"
        rvol_c  = "rvol-high" if s.get("rvol", 0) >= 5 else "rvol-norm"

        if analyzed:
            cat_cell   = category_badge(s.get("category"))
            reason_cel = f'<div class="reasoning">{s.get("reasoning") or "—"}</div>'
            hyp_cell   = hypothesis_html(s.get("hypothesis"), s.get("hypothesis_detail"))
            conv_cell  = conviction_html(s.get("conviction"))
        else:
            pending    = '<span style="color:#3f3f46;font-size:10px">PENDING</span>'
            cat_cell   = pending
            reason_cel = pending
            hyp_cell   = pending
            conv_cell  = pending

        rows += f"""
        <tr class="{rc}">
          <td>
            <a href="{tv_url}" target="_blank" class="ticker-sym">{ticker}</a>
            <div class="mkt-cap">{fmt_cap(s['mkt_cap'])}</div>
          </td>
          <td><span class="{gap_cls}">+{s['gap_pct']:.1f}%</span></td>
          <td>
            <div class="vol-val">{fmt_vol(s['pm_volume'])}</div>
            <div class="{rvol_c}" style="font-size:10px">{s.get('rvol',0):.1f}x RVOL</div>
          </td>
          <td>{cat_cell}</td>
          <td>{reason_cel}</td>
          <td>{hyp_cell}</td>
          <td>{conv_cell}</td>
        </tr>"""

    html = f"""
    <div class="table-wrap">
    <table class="gapper-table">
      <thead><tr>
        <th>TICKER</th><th>GAP %</th><th>PM VOL / RVOL</th>
        <th>CATEGORY</th><th>REASONING</th><th>HYPOTHESIS</th><th>CONVICTION</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    </div>"""
    st.markdown(html, unsafe_allow_html=True)


def render_history():
    if not HISTORY_FILE.exists() or HISTORY_FILE.stat().st_size < 10:
        st.markdown(
            '<div class="empty"><div class="e-icon">📂</div>'
            '<h3>No saved history</h3>'
            '<p>Click SAVE after loading gappers to save a session.</p></div>',
            unsafe_allow_html=True,
        )
        return
    try:
        history = json.loads(HISTORY_FILE.read_text())
    except Exception:
        st.error("Failed to load history.json")
        return

    st.markdown(f"### 📂 Saved Sessions ({len(history)})")
    for session in history:
        ctx = ""
        if session.get("spy_price"):
            ctx = f"  ·  SPY ${session['spy_price']} · QQQ ${session.get('qqq_price','?')}"
        label = f"📅  {session['timestamp']}  —  {session.get('count','?')} gappers{ctx}"
        with st.expander(label):
            tickers = session.get("tickers", [])
            if tickers:
                is_analyzed = any(t.get("category") for t in tickers)
                render_table(tickers, analyzed=is_analyzed)


# ─── Button Handlers ─────────────────────────────────────────────────────────
if load_btn:
    with st.spinner("Scanning TradingView Screener…"):
        results = load_gappers(
            min_price=min_price, max_price=max_price,
            min_gap_pct=min_gap,
            min_pm_vol=min_pm_vol_k * 1_000,
            min_mkt_cap_b=min_mkt_cap_b,
            min_adv=min_adv_k * 1_000,
        )
    if results:
        st.session_state.scan_results  = results
        st.session_state.analysis_done = False
        st.rerun()
    else:
        st.warning("No gappers matched current filters. Try lowering thresholds or check market hours (08:00–09:29 AM ET).")

if analyze_btn and has_results:
    analyzed = analyze_all(st.session_state.scan_results)
    st.session_state.scan_results  = analyzed
    st.session_state.analysis_done = True
    st.rerun()

if save_btn and has_results:
    save_snapshot(st.session_state.scan_results)

if hist_btn:
    st.session_state.show_history = not st.session_state.show_history
    st.rerun()

# ─── Header ──────────────────────────────────────────────────────────────────
now_str = datetime.now(ET_TZ).strftime("%H:%M:%S ET")
st.markdown(f"""
<div class="term-header">
  <span class="pulse"></span>
  <span style="font-size:15px;font-weight:700;letter-spacing:.06em">PRE-MARKET GAPPER ANALYZER</span>
  <span style="font-size:10px;color:#3f3f46;margin-left:8px">v2.0 · TERMINAL</span>
  <span style="margin-left:auto;font-size:10px;color:#3f3f46">{now_str}</span>
</div>
""", unsafe_allow_html=True)

# ─── Main Display ────────────────────────────────────────────────────────────
if st.session_state.show_history:
    render_history()

elif has_results:
    analyzed = st.session_state.analysis_done
    n        = len(st.session_state.scan_results)
    ai_hint  = "AI analysis complete" if analyzed else "Click <b>ANALYZE (AI)</b> for catalyst intelligence"
    st.markdown(f"""
    <div class="status-bar">
      <span class="count">{n} GAPPERS</span>
      <span class="hint">·</span>
      <span class="hint">{ai_hint}</span>
      {'<span style="margin-left:auto;font-size:10px;color:#22c55e">● ANALYZED</span>' if analyzed else ''}
    </div>
    """, unsafe_allow_html=True)
    render_table(st.session_state.scan_results, analyzed=analyzed)

else:
    st.markdown("""
    <div class="empty">
      <div class="e-icon">📡</div>
      <h3>No Data Loaded</h3>
      <p>Set filters in the sidebar and click <b>⚡ LOAD GAPPERS</b> to scan.</p>
      <p style="margin-top:6px;font-size:10px;color:#3f3f46">Best results 08:00 – 09:29 AM ET on trading days</p>
    </div>
    """, unsafe_allow_html=True)
