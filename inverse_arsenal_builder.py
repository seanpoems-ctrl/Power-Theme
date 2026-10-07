import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows encoding fix
"""
inverse_arsenal_builder.py — liquid inverse / hedge vehicles for the Short tab.

Builds public/inverse_arsenal.json: a short, always-ready list of the most liquid
inverse (and long-volatility) ETFs, so there is no scramble to find the vehicle once
the market turns. Two tiers:

  core - one most-liquid vehicle per exposure (Nasdaq-100, S&P 500, Dow, small caps,
         key sectors, crude oil / natgas / gold / silver, long volatility). Always
         shown; a vehicle that fails the liquidity floors is flagged, not hidden,
         if it is one of the user's pinned names (SQQQ SOXS LABD TZA UVIX).
  hot  - every other 2x/3x inverse ETF that passes Jeff Sun's inverse-ETF Finviz
         screen (avg volume > 2M, weekly volatility > 3%) plus a $20M/day dollar
         volume floor. Mostly single-stock inverses; changes with the tape.

TradingView supplies the universe (its screener has an inverse flag and leverage
ratio; Finviz's free tier ignores its ETF-type filters) and Yahoo supplies the
price-only 1D / 1W / 1M, same conventions as etf_rs_builder.py.

Run: python inverse_arsenal_builder.py
"""

import sys
sys.dont_write_bytecode = True

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
from tradingview_screener import Query, col

ROOT = Path(__file__).parent
OUTPUT_JSON = ROOT / "public" / "inverse_arsenal.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Rules ─────────────────────────────────────────────────────────────────────
MIN_AVG_VOLUME = 2_000_000          # Jeff Sun's Finviz inverse-ETF screen: avg volume > 2M
MIN_DOLLAR_VOLUME = 20_000_000      # added floor: avg shares x price >= $20M a day
HOT_WEEKLY_VOLATILITY = 3.0         # Jeff Sun: weekly volatility > 3%
HOT_LEVERAGE = ("2x", "3x")         # Jeff Sun: inverse 2x / 3x only

# Pinned by the user: always shown, flagged (not dropped) if they miss a floor.
# ETHD is the only Ether inverse; it is thin, so it is kept but flagged rather than dropped.
PINNED = {"SQQQ", "SOXS", "LABD", "TZA", "UVIX", "ETHD"}

# (group, exposure, candidate tickers most-liquid-first-ish, underlying ETF for context)
# Broad index exposures keep only the single most liquid candidate that passes the floors.
CORE_EXPOSURES = [
    ("Index",      "Nasdaq-100",             ["SQQQ", "QID"],                "QQQ"),
    ("Index",      "S&P 500",                ["SPXS", "SPXU", "SDS"],        "SPY"),
    ("Index",      "Dow 30",                 ["SDOW", "DXD"],                "DIA"),
    ("Index",      "Small Cap (Russell 2000)", ["TZA", "SRTY", "TWM"],       "IWM"),
    ("Sector",     "Semiconductors",         ["SOXS", "SSG"],                "SOXX"),
    ("Sector",     "Biotech",                ["LABD"],                       "XBI"),
    ("Sector",     "Energy",                 ["ERY", "DRIP"],                "XLE"),
    ("Sector",     "Financials",             ["FAZ"],                        "XLF"),
    ("Sector",     "Technology",             ["TECS"],                       "XLK"),
    ("Sector",     "Gold Miners",            ["GDXD", "DUST"],               "GDX"),
    ("Rates",      "20Y Treasuries",         ["TMV"],                        "TLT"),
    ("Commodity",  "Crude Oil",              ["SCO"],                        "USO"),
    ("Commodity",  "Natural Gas",            ["KOLD"],                       "UNG"),
    ("Commodity",  "Gold",                   ["GLL"],                        "GLD"),
    ("Commodity",  "Silver",                 ["ZSL"],                        "SLV"),
    ("Crypto",     "Bitcoin",                ["BTCZ", "SBIT", "BITI"],       "IBIT"),
    ("Crypto",     "Ethereum",               ["ETHD"],                       "ETHA"),
    ("Crypto",     "MicroStrategy (MSTR)",   ["MSTZ", "SMST"],               "MSTR"),
]
# Long-volatility funds are not "inverse" in TradingView's flag (they are long VIX
# futures), but they are the other half of a downside kit. Keep every one that passes.
VOLATILITY = ["UVXY", "UVIX"]
# TradingView does not flag the Bitcoin / Ether short funds as "Inverse" (they come back
# as Leveraged or Non-leveraged), so they are fetched by name and signed negative here.
CRYPTO_INVERSE = ["BTCZ", "SBIT", "BITI", "ETHD"]

# Long-side ETFs (all tracked in etf_rs.json) that an arsenal vehicle expresses. Used to
# tell the UI which weak-RS ETF each vehicle is the instrument for.
RELATED_ETFS = {
    "Nasdaq-100":               ["QQQ", "QQQE"],
    "S&P 500":                  ["SPY", "IVV"],
    "Dow 30":                   ["DIA"],
    "Small Cap (Russell 2000)": ["IWM", "IJR", "IJS", "IJT"],
    "Semiconductors":           ["SOXX", "SMH", "XSD"],
    "Biotech":                  ["XBI", "IBB"],
    "Energy":                   ["XLE", "XOP", "OIH", "RSPG", "XES"],
    "Financials":               ["XLF"],
    "Technology":               ["XLK", "RSPT", "IGV"],
    "Gold Miners":              ["GDX", "GDXJ"],
    "20Y Treasuries":           ["TLT"],
    "Crude Oil":                ["USO"],
    "Natural Gas":              ["UNG"],
    "Gold":                     ["GLD"],
    "Silver":                   ["SLV"],
    "Bitcoin":                  ["IBIT"],
    "Ethereum":                 ["ETHA"],
}
ETF_RS_JSON = ROOT / "public" / "etf_rs.json"

TV_COLS = ["name", "description", "close", "change", "volume", "average_volume_60d_calc",
           "ATR", "Volatility.W", "EMA20", "SMA50", "leverage_ratio", "leveraged_flag",
           "Perf.W", "Perf.1M", "price_52_week_high"]   # last three: fallback when Yahoo is unavailable


def _tv(label: str, *conds, limit: int = 300) -> pd.DataFrame:
    """One TradingView query; logs how many rows came back so an empty result is visible in CI."""
    try:
        q = (Query().set_markets('america').select(*TV_COLS).where(*conds)
             .order_by('average_volume_60d_calc', ascending=False).limit(limit))
        # tradingview_screener >= 3.2 adds a default `filter2` that EXCLUDES ETFs, mutual funds and
        # closed-end funds (type=fund AND typespecs has_none_of [etf, mutual, closedend]). CI installs
        # the latest version, so every ETF query came back empty there ("Universe: 0") while 3.1.0
        # locally returned them. Drop the default; our own conditions already restrict to ETFs.
        q.query.pop('filter2', None)
        total, df = q.get_scanner_data()
        logger.info("TradingView [%s]: total=%s rows=%d", label, total, len(df))
        return df
    except Exception as e:
        logger.warning("TradingView [%s] failed: %s", label, e)
        return pd.DataFrame()


def _previous_tickers() -> set[str]:
    """Tickers from the last good output, so the universe survives a TradingView hiccup."""
    try:
        old = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
        return {r["ticker"] for r in old.get("core", []) + old.get("hot", [])}
    except Exception:
        return set()


def _fetch_universe() -> pd.DataFrame:
    """Inverse ETFs (TradingView flag) with a liquidity pre-filter, plus the VIX and crypto funds.

    2026-10-01: the nightly CI run got zero rows from the flag-based query (it returned 31 locally)
    and then asked Yahoo for an empty ticker list, which surfaced as "No objects to concatenate".
    So: log every query's row count, and if the flag query comes back empty fall back to a
    by-name query of the known candidates plus last night's tickers."""
    try:
        import importlib.metadata as md
        logger.info("tradingview_screener %s", md.version("tradingview_screener"))
    except Exception:
        pass
    base = [col('type') == 'fund', col('typespecs').has('etf')]
    inv = _tv("inverse flag", *base, col('leveraged_flag') == 'Inverse', col('average_volume_60d_calc') > 300_000)
    named = VOLATILITY + CRYPTO_INVERSE
    if inv.empty:
        names = sorted({c for _, _, cands, _ in CORE_EXPOSURES for c in cands} | set(named) | _previous_tickers())
        logger.warning("Inverse-flag query returned nothing - falling back to %d known tickers by name.", len(names))
        inv = _tv("known tickers by name", col('name').isin(names), limit=len(names) + 20)
    vol = _tv("volatility + crypto by name", col('name').isin(named), limit=30)
    frames = [d for d in (inv, vol) if not d.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True).drop_duplicates('name')
    df['dollar_volume'] = df['average_volume_60d_calc'] * df['close']
    df['is_inverse'] = [_is_inverse(r) for r in df.to_dict('records')]
    df['lev'] = [_ratio(r) for r in df.to_dict('records')]
    return df


def _pct_back(s: pd.Series, bars_back: int):
    """Exact N-trading-day lookback on price-only closes (same as etf_rs_builder._pct_back)."""
    if len(s) <= bars_back:
        return None
    start = float(s.iloc[-1 - bars_back])
    return round((float(s.iloc[-1]) / start - 1) * 100, 2) if start > 0 else None


def _load_etf_rs() -> dict:
    """ETF RS table written earlier in the nightly run: {ticker: row}. Empty if unavailable."""
    try:
        return {e["ticker"]: e for e in json.loads(ETF_RS_JSON.read_text(encoding="utf-8")).get("etfs", [])}
    except Exception as e:
        logger.warning("etf_rs.json unavailable (%s) - underlying RS columns will be empty.", e)
        return {}


def _rs_1m_pct(hist) -> int | None:
    """Where the latest RS-vs-SPY histogram bar sits in its 25-day range (same as the ETF RS table)."""
    if not hist or len(hist) < 2:
        return None
    lo, hi = min(hist), max(hist)
    return 50 if hi == lo else round((hist[-1] - lo) / (hi - lo) * 100)


INVERSE_NAMES = {c for _, _, cands, _ in CORE_EXPOSURES for c in cands} | set(CRYPTO_INVERSE)
KNOWN_RATIO = {"UVXY": "1.5x", "UVIX": "2x"}   # long-VIX funds, for when TradingView omits leverage_ratio


def _is_inverse(row) -> bool:
    """TradingView's flag when present; otherwise our own lists and the fund's name. (UVXY / UVIX
    are long-volatility, and their names contain 'Short Term' / 'Short' only incidentally.)"""
    name = row.get('name')
    if name in VOLATILITY:
        return False
    if row.get('leveraged_flag') == 'Inverse' or name in INVERSE_NAMES:
        return True
    return bool(re.search(r'\b(Short|Bear|Inverse|UltraShort)\b', str(row.get('description') or ''), re.I))


def _ratio(row) -> str:
    """'2x' / '3x' from TradingView's leverage_ratio, else read from the name ('Bear 3X', 'UltraPro
    Short' = 3x, 'UltraShort' = 2x), else '1x'."""
    ratio = row.get('leverage_ratio')
    if isinstance(ratio, str) and ratio.endswith('x'):
        return ratio
    if row.get('name') in KNOWN_RATIO:
        return KNOWN_RATIO[row['name']]
    desc = str(row.get('description') or '')
    m = re.search(r'(\d(?:\.\d)?)\s*[xX]\b', desc)
    if m:
        return f"{m.group(1)}x"
    if re.search(r'UltraPro', desc, re.I):
        return '3x'
    if re.search(r'UltraShort|Ultra\b', desc, re.I):
        return '2x'
    return '1x'


def _leverage_label(row) -> str:
    return ('-' if _is_inverse(row) else '+') + _ratio(row)


# Funds that name the underlying by company ("T-Rex 2X Inverse NVIDIA Daily Target ETF") instead of by ticker.
# The first word of each company's legal name -> ticker is built from the screener universe the dashboard already
# ships; _NAME_ALIASES covers names that don't appear in the issuer's legal name.
_NAME_ALIASES = {"spacex": "SPCX"}
_NAME_STOPWORDS = {"daily", "target", "etf", "index", "the", "bull", "bear", "short", "inverse", "leveraged"}
_name_index = None


def _company_name_index() -> dict:
    global _name_index
    if _name_index is None:
        _name_index = {}
        try:
            d = json.loads((ROOT / "public" / "screener_stocks.json").read_text(encoding="utf-8"))
            stocks = d["stocks"] if isinstance(d, dict) else d
            for st in sorted(stocks, key=lambda x: -(x.get("market_cap_b") or 0)):   # biggest company wins a shared name
                words = re.findall(r"[A-Za-z0-9]+", st.get("company") or "")
                if words and st.get("ticker"):
                    _name_index.setdefault(words[0].lower(), st["ticker"])
        except Exception as e:  # noqa: BLE001 - optional enrichment; the ticker regex still works without it
            logger.warning("company-name index unavailable (%s) - name-only inverse funds keep a blank exposure.", e)
    return _name_index


def _underlying_hint(desc: str):
    """Best-effort underlying ticker from names like '2x Short NVDA Daily ETF' or '2X Inverse NVIDIA Daily Target ETF'."""
    m = re.search(r'(?:Short|Inverse|Bear)\s+([A-Za-z][A-Za-z0-9]*)', desc or '')
    if not m:
        return None
    word = m.group(1)
    if re.fullmatch(r'[A-Z]{2,5}', word):
        return word
    key = word.lower()
    if key in _NAME_STOPWORDS:
        return None
    return _NAME_ALIASES.get(key) or _company_name_index().get(key)


def _passes_floors(r) -> bool:
    return bool(r['average_volume_60d_calc'] >= MIN_AVG_VOLUME and r['dollar_volume'] >= MIN_DOLLAR_VOLUME)


def _select_core(df: pd.DataFrame) -> list[dict]:
    by = df.set_index('name')
    picks: list[dict] = []
    for group, exposure, cands, underlying in CORE_EXPOSURES:
        avail = [c for c in cands if c in by.index]
        if not avail:
            continue
        passing = [c for c in avail if _passes_floors(by.loc[c])]
        pinned = [c for c in avail if c in PINNED]
        pool = passing or pinned          # nothing liquid enough and nothing pinned -> omit
        if not pool:
            continue
        # pinned names win when they pass; otherwise the most liquid passing candidate
        best = max(pool, key=lambda c: (c in PINNED and c in passing, by.loc[c, 'dollar_volume']))
        picks.append({"ticker": best, "group": group, "exposure": exposure, "underlying": underlying})
    for t in VOLATILITY:
        if t in by.index and (t in PINNED or _passes_floors(by.loc[t])):
            picks.append({"ticker": t, "group": "Volatility", "exposure": "Long VIX futures", "underlying": None})
    return picks


def _download_closes(tickers: list[str]):
    """Yahoo daily bars with retries. In CI this runs right after etf_rs_builder.py has pulled
    ~210 tickers, and Yahoo can answer with nothing at all (yfinance then raises "No objects to
    concatenate"), so wait and try again before giving up."""
    waits = (0, 20, 60) if os.environ.get("CI") else (0, 5, 15)
    for i, wait in enumerate(waits, 1):
        if wait:
            time.sleep(wait)
        try:
            data = yf.download(tickers, period="15mo", interval="1d", auto_adjust=False, progress=False)
            if data is not None and not data.empty and "Close" in data:
                return data
            logger.warning("Yahoo returned no data (attempt %d/%d).", i, len(waits))
        except Exception as e:  # yfinance raises ValueError when every ticker fails
            logger.warning("Yahoo download failed (attempt %d/%d): %s", i, len(waits), e)
    return None


def _perf_table(tickers: list[str]) -> dict:
    """Price-only 1D / 1W / 1M plus the 52W-high gap, per ticker (Yahoo daily closes)."""
    data = _download_closes(tickers)
    if data is None:
        return {}
    closes, highs = data["Close"], data["High"]
    out = {}
    for t in tickers:
        if t not in closes.columns:
            continue
        s = closes[t].dropna()
        if len(s) < 25:
            continue
        h = highs[t].dropna().tail(252).max()
        out[t] = {"perf_1d": _pct_back(s, 1), "perf_1w": _pct_back(s, 5), "perf_1m": _pct_back(s, 20),
                  "off_52w_high": round((float(s.iloc[-1]) / float(h) - 1) * 100, 2) if h else None,
                  "last_bar": s.index[-1].strftime("%Y-%m-%d")}
    return out


def _tv_perf_fallback(df: pd.DataFrame) -> dict:
    """Yahoo is down: use TradingView's own 1D / 1W / 1M and 52W high so the file still refreshes.
    TradingView's windows are not identical to the price-only lookbacks above (the UI footer says
    so via perf_source), and the underlying-ETF columns stay empty."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    out = {}
    for _, r in df.iterrows():
        hi = r.get('price_52_week_high')
        out[r['name']] = {
            "perf_1d": round(float(r['change']), 2) if pd.notna(r['change']) else None,
            "perf_1w": round(float(r['Perf.W']), 2) if pd.notna(r['Perf.W']) else None,
            "perf_1m": round(float(r['Perf.1M']), 2) if pd.notna(r['Perf.1M']) else None,
            "off_52w_high": round((float(r['close']) / float(hi) - 1) * 100, 2) if pd.notna(hi) and hi else None,
            "last_bar": today,
        }
    return out


def _row(r, perf: dict, meta: dict) -> dict:
    t = r['name']
    p = perf.get(t, {})
    close = float(r['close'])
    return {
        **meta,
        "ticker": t,
        "description": r['description'],
        "leverage": _leverage_label(r),
        "price": round(close, 2),
        "perf_1d": p.get("perf_1d"), "perf_1w": p.get("perf_1w"), "perf_1m": p.get("perf_1m"),
        "adr_pct": round(float(r['ATR']) / close * 100, 2) if pd.notna(r['ATR']) and close else None,
        "avg_volume": int(r['average_volume_60d_calc']),
        "dollar_volume": int(r['dollar_volume']),
        "weekly_volatility": round(float(r['Volatility.W']), 2) if pd.notna(r['Volatility.W']) else None,
        "above_ema20": bool(close > r['EMA20']) if pd.notna(r['EMA20']) else None,
        "above_sma50": bool(close > r['SMA50']) if pd.notna(r['SMA50']) else None,
        "hot": bool(pd.notna(r['Volatility.W']) and r['Volatility.W'] > HOT_WEEKLY_VOLATILITY),
        "below_floor": not _passes_floors(r),
        "pinned": t in PINNED,
    }


def main() -> None:
    df = _fetch_universe()
    logger.info("Universe: %d inverse / volatility ETFs from TradingView.", len(df))
    if df.empty:
        # Never hand Yahoo an empty ticker list (that is what produced "No objects to concatenate").
        logger.error("TradingView returned no ETFs - leaving %s untouched.", OUTPUT_JSON)
        return
    by = df.set_index('name')

    core_picks = _select_core(df)
    core_tickers = {p['ticker'] for p in core_picks}

    hot_df = df[(df['name'].isin(core_tickers) == False)
                & df['lev'].isin(HOT_LEVERAGE)
                & df['is_inverse']
                & (df['Volatility.W'] > HOT_WEEKLY_VOLATILITY)
                & (df['average_volume_60d_calc'] >= MIN_AVG_VOLUME)
                & (df['dollar_volume'] >= MIN_DOLLAR_VOLUME)].sort_values('dollar_volume', ascending=False)

    underlyings = sorted({p['underlying'] for p in core_picks if p['underlying']})
    perf = _perf_table(sorted(core_tickers | set(hot_df['name']) | set(underlyings)))
    perf_source = "yahoo"
    if not perf:
        logger.warning("No Yahoo price history - falling back to TradingView performance figures.")
        perf = _tv_perf_fallback(df[df['name'].isin(core_tickers | set(hot_df['name']))])
        perf_source = "tradingview"
    if not perf:
        logger.error("No performance data from any source - leaving %s untouched.", OUTPUT_JSON)
        return

    etf_rs = _load_etf_rs()
    core = []
    for p in core_picks:
        row = _row(by.loc[p['ticker']].rename(p['ticker']).to_dict() | {"name": p['ticker']}, perf,
                   {"group": p['group'], "exposure": p['exposure'], "underlying": p['underlying']})
        u = perf.get(p['underlying'], {}) if p['underlying'] else {}
        e = etf_rs.get(p['underlying'], {}) if p['underlying'] else {}
        row.update({"und_1w": u.get("perf_1w"), "und_1m": u.get("perf_1m"), "und_off_52w": u.get("off_52w_high"),
                    "und_rs_thrust": e.get("rs_thrust_1w"), "und_rs_1m": _rs_1m_pct(e.get("rs_histogram"))})
        core.append(row)

    # Which weak-RS ETF each core vehicle is the instrument for (only ETFs present in etf_rs.json).
    exposure_map = [{"etf": etf, "vehicle": r["ticker"], "exposure": r["exposure"]}
                    for r in core for etf in RELATED_ETFS.get(r["exposure"], []) if etf in etf_rs]

    hot = []
    for _, r in hot_df.iterrows():
        hot.append(_row(r.to_dict(), perf, {"group": "Hot", "exposure": _underlying_hint(r['description']) or "—",
                                             "underlying": None}))

    last_bar = max((v["last_bar"] for v in perf.values()), default=None)
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps({
        "built_at": datetime.now(tz=timezone.utc).isoformat(),
        "as_of": last_bar,
        "perf_source": perf_source,
        "rules": {"min_avg_volume": MIN_AVG_VOLUME, "min_dollar_volume": MIN_DOLLAR_VOLUME,
                  "hot_weekly_volatility": HOT_WEEKLY_VOLATILITY, "hot_leverage": list(HOT_LEVERAGE)},
        "core": core,
        "hot": hot,
        "exposure_map": exposure_map,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote %s: %d core (%d below a floor), %d hot.", OUTPUT_JSON, len(core),
                sum(r["below_floor"] for r in core), len(hot))


if __name__ == "__main__":
    main()
