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


# ─── TICK / TRIN (IBKR live session only) ────────────────────────────────────

def _fetch_ibkr_index(symbol: str, exchange: str = "NYSE") -> tuple[float | None, str]:
    """
    Request a market-internal index via IBKR reqMktData.
    $TICK and $TRIN are NYSE-disseminated; only available during the regular session.
    Returns (value, source_label).
    """
    try:
        import ibkr_client
        if not ibkr_client.IS_LIVE or ibkr_client._ib is None:
            return None, "null"

        from ib_insync import Index
        ib = ibkr_client._ib

        contract = Index(symbol, exchange)
        ticker   = ib.reqMktData(contract, "", False, False)
        ib.sleep(2)

        # TICK/TRIN appear in .last or .close depending on session
        val = ticker.last
        if val is None or (isinstance(val, float) and val != val):
            val = ticker.close
        if val is None or (isinstance(val, float) and val != val):
            ib.cancelMktData(contract)
            logger.warning("%s: no market data returned (outside session?)", symbol)
            return None, "null"

        ib.cancelMktData(contract)
        result = _round2(val)
        logger.info("%s from IBKR: %s", symbol, result)
        return result, "ibkr"

    except Exception as exc:
        logger.warning("IBKR %s failed: %s", symbol, exc)
        return None, "null"


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

def _fetch_t2108() -> tuple[float | None, str]:
    val = _yfinance_last("^T2108")
    if val is not None:
        logger.info("T2108 from yfinance: %.2f", val)
        return val, "yfinance"
    logger.warning("T2108 unavailable")
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

def run() -> dict:
    import ibkr_client

    # Run breadth first — it's the slowest call (S&P 500 batch download)
    s5fi, mmth, breadth_src = _fetch_breadth()

    vix,      vix_src   = _fetch_vix()
    tick,     tick_src  = _fetch_ibkr_index("$TICK")
    trin,     trin_src  = _fetch_ibkr_index("$TRIN")
    t2108,    t2_src    = _fetch_t2108()
    yield_10y, y10_src  = _fetch_yield_10y()

    ibkr_client.disconnect()

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
            "tick":      tick_src,
            "trin":      trin_src,
            "s5fi_50d":  breadth_src,
            "mmth_200d": breadth_src,
            "t2108":     t2_src,
            "yield_10y": y10_src,
        },
    }


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
