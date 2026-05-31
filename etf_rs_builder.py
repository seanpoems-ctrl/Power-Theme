"""
etf_rs_builder.py — ETF Relative Strength (IBD-style) Builder
==============================================================
Fetches 6-month price history for every ETF in THEME_ETF_MAP, computes:
  - 6M / 3M / 1M total return
  - IBD-style RS score (1–99 percentile rank vs SPY)
    Formula: 0.4×Q4(0-3mo) + 0.2×Q3(3-6mo) + 0.2×Q2(6-9mo) + 0.2×Q1(9-12mo)
    then ranked 1-99 among all ETFs.
Writes: public/etf_rs.json
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_PATH = Path("public/etf_rs.json")

# ── ETF map — loaded from public/etf_map.json (single source of truth) ───────
# To add a new ETF: edit public/etf_map.json only.
def _load_etf_map() -> dict:
    p = Path(__file__).parent / "public" / "etf_map.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Could not load etf_map.json ({e}) — falling back to hardcoded map")
        return {}

_loaded = _load_etf_map()
THEME_ETF_MAP = _loaded if _loaded else {
    # Technology & Innovation
    "Artificial Intelligence":          "AIQ",
    "Semiconductors":                   "SOXX",
    "Memory & Semiconductors":          "DRAM",
    "Cloud Computing":                  "WCLD",
    "Cybersecurity":                    "CIBR",
    "Software":                         "IGV",
    "Disruptive Innovation":            "ARKK",
    "Internet of Things":               "SNSR",
    "FinTech":                          "FINX",
    "Quantum Computing":                "QTUM",
    "5G Connectivity":                  "FIVG",
    # Energy & Resources
    "Energy Renewable":                 "ICLN",
    "Solar Energy":                     "TAN",
    "Wind Energy":                      "FAN",
    "Hydrogen":                         "HYDR",
    "Smart Grid":                       "GRID",
    "Uranium & Nuclear":                "URA",
    "Nuclear Energy":                   "NLR",
    "Energy Traditional":               "XLE",
    "Commodities Metals":               "GDX",
    "Copper Miners":                    "COPX",
    "Metals & Mining":                  "PICK",
    "Rare Earth & Strategic Metals":    "REMX",
    "Silver Miners":                    "SILJ",
    "Commodities Agriculture":          "DBA",
    # Mobility & Industrials
    "Electric Vehicles":                "LIT",
    "Autonomous Systems":               "DRIV",
    "ARK Autonomous Tech & Robotics":   "ARKQ",
    "Robotics":                         "BOTZ",
    "Industrial Automation":            "ROBO",
    "Defense & Aerospace":              "ITA",
    "Transportation & Logistics":       "XTN",
    "Airlines & Travel":                "JETS",
    "Infrastructure":                   "PAVE",
    "Homebuilders":                     "ITB",
    # Healthcare & Life Sciences
    "Healthcare & Biotech":             "XBI",
    "Genomics":                         "GNOM",
    "ARK Genomic Revolution":           "ARKG",
    # Finance & Crypto
    "Crypto & Blockchain":              "BLOK",
    "Regional Banks":                   "KRE",
    # Consumer & Media
    "E-Commerce":                       "IBUY",
    "Social Media":                     "SOCL",
    "Digital Entertainment":            "HERO",
    "Sports Betting & iGaming":         "BETZ",
    # Space, Environment & Agriculture
    "Space Tech":                       "UFO",
    "ARK Space Exploration":            "ARKX",
    "Agriculture & FoodTech":           "MOO",
    # Broad Sector
    "Industrials":                      "XLI",
    "Consumer Staples":                 "XLP",
    "Consumer Discretionary":           "XLY",
    "Financials":                       "XLF",
    "Telecommunications":               "XLC",
    "Real Estate & REITs":              "VNQ",
    "Materials & Mining":               "XLB",
    # Additional ETFs
    "Meme Stocks":                      "MEME",
    "Crypto Mining":                    "WGMI",
    "Clean Energy Alt":                 "PBW",
    "Volatility":                       "SVIX",
    "Social Media Alt":                 "BUZZ",
    "Defense & Aerospace Equal":        "XAR",
    "South Korea Equities":             "EWY",
    "EV & Future Transport":            "FDRV",
    "Software Equal Weight":            "XSW",
    "Semiconductors Equal":             "XSD",
    "Semiconductors Giants":            "SMH",
    "AI & Tech Active":                 "BAI",
    "Natural Gas":                      "UNG",
    "Space Economy":                    "NASA",
    "Telecom Services":                 "XTL",
    "S&P 500 Momentum":                 "SPMO",
    "Steel":                            "SLX",
    "Argentina Equities":               "ARGT",
    "Internet Giants":                  "FDN",
    "Metals & Mining Equal":            "XME",
    "Telecommunications Services":      "IYZ",
    "Transportation":                   "IYT",
    # ── Jeff Sun's 53 Equal-Weight Industry Group ETFs (additional) ──
    "Natural Gas Producers":            "FCG",
    "Banking Equal Weight":             "KBE",
    "Energy Equipment":                 "XES",
    "Silver Miners":                    "SIL",
    "Real Estate Schwab":               "SCHH",
    "Capital Markets Equal":            "KCE",
    "Leisure & Entertainment":          "PEJ",
    "Pharma Equal Weight":              "XPH",
    "Insurance Equal Weight":           "KIE",
    "Esports & Gaming":                 "ESPO",
    "IBD 50":                           "FFTY",
    "Medical Devices":                  "IHI",
    "MLP & Midstream":                  "AMLP",
    "Oil & Gas Equal Weight":           "XOP",
    "Gold":                             "GLD",
    "Biotech Equal Weight":             "PBE",
    "Healthcare Equipment Equal":       "XHE",
    "Homebuilders Equal":               "XHB",
}

# Deduplicate: keep first theme per ETF ticker
TICKER_TO_THEME: dict[str, str] = {}
for theme, tkr in THEME_ETF_MAP.items():
    if tkr not in TICKER_TO_THEME:
        TICKER_TO_THEME[tkr] = theme

ALL_TICKERS = sorted(set(THEME_ETF_MAP.values()))


def _safe_pct(series: pd.Series, periods: int) -> float | None:
    """Return % change over `periods` trading days from the end of series."""
    if len(series) <= periods:
        return None
    try:
        end   = float(series.iloc[-1])
        start = float(series.iloc[-periods])
        if start <= 0:
            return None
        return round((end - start) / start * 100, 2)
    except (IndexError, TypeError, ZeroDivisionError):
        return None


def compute_ibd_rs(perf_q4: float | None, perf_q3: float | None,
                   perf_q2: float | None, perf_q1: float | None) -> float | None:
    """IBD RS composite: 40% recent qtr + 20% each of the prior 3 qtrs."""
    weights = [(perf_q4, 0.4), (perf_q3, 0.2), (perf_q2, 0.2), (perf_q1, 0.2)]
    total_w = sum(w for v, w in weights if v is not None)
    if total_w == 0:
        return None
    score = sum(v * w for v, w in weights if v is not None) / total_w
    return round(score, 4)


def build_etf_rs() -> dict:
    logger.info("Fetching 12-month daily price history for %d ETFs…", len(ALL_TICKERS))

    all_syms = ALL_TICKERS + ["SPY"]

    # 12-month data for RS, sparklines, 52W high
    raw = yf.download(
        all_syms,
        period="12mo",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    # 5-day data for accurate 1D / intraday change (most recent trading day vs prior)
    raw5 = yf.download(
        all_syms,
        period="5d",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    # Extract close prices
    if isinstance(raw.columns, pd.MultiIndex):
        closes  = raw["Close"]
    else:
        closes  = raw[["Close"]].rename(columns={"Close": ALL_TICKERS[0]})

    if isinstance(raw5.columns, pd.MultiIndex):
        closes5 = raw5["Close"]
    else:
        closes5 = raw5[["Close"]].rename(columns={"Close": ALL_TICKERS[0]})

    D20  = 20   # Jeff Sun's 1-month RS baseline period (20 trading days)
    D25  = 25   # Jeff Sun's histogram window (25 trading days for daily RS bars)
    D21, D63, D126, D252 = D20, 63, 126, 252
    rows: list[dict] = []

    for tkr in ALL_TICKERS:
        if tkr not in closes.columns:
            logger.warning("  ✗ %s: no price data", tkr)
            continue
        s = closes[tkr].dropna()
        if len(s) < 10:
            logger.warning("  ✗ %s: insufficient data (%d rows)", tkr, len(s))
            continue

        # 1-day change: compare last close vs prior close
        s5 = closes5[tkr].dropna() if tkr in closes5.columns else pd.Series(dtype=float)
        p1d = _safe_pct(s5, 2) if len(s5) >= 3 else _safe_pct(s, 2)

        # 1-week (5 trading days)
        p1w = _safe_pct(s5, 6) if len(s5) >= 6 else _safe_pct(s, 6)

        # Longer-term performance from 12-month data
        p1m  = _safe_pct(s, D21)
        p3m  = _safe_pct(s, D63)
        p6m  = _safe_pct(s, D126)
        p12m = _safe_pct(s, D252)

        # % off 52-week high
        h52 = float(s.tail(D252).max()) if len(s) >= 5 else None
        cur = float(s.iloc[-1])
        pct_off_52wh = round((cur / h52 - 1) * 100, 1) if h52 and h52 > 0 else None

        # 1-month sparkline (last 21 closes, normalised to 0-100 for SVG)
        spark_raw = s.tail(D21).round(4).tolist()
        spark_min, spark_max = min(spark_raw), max(spark_raw)
        rng = spark_max - spark_min or 1
        sparkline = [round((v - spark_min) / rng * 100, 1) for v in spark_raw]

        # 25-day cumulative RS histogram vs SPY
        # Formula: cumRS[i] = (ETF[i]/ETF[0]) / (SPY[i]/SPY[0])
        # Always positive → all bars green; rising = outperforming SPY over time
        rs_histogram: list[float] = []
        if "SPY" in closes.columns:
            spy_s   = closes["SPY"].dropna()
            common  = s.index.intersection(spy_s.index)
            etf_w   = s.loc[common].tail(D25 + 1)   # +1 for base day
            spy_w   = spy_s.loc[common].tail(D25 + 1)
            if len(etf_w) >= 2 and len(spy_w) >= 2:
                etf_base = float(etf_w.iloc[0])
                spy_base = float(spy_w.iloc[0])
                for ep, sp in zip(etf_w.iloc[1:].tolist(), spy_w.iloc[1:].tolist()):
                    if etf_base > 0 and spy_base > 0 and sp > 0:
                        cum_rs = (ep / etf_base) / (sp / spy_base)
                        rs_histogram.append(round(cum_rs, 4))
                    else:
                        rs_histogram.append(1.0)

        # IBD RS quarters
        D189 = D63 * 3
        q4 = p3m
        q3 = _safe_pct(s.iloc[:-D63],  D63) if len(s) > D63 * 2 else None
        q2 = _safe_pct(s.iloc[:-D126], D63) if len(s) > D126 + D63 else None
        q1 = _safe_pct(s.iloc[:-D189], D63) if len(s) > D63 * 4 else None
        ibd_raw = compute_ibd_rs(q4, q3, q2, q1)

        rows.append({
            "ticker":         tkr,
            "theme":          TICKER_TO_THEME.get(tkr, tkr),
            "perf_intraday":  round(p1d,  2) if p1d  is not None else None,
            "perf_1d":        round(p1d,  2) if p1d  is not None else None,
            "perf_1w":        round(p1w,  2) if p1w  is not None else None,
            "perf_1m":        p1m,
            "perf_3m":        p3m,
            "perf_6m":        p6m,
            "perf_12m":       p12m,
            "pct_off_52wh":   pct_off_52wh,
            "sparkline":      sparkline,
            "rs_histogram":   rs_histogram,
            "ibd_raw":        ibd_raw,
            # Raw returns stored for peer-ranking (removed after ranking)
            "_raw_1d":        p1d,
            "_raw_1w":        p1w,
            "_raw_1m":        p1m,
            "_raw_3m":        p3m,
            "_raw_6m":        p6m,
            "_raw_12m":       p12m,
            # Peer RS ranks (1-99) — filled in after all rows collected
            "rs_day":         None,
            "rs_wk":          None,
            "rs_mth":         None,
            "rs_qtr":         None,
            "rs_hy":          None,
            "rs_yr":          None,
            "score":          None,   # composite weighted score
            # Legacy fields kept for backward compat
            "rs":             None,
            "rs_pct":         None,
            "rs_1m":          None,
            "rs_1m_pct":      None,
        })
        logger.info("  ✓ %-6s  1D=%+5.1f%%  1M=%+6.1f%%  52WH=%+5.1f%%",
                    tkr,
                    p1d  if p1d  is not None else float("nan"),
                    p1m  if p1m  is not None else float("nan"),
                    pct_off_52wh if pct_off_52wh is not None else float("nan"),
                    )

    # ── Multi-timeframe peer percentile RS ranks (1–99) ─────────────────────────
    # Formula: rank = round((peers_beaten / (total-1)) * 98) + 1  → range 1-99

    def _peer_rank(rows: list[dict], raw_key: str, rank_key: str) -> None:
        """Rank all rows by raw_key, write 1-99 integer into rank_key."""
        scored = [(i, r) for i, r in enumerate(rows) if r.get(raw_key) is not None]
        if not scored:
            return
        scored.sort(key=lambda x: x[1][raw_key])
        n = len(scored)
        for pos, (_, r) in enumerate(scored):
            r[rank_key] = round((pos / max(n - 1, 1)) * 98) + 1

    _peer_rank(rows, "_raw_1d",  "rs_day")
    _peer_rank(rows, "_raw_1w",  "rs_wk")
    _peer_rank(rows, "_raw_1m",  "rs_mth")
    _peer_rank(rows, "_raw_3m",  "rs_qtr")
    _peer_rank(rows, "_raw_6m",  "rs_hy")
    _peer_rank(rows, "_raw_12m", "rs_yr")

    # ── Composite Score = 0.20×Day + 0.20×Wk + 0.20×Mth + 0.20×Qtr + 0.10×HY + 0.10×Yr
    WEIGHTS = [("rs_day",0.20),("rs_wk",0.20),("rs_mth",0.20),
               ("rs_qtr",0.20),("rs_hy",0.10),("rs_yr",0.10)]
    for r in rows:
        parts = [(r.get(k), w) for k, w in WEIGHTS if r.get(k) is not None]
        if parts:
            total_w = sum(w for _, w in parts)
            r["score"] = round(sum(v * w for v, w in parts) / total_w, 1)

    # ── Legacy rs / rs_pct (12M IBD composite) ───────────────────────────────
    scored_12m = [r for r in rows if r["ibd_raw"] is not None]
    scored_12m.sort(key=lambda r: r["ibd_raw"])
    n = len(scored_12m)
    for i, r in enumerate(scored_12m):
        rs_raw = (i / max(n - 1, 1)) * 100
        r["rs"]     = round(rs_raw)
        r["rs_pct"] = min(100, round(rs_raw / 5) * 5)

    # ── rs_1m / rs_1m_pct: position of last histogram bar in 25-day range ────
    for r in rows:
        hist = r.get("rs_histogram", [])
        if len(hist) >= 2:
            lo, hi = min(hist), max(hist)
            rng = hi - lo
            pct_raw = ((hist[-1] - lo) / rng * 100) if rng > 0 else 50.0
            r["rs_1m"]     = round(pct_raw)
            r["rs_1m_pct"] = min(100, round(pct_raw / 5) * 5)
        else:
            r["rs_1m"] = r["rs_1m_pct"] = None

    # Strip internal raw keys before output
    for r in rows:
        for k in ("_raw_1d","_raw_1w","_raw_1m","_raw_3m","_raw_6m","_raw_12m"):
            r.pop(k, None)

    # Sort by composite Score descending
    result_rows = sorted(rows, key=lambda r: -(r["score"] or 0))

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "etfs": result_rows,
    }


def main() -> None:
    data = build_etf_rs()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "Done — %d ETFs ranked → %s",
        sum(1 for e in data["etfs"] if e["rs"] is not None),
        OUTPUT_PATH,
    )


if __name__ == "__main__":
    main()
