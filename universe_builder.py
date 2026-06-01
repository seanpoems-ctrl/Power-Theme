"""
universe_builder.py — Daily Stock Universe Builder
===================================================
Aggregates stocks from ETF holdings + thematic scanner data.
Computes:
  - ETF rotation signals  (Rotating In / Rotating Out)
  - Stock signals         (Strong / Emerging / Watch / Weakening / Neutral)
Outputs: public/universe.json
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_PATH   = Path("public/universe.json")
THEMATIC_PATH = Path("public/thematic_data.json")
ETF_RS_PATH   = Path("public/etf_rs.json")


# ── Signal thresholds ─────────────────────────────────────────────────────────

def stock_signal(rs, perf_1d, perf_1w, perf_1m, perf_3m, etf_count):
    """Classify a stock into Strong / Emerging / Watch / Weakening / Neutral."""
    rs      = rs      or 0
    p1m     = perf_1m or 0
    p3m     = perf_3m or 0
    p1w     = perf_1w or 0

    if rs >= 90 and p1m >= 10:
        return "Strong"
    if 70 <= rs < 90 and p1m >= 8 and (p3m >= 15 or etf_count >= 2):
        return "Emerging"
    if 60 <= rs < 70 and p1w >= 3:
        return "Watch"
    if rs < 50 or p1m < -5:
        return "Weakening"
    return "Neutral"


def etf_rotation(e):
    """Rotating In / Rotating Out / Neutral based on short vs long RS trend."""
    day = e.get("rs_day")
    mth = e.get("rs_mth")
    qtr = e.get("rs_qtr")
    score = e.get("score") or 0

    if day is None or mth is None:
        return "Neutral"

    # Rotating In: short-term RS accelerating above medium-term
    if day > mth and score >= 55:
        return "Rotating In"

    # Rotating Out: short-term RS sharply below medium-term
    if day < mth - 15:
        return "Rotating Out"

    return "Neutral"


# ── Main builder ─────────────────────────────────────────────────────────────

def build_universe() -> dict:
    # ── Load sources ──────────────────────────────────────────────────────────
    try:
        thematic = json.loads(THEMATIC_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Could not load thematic_data.json: {e}")
        thematic = {}

    try:
        etf_rs_data = json.loads(ETF_RS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Could not load etf_rs.json: {e}")
        etf_rs_data = {}

    etf_holdings = thematic.get("etf_holdings", {})
    etf_rs_list  = etf_rs_data.get("etfs", [])

    # ── ETF rotation ──────────────────────────────────────────────────────────
    rotating_in, rotating_out = [], []
    for e in etf_rs_list:
        rot = etf_rotation(e)
        rec = {
            "ticker":  e["ticker"],
            "theme":   e.get("theme", ""),
            "score":   e.get("score"),
            "day_rs":  e.get("rs_day"),
            "wk_rs":   e.get("rs_wk"),
            "mth_rs":  e.get("rs_mth"),
            "qtr_rs":  e.get("rs_qtr"),
            "perf_1d": e.get("perf_1d"),
            "perf_1w": e.get("perf_1w"),
            "perf_1m": e.get("perf_1m"),
            "perf_3m": e.get("perf_3m"),
            "pct_off_52wh": e.get("pct_off_52wh"),
        }
        if rot == "Rotating In":
            rotating_in.append(rec)
        elif rot == "Rotating Out":
            rotating_out.append(rec)

    rotating_in.sort(key=lambda x: -(x.get("score") or 0))
    rotating_out.sort(key=lambda x: (x.get("score") or 0))

    # ── Stock universe ─────────────────────────────────────────────────────────
    stock_map: dict[str, dict] = {}

    # 1) ETF holdings (price/perf/RS already enriched by scraper)
    for etf_tkr, holdings in etf_holdings.items():
        for h in holdings:
            tkr = h.get("ticker", "").strip().upper()
            if not tkr or len(tkr) > 7 or "." in tkr:
                continue
            if tkr not in stock_map:
                stock_map[tkr] = {
                    "ticker":        tkr,
                    "name":          h.get("name", ""),
                    "rs":            h.get("rs"),
                    "perf_1d":       h.get("perf_1d"),
                    "perf_1w":       h.get("perf_1w"),
                    "perf_1m":       h.get("perf_1m"),
                    "perf_3m":       h.get("perf_3m"),
                    "adr_pct":       h.get("adr_pct"),
                    "dollar_volume": h.get("dollar_volume"),
                    "mkt_cap":       h.get("mkt_cap"),
                    "themes":        [],
                    "etfs":          set(),
                }
            stock_map[tkr]["etfs"].add(etf_tkr)

    # 2) Thematic scanner stocks (may have RS + SMA data not in holdings)
    for theme in thematic.get("themes", []):
        for sub in theme.get("subthemes", []):
            for s in sub.get("stocks", []):
                tkr = s.get("ticker", "").strip().upper()
                if not tkr:
                    continue
                if tkr not in stock_map:
                    stock_map[tkr] = {
                        "ticker":        tkr,
                        "name":          s.get("company", ""),
                        "rs":            s.get("rs_52w"),
                        "perf_1d":       s.get("perf_1d"),
                        "perf_1w":       s.get("perf_1w"),
                        "perf_1m":       s.get("perf_1m"),
                        "perf_3m":       s.get("perf_3m"),
                        "adr_pct":       s.get("adr_pct"),
                        "dollar_volume": s.get("dollar_volume"),
                        "mkt_cap":       None,
                        "themes":        [],
                        "etfs":          set(),
                    }
                else:
                    # Prefer thematic RS (more granular) and fill missing fields
                    if s.get("rs_52w") is not None:
                        stock_map[tkr]["rs"] = s["rs_52w"]
                    if not stock_map[tkr]["name"] and s.get("company"):
                        stock_map[tkr]["name"] = s["company"]
                    for field in ("perf_1d", "perf_1w", "perf_1m", "perf_3m", "adr_pct", "dollar_volume"):
                        if stock_map[tkr].get(field) is None and s.get(field) is not None:
                            stock_map[tkr][field] = s[field]

                if theme["name"] not in stock_map[tkr]["themes"]:
                    stock_map[tkr]["themes"].append(theme["name"])

    # ── Classify signals + serialise sets ────────────────────────────────────
    stocks_out = []
    signal_counts: dict[str, int] = {}
    for tkr, s in stock_map.items():
        s["etfs"]      = sorted(s["etfs"])
        s["etf_count"] = len(s["etfs"])
        sig = stock_signal(
            s.get("rs"), s.get("perf_1d"), s.get("perf_1w"),
            s.get("perf_1m"), s.get("perf_3m"), s["etf_count"],
        )
        s["signal"] = sig
        signal_counts[sig] = signal_counts.get(sig, 0) + 1
        stocks_out.append(s)

    stocks_out.sort(key=lambda x: -(x.get("rs") or 0))

    logger.info(
        "Universe built: %d stocks | Rotating In: %d | Rotating Out: %d",
        len(stocks_out), len(rotating_in), len(rotating_out),
    )
    logger.info("Signal breakdown: %s", signal_counts)

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "date":         datetime.now(tz=timezone.utc).date().isoformat(),
        "summary": {
            "total_stocks":  len(stocks_out),
            "rotating_in":   len(rotating_in),
            "rotating_out":  len(rotating_out),
            "signal_counts": signal_counts,
        },
        "etf_rotation": {
            "rotating_in":  rotating_in,
            "rotating_out": rotating_out,
        },
        "stocks": stocks_out,
    }


def main() -> None:
    data = build_universe()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Done → %s", OUTPUT_PATH)


if __name__ == "__main__":
    main()
