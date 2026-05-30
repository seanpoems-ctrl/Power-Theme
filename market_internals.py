"""
market_internals.py — Market Breadth & Internals Snapshot

Fetches:
  VIX       — ibkr_client.get_vix()  (fallback: yfinance ^VIX)
  TICK      — IBKR reqMktData $TICK  (live session only; skipped on failure)
  TRIN      — IBKR reqMktData $TRIN  (live session only; skipped on failure)
  S5FI      — % S&P 500 above 50D MA  (scraper._build_sp500_rs_universe breadth_50)
  MMTH      — % S&P 500 above 200D MA (scraper._build_sp500_rs_universe breadth_200)
  T2108     — yfinance ^T2108
  10Y Yield — yfinance ^TNX

Writes public/market_internals.json.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_PATH = Path("public/market_internals.json")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _round2(v) -> float | None:
    try:
        f = float(v)
        return None if f != f else round(f, 2)   # NaN guard
    except (TypeError, ValueError):
        return None


def _yfinance_last(symbol: str) -> float | None:
    """Return the most recent close for *symbol* via yfinance."""
    try:
        import yfinance as yf
        hist = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=True)
        if hist.empty:
            return None
        return _round2(hist["Close"].dropna().iloc[-1])
    except Exception as exc:
        logger.warning("yfinance %s failed: %s", symbol, exc)
        return None


# ─── VIX ─────────────────────────────────────────────────────────────────────

def _fetch_vix() -> tuple[float | None, str]:
    """Returns (value, source_label)."""
    try:
        import ibkr_client
        if ibkr_client.IS_LIVE:
            val = ibkr_client.get_vix()
            if val is not None:
                logger.info("VIX from IBKR: %.2f", val)
                return _round2(val), "ibkr"
    except Exception as exc:
        logger.warning("IBKR VIX failed: %s", exc)

    val = _yfinance_last("^VIX")
    if val is not None:
        logger.info("VIX from yfinance: %.2f", val)
        return val, "yfinance"

    logger.warning("VIX unavailable from all sources")
    return None, "null"


# ─── A/D Net (TICK proxy) & TRIN ─────────────────────────────────────────────

def _fetch_tick_trin() -> tuple[float | None, float | None, str]:
    """
    Compute A/D Net (advancing − declining issues) and TRIN (Arms Index) from
    TradingView screener end-of-day data.

    The 'tick' field stores the A/D Net — a daily breadth measure that captures
    the same signal as NYSE TICK (net breadth) but calculated at market close.

    TRIN (Arms Index) = (adv_count / dec_count) / (adv_volume / dec_volume)
      < 0.7 → bullish (volume concentrated in advancers)
      0.7–1.3 → neutral
      > 1.3 → bearish (volume concentrated in decliners)

    Falls back to IBKR reqMktData $TICK/$TRIN if a live session is detected
    (paper/live trading session running locally).

    Returns (ad_net, trin, source_label).
    """
    # Try IBKR first if live session is available
    try:
        import ibkr_client
        if ibkr_client.IS_LIVE and ibkr_client._ib is not None:
            from ib_insync import Index
            ib = ibkr_client._ib

            def _ibkr_val(sym: str) -> float | None:
                c  = Index(sym, "NYSE")
                tk = ib.reqMktData(c, "", False, False)
                ib.sleep(2)
                v  = tk.last
                if v is None or (isinstance(v, float) and v != v):
                    v = tk.close
                ib.cancelMktData(c)
                return _round2(v) if v is not None and not (isinstance(v, float) and v != v) else None

            tick_ibkr = _ibkr_val("$TICK")
            trin_ibkr = _ibkr_val("$TRIN")
            if tick_ibkr is not None or trin_ibkr is not None:
                logger.info("TICK/TRIN from IBKR: tick=%s, trin=%s", tick_ibkr, trin_ibkr)
                return tick_ibkr, trin_ibkr, "ibkr"
    except Exception as exc:
        logger.warning("IBKR TICK/TRIN failed (continuing to TV fallback): %s", exc)

    # TradingView screener — compute from EOD change and volume
    try:
        from tradingview_screener import Query, col as tv_col  # type: ignore
        _, df = (
            Query()
            .select("name", "change", "volume")
            .where(
                tv_col("close") >= 1,
                tv_col("average_volume_10d_calc") >= 50_000,
                tv_col("type").isin(["stock", "dr"]),
                tv_col("exchange").isin(["NYSE", "NASDAQ", "AMEX"]),
            )
            .limit(10_000)
            .get_scanner_data()
        )
        if df is None or df.empty:
            logger.warning("TICK/TRIN: TradingView returned empty DataFrame")
            return None, None, "null"

        df = df.dropna(subset=["change", "volume"])
        df = df[df["volume"] > 0]

        adv = df[df["change"] > 0]
        dec = df[df["change"] < 0]

        adv_n   = int(len(adv))
        dec_n   = int(len(dec))
        adv_vol = float(adv["volume"].sum())
        dec_vol = float(dec["volume"].sum())

        ad_net = adv_n - dec_n          # stored in the 'tick' JSON field

        if dec_n > 0 and dec_vol > 0 and adv_vol > 0:
            trin = round((adv_n / dec_n) / (adv_vol / dec_vol), 2)
        else:
            trin = None

        logger.info(
            "A/D Net: %+d (adv=%d, dec=%d)  |  TRIN: %s [tradingview]",
            ad_net, adv_n, dec_n, trin,
        )
        return float(ad_net), trin, "tradingview"

    except Exception as exc:
        logger.warning("TICK/TRIN TradingView failed: %s", exc)
        return None, None, "null"


# ─── Breadth from scraper RS universe ────────────────────────────────────────

def _fetch_breadth() -> tuple[float | None, float | None, str]:
    """
    Call scraper._build_sp500_rs_universe() and extract:
      breadth_50  → S5FI  (% above 50D MA)
      breadth_200 → MMTH  (% above 200D MA)

    Returns (s5fi, mmth, source_label).
    The universe download is slow (~30–60s); this is the canonical computation
    used by the main scraper, so results are consistent.
    """
    try:
        from scraper import _build_sp500_rs_universe
        logger.info("Building S&P 500 RS universe for breadth...")
        result    = _build_sp500_rs_universe()
        # Signature: (rs_dict, breadth_50, breadth_200, price_data, ...)
        breadth_50  = _round2(result[1])
        breadth_200 = _round2(result[2])
        logger.info("S5FI (above 50D): %s%%  |  MMTH (above 200D): %s%%", breadth_50, breadth_200)
        return breadth_50, breadth_200, "scraper"
    except Exception as exc:
        logger.error("Breadth computation failed: %s", exc)
        return None, None, "null"


# ─── T2108 ────────────────────────────────────────────────────────────────────
# ^T2108 was delisted from Yahoo Finance (Worden Brothers proprietary indicator).
# Primary source: breadth_monitor.json (Stockbee spreadsheet — updated daily).
# Fallback: TradingView screener — compute % of US stocks above their 50-day SMA
# as a close proxy (same concept, slightly different MA period).

def _fetch_t2108() -> tuple[float | None, str]:
    # Source 1: breadth_monitor.json (Stockbee spreadsheet value)
    try:
        bm_path = OUTPUT_PATH.parent / "breadth_monitor.json"
        if bm_path.exists():
            import json as _json
            bm = _json.loads(bm_path.read_text(encoding="utf-8"))
            rows = bm.get("rows", [])
            if rows:
                val = rows[0].get("t2108")
                if val is not None:
                    logger.info("T2108 from breadth_monitor.json: %.2f", val)
                    return _round2(val), "breadth_monitor"
    except Exception as exc:
        logger.warning("T2108 from breadth_monitor.json failed: %s", exc)

    # Source 2: TradingView screener — % US stocks (price ≥ $2) above SMA50
    # This is the same concept as T2108 (% above a medium-term MA), slightly
    # different period (50 vs 40 days) but directionally equivalent.
    try:
        from tradingview_screener import Query, col as tv_col  # type: ignore
        _, df = (
            Query()
            .select("name", "close", "SMA50")
            .where(
                tv_col("close") >= 2,
                tv_col("average_volume_10d_calc") >= 50_000,
                tv_col("type").isin(["stock", "dr"]),
                tv_col("exchange").isin(["NYSE", "NASDAQ", "AMEX", "NYSE ARCA"]),
            )
            .limit(10_000)
            .get_scanner_data()
        )
        if df is not None and not df.empty:
            df = df.dropna(subset=["close", "SMA50"])
            df = df[df["close"] > 0]
            pct = round((df["close"] > df["SMA50"]).sum() / len(df) * 100, 2)
            logger.info("T2108 proxy (>SMA50) from TradingView: %.2f%%", pct)
            return pct, "tradingview"
    except Exception as exc:
        logger.warning("T2108 TradingView fallback failed: %s", exc)

    logger.warning("T2108 unavailable from all sources")
    return None, "null"


# ─── 10Y Yield ────────────────────────────────────────────────────────────────

def _fetch_yield_10y() -> tuple[float | None, str]:
    val = _yfinance_last("^TNX")
    if val is not None:
        logger.info("10Y yield from yfinance: %.2f", val)
        return val, "yfinance"
    logger.warning("10Y yield unavailable")
    return None, "null"


# ─── Main ────────────────────────────────────────────────────────────────────

def build_internals(
    s5fi: float | None = None,
    mmth: float | None = None,
) -> dict:
    """
    Entry point for scraper.py integration.

    When *s5fi* and *mmth* are supplied (already computed by scraper's step 5),
    the expensive S&P 500 batch download is skipped.  Called from build_data()
    and also from main() as a standalone run.

    Returns the internals dict without writing any file.
    """
    import ibkr_client

    if s5fi is not None or mmth is not None:
        # Use pre-computed breadth values — skip the re-download
        breadth_src = "scraper"
        logger.info("market_internals: using pre-computed breadth from scraper (s5fi=%s, mmth=%s)", s5fi, mmth)
    else:
        s5fi, mmth, breadth_src = _fetch_breadth()

    vix,        vix_src           = _fetch_vix()
    tick, trin, tick_trin_src    = _fetch_tick_trin()
    t2108,      t2_src           = _fetch_t2108()
    yield_10y,  y10_src          = _fetch_yield_10y()

    try:
        import ibkr_client
        ibkr_client.disconnect()
    except Exception:
        pass

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "vix":       vix,
        "tick":      tick,
        "trin":      trin,
        "s5fi_50d":  s5fi,
        "mmth_200d": mmth,
        "t2108":     t2108,
        "yield_10y": yield_10y,
        "data_sources": {
            "vix":       vix_src,
            "tick":      tick_trin_src,
            "trin":      tick_trin_src,
            "s5fi_50d":  breadth_src,
            "mmth_200d": breadth_src,
            "t2108":     t2_src,
            "yield_10y": y10_src,
        },
    }


def run() -> dict:
    """Standalone run: fetches breadth independently then delegates to build_internals()."""
    return build_internals()


def main() -> None:
    result = run()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "Done — VIX=%.2s  10Y=%.2s  S5FI=%.2s%%  MMTH=%.2s%%  → %s",
        result["vix"],
        result["yield_10y"],
        result["s5fi_50d"],
        result["mmth_200d"],
        OUTPUT_PATH,
    )


if __name__ == "__main__":
    main()
