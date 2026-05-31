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

# ── Full ETF map (must mirror THEME_ETF_MAP in App.js) ────────────────────────
THEME_ETF_MAP = {
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

    # Batch download — one call for all tickers + SPY benchmark
    raw = yf.download(
        ALL_TICKERS + ["SPY"],
        period="12mo",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    # Extract close prices
    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw["Close"]
        highs  = raw["High"]  if "High"  in raw.columns.get_level_values(0) else None
    else:
        closes = raw[["Close"]].rename(columns={"Close": ALL_TICKERS[0]})
        highs  = None

    D21, D63, D126, D252 = 21, 63, 126, 252
    rows: list[dict] = []

    for tkr in ALL_TICKERS:
        if tkr not in closes.columns:
            logger.warning("  ✗ %s: no price data", tkr)
            continue
        s = closes[tkr].dropna()
        if len(s) < 10:
            logger.warning("  ✗ %s: insufficient data (%d rows)", tkr, len(s))
            continue

        # Performance
        p1d  = _safe_pct(s, 1)
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

        # 25-day RS histogram vs SPY: daily ETF return ÷ daily SPY return
        rs_histogram: list[float] = []
        if "SPY" in closes.columns:
            spy_s  = closes["SPY"].dropna()
            etf_d  = s.pct_change().dropna()
            spy_d  = spy_s.pct_change().dropna()
            common = etf_d.index.intersection(spy_d.index)
            etf_25 = etf_d.loc[common].tail(25)
            spy_25 = spy_d.loc[common].tail(25)
            for er, sr in zip(etf_25.tolist(), spy_25.tolist()):
                if abs(sr) > 0.0001:
                    rs_histogram.append(round(er / sr, 3))
                else:
                    rs_histogram.append(round(er * 100, 3))

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
            "perf_intraday":  p1d,
            "perf_1d":        p1d,
            "perf_1m":        p1m,
            "perf_3m":        p3m,
            "perf_6m":        p6m,
            "perf_12m":       p12m,
            "pct_off_52wh":   pct_off_52wh,
            "sparkline":      sparkline,
            "rs_histogram":   rs_histogram,
            "ibd_raw":        ibd_raw,
            "rs":             None,   # filled after ranking
            "rs_pct":         None,   # 0-100 display value
        })
        logger.info("  ✓ %-6s  1D=%+5.1f%%  1M=%+6.1f%%  52WH=%+5.1f%%",
                    tkr,
                    p1d  if p1d  is not None else float("nan"),
                    p1m  if p1m  is not None else float("nan"),
                    pct_off_52wh if pct_off_52wh is not None else float("nan"),
                    )

    # ── Rank to 1-99 → display as RS% (rounded to nearest 5) ────────────────
    scored = [r for r in rows if r["ibd_raw"] is not None]
    scored.sort(key=lambda r: r["ibd_raw"])
    n = len(scored)
    for i, r in enumerate(scored):
        rs = max(1, min(99, round((i / max(n - 1, 1)) * 98 + 1)))
        r["rs"]     = rs
        r["rs_pct"] = min(100, round(rs / 5) * 5)   # round to nearest 5 for display

    result_rows = sorted(rows, key=lambda r: -(r["rs"] or 0))

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
