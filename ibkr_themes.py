"""
ibkr_themes.py — Pre-Market Power-Theme Identification

Data pipeline:
  1. Fetch pre-market movers via IBKR (or TradingView fallback)
  2. Enrich each ticker with Finviz detail (industry, adr_pct, price) and yfinance
     (mkt_cap, avg_dollar_vol_30d, 6m perf for RS)
  3. Build S&P 500 RS universe → compute RS percentile per stock
  4. Map ticker → themes via TICKER_THEME_OVERRIDE / INDUSTRY_TO_THEME
  5. Apply 5 hard gates → gates_passed + gates_detail
  6. Group passing stocks by theme → compute theme_rs (mean RS)
  7. Resolve primary_theme (highest theme_rs) and secondary_themes per stock
  8. Write public/ibkr_themes.json
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─── Gate thresholds ────────────────────────────────────────────────────────
GATE_RS     = 85
GATE_PRICE  = 12.0
GATE_DVOL   = 100_000_000
GATE_MKTCAP = 2_000_000_000
GATE_ADR    = 4.0

OUTPUT_PATH = Path("public/ibkr_themes.json")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _setup_label(rs: int, adr: float, gates_passed: int) -> str:
    if rs >= 90 and adr >= 5.0:
        return "Flag"
    if rs >= 85 and adr >= 4.0:
        return "Base"
    if gates_passed == 5:
        return "Watch"
    return "Skip"


def _apply_gates(row: dict) -> tuple[int, dict]:
    checks = {
        "rs_52w > 85":               (row.get("rs_52w") or 0) > GATE_RS,
        "price > 12":                (row.get("price") or 0.0) > GATE_PRICE,
        "avg_dollar_vol_30d > 100M": (row.get("avg_dollar_vol_30d") or 0) > GATE_DVOL,
        "mkt_cap > 2B":              (row.get("mkt_cap") or 0) > GATE_MKTCAP,
        "adr_pct >= 4.0":            (row.get("adr_pct") or 0.0) >= GATE_ADR,
    }
    return sum(checks.values()), checks


def _get_ticker_themes(ticker: str, industry: str) -> list[str]:
    """
    Return all themes a ticker belongs to.

    Priority:
      1. TICKER_THEME_OVERRIDE  (explicit per-ticker mapping)
      2. INDUSTRY_TO_THEME      (broad industry mapping)
    Both are checked so a ticker can carry two themes when they differ.
    """
    from scraper import INDUSTRY_TO_THEME, TICKER_THEME_OVERRIDE

    themes: list[str] = []
    if ticker in TICKER_THEME_OVERRIDE:
        themes.append(TICKER_THEME_OVERRIDE[ticker][0])
    if industry:
        mapped = INDUSTRY_TO_THEME.get(industry)
        if mapped and mapped not in themes:
            themes.append(mapped)
    return themes or ["Other"]


def _rs_percentile(perf_6m: float | None, rs_universe: dict[str, float]) -> int:
    """Percentile rank (1–99) of perf_6m against the S&P 500 universe."""
    if not rs_universe or perf_6m is None:
        return 50
    sorted_perfs = sorted(rs_universe.values())
    n = len(sorted_perfs)
    rank = sum(1 for v in sorted_perfs if v <= perf_6m)
    return max(1, min(99, int((rank / max(n, 1)) * 98) + 1))


# ─── Per-ticker enrichment ───────────────────────────────────────────────────

def _finviz_detail(ticker: str) -> dict:
    """
    Fetch price, industry, adr_pct, perf_1d, rvol from Finviz quote page.
    Returns {} on any failure.
    """
    try:
        from scraper import fetch_stock_detail
        detail = fetch_stock_detail(ticker)
        return detail or {}
    except Exception as exc:
        logger.warning("Finviz detail failed for %s: %s", ticker, exc)
        return {}


def _yfinance_enrich(ticker: str) -> dict:
    """
    Fetch mkt_cap, avg_dollar_vol_30d, perf_6m from yfinance.
    Returns safe defaults on failure.
    """
    out = {"mkt_cap": 0, "avg_dollar_vol_30d": 0, "perf_6m": None}
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)

        # Single history call covers both avg_dvol_30d and perf_6m
        hist = t.history(period="6mo", interval="1d", auto_adjust=True)
        if len(hist) >= 2:
            perf_6m_val = (float(hist["Close"].iloc[-1]) - float(hist["Close"].iloc[0])) / float(hist["Close"].iloc[0]) * 100
            out["perf_6m"] = round(perf_6m_val, 2)
        if len(hist) >= 10:
            out["avg_dollar_vol_30d"] = round(float((hist["Close"] * hist["Volume"]).tail(30).mean()))

        # marketCap from info (separate call — cached by yfinance)
        info = t.fast_info
        out["mkt_cap"] = int(getattr(info, "market_cap", None) or 0)
    except Exception as exc:
        logger.warning("yfinance enrich failed for %s: %s", ticker, exc)
    return out


def _enrich_ticker(ticker: str, base: dict) -> dict:
    """
    Merge Finviz detail + yfinance data into *base* dict.
    *base* may already have price/mkt_cap/avg_dollar_vol/rvol (TV fallback path).
    """
    fv = _finviz_detail(ticker)
    yf = _yfinance_enrich(ticker)

    # Prefer live Finviz price over scanner price; fall back to base
    price    = fv.get("price") or base.get("price") or 0.0
    adr_pct  = fv.get("adr_pct") or 0.0
    industry = fv.get("industry") or ""
    perf_1d  = fv.get("perf_1d") or fv.get("change_pct") or base.get("gap_pct") or 0.0
    rvol     = fv.get("rvol") or base.get("rvol") or 0.0

    mkt_cap         = yf["mkt_cap"] or base.get("mkt_cap") or 0
    avg_dollar_vol  = yf["avg_dollar_vol_30d"] or base.get("avg_dollar_vol") or 0
    perf_6m         = yf["perf_6m"]

    return {
        **base,
        "ticker":            ticker,
        "price":             price,
        "adr_pct":           adr_pct,
        "industry":          industry,
        "perf_1d":           perf_1d,
        "vol_surge":         rvol,
        "mkt_cap":           mkt_cap,
        "avg_dollar_vol_30d": avg_dollar_vol,
        "perf_6m":           perf_6m,
    }


# ─── Scanner sources ─────────────────────────────────────────────────────────

def _fetch_ibkr_movers() -> list[dict]:
    """Wrap ibkr_client.get_premarket_scanner(); returns list of base dicts with ticker key."""
    import ibkr_client
    raw = ibkr_client.get_premarket_scanner()
    # ibkr scanner returns {ticker, last, change_pct, volume, rs_placeholder}
    # Normalise key names so downstream code is source-agnostic
    results = []
    for item in (raw or []):
        results.append({
            "ticker":  item.get("ticker", ""),
            "price":   item.get("last") or 0.0,
            "rvol":    0.0,
            "mkt_cap": 0,
        })
    return [r for r in results if r["ticker"]]


def _fetch_tv_movers() -> list[dict]:
    """Wrap gapper_service.fetch_gappers(); normalise to same base-dict shape."""
    try:
        from gapper_service import fetch_gappers
        raw = fetch_gappers()
        results = []
        for item in (raw or []):
            results.append({
                "ticker":      item.get("ticker", ""),
                "price":       item.get("price") or 0.0,
                "gap_pct":     item.get("gap_pct") or 0.0,
                "rvol":        item.get("rvol") or 0.0,
                "mkt_cap":     item.get("mkt_cap") or 0,
                "avg_dollar_vol": item.get("avg_dollar_vol") or 0,
            })
        return [r for r in results if r["ticker"]]
    except Exception as exc:
        logger.error("TradingView fallback failed: %s", exc)
        return []


# ─── Main ────────────────────────────────────────────────────────────────────

def run() -> dict:
    import ibkr_client

    data_source = ibkr_client.get_data_source()
    logger.info("Data source: %s", data_source)

    # ── 1. Fetch movers ──────────────────────────────────────────────────────
    if ibkr_client.IS_LIVE:
        raw_movers = _fetch_ibkr_movers()
        logger.info("IBKR scanner returned %d tickers", len(raw_movers))
    else:
        logger.warning("IBKR not connected — using TradingView fallback")
        raw_movers = _fetch_tv_movers()
        logger.info("TradingView returned %d tickers", len(raw_movers))

    if not raw_movers:
        logger.warning("No movers returned from any source")

    # ── 2. Build RS universe (once) ──────────────────────────────────────────
    logger.info("Building S&P 500 RS universe...")
    try:
        from scraper import _build_sp500_rs_universe
        rs_result  = _build_sp500_rs_universe()
        rs_universe: dict[str, float] = rs_result[0]
        logger.info("RS universe: %d stocks", len(rs_universe))
    except Exception as exc:
        logger.error("RS universe build failed: %s", exc)
        rs_universe = {}

    # ── 3. Enrich each ticker ────────────────────────────────────────────────
    enriched: list[dict] = []
    for base in raw_movers:
        ticker = base["ticker"]
        logger.info("  Enriching %s...", ticker)
        row = _enrich_ticker(ticker, base)

        # Compute RS percentile
        row["rs_52w"] = _rs_percentile(row.get("perf_6m"), rs_universe)

        # Determine all themes
        row["_themes"] = _get_ticker_themes(ticker, row.get("industry", ""))

        # Apply gates
        gates_passed, gates_detail = _apply_gates(row)
        row["gates_passed"]  = gates_passed
        row["gates_detail"]  = gates_detail

        enriched.append(row)
        time.sleep(0.5)  # respectful rate-limiting between Finviz calls

    # ── 4. Compute theme_rs (mean RS per theme across all stocks) ────────────
    theme_rs_acc: dict[str, list[int]] = {}
    for row in enriched:
        for theme in row["_themes"]:
            theme_rs_acc.setdefault(theme, []).append(row["rs_52w"])
    theme_rs: dict[str, float] = {
        t: round(sum(vals) / len(vals), 1) for t, vals in theme_rs_acc.items()
    }

    # ── 5. Assign primary_theme / secondary_themes per stock ─────────────────
    for row in enriched:
        themes_sorted = sorted(row["_themes"], key=lambda t: theme_rs.get(t, 0), reverse=True)
        row["primary_theme"]    = themes_sorted[0] if themes_sorted else "Other"
        row["secondary_themes"] = themes_sorted[1:]
        row.pop("_themes", None)

    # ── 6. Build power_themes — only stocks that pass all 5 gates ────────────
    passing = [r for r in enriched if r["gates_passed"] == 5]

    # Group by primary_theme
    theme_map: dict[str, list[dict]] = {}
    for row in passing:
        theme_map.setdefault(row["primary_theme"], []).append(row)

    power_themes = []
    for theme_name, stocks in theme_map.items():
        # perf_1d for the theme: mean of constituent stocks' perf_1d
        perf_vals = [s["perf_1d"] for s in stocks if s.get("perf_1d") is not None]
        theme_perf_1d = round(sum(perf_vals) / len(perf_vals), 2) if perf_vals else 0.0

        rs_vals = [s["rs_52w"] for s in stocks]
        computed_theme_rs = round(sum(rs_vals) / len(rs_vals), 1) if rs_vals else 0.0

        leaders = []
        for s in sorted(stocks, key=lambda x: x["rs_52w"], reverse=True):
            rs   = s["rs_52w"]
            adr  = s.get("adr_pct") or 0.0
            gp   = s["gates_passed"]
            leaders.append({
                "ticker":           s["ticker"],
                "price":            s.get("price") or 0.0,
                "rs_52w":           rs,
                "adr_pct":          adr,
                "vol_surge":        s.get("vol_surge") or 0.0,
                "mkt_cap":          s.get("mkt_cap") or 0,
                "gates_passed":     gp,
                "gates_detail":     s["gates_detail"],
                "primary_theme":    s["primary_theme"],
                "secondary_themes": s["secondary_themes"],
                "setup_label":      _setup_label(rs, adr, gp),
            })

        power_themes.append({
            "name":      theme_name,
            "theme_rs":  computed_theme_rs,
            "perf_1d":   theme_perf_1d,
            "leaders":   leaders,
        })

    # Sort themes by theme_rs descending
    power_themes.sort(key=lambda t: t["theme_rs"], reverse=True)

    # ── 7. Account equity (IBKR only) ────────────────────────────────────────
    account_equity: float | None = None
    if ibkr_client.IS_LIVE:
        account_equity = ibkr_client.get_account_equity()

    # ── 8. Disconnect ────────────────────────────────────────────────────────
    ibkr_client.disconnect()

    return {
        "generated_at":  datetime.now(tz=timezone.utc).isoformat(),
        "data_source":   data_source,
        "power_themes":  power_themes,
        "account_equity": account_equity,
    }


def main() -> None:
    result = run()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    theme_count  = len(result["power_themes"])
    leader_count = sum(len(t["leaders"]) for t in result["power_themes"])
    logger.info(
        "Done — %d power themes, %d passing leaders → %s",
        theme_count, leader_count, OUTPUT_PATH,
    )


if __name__ == "__main__":
    main()
