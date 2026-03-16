"""
Elite Market Regime & Breadth Dashboard
Streamlit app — run with: streamlit run market_dashboard.py
"""

import warnings
warnings.filterwarnings("ignore")

import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime

# ── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Market Health Dashboard",
    layout="wide",
    page_icon="📊",
    initial_sidebar_state="collapsed",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .stApp { background-color: #09090b; }
  .block-container { padding: 1.5rem 2rem 2rem; max-width: 1400px; }
  h1, h2, h3, h4 { color: #f4f4f5; }
  div[data-testid="stHorizontalBlock"] { gap: 1.5rem; }
  .metric-row {
    display: flex; justify-content: space-between;
    padding: 4px 0; border-bottom: 1px solid #27272a;
  }
  .metric-label { color: #71717a; font-size: 11px; }
  .metric-val   { font-size: 11px; font-weight: 600; font-family: monospace; }
  .alert-box {
    background: rgba(239,68,68,0.10); border: 1px solid rgba(239,68,68,0.35);
    border-radius: 8px; padding: 12px 16px; color: #fca5a5; margin-bottom: 12px;
  }
  .section-label {
    color: #52525b; font-size: 10px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px;
  }
  /* dataframe dark */
  .stDataFrame { background: #18181b; }
</style>
""", unsafe_allow_html=True)

# ── Constants ─────────────────────────────────────────────────────────────────
INDICES      = ["QQQ", "SPY", "IWM"]          # display order: left → right
INDEX_NAMES  = {"QQQ": "Nasdaq 100", "SPY": "S&P 500", "IWM": "Russell 2000"}
BREADTH_SRC  = {"QQQ": "nasdaq100",  "SPY": "sp500",   "IWM": "sp500"}  # IWM uses S&P 500 proxy

# ── Data helpers ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=86400, show_spinner=False)
def get_sp500_tickers() -> list[str]:
    try:
        df = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        return df["Symbol"].str.replace(".", "-", regex=False).tolist()
    except Exception as e:
        st.warning(f"S&P 500 ticker fetch failed: {e}")
        return []


@st.cache_data(ttl=86400, show_spinner=False)
def get_nasdaq100_tickers() -> list[str]:
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100")
        for tbl in tables:
            lower_cols = {c.lower(): c for c in tbl.columns}
            key = lower_cols.get("ticker") or lower_cols.get("symbol")
            if key:
                tickers = tbl[key].dropna().str.replace(".", "-", regex=False).tolist()
                if len(tickers) > 50:
                    return tickers
        return []
    except Exception as e:
        st.warning(f"Nasdaq 100 ticker fetch failed: {e}")
        return []


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_breadth(tickers: tuple) -> pd.Series:
    """Daily series: % of stocks above their 50-day SMA."""
    if not tickers:
        return pd.Series(dtype=float)
    try:
        raw = yf.download(list(tickers), period="1y", interval="1d",
                          auto_adjust=True, progress=False, threads=True)
        closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
        if isinstance(closes, pd.Series):
            closes = closes.to_frame()
        sma50  = closes.rolling(50).mean()
        above  = (closes > sma50).astype(float)
        valid  = closes.notna().astype(float)
        pct    = above.sum(axis=1) / valid.sum(axis=1) * 100
        return pct.dropna()
    except Exception as e:
        st.warning(f"Breadth calculation failed: {e}")
        return pd.Series(dtype=float)


@st.cache_data(ttl=900, show_spinner=False)
def fetch_index_prices() -> dict[str, pd.Series]:
    """Returns {ticker: close_series} for all indices."""
    try:
        raw = yf.download(INDICES, period="1y", interval="1d",
                          auto_adjust=True, progress=False, group_by="ticker")
        out = {}
        for t in INDICES:
            try:
                out[t] = raw[t]["Close"].dropna()
            except Exception:
                out[t] = pd.Series(dtype=float)
        return out
    except Exception as e:
        st.error(f"Index price fetch failed: {e}")
        return {t: pd.Series(dtype=float) for t in INDICES}


# ── Analysis ──────────────────────────────────────────────────────────────────

def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    d   = series.diff()
    g   = d.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    l   = (-d.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    rs  = g / l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def sma_slope(series: pd.Series, win: int, lookback: int = 10) -> float:
    """Normalised slope of the SMA over last `lookback` days (% / day)."""
    s = series.rolling(win).mean().dropna().tail(lookback)
    if len(s) < 2:
        return 0.0
    x = np.arange(len(s))
    return float(np.polyfit(x, s.values, 1)[0] / s.mean() * 100)


def determine_regime(closes: pd.Series, breadth_val: float) -> tuple:
    """Returns (label, hex_color, details_dict)."""
    if len(closes) < 55 or np.isnan(breadth_val):
        return "Unknown", "#71717a", {}

    price  = float(closes.iloc[-1])
    sma10  = float(closes.rolling(10).mean().iloc[-1])
    sma20  = float(closes.rolling(20).mean().iloc[-1])
    sma50  = float(closes.rolling(50).mean().iloc[-1])
    sma200 = float(closes.rolling(200).mean().iloc[-1]) if len(closes) >= 200 else None
    rsi    = float(calc_rsi(closes).iloc[-1])
    slope50 = sma_slope(closes, 50)

    def pct(a, b): return round((a / b - 1) * 100, 2) if b else None

    details = {
        "Price":     round(price, 2),
        "vs 10SMA":  pct(price, sma10),
        "vs 20SMA":  pct(price, sma20),
        "vs 50SMA":  pct(price, sma50),
        "vs 200SMA": pct(price, sma200) if sma200 else None,
        "RSI(14)":   round(rsi, 1),
        "Breadth":   round(breadth_val, 1),
        "50SMA Slope": round(slope50, 3),
    }

    # ── Strong ──
    if price > sma10 > sma20 > sma50 and breadth_val > 70 and 60 <= rsi <= 80:
        return "Strong", "#22c55e", details

    # ── Lagging ──
    below_200 = (sma200 is None) or (price < sma200)
    if price < sma50 and below_200 and breadth_val < 30:
        return "Lagging", "#ef4444", details

    # ── Weakening (price ok but breadth diverging) ──
    if price >= sma50 and breadth_val < 40:
        details["_divergence_flag"] = True
        return "Weakening", "#f97316", details

    # ── Mediocre ──
    return "Mediocre", "#f59e0b", details


def check_divergence_alert(closes: pd.Series, breadth: pd.Series, lookback: int = 20) -> bool:
    """True if index is near 20-day high while breadth has fallen."""
    if len(closes) < lookback or len(breadth) < lookback:
        return False
    at_high = float(closes.iloc[-1]) >= float(closes.tail(lookback).max()) * 0.99
    breadth_fell = float(breadth.iloc[-1]) < float(breadth.iloc[-lookback])
    return bool(at_high and breadth_fell)


# ── Chart builders ────────────────────────────────────────────────────────────

def _hex_rgb(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    return f"{int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)}"


def make_gauge(val: float, ticker: str, color: str) -> go.Figure:
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=val,
        number={"suffix": "%", "font": {"size": 30, "color": "#f4f4f5"}},
        gauge={
            "axis": {
                "range": [0, 100], "tickwidth": 1,
                "tickcolor": "#52525b", "tickfont": {"color": "#71717a", "size": 9},
            },
            "bar": {"color": color, "thickness": 0.28},
            "bgcolor": "#18181b",
            "borderwidth": 0,
            "steps": [
                {"range": [0,  30],  "color": "rgba(239,68,68,0.18)"},
                {"range": [30, 40],  "color": "rgba(249,115,22,0.12)"},
                {"range": [40, 60],  "color": "rgba(245,158,11,0.10)"},
                {"range": [60, 70],  "color": "rgba(132,204,22,0.10)"},
                {"range": [70, 100], "color": "rgba(34,197,94,0.18)"},
            ],
            "threshold": {"line": {"color": "#52525b", "width": 2}, "thickness": 0.75, "value": 50},
        },
        title={
            "text": (
                f"% Above 50-day SMA<br>"
                f"<span style='font-size:10px;color:#71717a'>"
                f"{'Nasdaq 100' if ticker=='QQQ' else 'S&P 500'} constituents"
                f"{'  (proxy)' if ticker=='IWM' else ''}</span>"
            ),
            "font": {"color": "#a1a1aa", "size": 12},
        },
    ))
    fig.update_layout(
        height=230, margin=dict(l=20, r=20, t=55, b=5),
        paper_bgcolor="#09090b", font={"color": "#f4f4f5"},
    )
    return fig


def make_price_chart(closes: pd.Series, ticker: str, color: str) -> go.Figure:
    sma20  = closes.rolling(20).mean()
    sma50  = closes.rolling(50).mean()
    sma200 = closes.rolling(200).mean()
    idx    = closes.tail(90).index

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=idx, y=sma200.reindex(idx), name="200",
        line=dict(color="#52525b", width=1, dash="dot"), opacity=0.7,
    ))
    fig.add_trace(go.Scatter(
        x=idx, y=sma50.reindex(idx), name="50",
        line=dict(color="#f59e0b", width=1.2),
    ))
    fig.add_trace(go.Scatter(
        x=idx, y=sma20.reindex(idx), name="20",
        line=dict(color="#60a5fa", width=1.2),
    ))
    fig.add_trace(go.Scatter(
        x=idx, y=closes.reindex(idx), name=ticker,
        line=dict(color=color, width=2),
        fill="tozeroy", fillcolor=f"rgba({_hex_rgb(color)},0.06)",
    ))
    fig.update_layout(
        height=190, margin=dict(l=0, r=0, t=4, b=0),
        paper_bgcolor="#09090b", plot_bgcolor="#09090b",
        legend=dict(orientation="h", y=1.08, x=0, font=dict(size=9, color="#71717a"),
                    bgcolor="rgba(0,0,0,0)", traceorder="reversed"),
        xaxis=dict(showgrid=False, tickfont=dict(size=9, color="#52525b"),
                   showline=False, zeroline=False),
        yaxis=dict(showgrid=True, gridcolor="#1f1f23", tickfont=dict(size=9, color="#52525b"),
                   showline=False, zeroline=False),
        hovermode="x unified",
    )
    return fig


def regime_badge_html(label: str, color: str) -> str:
    rgb = _hex_rgb(color)
    return (
        f'<span style="background:rgba({rgb},0.15);color:{color};'
        f'padding:2px 10px;border-radius:4px;font-size:11px;font-weight:700;'
        f'border:1px solid rgba({rgb},0.30)">{label}</span>'
    )


# ── App ───────────────────────────────────────────────────────────────────────

st.markdown(
    "## 📊 Market Health Dashboard"
    "<span style='color:#52525b;font-size:12px;margin-left:12px'>Elite Regime & Breadth Analysis</span>",
    unsafe_allow_html=True,
)

col_hdr, col_btn = st.columns([6, 1])
with col_btn:
    if st.button("🔄 Refresh", type="secondary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ── Load data ──
with st.spinner("Loading market data & breadth (this may take ~30 s on first load)…"):
    sp500_tickers    = get_sp500_tickers()
    nasdaq100_tickers = get_nasdaq100_tickers()
    sp500_breadth    = fetch_breadth(tuple(sp500_tickers))   if sp500_tickers    else pd.Series(dtype=float)
    nasdaq_breadth   = fetch_breadth(tuple(nasdaq100_tickers)) if nasdaq100_tickers else pd.Series(dtype=float)
    price_data       = fetch_index_prices()

BREADTH = {"QQQ": nasdaq_breadth, "SPY": sp500_breadth, "IWM": sp500_breadth}

st.markdown(
    f'<p style="color:#52525b;font-size:10px;margin:-6px 0 16px">Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>',
    unsafe_allow_html=True,
)

# ── Compute regimes ──
regimes: dict = {}
spy_closes = price_data.get("SPY", pd.Series(dtype=float))

for ticker in INDICES:
    closes  = price_data.get(ticker, pd.Series(dtype=float))
    breadth = BREADTH[ticker]
    b_val   = float(breadth.iloc[-1]) if len(breadth) > 0 else float("nan")
    label, color, details = determine_regime(closes, b_val)

    rs_vs_spy = None
    if ticker != "SPY" and len(closes) >= 20 and len(spy_closes) >= 20:
        r_idx = float(closes.iloc[-1]) / float(closes.iloc[-20]) - 1
        r_spy = float(spy_closes.iloc[-1]) / float(spy_closes.iloc[-20]) - 1
        rs_vs_spy = round((r_idx - r_spy) * 100, 2)

    regimes[ticker] = {
        "closes":     closes,
        "breadth":    breadth,
        "b_val":      b_val,
        "label":      label,
        "color":      color,
        "details":    details,
        "rs_vs_spy":  rs_vs_spy,
        "divergence": check_divergence_alert(closes, breadth),
    }

# ── Divergence alerts ──
for ticker, r in regimes.items():
    if r["divergence"]:
        st.markdown(
            f'<div class="alert-box">⚠️ <strong>Divergence Alert — {ticker} ({INDEX_NAMES[ticker]})</strong><br>'
            f'<span style="font-size:12px">{ticker} is near its 20-day high but breadth has <em>declined</em> '
            f'over the same period — potential hidden distribution.</span></div>',
            unsafe_allow_html=True,
        )

# ── Three columns: QQQ | SPY | IWM ──
cols = st.columns(3)

for ci, ticker in enumerate(INDICES):   # QQQ=0, SPY=1, IWM=2
    r = regimes[ticker]
    d = r["details"]

    with cols[ci]:
        price_str = f"${d['Price']:,.2f}" if d.get("Price") else "—"

        # Header row
        st.markdown(
            f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">'
            f'  <div>'
            f'    <span style="color:#f4f4f5;font-size:20px;font-weight:700">{ticker}</span>'
            f'    <span style="color:#71717a;font-size:12px;margin-left:8px">{INDEX_NAMES[ticker]}</span>'
            f'  </div>'
            f'  {regime_badge_html(r["label"], r["color"])}'
            f'</div>'
            f'<div style="color:{r["color"]};font-size:26px;font-weight:700;margin-bottom:8px">{price_str}</div>',
            unsafe_allow_html=True,
        )

        # Price chart
        if len(r["closes"]) > 55:
            st.plotly_chart(
                make_price_chart(r["closes"], ticker, r["color"]),
                use_container_width=True, config={"displayModeBar": False},
            )

        # Breadth gauge
        if not np.isnan(r["b_val"]):
            st.plotly_chart(
                make_gauge(r["b_val"], ticker, r["color"]),
                use_container_width=True, config={"displayModeBar": False},
            )

        # Metrics rows
        def _row(label_txt, val, kind="pct"):
            if val is None:
                return
            if kind == "pct":
                col = "#22c55e" if val > 0 else "#ef4444"
                vs  = f"{'+' if val > 0 else ''}{val:.1f}%"
            elif kind == "rsi":
                col = "#22c55e" if 60 <= val <= 80 else "#f59e0b" if 45 <= val < 60 else "#ef4444"
                vs  = f"{val:.1f}"
            else:
                col = "#a1a1aa"
                vs  = f"{val:.3f}"
            st.markdown(
                f'<div class="metric-row">'
                f'  <span class="metric-label">{label_txt}</span>'
                f'  <span class="metric-val" style="color:{col}">{vs}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        _row("vs 10 SMA",  d.get("vs 10SMA"))
        _row("vs 20 SMA",  d.get("vs 20SMA"))
        _row("vs 50 SMA",  d.get("vs 50SMA"))
        _row("vs 200 SMA", d.get("vs 200SMA"))
        _row("RSI (14)",   d.get("RSI(14)"),   kind="rsi")
        _row("50SMA Slope",d.get("50SMA Slope"), kind="slope")
        if ticker != "SPY" and r["rs_vs_spy"] is not None:
            _row("RS vs SPY (20d)", r["rs_vs_spy"])

# ── Health Summary Table ──
st.markdown("---")
st.markdown('<p class="section-label">Health Summary Table</p>', unsafe_allow_html=True)

rows = []
for ticker in INDICES:
    r = regimes[ticker]
    d = r["details"]
    above_all = all(d.get(k, -1) is not None and d.get(k, -1) > 0
                    for k in ["vs 10SMA", "vs 20SMA", "vs 50SMA"])
    price_status = (
        "Above All SMAs" if above_all
        else "Below 50 SMA" if (d.get("vs 50SMA") or 1) < 0
        else "Mixed"
    )
    rows.append({
        "Ticker":          ticker,
        "Index":           INDEX_NAMES[ticker],
        "Price":           f"${d.get('Price', 0):,.2f}" if d.get("Price") else "—",
        "Price Status":    price_status,
        "Breadth Score":   f"{r['b_val']:.1f}%" if not np.isnan(r["b_val"]) else "—",
        "RSI (14)":        f"{d.get('RSI(14)', '—')}",
        "RS vs SPY (20d)": (
            f"{'+' if (r['rs_vs_spy'] or 0) > 0 else ''}{r['rs_vs_spy']:.1f}%"
            if r["rs_vs_spy"] is not None else "—"
        ),
        "Regime":          r["label"],
    })

df = pd.DataFrame(rows)

def _color_regime(val):
    m = {"Strong": "color: #22c55e", "Mediocre": "color: #f59e0b",
         "Lagging": "color: #ef4444", "Weakening": "color: #f97316"}
    return m.get(val, "color: #a1a1aa")

styled = df.style.map(_color_regime, subset=["Regime"])
st.dataframe(styled, use_container_width=True, hide_index=True)
