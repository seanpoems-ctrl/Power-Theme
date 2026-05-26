"""
backfill_perf_history.py
========================
Backfills missing qtd, mtd, d34, and dv fields into all existing
public/breadth_history/YYYY-MM-DD.json archive files.

Fields added per stock:
  qtd  – % return from start of calendar quarter to that archive date
  mtd  – % return from start of calendar month to that archive date
  d34  – % return from 34 trading days prior to that archive date
  dv   – dollar volume (formatted "$X.XB" / "$X.XM") for that archive date

Uses a single yfinance batch download (12 months of daily OHLCV).

Run once:
    python backfill_perf_history.py
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PUBLIC_DIR  = Path("public")
HISTORY_DIR = PUBLIC_DIR / "breadth_history"


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def quarter_start(d: date) -> date:
    month = ((d.month - 1) // 3) * 3 + 1
    return date(d.year, month, 1)

def month_start(d: date) -> date:
    return date(d.year, d.month, 1)

def fmt_dv(raw: float) -> str:
    if raw >= 1e9:
        return f"${raw / 1e9:.2f}B"
    if raw >= 1e6:
        return f"${raw / 1e6:.1f}M"
    if raw >= 1e3:
        return f"${raw / 1e3:.0f}K"
    return f"${raw:.0f}"


# ---------------------------------------------------------------------------
# Build per-date, per-ticker stats from downloaded OHLCV
# ---------------------------------------------------------------------------

def build_stats(closes: pd.DataFrame, volumes: pd.DataFrame,
                archive_dates: list[date]) -> dict:
    """
    Returns {date_str: {ticker: {qtd, mtd, d34, dv}}}
    Only populates keys whose values could be computed.
    """
    idx = closes.index  # DatetimeIndex of trading days

    results: dict = {}

    for arch_date in archive_dates:
        date_str = arch_date.strftime("%Y-%m-%d")

        # Closest trading day at or before arch_date
        mask = idx <= pd.Timestamp(arch_date)
        if not mask.any():
            results[date_str] = {}
            continue
        date_loc = idx[mask][-1]
        date_pos = idx.get_loc(date_loc)  # integer position

        # Anchor dates
        qs_ts = pd.Timestamp(quarter_start(arch_date))
        ms_ts = pd.Timestamp(month_start(arch_date))

        # Closest trading day at or before anchor
        qs_mask = idx <= qs_ts
        qs_loc  = idx[qs_mask][-1] if qs_mask.any() else None
        ms_mask = idx <= ms_ts
        ms_loc  = idx[ms_mask][-1] if ms_mask.any() else None

        # 34 trading days back (exclusive of current date)
        d34_pos = date_pos - 34
        d34_loc = idx[d34_pos] if d34_pos >= 0 else None

        ticker_stats: dict = {}

        for tkr in closes.columns:
            try:
                price_now = closes.at[date_loc, tkr]
                if pd.isna(price_now) or price_now <= 0:
                    continue

                s: dict = {}

                # QTD
                if qs_loc is not None:
                    p = closes.at[qs_loc, tkr]
                    if not pd.isna(p) and p > 0:
                        s["qtd"] = round((price_now / p - 1) * 100, 2)

                # MTD
                if ms_loc is not None:
                    p = closes.at[ms_loc, tkr]
                    if not pd.isna(p) and p > 0:
                        s["mtd"] = round((price_now / p - 1) * 100, 2)

                # 34D
                if d34_loc is not None:
                    p = closes.at[d34_loc, tkr]
                    if not pd.isna(p) and p > 0:
                        s["d34"] = round((price_now / p - 1) * 100, 2)

                # Dollar volume
                if tkr in volumes.columns:
                    vol = volumes.at[date_loc, tkr]
                    if not pd.isna(vol) and vol > 0:
                        s["dv"] = fmt_dv(float(price_now * vol))

                if s:
                    ticker_stats[tkr] = s

            except Exception:
                continue

        results[date_str] = ticker_stats
        logger.info("  %s: computed stats for %d tickers", date_str, len(ticker_stats))

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    files = sorted(HISTORY_DIR.glob("*.json"))
    if not files:
        logger.error("No archive files found in %s", HISTORY_DIR)
        return

    # ── Collect tickers + dates ──────────────────────────────────────────────
    all_tickers: set[str] = set()
    archive_dates: list[date] = []

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for stocks in data.get("filters", {}).values():
                for s in stocks:
                    t = s.get("t", "").strip()
                    if t:
                        all_tickers.add(t)
            archive_dates.append(datetime.strptime(path.stem, "%Y-%m-%d").date())
        except Exception as exc:
            logger.warning("Could not read %s: %s", path.name, exc)

    if not all_tickers:
        logger.error("No tickers found — nothing to do")
        return

    tickers_list = sorted(all_tickers)
    logger.info(
        "Found %d unique tickers across %d archive files — downloading 12 months of OHLCV …",
        len(tickers_list), len(files),
    )

    # ── Single batch download ────────────────────────────────────────────────
    hist = yf.download(
        tickers_list, period="12mo", interval="1d",
        auto_adjust=True, progress=False,
    )

    if hist is None or hist.empty:
        logger.error("yfinance download returned empty — aborting")
        return

    closes  = hist["Close"]
    volumes = hist["Volume"] if "Volume" in hist else pd.DataFrame()

    # Single-ticker download returns flat columns; normalise to DataFrame with ticker column
    if len(tickers_list) == 1:
        tkr = tickers_list[0]
        closes  = closes.to_frame(name=tkr)  if isinstance(closes,  pd.Series) else closes.rename(columns={"Close":  tkr})
        volumes = volumes.to_frame(name=tkr) if isinstance(volumes, pd.Series) else volumes.rename(columns={"Volume": tkr})

    logger.info("Downloaded %d tickers × %d days", closes.shape[1], closes.shape[0])

    # ── Compute stats per date ───────────────────────────────────────────────
    logger.info("Computing qtd/mtd/d34/dv for each archive date …")
    stats_by_date = build_stats(closes, volumes, archive_dates)

    # ── Stamp archives ───────────────────────────────────────────────────────
    total_updated = total_stamped = 0

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            date_str    = path.stem
            date_stats  = stats_by_date.get(date_str, {})

            changed = False
            for stocks in data.get("filters", {}).values():
                for s in stocks:
                    tkr = s.get("t", "")
                    ts  = date_stats.get(tkr, {})
                    for field in ("qtd", "mtd", "d34", "dv"):
                        if field in ts and s.get(field) is None:
                            s[field] = ts[field]
                            changed  = True
                            total_stamped += 1

            if changed:
                path.write_text(
                    json.dumps(data, separators=(",", ":")),
                    encoding="utf-8",
                )
                total_updated += 1
                logger.info("  Updated  %s", path.name)
            else:
                logger.info("  Unchanged %s", path.name)

        except Exception as exc:
            logger.warning("Could not process %s: %s", path.name, exc)

    logger.info(
        "Done: %d/%d files updated, %d fields stamped",
        total_updated, len(files), total_stamped,
    )


if __name__ == "__main__":
    main()
