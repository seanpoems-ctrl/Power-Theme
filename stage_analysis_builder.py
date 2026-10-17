from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
stage_analysis_builder.py — Weinstein stage analysis of the whole market (Breadth tab → "Stage Analysis")
==========================================================================================================
Classifies every stock in the universe into Stan Weinstein's four stages each week and counts them, so the Breadth tab can show how much of
the market is basing (S1), advancing (S2), topping (S3) or declining (S4) — overall and per sector, with ~2 years of history.

Universe: NASDAQ / NYSE / AMEX stocks + ADRs, market cap ≥ $500M, 10-day avg volume ≥ 100K shares (one TradingView call), ~2,900 names.
Prices:   3 years of daily closes from Yahoo (yfinance, batched), resampled to weekly (Friday close; the newest, possibly partial week uses the
          latest close, so the current reading updates every night).

Stage rules (a rules-based reading of Weinstein, following TraderLion's "Complete Guide to Stan Weinstein's Stage Analysis"; his own are by eye on a weekly chart):
  MA30  = 30-week simple moving average of the weekly close (≈ the 150-day);  MA40 = 40-week SMA (≈ the 200-day).
  slope = MA now ÷ MA 5 weeks ago − 1;  flat = |MA30 slope| ≤ FLAT (0.5 %).
  S2 advancing = close above BOTH averages, MA30 rising and MA40 not falling.   S4 declining = close below BOTH averages, MA30 falling and MA40 falling.
  Anything else is a transition and takes its meaning from the last trend the stock was in:
  S3 topping   = flat / mixed after an S2 advance.          S1 basing = flat / mixed after an S4 decline.
  (A stock with no earlier trend in the window is S3 when above its MA, S1 when below.)
  Stocks need ≥ 40 weekly closes (+5 for the slope), so recent IPOs only count once they have that history.

Output: public/stage_analysis.json
  { generated_at, as_of, universe, weeks: ["2024-10-18", …], groups: { "All": {n, s1:[…counts per week], s2, s3, s4}, "<Sector>": {…} } }
        public/stage_stocks.json — the stocks behind the counts (for the card's "Stage Movers" table):
  { as_of, cols: [...], stocks: [[ticker, company, sector, stage, weeks_in_stage, prev_stage, price, pct_vs_30w_ma, breakout_vol_x, avg_dollar_vol,
                                                   pct_vs_10w_ma, weeks_below_10w_ma, break_vol_x], …] }
  weeks_in_stage = consecutive weeks in the current stage (1 = entered this week); prev_stage = the stage before that (0 = unknown / start of history).
  breakout_vol_x (only for stage changes in the last 12 weeks) = the highest DAILY volume since the entry week began ÷ the average daily volume of the
  30 trading days before it — TraderLion's "breakout on at least twice average volume" test (≥ 2 is a high-volume breakout; the same number on a
  move into S4 / S3 is a high-volume breakdown).
  10-week MA warning (Stage 3 early warning in TraderLion's guide: "slices below the 10-week MA on heavy volume"): pct_vs_10w_ma = close vs the 10-week
  SMA (≈ 50-day); weeks_below_10w_ma = consecutive weeks the weekly close has been under it (0 = above); break_vol_x = the same volume ratio measured
  from the first week below it.
"""
import json
import logging
import math
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "public" / "stage_analysis.json"
OUT_STOCKS = ROOT / "public" / "stage_stocks.json"
DB = ROOT / "public" / "stock_db.json"
ET = ZoneInfo("America/New_York")

MIN_CAP = 500e6
MIN_AVG_VOL = 100_000
MA_WEEKS = 30
LONG_MA_WEEKS = 40      # 200-day equivalent
SHORT_MA_WEEKS = 10     # 50-day equivalent — the "10-week MA" Stage 3 warning
SLOPE_WEEKS = 5
FLAT = 0.005            # ±0.5 % change of the MA over SLOPE_WEEKS counts as "flat"
HISTORY_WEEKS = 104     # weeks written to the file
CHUNK = 400
VOL_BASE_DAYS = 30      # baseline for the breakout-volume ratio
VOL_MAX_WEEKS = 12      # compute it only for stage changes this recent
MIN_COVERAGE = 0.7      # refuse to publish if fewer than this share of the universe downloaded


def universe() -> list[str]:
    from tradingview_screener import Query, col
    q = (Query().set_markets("america").select("name", "market_cap_basic", "average_volume_10d_calc")
         .where(col("type").isin(["stock", "dr"]), col("exchange").isin(["NASDAQ", "NYSE", "AMEX"]),
                col("market_cap_basic") >= MIN_CAP, col("average_volume_10d_calc") >= MIN_AVG_VOL)
         .order_by("market_cap_basic", ascending=False).limit(6000))
    q.query.pop("filter2", None)
    _, df = q.get_scanner_data()
    # Yahoo spells class shares BRK-B; "/" lines are preferred shares (WFC/PD) that have no Yahoo history
    tickers = sorted({str(t).replace(".", "-") for t in df["name"] if "/" not in str(t)})
    logger.info("TradingView universe: %d stocks ≥ $%dM", len(tickers), MIN_CAP // 1_000_000)
    return tickers


def download_prices(tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    import yfinance as yf
    frames, vframes = [], []
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        t0 = time.time()
        for attempt in range(3):
            try:
                raw = yf.download(chunk, period="3y", interval="1d", auto_adjust=True, progress=False, group_by="column", threads=True)
                if isinstance(raw.columns, pd.MultiIndex):
                    close, vol = raw["Close"], raw["Volume"]
                else:
                    close, vol = raw[["Close"]].rename(columns={"Close": chunk[0]}), raw[["Volume"]].rename(columns={"Volume": chunk[0]})
                frames.append(close.dropna(axis=1, how="all"))
                vframes.append(vol)
                break
            except Exception as exc:        # noqa: BLE001
                logger.warning("chunk %d attempt %d failed: %s", i // CHUNK, attempt + 1, exc)
                time.sleep(5)
        logger.info("downloaded %d/%d (%.0fs)", min(i + CHUNK, len(tickers)), len(tickers), time.time() - t0)
    if not frames:
        return pd.DataFrame(), pd.DataFrame()
    return pd.concat(frames, axis=1).sort_index(), pd.concat(vframes, axis=1).sort_index()


def weekly(close: pd.DataFrame) -> pd.DataFrame:
    w = close.resample("W-FRI").last()
    # the last (possibly partial) week is labelled with its real last trading day instead of a Friday that hasn't happened
    last_day = close.dropna(how="all").index[-1]
    if w.index[-1] > last_day:
        w.index = list(w.index[:-1]) + [last_day]
    return w.dropna(how="all")


def stages(wk: pd.DataFrame) -> np.ndarray:
    """(weeks × stocks) array: 0 = not enough history, else 1-4."""
    c = wk.to_numpy(dtype=float)
    ma = wk.rolling(MA_WEEKS, min_periods=MA_WEEKS).mean().to_numpy()
    ma40 = wk.rolling(LONG_MA_WEEKS, min_periods=LONG_MA_WEEKS).mean().to_numpy()
    slope = ma / np.roll(ma, SLOPE_WEEKS, axis=0) - 1
    slope40 = ma40 / np.roll(ma40, SLOPE_WEEKS, axis=0) - 1
    slope[:SLOPE_WEEKS] = np.nan
    slope40[:SLOPE_WEEKS] = np.nan
    valid = np.isfinite(c) & np.isfinite(ma) & np.isfinite(slope) & np.isfinite(ma40) & np.isfinite(slope40)
    above = c > ma
    up = valid & above & (c > ma40) & (slope > FLAT) & (slope40 >= 0)
    down = valid & ~above & (c < ma40) & (slope < -FLAT) & (slope40 < 0)
    out = np.zeros(c.shape, dtype=np.int8)
    trend = np.zeros(c.shape[1], dtype=np.int8)          # +1 last in an advance, -1 last in a decline
    for t in range(c.shape[0]):
        trend = np.where(up[t], 1, np.where(down[t], -1, trend))
        amb = valid[t] & ~up[t] & ~down[t]
        s = np.where(up[t], 2, np.where(down[t], 4,
            np.where(amb, np.where(trend == 1, 3, np.where(trend == -1, 1, np.where(above[t], 3, 1))), 0)))
        out[t] = s
    return out


STOCK_COLS = ["ticker", "company", "sector", "stage", "weeks_in_stage", "prev_stage", "price", "pct_vs_30w_ma", "breakout_vol_x", "avg_dollar_vol",
              "pct_vs_10w_ma", "weeks_below_10w_ma", "break_vol_x"]


def stock_rows(wk: pd.DataFrame, st: np.ndarray, close: pd.DataFrame, volume: pd.DataFrame, db: list) -> list:
    """One row per classified stock: current stage, how long, where from, price vs the 30-week MA, breakout volume, liquidity."""
    info = {r["ticker"]: r for r in db if r.get("ticker")}
    cols = list(wk.columns)
    ma30 = wk.rolling(MA_WEEKS, min_periods=MA_WEEKS).mean().iloc[-1].to_numpy()
    ma10_all = wk.rolling(SHORT_MA_WEEKS, min_periods=SHORT_MA_WEEKS).mean().to_numpy()
    close_a = wk.to_numpy(dtype=float)
    below10 = np.isfinite(close_a) & np.isfinite(ma10_all) & (close_a < ma10_all)       # weekly close under the 10-week MA
    vol = volume.reindex(index=close.index, columns=cols)
    vol_a = vol.to_numpy(dtype=float)
    days = close.index.values
    week_ends = wk.index.values
    n_weeks = st.shape[0]
    last_px = wk.iloc[-1].to_numpy(dtype=float)
    def vol_ratio(j: int, weeks_back: int):
        """Highest daily volume since the week that began `weeks_back` weeks ago ÷ the average daily volume of the 30 trading days before it."""
        start = np.searchsorted(days, week_ends[n_weeks - 1 - weeks_back], side="right")
        seg = vol_a[start:, j]
        base = vol_a[max(0, start - VOL_BASE_DAYS):start, j]
        seg, base = seg[np.isfinite(seg)], base[np.isfinite(base)]
        if len(seg) and len(base) >= 15 and base.mean() > 0:
            return round(float(seg.max() / base.mean()), 2)
        return None

    rows = []
    for j, t in enumerate(cols):
        cur = int(st[-1, j])
        if cur == 0 or not np.isfinite(last_px[j]):
            continue
        w = 1
        while w < n_weeks and st[-1 - w, j] == cur:
            w += 1
        prev = int(st[-1 - w, j]) if w < n_weeks else 0
        vx = vol_ratio(j, w) if (w <= VOL_MAX_WEEKS and prev) else None
        # 10-week MA warning: consecutive weeks (incl. this one) the weekly close has been below the 10-week MA, and the volume of that break
        bw = 0
        while bw < n_weeks and below10[-1 - bw, j]:
            bw += 1
        bvx = vol_ratio(j, bw) if (0 < bw <= VOL_MAX_WEEKS) else None
        d10 = round((last_px[j] / ma10_all[-1, j] - 1) * 100, 1) if np.isfinite(ma10_all[-1, j]) and ma10_all[-1, j] > 0 else None
        recent = vol_a[-VOL_BASE_DAYS:, j]
        recent = recent[np.isfinite(recent)]
        dv = int(last_px[j] * recent.mean()) if len(recent) else None
        d = round((last_px[j] / ma30[j] - 1) * 100, 1) if np.isfinite(ma30[j]) and ma30[j] > 0 else None
        r = info.get(t, {})
        rows.append([t, r.get("company") or "", r.get("sector") or "", cur, w, prev, round(float(last_px[j]), 2), d, vx, dv, d10, bw, bvx])
    return rows


def build() -> dict | None:
    tickers = universe()
    if len(tickers) < 1500:
        logger.error("Universe too small (%d) - refusing to publish", len(tickers))
        return None
    close, volume = download_prices(tickers)
    if close.empty or close.shape[1] < MIN_COVERAGE * len(tickers):
        logger.error("Only %d of %d tickers downloaded - refusing to publish", close.shape[1] if not close.empty else 0, len(tickers))
        return None
    wk = weekly(close)
    st = stages(wk)
    logger.info("weekly matrix %s", wk.shape)

    try:
        db = json.loads(DB.read_text(encoding="utf-8"))
    except Exception:       # noqa: BLE001
        db = []
    sector_of = {r["ticker"]: r.get("sector") for r in db if r.get("ticker") and r.get("sector")}

    n_weeks = min(HISTORY_WEEKS, len(wk))
    weeks = [d.strftime("%Y-%m-%d") for d in wk.index[-n_weeks:]]
    cols = list(wk.columns)
    sectors = [sector_of.get(t) for t in cols]
    groups: dict[str, dict] = {}

    def add(name: str, mask: np.ndarray) -> None:
        sub = st[-n_weeks:, mask]
        g = {f"s{k}": [int((sub[t] == k).sum()) for t in range(n_weeks)] for k in (1, 2, 3, 4)}
        g["n"] = int(sum(g[f"s{k}"][-1] for k in (1, 2, 3, 4)))
        if g["n"] >= 15:        # skip groups too small to mean anything
            groups[name] = g

    add("All", np.ones(len(cols), dtype=bool))
    for sec in sorted({s for s in sectors if s}):
        add(sec, np.array([s == sec for s in sectors]))

    stocks = stock_rows(wk, st, close, volume, db)
    logger.info("stage_stocks rows: %d", len(stocks))

    now = datetime.now(ET)
    last = groups["All"]
    logger.info("latest week %s — S1 %d  S2 %d  S3 %d  S4 %d (n=%d)", weeks[-1], last["s1"][-1], last["s2"][-1], last["s3"][-1], last["s4"][-1], last["n"])
    return {"generated_at": now.strftime("%Y-%m-%d %H:%M ET"), "as_of": weeks[-1], "universe": len(tickers), "weeks": weeks, "groups": groups,
            "_stocks": {"as_of": weeks[-1], "cols": STOCK_COLS, "stocks": stocks}}


def main() -> None:
    payload = build()
    if not payload:
        logger.error("Stage analysis build failed - leaving the existing file untouched")
        return
    stocks_payload = payload.pop("_stocks")
    OUT_STOCKS.write_text(json.dumps(stocks_payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    logger.info("Written %s (%d stocks)", OUT_STOCKS, len(stocks_payload["stocks"]))
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    logger.info("Written %s (%d groups, %d weeks)", OUT, len(payload["groups"]), len(payload["weeks"]))
    for name, g in payload["groups"].items():
        n = g["n"] or 1
        print(f"{name:24s} n={g['n']:5d}  S1 {g['s1'][-1]/n:5.1%}  S2 {g['s2'][-1]/n:5.1%}  S3 {g['s3'][-1]/n:5.1%}  S4 {g['s4'][-1]/n:5.1%}")


if __name__ == "__main__":
    main()
