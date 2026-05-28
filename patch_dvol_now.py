"""
patch_dvol_now.py
=================
One-shot script: fetches TradingView average_volume_10d_calc × close for
every stock in public/thematic_data.json (themes + heatmap_themes + etf_holdings)
and writes the result back as dollar_volume / avg_dollar_volume.

Also patches company names (description field) for all stocks and ETF holdings.

Run once:
    python patch_dvol_now.py
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_PATH = Path("public/thematic_data.json")


def _tv_lookup(tickers: list[str]) -> dict[str, dict]:
    """
    Returns {ticker: {company, dollar_volume}} via TradingView screener.
    dollar_volume = average_volume_10d_calc × close
    """
    if not tickers:
        return {}
    try:
        from tradingview_screener import Query, col as tv_col  # type: ignore
    except ImportError:
        logger.error("tradingview_screener not installed — pip install tradingview-screener")
        return {}

    result: dict[str, dict] = {}
    batch_size = 1500
    for i in range(0, len(tickers), batch_size):
        chunk = tickers[i : i + batch_size]
        try:
            _, df = (
                Query()
                .select("name", "description", "close", "average_volume_10d_calc")
                .where(tv_col("name").isin(chunk))
                .limit(len(chunk) + 50)
                .get_scanner_data()
            )
            for _, row in df.iterrows():
                tkr  = str(row["name"])
                desc = str(row.get("description", "")).strip()
                try:
                    tv_close   = float(row["close"])
                    tv_avg_vol = float(row["average_volume_10d_calc"])
                    dvol = round(tv_close * tv_avg_vol) if tv_close > 0 and tv_avg_vol > 0 else None
                except (TypeError, ValueError):
                    dvol = None
                result[tkr] = {"company": desc or None, "dollar_volume": dvol}
            logger.info("  batch %d/%d — %d resolved so far",
                        i // batch_size + 1, -(-len(tickers) // batch_size), len(result))
        except Exception as exc:
            logger.warning("batch %d failed: %s", i, exc)
    return result


def main() -> None:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    # ── Collect all unique US tickers ────────────────────────────────────────
    tickers: list[str] = []
    seen: set[str] = set()

    def _add(tkr: str) -> None:
        if tkr and "." not in tkr and tkr not in seen:
            seen.add(tkr)
            tickers.append(tkr)

    for th in data.get("themes", []) + data.get("heatmap_themes", []):
        for sub in th.get("subthemes", []):
            for s in sub.get("stocks", []):
                _add(s.get("ticker", ""))

    for holdings in data.get("etf_holdings", {}).values():
        for h in holdings:
            _add(h.get("ticker", ""))

    logger.info("Fetching TradingView data for %d unique US tickers …", len(tickers))
    lookup = _tv_lookup(tickers)
    logger.info("Resolved %d / %d tickers", len(lookup), len(tickers))

    # ── Patch thematic stocks ─────────────────────────────────────────────────
    patched_stocks = 0
    for th in data.get("themes", []) + data.get("heatmap_themes", []):
        for sub in th.get("subthemes", []):
            for s in sub.get("stocks", []):
                tkr = s.get("ticker", "")
                info = lookup.get(tkr)
                if not info:
                    continue
                if info["company"]:
                    s["company"] = info["company"]
                if info["dollar_volume"] is not None:
                    s["dollar_volume"]     = info["dollar_volume"]
                    s["avg_dollar_volume"] = info["dollar_volume"]
                patched_stocks += 1

    # ── Patch ETF holdings ────────────────────────────────────────────────────
    patched_etf = 0
    for holdings in data.get("etf_holdings", {}).values():
        for h in holdings:
            tkr  = h.get("ticker", "")
            info = lookup.get(tkr)
            if not info:
                continue
            if info["company"]:
                h["name"] = info["company"]
            if info["dollar_volume"] is not None:
                h["dollar_volume"] = info["dollar_volume"]
            patched_etf += 1

    DATA_PATH.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    logger.info("Done — patched %d thematic stocks, %d ETF holdings → %s",
                patched_stocks, patched_etf, DATA_PATH)


if __name__ == "__main__":
    main()
