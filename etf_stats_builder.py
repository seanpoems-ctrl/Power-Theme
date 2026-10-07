from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
etf_stats_builder.py — per-ETF and per-holding stats for the Leadership → Themes group popup
============================================================================================
Click a theme group in Watchlist → 🏆 Leadership → Themes and a popup lists the group's ETFs (ADR, Avg $ Vol, Extension,
description) and their combined top stock holdings (Avg $ Vol, Extension). Those numbers are not in etf_rs.json /
thematic_data.json, so this script looks them up from TradingView in two batched calls:

  etfs[ticker]               adr_pct (ATR / close), avg_dollar_volume (10d avg shares x price), extension (ATR% multiple
                             from the 50-MA — same formula as everywhere else on the dashboard), description (etf_metadata.json)
  holdings_extension[ticker] Extension for every stock held by any tracked ETF

Output: public/etf_stats.json
"""
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
OUTPUT_PATH = ROOT / "public" / "etf_stats.json"
ET = ZoneInfo("America/New_York")


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _lookup(tickers: list[str], types: list[str]) -> dict[str, dict]:
    """{ticker: {close, atr, sma50, avg_vol}} via TradingView, batched. `types` = TradingView `type` values to accept."""
    from tradingview_screener import Query, col
    out: dict[str, dict] = {}
    alias = {t.replace("-", "."): t for t in tickers if "-" in t}               # BRK-B -> BRK.B
    asked = [t.replace("-", ".") for t in tickers]
    for i in range(0, len(asked), 400):
        chunk = asked[i:i + 400]
        q = (Query().set_markets("america").select("name", "close", "ATR", "SMA50", "average_volume_10d_calc")
             .where(col("name").isin(chunk), col("type").isin(types)).limit(len(chunk) + 100))
        q.query.pop("filter2", None)       # tradingview_screener >= 3.2 hides funds by default
        _, df = q.get_scanner_data()
        for _, r in df.iterrows():
            name = str(r["name"])
            t = alias.get(name, name)
            if t in out:                    # keep the first (highest-volume-sorted) listing
                continue
            out[t] = {"close": _num(r.get("close")), "atr": _num(r.get("ATR")), "sma50": _num(r.get("SMA50")),
                      "avg_vol": _num(r.get("average_volume_10d_calc"))}
    return out


def _ext(d: dict) -> float | None:
    c, a, s = d.get("close"), d.get("atr"), d.get("sma50")
    if c and a and s and a > 0 and s > 0:
        return round(((c / s) - 1) / (a / c), 2)
    return None


def main() -> None:
    try:
        meta = json.loads((ROOT / "public" / "etf_metadata.json").read_text(encoding="utf-8"))
        holdings = json.loads((ROOT / "public" / "thematic_data.json").read_text(encoding="utf-8")).get("etf_holdings", {})
    except Exception as exc:  # noqa: BLE001
        logger.error("Inputs unavailable (%s) - leaving existing file untouched", exc)
        return
    etf_tickers = sorted({m["ticker"] for m in meta if m.get("ticker")})
    hold_tickers = sorted({h["ticker"] for hs in holdings.values() for h in hs if h.get("ticker")})
    try:
        etf_data = _lookup(etf_tickers, ["fund"])
        hold_data = _lookup(hold_tickers, ["stock", "dr"])
    except Exception as exc:  # noqa: BLE001
        logger.error("TradingView lookup failed (%s) - leaving existing file untouched", exc)
        return
    desc = {m["ticker"]: m.get("description") for m in meta if m.get("ticker")}

    etfs = {}
    for t in etf_tickers:
        d = etf_data.get(t)
        if not d:
            continue
        adr = round(d["atr"] / d["close"] * 100, 2) if d.get("atr") and d.get("close") else None
        dvol = round(d["close"] * d["avg_vol"]) if d.get("close") and d.get("avg_vol") else None
        etfs[t] = {"adr_pct": adr, "avg_dollar_volume": dvol, "extension": _ext(d), "description": desc.get(t)}
    hext = {t: e for t, d in hold_data.items() if (e := _ext(d)) is not None}

    if len(etfs) < 0.5 * len(etf_tickers):
        logger.error("Only %d / %d ETFs resolved - refusing to publish", len(etfs), len(etf_tickers))
        return
    payload = {"generated_at": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"), "etfs": etfs, "holdings_extension": hext}
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("Written %s: %d / %d ETFs, %d / %d holdings with Extension", OUTPUT_PATH, len(etfs), len(etf_tickers), len(hext), len(hold_tickers))
    for t in ("BUG", "HACK", "CIBR"):
        print(t, etfs.get(t))


if __name__ == "__main__":
    main()
