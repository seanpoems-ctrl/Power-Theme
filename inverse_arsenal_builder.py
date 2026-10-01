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
import re
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

TV_COLS = ["name", "description", "close", "change", "volume", "average_volume_60d_calc",
           "ATR", "Volatility.W", "EMA20", "SMA50", "leverage_ratio", "leveraged_flag"]


def _fetch_universe() -> pd.DataFrame:
    """Inverse ETFs (TradingView flag) with a sane liquidity pre-filter, plus the VIX and crypto funds."""
    base = [col('type') == 'fund', col('typespecs').has('etf')]
    _, inv = (Query().set_markets('america').select(*TV_COLS)
              .where(*base, col('leveraged_flag') == 'Inverse', col('average_volume_60d_calc') > 300_000)
              .order_by('average_volume_60d_calc', ascending=False).limit(300)).get_scanner_data()
    _, vol = (Query().set_markets('america').select(*TV_COLS)
              .where(*base, col('name').isin(VOLATILITY + CRYPTO_INVERSE)).limit(30)).get_scanner_data()
    df = pd.concat([inv, vol], ignore_index=True).drop_duplicates('name')
    df['dollar_volume'] = df['average_volume_60d_calc'] * df['close']
    return df


def _pct_back(s: pd.Series, bars_back: int):
    """Exact N-trading-day lookback on price-only closes (same as etf_rs_builder._pct_back)."""
    if len(s) <= bars_back:
        return None
    start = float(s.iloc[-1 - bars_back])
    return round((float(s.iloc[-1]) / start - 1) * 100, 2) if start > 0 else None


def _leverage_label(row) -> str:
    ratio = row.get('leverage_ratio')
    ratio = ratio if isinstance(ratio, str) and ratio.endswith('x') else '1x'
    inverse = row.get('leveraged_flag') == 'Inverse' or row.get('name') in CRYPTO_INVERSE
    return ('-' if inverse else '+') + ratio


def _underlying_hint(desc: str):
    """Best-effort underlying ticker from names like '2x Short NVDA Daily ETF'."""
    m = re.search(r'(?:Short|Inverse|Bear)\s+([A-Z]{2,5})\b', desc or '')
    return m.group(1) if m else None


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


def _perf_table(tickers: list[str]) -> dict:
    """Price-only 1D / 1W / 1M plus the 52W-high gap, per ticker (Yahoo daily closes)."""
    data = yf.download(tickers, period="15mo", interval="1d", auto_adjust=False, progress=False)
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
    by = df.set_index('name')

    core_picks = _select_core(df)
    core_tickers = {p['ticker'] for p in core_picks}

    hot_df = df[(df['name'].isin(core_tickers) == False)
                & df['leverage_ratio'].isin(HOT_LEVERAGE)
                & (df['leveraged_flag'] == 'Inverse')
                & (df['Volatility.W'] > HOT_WEEKLY_VOLATILITY)
                & (df['average_volume_60d_calc'] >= MIN_AVG_VOLUME)
                & (df['dollar_volume'] >= MIN_DOLLAR_VOLUME)].sort_values('dollar_volume', ascending=False)

    underlyings = sorted({p['underlying'] for p in core_picks if p['underlying']})
    perf = _perf_table(sorted(core_tickers | set(hot_df['name']) | set(underlyings)))
    if not perf:
        logger.error("No price history came back — leaving %s untouched.", OUTPUT_JSON)
        return

    core = []
    for p in core_picks:
        row = _row(by.loc[p['ticker']].rename(p['ticker']).to_dict() | {"name": p['ticker']}, perf,
                   {"group": p['group'], "exposure": p['exposure'], "underlying": p['underlying']})
        u = perf.get(p['underlying'], {}) if p['underlying'] else {}
        row.update({"und_1w": u.get("perf_1w"), "und_1m": u.get("perf_1m"), "und_off_52w": u.get("off_52w_high")})
        core.append(row)

    hot = []
    for _, r in hot_df.iterrows():
        hot.append(_row(r.to_dict(), perf, {"group": "Hot", "exposure": _underlying_hint(r['description']) or "—",
                                             "underlying": None}))

    last_bar = max((v["last_bar"] for v in perf.values()), default=None)
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps({
        "built_at": datetime.now(tz=timezone.utc).isoformat(),
        "as_of": last_bar,
        "rules": {"min_avg_volume": MIN_AVG_VOLUME, "min_dollar_volume": MIN_DOLLAR_VOLUME,
                  "hot_weekly_volatility": HOT_WEEKLY_VOLATILITY, "hot_leverage": list(HOT_LEVERAGE)},
        "core": core,
        "hot": hot,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote %s: %d core (%d below a floor), %d hot.", OUTPUT_JSON, len(core),
                sum(r["below_floor"] for r in core), len(hot))


if __name__ == "__main__":
    main()
