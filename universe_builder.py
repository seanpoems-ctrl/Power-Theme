"""
universe_builder.py — Daily Stock Universe Builder  v2
=======================================================
Phases covered:
  Phase 1  Daily snapshot + 30-day rolling history + day-over-day RS change
  Phase 2  ETF rotation signals + sector heatmap + 20-day ETF score trend
  Phase 3  Stock signals (Strong/Emerging/Watch/Weakening) + new-ETF-entrant detection

Outputs:
  public/universe.json              — main dashboard feed
  public/universe_history/<date>.json — lightweight daily snapshot (30-day window)
  public/etf_rs_trend.json          — 20-day rolling Score per ETF
"""

import json
import logging
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_PATH    = Path("public/universe.json")
THEMATIC_PATH  = Path("public/thematic_data.json")
ETF_RS_PATH    = Path("public/etf_rs.json")
HISTORY_DIR    = Path("public/universe_history")
TREND_PATH     = Path("public/etf_rs_trend.json")
HISTORY_DAYS   = 30   # keep last N daily snapshots
TREND_DAYS     = 20   # rolling window for ETF score trend


# ── Sector groupings for heatmap ──────────────────────────────────────────────
SECTOR_GROUPS: dict[str, list[str]] = {
    "Semiconductors":   ["SOXX", "SMH", "XSD", "DRAM"],
    "Tech & AI":        ["AIQ", "BAI", "IGV", "XSW", "QTUM", "IDGT", "FIVG"],
    "Cloud":            ["WCLD", "CLOU", "SKYY"],
    "Cybersecurity":    ["CIBR", "BUG", "HACK"],
    "Space & Defense":  ["ITA", "XAR", "SHLD", "UFO", "NASA", "ARKX"],
    "Healthcare":       ["XBI", "ARKG", "GNOM", "IHI", "PBE", "XHE", "IHF"],
    "Energy":           ["ICLN", "TAN", "PBW", "XLE", "UNG", "HYDR", "FCG", "XOP"],
    "Crypto & Fintech": ["BLOK", "WGMI", "DXYZ", "FINX"],
    "Industrials":      ["XLI", "PAVE", "BOTZ", "ROBO", "DRIV", "FDRV", "XTN", "IYT", "ARKQ"],
    "Financials":       ["XLF", "KRE", "KBE", "KCE", "KIE", "AMLP"],
    "Commodities":      ["GDX", "GLD", "COPX", "SIL", "SILJ", "XME", "SLX", "PICK", "REMX"],
    "Consumer":         ["XLY", "XLP", "IBUY", "MEME", "BETZ", "HERO", "METV", "MOO"],
    "ARK Funds":        ["ARKK", "ARKW", "ARKG", "ARKF", "ARKQ", "ARKX"],
    "Comms & Media":    ["XLC", "FCOM", "XTL", "IYZ", "SOCL", "BUZZ", "FDN", "SNSR"],
}


# ── Signal classification ─────────────────────────────────────────────────────

def stock_signal(rs, perf_1d, perf_1w, perf_1m, perf_3m, etf_count):
    rs  = rs  or 0
    p1m = perf_1m or 0
    p3m = perf_3m or 0
    p1w = perf_1w or 0
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
    day, mth, score = e.get("rs_day"), e.get("rs_mth"), e.get("score") or 0
    if day is None or mth is None:
        return "Neutral"
    if day > mth and score >= 55:
        return "Rotating In"
    if day < mth - 15:
        return "Rotating Out"
    return "Neutral"


# ── History helpers ───────────────────────────────────────────────────────────

def _today_str() -> str:
    return datetime.now(tz=timezone.utc).date().isoformat()


def load_yesterday() -> dict[str, dict]:
    """Load yesterday's light snapshot → {ticker: {rs, etfs}}. Returns {} if missing."""
    yesterday = (datetime.now(tz=timezone.utc).date() - timedelta(days=1)).isoformat()
    p = HISTORY_DIR / f"{yesterday}.json"
    # Also try up to 3 days back (weekends / holidays)
    for delta in range(1, 5):
        d = (datetime.now(tz=timezone.utc).date() - timedelta(days=delta)).isoformat()
        p = HISTORY_DIR / f"{d}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")).get("stocks", {})
            except Exception:
                pass
    return {}


def save_snapshot(today: str, stocks: list[dict]) -> None:
    """Save a lightweight snapshot and prune files older than HISTORY_DAYS."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    snap = {
        "date": today,
        "stocks": {s["ticker"]: {"rs": s.get("rs"), "etfs": s.get("etfs", [])} for s in stocks},
    }
    (HISTORY_DIR / f"{today}.json").write_text(
        json.dumps(snap, separators=(",", ":")), encoding="utf-8"
    )
    # Prune old files
    cutoff = (datetime.now(tz=timezone.utc).date() - timedelta(days=HISTORY_DAYS)).isoformat()
    for f in HISTORY_DIR.glob("*.json"):
        if f.stem < cutoff:
            f.unlink(missing_ok=True)
    logger.info("Snapshot saved → %s", HISTORY_DIR / f"{today}.json")


def update_etf_trend(etf_rs_list: list[dict]) -> dict[str, list[float]]:
    """Append today's ETF Score to a rolling TREND_DAYS window. Returns updated history."""
    try:
        existing = json.loads(TREND_PATH.read_text(encoding="utf-8")).get("history", {})
    except Exception:
        existing = {}

    for e in etf_rs_list:
        tkr   = e["ticker"]
        score = e.get("score")
        if score is None:
            continue
        prev = existing.get(tkr, [])
        prev.append(round(score, 1))
        existing[tkr] = prev[-TREND_DAYS:]   # keep last N days

    TREND_PATH.write_text(
        json.dumps({"updated_at": _today_str(), "history": existing}, separators=(",", ":")),
        encoding="utf-8",
    )
    logger.info("ETF RS trend updated → %s ETFs in history", len(existing))
    return existing


# ── Sector heatmap ─────────────────────────────────────────────────────────────

def build_sector_heatmap(etf_rs_list: list[dict]) -> list[dict]:
    lookup = {e["ticker"]: e for e in etf_rs_list}
    rows = []
    for sector, tickers in SECTOR_GROUPS.items():
        members = [lookup[t] for t in tickers if t in lookup]
        if not members:
            continue

        def avg(field: str):
            vals = [m[field] for m in members if m.get(field) is not None]
            return round(sum(vals) / len(vals), 1) if vals else None

        rows.append({
            "sector":    sector,
            "etfs":      [m["ticker"] for m in members],
            "etf_count": len(members),
            "score":     avg("score"),
            "rs_day":    avg("rs_day"),
            "rs_wk":     avg("rs_wk"),
            "rs_mth":    avg("rs_mth"),
            "rs_qtr":    avg("rs_qtr"),
            "rs_hy":     avg("rs_hy"),
            "rs_yr":     avg("rs_yr"),
            "perf_1d":   avg("perf_1d"),
            "perf_1m":   avg("perf_1m"),
            "perf_3m":   avg("perf_3m"),
        })
    rows.sort(key=lambda x: -(x.get("score") or 0))
    return rows


# ── Main builder ──────────────────────────────────────────────────────────────

def build_universe() -> dict:
    today = _today_str()

    # Load sources
    try:
        thematic = json.loads(THEMATIC_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Could not load thematic_data.json: %s", e); thematic = {}

    try:
        etf_rs_data = json.loads(ETF_RS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Could not load etf_rs.json: %s", e); etf_rs_data = {}

    etf_holdings = thematic.get("etf_holdings", {})
    etf_rs_list  = etf_rs_data.get("etfs", [])

    # ── Phase 1: load yesterday for diff ─────────────────────────────────────
    yesterday_snap = load_yesterday()

    # ── Phase 2: ETF rotation + sector heatmap + trend ───────────────────────
    rotating_in, rotating_out = [], []
    etf_trend = update_etf_trend(etf_rs_list)

    for e in etf_rs_list:
        rot = etf_rotation(e)
        rec = {k: e.get(k) for k in ("ticker","theme","score","rs_day","rs_wk",
                                      "rs_mth","rs_qtr","rs_hy","rs_yr",
                                      "perf_1d","perf_1w","perf_1m","perf_3m","pct_off_52wh")}
        rec["trend"] = etf_trend.get(e["ticker"], [])
        if rot == "Rotating In":   rotating_in.append(rec)
        elif rot == "Rotating Out": rotating_out.append(rec)

    rotating_in.sort(key=lambda x: -(x.get("score") or 0))
    rotating_out.sort(key=lambda x: (x.get("score") or 0))

    sector_heatmap = build_sector_heatmap(etf_rs_list)

    # ── Phase 3: stock universe ───────────────────────────────────────────────
    stock_map: dict[str, dict] = {}

    # From ETF holdings
    for etf_tkr, holdings in etf_holdings.items():
        for h in holdings:
            tkr = h.get("ticker", "").strip().upper()
            if not tkr or len(tkr) > 7 or "." in tkr:
                continue
            if tkr not in stock_map:
                stock_map[tkr] = {
                    "ticker": tkr, "name": h.get("name",""),
                    "rs": h.get("rs"), "perf_1d": h.get("perf_1d"),
                    "perf_1w": h.get("perf_1w"), "perf_1m": h.get("perf_1m"),
                    "perf_3m": h.get("perf_3m"), "adr_pct": h.get("adr_pct"),
                    "dollar_volume": h.get("dollar_volume"), "mkt_cap": h.get("mkt_cap"),
                    "themes": [], "etfs": set(),
                }
            stock_map[tkr]["etfs"].add(etf_tkr)

    # From thematic scanner stocks
    for theme in thematic.get("themes", []):
        for sub in theme.get("subthemes", []):
            for s in sub.get("stocks", []):
                tkr = s.get("ticker","").strip().upper()
                if not tkr: continue
                if tkr not in stock_map:
                    stock_map[tkr] = {
                        "ticker": tkr, "name": s.get("company",""),
                        "rs": s.get("rs_52w"), "perf_1d": s.get("perf_1d"),
                        "perf_1w": s.get("perf_1w"), "perf_1m": s.get("perf_1m"),
                        "perf_3m": s.get("perf_3m"), "adr_pct": s.get("adr_pct"),
                        "dollar_volume": s.get("dollar_volume"), "mkt_cap": None,
                        "themes": [], "etfs": set(),
                    }
                else:
                    if s.get("rs_52w") is not None: stock_map[tkr]["rs"] = s["rs_52w"]
                    if not stock_map[tkr]["name"] and s.get("company"):
                        stock_map[tkr]["name"] = s["company"]
                    for f in ("perf_1d","perf_1w","perf_1m","perf_3m","adr_pct","dollar_volume"):
                        if stock_map[tkr].get(f) is None and s.get(f) is not None:
                            stock_map[tkr][f] = s[f]
                if theme["name"] not in stock_map[tkr]["themes"]:
                    stock_map[tkr]["themes"].append(theme["name"])

    # Classify signals + compute RS change + detect new ETF entrants
    stocks_out = []
    signal_counts: dict[str, int] = {}

    for tkr, s in stock_map.items():
        s["etfs"]      = sorted(s["etfs"])
        s["etf_count"] = len(s["etfs"])
        s["signal"]    = stock_signal(s.get("rs"), s.get("perf_1d"), s.get("perf_1w"),
                                       s.get("perf_1m"), s.get("perf_3m"), s["etf_count"])
        signal_counts[s["signal"]] = signal_counts.get(s["signal"], 0) + 1

        # Phase 1: RS change vs yesterday
        prev = yesterday_snap.get(tkr, {})
        prev_rs = prev.get("rs")
        cur_rs  = s.get("rs")
        if cur_rs is not None and prev_rs is not None:
            s["rs_change"] = cur_rs - prev_rs
        else:
            s["rs_change"] = None

        # Phase 3: newly entered ETFs since yesterday
        prev_etfs = set(prev.get("etfs", []))
        new_etfs  = [e for e in s["etfs"] if e not in prev_etfs]
        s["new_etfs"] = new_etfs

        stocks_out.append(s)

    stocks_out.sort(key=lambda x: -(x.get("rs") or 0))

    # Save daily snapshot for tomorrow's diff
    save_snapshot(today, stocks_out)

    logger.info("Universe: %d stocks | Rotating In: %d | Rotating Out: %d | Sectors: %d",
                len(stocks_out), len(rotating_in), len(rotating_out), len(sector_heatmap))
    logger.info("Signals: %s", signal_counts)

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "date":   today,
        "summary": {
            "total_stocks":  len(stocks_out),
            "rotating_in":   len(rotating_in),
            "rotating_out":  len(rotating_out),
            "signal_counts": signal_counts,
        },
        "sector_heatmap": sector_heatmap,
        "etf_rotation":   {"rotating_in": rotating_in, "rotating_out": rotating_out},
        "stocks":         stocks_out,
    }


def main() -> None:
    data = build_universe()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Done → %s", OUTPUT_PATH)


if __name__ == "__main__":
    main()
