import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows encoding fix
"""
backfill_breadth_extras.py — One-time historical backfill for breadth_monitor.json
=====================================================================================
breadth_monitor.py only ever patches atr_10x_ext / above_50dma_pct / universe_1b
into the SINGLE most-recent row at scrape time (see fetch_breadth_monitor()).
Historical rows that existed before that enrichment logic shipped were never
retroactively filled, leaving a gap of blank cells for those three columns.

This script reconstructs them for any row missing all three, using the same
methodology as _compute_breadth_extras() in breadth_monitor.py but against
yfinance historical daily bars instead of a live TradingView snapshot:

  1. Universe: today's TradingView screener snapshot (price >= $2, avg 10d vol
     >= 50K, US stock/DR, NYSE/NASDAQ/AMEX/NYSE ARCA) — used as an approximate
     stand-in for each historical date's membership. Over the short ~1-month
     backfill window here, drift from delistings/new listings is negligible.
  2. Market cap on each historical date is estimated by scaling today's market
     cap by (historical_close / today's_close), i.e. assuming shares
     outstanding didn't materially change — reasonable over a few weeks.
  3. ATR(14) is computed with Wilder's smoothing (matches TradingView's
     default ATR) from historical daily high/low/close.
  4. SMA(50) is a simple rolling mean of daily closes.

Run once: `python backfill_breadth_extras.py`. Writes directly to
public/breadth_monitor.json, patching only rows that are missing the fields
(never overwrites a row that already has real data).
"""
import json
import logging
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from tradingview_screener import Query, col

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BREADTH_PATH = Path("public/breadth_monitor.json")
MIN_MKTCAP = 1_000_000_000


def _wilders_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.copy()
    atr.iloc[:period] = np.nan
    if len(tr) > period:
        atr.iloc[period - 1] = tr.iloc[:period].mean()
        for i in range(period, len(tr)):
            atr.iloc[i] = (atr.iloc[i - 1] * (period - 1) + tr.iloc[i]) / period
    return atr


def main():
    data = json.loads(BREADTH_PATH.read_text(encoding="utf-8"))
    rows = data["rows"]
    missing_dates = sorted(
        r["date"] for r in rows
        if r.get("atr_10x_ext") is None or r.get("above_50dma_pct") is None or r.get("universe_1b") is None
    )
    if not missing_dates:
        logger.info("No missing rows — nothing to backfill.")
        return
    logger.info("Backfilling %d dates: %s .. %s", len(missing_dates), missing_dates[0], missing_dates[-1])

    # ── 1. Today's universe snapshot (ticker + current close + current mkt cap) ──
    logger.info("Fetching current TradingView universe snapshot…")
    _, uni = (
        Query()
        .select("name", "close", "market_cap_basic")
        .where(
            col("close") >= 2,
            col("average_volume_10d_calc") >= 50_000,
            col("type").isin(["stock", "dr"]),
            col("exchange").isin(["NYSE", "NASDAQ", "AMEX", "NYSE ARCA"]),
        )
        .limit(10_000)
        .get_scanner_data()
    )
    uni = uni.dropna(subset=["name", "close", "market_cap_basic"])
    uni = uni[uni["close"] > 0]
    logger.info("Universe: %d tickers", len(uni))

    today_close = dict(zip(uni["name"], uni["close"]))
    today_mktcap = dict(zip(uni["name"], uni["market_cap_basic"]))
    yf_tickers = {t: t.replace(".", "-") for t in uni["name"]}

    # ── 2. Chunked historical download — enough lookback for SMA50 + ATR14 + buffer ──
    # A single ~4100-ticker yf.download() call hits Yahoo's rate limiter partway
    # through and silently drops hundreds of tickers (including mega-caps) with
    # no error — chunking + pacing + retries keeps coverage complete instead.
    all_yf_tickers = list(yf_tickers.values())
    CHUNK = 200
    chunks = [all_yf_tickers[i:i + CHUNK] for i in range(0, len(all_yf_tickers), CHUNK)]
    logger.info("Downloading historical daily bars for %d tickers in %d chunks of %d…",
                len(all_yf_tickers), len(chunks), CHUNK)

    per_ticker_hist: dict[str, pd.DataFrame] = {}
    for ci, chunk in enumerate(chunks):
        remaining = list(chunk)
        for attempt in range(4):
            if not remaining:
                break
            try:
                df = yf.download(
                    remaining, start="2026-04-01", end="2026-09-02", interval="1d",
                    auto_adjust=True, progress=False, threads=False, group_by="ticker",
                )
            except Exception as exc:
                logger.warning("  chunk %d attempt %d: download raised %s", ci, attempt, exc)
                time.sleep(5 * (attempt + 1))
                continue
            still_missing = []
            for t in remaining:
                try:
                    sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                    sub = sub.dropna(subset=["Close"]) if "Close" in sub else pd.DataFrame()
                except Exception:
                    sub = pd.DataFrame()
                if len(sub) >= 55:
                    per_ticker_hist[t] = sub
                else:
                    still_missing.append(t)
            remaining = still_missing
            if remaining:
                logger.info("  chunk %d/%d: %d/%d ok, retrying %d in %ds",
                            ci + 1, len(chunks), len(chunk) - len(remaining), len(chunk), len(remaining), 5 * (attempt + 1))
                time.sleep(5 * (attempt + 1))
        if remaining:
            logger.warning("  chunk %d/%d: %d tickers still missing after retries: %s",
                           ci + 1, len(chunks), len(remaining), remaining[:15])
        time.sleep(1.5)  # pace between chunks regardless of outcome

    logger.info("Historical bars retrieved for %d / %d tickers", len(per_ticker_hist), len(all_yf_tickers))

    # ── 3. Per-ticker SMA50 / ATR14 time series ──────────────────────────────
    closes_by_date: dict[str, dict[str, float]] = {}
    sma50_by_date: dict[str, dict[str, float]] = {}
    atr_by_date: dict[str, dict[str, float]] = {}

    n_ok = 0
    for tv_ticker, yf_ticker in yf_tickers.items():
        sub = per_ticker_hist.get(yf_ticker)
        if sub is None or len(sub) < 55:
            continue
        sma50 = sub["Close"].rolling(50).mean()
        atr14 = _wilders_atr(sub["High"], sub["Low"], sub["Close"], 14)
        n_ok += 1
        for ts, close_v, sma_v, atr_v in zip(sub.index, sub["Close"], sma50, atr14):
            d = ts.strftime("%Y-%m-%d")
            closes_by_date.setdefault(d, {})[tv_ticker] = close_v
            if not math.isnan(sma_v):
                sma50_by_date.setdefault(d, {})[tv_ticker] = sma_v
            if not math.isnan(atr_v):
                atr_by_date.setdefault(d, {})[tv_ticker] = atr_v
    logger.info("Computed SMA50/ATR14 for %d / %d tickers", n_ok, len(yf_tickers))

    coverage = n_ok / len(yf_tickers) if yf_tickers else 0
    if coverage < 0.95:
        logger.error(
            "Coverage only %.1f%% (%d/%d) — too low to trust (rate-limiting or "
            "data gaps likely dropped names disproportionately, which would "
            "undercount universe_1b/above_50dma_pct). Aborting without writing.",
            coverage * 100, n_ok, len(yf_tickers),
        )
        return
    logger.info("Coverage %.1f%% — proceeding.", coverage * 100)

    # ── 4. Compute the 3 extras for each missing date ────────────────────────
    patched = 0
    for row in rows:
        d = row["date"]
        if d not in missing_dates:
            continue
        closes = closes_by_date.get(d, {})
        sma50s = sma50_by_date.get(d, {})
        atrs = atr_by_date.get(d, {})
        if not closes or not sma50s or not atrs:
            logger.warning("  %s: no historical bar found (holiday/gap?) — skipping", d)
            continue

        atr_ext_count = 0
        above_count = 0
        mktcap_1b_count = 0
        sma_total = 0

        for tv_ticker, hist_close in closes.items():
            if hist_close is None or hist_close <= 0:
                continue
            base_close = today_close.get(tv_ticker)
            base_mktcap = today_mktcap.get(tv_ticker)
            if not base_close or not base_mktcap:
                continue
            est_mktcap = base_mktcap * (hist_close / base_close)
            if est_mktcap < MIN_MKTCAP:
                continue
            mktcap_1b_count += 1

            sma50_v = sma50s.get(tv_ticker)
            if sma50_v and sma50_v > 0:
                sma_total += 1
                if hist_close > sma50_v:
                    above_count += 1

                atr_v = atrs.get(tv_ticker)
                if atr_v and atr_v > 0:
                    atr_pct = atr_v / hist_close
                    pct_gain_50ma = (hist_close - sma50_v) / sma50_v
                    if atr_pct > 0 and abs(pct_gain_50ma / atr_pct) >= 10:
                        atr_ext_count += 1

        row["atr_10x_ext"] = atr_ext_count
        row["above_50dma_pct"] = round(above_count / sma_total * 100, 1) if sma_total else None
        row["universe_1b"] = mktcap_1b_count
        patched += 1
        logger.info("  %s: atr_10x_ext=%d above_50dma=%.1f%% universe_1b=%d",
                     d, atr_ext_count, row["above_50dma_pct"] or 0, mktcap_1b_count)

    logger.info("Patched %d / %d missing rows", patched, len(missing_dates))

    # ── 5. Continuity sanity check vs. the real (non-backfilled) neighbour rows ──
    # universe_1b in particular should not jump >10% day-over-day at the seam —
    # a big jump there is the signature of an incomplete/rate-limited download.
    rows_by_date = {r["date"]: r for r in rows}
    sorted_dates = sorted(rows_by_date)
    for edge_date in (min(missing_dates), max(missing_dates)):
        idx = sorted_dates.index(edge_date)
        for neighbour_idx in (idx - 1, idx + 1):
            if 0 <= neighbour_idx < len(sorted_dates):
                nd = sorted_dates[neighbour_idx]
                if nd in missing_dates:
                    continue
                nrow = rows_by_date[nd]
                a, b = rows_by_date[edge_date].get("universe_1b"), nrow.get("universe_1b")
                if a and b:
                    pct_jump = abs(a - b) / b * 100
                    flag = "  <-- CHECK THIS" if pct_jump > 10 else ""
                    logger.info("  continuity %s (universe_1b=%s) vs %s (universe_1b=%s): %.1f%% jump%s",
                                edge_date, a, nd, b, pct_jump, flag)

    BREADTH_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote %s", BREADTH_PATH)


if __name__ == "__main__":
    main()
