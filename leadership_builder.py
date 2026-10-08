from __future__ import annotations
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
"""
leadership_builder.py — Liquid Leadership ladder (Watchlist → 🏆 Leadership)
============================================================================
Organizes the stock universe the way a leadership screen should be read, top to bottom:

  LL    Liquid Leaders      top performers over 1M / 3M / 6M / 1Y among liquid stocks
  NEL   Non-Extended        the LL that are NOT stretched above their 50-MA (Extension < NEL_MAX_EXTENSION)
  T-NEL Tight NEL           the NEL that are also coiling (low RMV and/or several contracting days)

The same ladder is also built for a SUPER LIQUID tier (SLL -> S-NEL -> T-SNEL): the top SUPER_TOP_N performers per window among
stocks with avg $ volume >= SUPER_MIN_DOLLAR_VOLUME. The reference page doesn't publish its cut-off; $1B/day is the level that
reproduces its list (it includes CIEN / CRDO / ALAB / HPE and leaves out OKTA / TEAM / AXTI / SMTC). The Extension and RMV/Coil
cut-offs are applied in the dashboard, so they are tunable there.

plus a snapshot of which industries hold the most leaders in each window, with a daily history so the dashboard can
chart leadership over time.

Universe: TradingView, NASDAQ/NYSE/AMEX common stocks + ADRs, price >= $2, avg volume >= 500K shares and
avg dollar volume (10d) >= $40M. Extension = ATR% multiple from the 50-MA, ((close / SMA50) - 1) / (ATR / close) — the
same formula as the Extension column everywhere else on the dashboard.

Tightness is computed here from Yahoo daily bars for the LL tickers only (~80 names):
  RMV   relative measured volatility — the 5-day average true range (as % of close), placed on a 0-100 scale between its own
        lowest and highest value over the last 50 sessions. 0 = the tightest the stock has been in 10 weeks.
  Coil  number of consecutive most-recent sessions (max 10) whose true range is below the 20-day average true range.
The dashboard filters on these, so the thresholds are tunable there without a rebuild.

Output: public/leadership.json  (+ public/leadership_history.json — rolling daily industry counts per window)
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
OUTPUT_PATH = ROOT / "public" / "leadership.json"
HISTORY_PATH = ROOT / "public" / "leadership_history.json"
SNAP_DIR = ROOT / "public" / "leadership_snapshots"     # one slim file per trading day + index.json, for the dashboard's date picker
SNAP_KEEP_DAYS = 120
SNAP_FIELDS = ("ticker", "industry", "perf", "avg_dollar_volume", "extension", "rmv", "coil", "lev_etf")
ET = ZoneInfo("America/New_York")

MIN_DOLLAR_VOLUME = 40_000_000
MIN_AVG_VOLUME = 500_000
MIN_PRICE = 2.0
TOP_N = 20                    # leaders kept per window
SUPER_MIN_DOLLAR_VOLUME = 1_000_000_000
SUPER_TOP_N = 10
LEV_ETF_PATH = ROOT / "public" / "leveraged_stock_etfs.json"
HISTORY_DAYS = 60
WINDOWS = [("1M", "Perf.1M"), ("3M", "Perf.3M"), ("6M", "Perf.6M"), ("1Y", "Perf.Y")]


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def fetch_universe():
    from tradingview_screener import Query, col
    q = (Query().set_markets("america")
         .select("name", "description", "close", "ATR", "SMA50", "average_volume_10d_calc", "market_cap_basic", "industry",
                 *[c for _, c in WINDOWS])
         .where(col("type").isin(["stock", "dr"]), col("exchange").isin(["NASDAQ", "NYSE", "AMEX"]),
                col("close") >= MIN_PRICE, col("average_volume_10d_calc") > MIN_AVG_VOLUME)
         .order_by("average_volume_10d_calc", ascending=False).limit(5000))
    q.query.pop("filter2", None)          # tradingview_screener >= 3.2 hides funds by default; harmless for stocks
    _, df = q.get_scanner_data()
    logger.info("TradingView: %d candidates above %dK shares", len(df), MIN_AVG_VOLUME // 1000)
    rows = []
    for _, r in df.iterrows():
        close, atr, sma50, vol = _num(r.get("close")), _num(r.get("ATR")), _num(r.get("SMA50")), _num(r.get("average_volume_10d_calc"))
        if not close or not vol:
            continue
        dvol = close * vol
        if dvol < MIN_DOLLAR_VOLUME:
            continue
        ext = None
        if atr and sma50 and atr > 0 and sma50 > 0:
            ext = round(((close / sma50) - 1) / (atr / close), 2)
        rows.append({
            "ticker": str(r["name"]),
            "company": str(r.get("description") or ""),
            "industry": str(r.get("industry") or "") or None,
            "price": round(close, 2),
            "avg_dollar_volume": round(dvol),
            "market_cap": _num(r.get("market_cap_basic")),
            "extension": ext,
            **{f"perf_{w.lower()}": (round(_num(r.get(c)), 1) if _num(r.get(c)) is not None else None) for w, c in WINDOWS},
        })
    logger.info("Liquid universe: %d stocks (avg $ vol >= $%dM)", len(rows), MIN_DOLLAR_VOLUME // 1_000_000)
    return rows


def leveraged_pairs() -> dict[str, str]:
    """{stock ticker: its most liquid long leveraged ETF} from leveraged_etf_builder.py's file (empty if missing)."""
    try:
        etfs = json.loads(LEV_ETF_PATH.read_text(encoding="utf-8")).get("etfs", [])
    except Exception:               # noqa: BLE001
        return {}
    best: dict[str, dict] = {}
    for e in etfs:
        u = e.get("underlying")
        if u and e.get("direction") == "bull" and (u not in best or (e.get("avg_dollar_volume") or 0) > (best[u].get("avg_dollar_volume") or 0)):
            best[u] = e
    return {u: e["ticker"] for u, e in best.items()}


def tightness(tickers: list[str]) -> dict[str, dict]:
    """{ticker: {"rmv": 0-100, "coil": n}} from Yahoo daily bars; missing tickers omitted."""
    out: dict[str, dict] = {}
    if not tickers:
        return out
    try:
        import yfinance as yf
        import pandas as pd
        yt = [t.replace(".", "-") for t in tickers]
        hist = yf.download(yt, period="6mo", interval="1d", group_by="ticker", progress=False, auto_adjust=False, threads=True)
        for orig, y in zip(tickers, yt):
            try:
                d = (hist[y] if len(yt) > 1 else hist).dropna(subset=["High", "Low", "Close"])
                if len(d) < 60:
                    continue
                prev = d["Close"].shift(1)
                tr = pd.concat([d["High"] - d["Low"], (d["High"] - prev).abs(), (d["Low"] - prev).abs()], axis=1).max(axis=1)
                v = (tr.rolling(5).mean() / d["Close"]).dropna().tail(50)
                if v.max() - v.min() <= 0:
                    continue
                rmv = round(100 * (v.iloc[-1] - v.min()) / (v.max() - v.min()))
                avg20 = tr.rolling(20).mean()
                coil = 0
                for i in range(1, 11):
                    if tr.iloc[-i] < avg20.iloc[-i]:
                        coil += 1
                    else:
                        break
                out[orig] = {"rmv": int(rmv), "coil": coil}
            except Exception:       # noqa: BLE001 - one bad ticker must not sink the run
                continue
    except Exception as exc:        # noqa: BLE001
        logger.warning("Tightness lookup failed (%s) - T-NEL will be empty", exc)
    return out


def build() -> dict | None:
    try:
        universe = fetch_universe()
    except Exception as exc:        # noqa: BLE001
        logger.error("TradingView query failed: %s", exc)
        return None
    if len(universe) < 200:
        logger.error("Only %d liquid stocks - refusing to publish", len(universe))
        return None

    leaders: dict[str, list[dict]] = {}
    for w, _ in WINDOWS:
        key = f"perf_{w.lower()}"
        ranked = sorted((s for s in universe if s.get(key) is not None), key=lambda s: -s[key])[:TOP_N]
        leaders[w] = [{**s, "perf": s[key]} for s in ranked]

    super_pool = [s for s in universe if (s.get("avg_dollar_volume") or 0) >= SUPER_MIN_DOLLAR_VOLUME]
    super_leaders: dict[str, list[dict]] = {}
    for w, _ in WINDOWS:
        key = f"perf_{w.lower()}"
        ranked = sorted((s for s in super_pool if s.get(key) is not None), key=lambda s: -s[key])[:SUPER_TOP_N]
        super_leaders[w] = [{**s, "perf": s[key]} for s in ranked]
    logger.info("Super liquid pool: %d stocks (avg $ vol >= $%dB)", len(super_pool), SUPER_MIN_DOLLAR_VOLUME // 1_000_000_000)

    union = sorted({s["ticker"] for grp in (leaders, super_leaders) for lst in grp.values() for s in lst})
    tight = tightness(union)
    pairs = leveraged_pairs()
    logger.info("Tightness for %d / %d leaders; %d leveraged-ETF pairs", len(tight), len(union), len(pairs))
    for grp in (leaders, super_leaders):
        for lst in grp.values():
            for s in lst:
                s.update(tight.get(s["ticker"], {"rmv": None, "coil": None}))
                s["lev_etf"] = pairs.get(s["ticker"])
                for k in ("perf_1m", "perf_3m", "perf_6m", "perf_1y"):
                    s.pop(k, None)

    def _counts(grp):
        out = {w: {} for w, _ in WINDOWS}
        for w in out:
            for s in grp[w]:
                ind = s.get("industry") or "Other"
                out[w][ind] = out[w].get(ind, 0) + 1
        return out
    counts, super_counts = _counts(leaders), _counts(super_leaders)
    return {"generated_at": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"), "date": datetime.now(ET).strftime("%Y-%m-%d"),
            "universe_size": len(universe), "top_n": TOP_N,
            "criteria": {"min_dollar_volume": MIN_DOLLAR_VOLUME, "min_avg_volume": MIN_AVG_VOLUME, "min_price": MIN_PRICE,
                         "super_min_dollar_volume": SUPER_MIN_DOLLAR_VOLUME},
            "super_top_n": SUPER_TOP_N, "super_pool_size": len(super_pool),
            "leaders": leaders, "industry_counts": counts,
            "super_leaders": super_leaders, "super_industry_counts": super_counts}


def update_history(payload: dict) -> None:
    try:
        hist = json.loads(HISTORY_PATH.read_text(encoding="utf-8")) if HISTORY_PATH.exists() else {}
    except Exception:               # noqa: BLE001
        hist = {}
    hist[payload["date"]] = {**payload["industry_counts"], "super": payload["super_industry_counts"]}
    for d in sorted(hist)[:-HISTORY_DAYS]:
        hist.pop(d, None)
    HISTORY_PATH.write_text(json.dumps(hist, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def write_snapshot(payload: dict) -> None:
    """public/leadership_snapshots/<date>.json (weekdays only) + index.json — lets the dashboard show any past day's leaders."""
    d = payload["date"]
    if datetime.strptime(d, "%Y-%m-%d").weekday() >= 5:
        return
    def slim(grp):
        return {w: [{k: s.get(k) for k in SNAP_FIELDS} for s in rows] for w, rows in grp.items()}
    snap = {"date": d, "generated_at": payload["generated_at"], "top_n": payload["top_n"], "super_top_n": payload["super_top_n"],
            "universe_size": payload["universe_size"], "super_pool_size": payload["super_pool_size"], "criteria": payload["criteria"],
            "leaders": slim(payload["leaders"]), "super_leaders": slim(payload["super_leaders"])}
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    (SNAP_DIR / f"{d}.json").write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    dates = sorted(p.stem for p in SNAP_DIR.glob("20??-??-??.json"))
    for old in dates[:-SNAP_KEEP_DAYS]:
        (SNAP_DIR / f"{old}.json").unlink(missing_ok=True)
    (SNAP_DIR / "index.json").write_text(json.dumps({"dates": dates[-SNAP_KEEP_DAYS:]}), encoding="utf-8")


def main() -> None:
    payload = build()
    if not payload:
        logger.error("Leadership build failed - leaving existing files untouched")
        return
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    update_history(payload)
    write_snapshot(payload)
    logger.info("Written %s", OUTPUT_PATH)
    for w, _ in WINDOWS:
        ll = payload["leaders"][w]
        nel = [s for s in ll if s["extension"] is not None and s["extension"] < 4]
        print(f"{w}: LL {len(ll)} | NEL(<4x) {len(nel)} | top industries {sorted(payload['industry_counts'][w].items(), key=lambda kv: -kv[1])[:3]}")


if __name__ == "__main__":
    main()
