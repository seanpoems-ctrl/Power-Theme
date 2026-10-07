import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows encoding fix
"""
focus_list_scanner.py — Jeff Sun-style Focus List screener battery
Runs 13 TradingView screener queries concurrently (8 momentum scans bucketed
by lookback window x market-cap size, plus 5 operational/tightness scans),
each filtered down to stocks tight above their SMA10/EMA5 (Minervini-style
"near the highs, not extended" tightness band) and within +/-5% of the 5-day
EMA (Jeff Sun's daily watchlist tightness scan). Stocks passing any scan are
consolidated into one "Scan Result" list, ranked by how many scans they hit.

Prints each bucket as a copyable comma-separated ticker list, writes
public/focus_list.json for the "Focus List" tab in the React app, and
(outside CI) saves the full detail — every column TradingView returned,
one sheet per scan — to Focus_List.xlsx for further review in Excel.

Run: python focus_list_scanner.py
"""

import sys
sys.dont_write_bytecode = True  # Prevent stale .pyc cache issues

import json
import logging
import os
import concurrent.futures
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from tradingview_screener import Query, col

ROOT = Path(__file__).parent
# Tightness filter applied on top of every scan. Two Jeff Sun variants:
#   ema5        - Mar 2024 daily "Watchlist Scan": price within +/-5% of the 5-day EMA
#   compression - Oct 2025 "Compression" screen: price above EMA20, today's high below
#                 the 1-month high, and within +/-3.5% of the open
# Switch with --tightness on the command line or the FOCUS_TIGHTNESS env var.
EMA5_BAND = 0.05
COMPRESSION_OPEN_BAND = 3.5
TIGHTNESS = os.environ.get("FOCUS_TIGHTNESS", "ema5")
TIGHTNESS_LABELS = {
    "ema5":        "within ±5% of EMA5",
    "compression": "above EMA20, below 1M high, ±3.5% from open",
}
TIGHTNESS_COLS = ['EMA5', 'EMA20', 'high', 'High.1M', 'open', 'SMA50']   # SMA50 feeds the Extension column
OUTPUT_JSON = ROOT / "public" / "focus_list.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# 1. Individual standalone operational & tightness scan tabs
# ──────────────────────────────────────────────────────────────
individual_scans = {
    "1_Fundamental_Growth": (
        Query().set_markets('america')
        .select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'EMA5', 'average_volume_10d_calc')
        .where(
            col('type') == 'stock',
            col('exchange').isin(['NASDAQ', 'NYSE', 'AMEX']),
            col('market_cap_basic') > 300_000_000,
            col('average_volume_60d_calc') > 300_000,
            col('float_shares_outstanding') < 100_000_000,
            col('earnings_per_share_diluted_yoy_growth_fq') > 25,
            col('free_cash_flow_yoy_growth_ttm') > 25,
            col('total_revenue_yoy_growth_fq') > 25
        ).order_by('change', ascending=False).limit(300)
    ),
    "3_Post_Earnings_Cont_Base": (
        Query().set_markets('america')
        .select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'EMA5', 'average_volume_10d_calc')
        .where(
            col('type') == 'stock',
            col('exchange').isin(['NASDAQ', 'NYSE', 'AMEX']),
            col('close') > col('SMA20'),
            col('market_cap_basic') > 50_000_000,
            col('average_volume_60d_calc') > 250_000,
            col('relative_volume_10d_calc') >= 2,
            col('float_shares_outstanding') < 50_000_000,
            col('gap') > 5
        ).order_by('change', ascending=False).limit(300)
    ),
    "4_Strongest_Stock_JK": (
        Query().set_markets('america')
        .select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'EMA5', 'average_volume_10d_calc', 'price_52_week_low', 'SMA10', 'SMA50', 'earnings_per_share_diluted_yoy_growth_fq', 'total_revenue_yoy_growth_fq')
        .where(
            col('type') == 'stock',
            col('exchange').isin(['NASDAQ', 'NYSE', 'AMEX']),
            col('market_cap_basic').between(300_000_000, 10_000_000_000),
            col('average_volume_60d_calc') > 500_000,
            col('Volatility.M') > 3,
            col('float_shares_outstanding') < 50_000_000,
            col('earnings_per_share_diluted_yoy_growth_fq') > 25,
            col('total_revenue_yoy_growth_fq') > 25,
            col('close') > col('SMA50')
        ).order_by('change', ascending=False).limit(300)
    ),
    "5_Strongest_Stock_10B_Rev_30_JK": (
        Query().set_markets('america')
        .select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'EMA5', 'average_volume_10d_calc', 'price_52_week_low', 'SMA10', 'SMA50', 'earnings_per_share_diluted_yoy_growth_fq', 'total_revenue_yoy_growth_fq')
        .where(
            col('type') == 'stock',
            col('exchange').isin(['NASDAQ', 'NYSE', 'AMEX']),
            col('market_cap_basic') > 10_000_000_000,
            col('average_volume_60d_calc') > 500_000,
            col('Volatility.M') > 2,
            col('float_shares_outstanding') < 150_000_000,
            col('earnings_per_share_diluted_yoy_growth_fq') > 25,
            col('total_revenue_yoy_growth_fq') > 25,
            col('close') > col('SMA50')
        ).order_by('change', ascending=False).limit(300)
    ),
    "Daily_Tightness_Swing": (
        Query().set_markets('america')
        .select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'EMA5', 'average_volume_10d_calc', 'price_52_week_low', 'SMA10', 'SMA20', 'Volatility.M', 'Perf.W')
        .where(
            col('type') == 'stock',
            col('exchange').isin(['NASDAQ', 'NYSE', 'AMEX']),
            col('market_cap_basic') > 300_000_000,
            col('average_volume_60d_calc') > 300_000,
            col('volume') > 100_000,
            col('float_shares_outstanding') < 50_000_000,
            col('Volatility.M') > 3.5,
            col('Perf.W') < 5
        ).order_by('change', ascending=False).limit(300)
    ),
}

# ──────────────────────────────────────────────────────────────
# 2. All 8 momentum scans — small-cap ($300M-$10B) vs large-cap (>$10B),
#    each across 1-week / 1-month / 3-month / 6-month lookback windows
# ──────────────────────────────────────────────────────────────
EXCHANGES = ['NASDAQ', 'NYSE', 'AMEX']
MOM_COLS = ('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'EMA5',
            'average_volume_10d_calc', 'price_52_week_low', 'SMA10')

# Jeff Sun's 2023 Finviz mover scans (X post 1659786288067928064): 1W >20%, 1M >30%,
# 3M >50%, 6M >100%; avg volume >300K, current volume >100K; weekly volatility >4%
# for the 1W scan and monthly volatility >5% for the others. No float filter.
# (label, TradingView perf column, min perf %, volatility column, min volatility %)
MOMENTUM_RULES = {
    "1W": ("1 Week",   "Perf.W",  20,  "Volatility.W", 4),
    "1M": ("1 Month",  "Perf.1M", 30,  "Volatility.M", 5),
    "3M": ("3 Months", "Perf.3M", 50,  "Volatility.M", 5),
    "6M": ("6 Months", "Perf.6M", 100, "Volatility.M", 5),
}


def _momentum_query(perf_col: str, perf_min: float, vol_col: str, vol_min: float, is_large: bool):
    mcap = col('market_cap_basic') > 10_000_000_000 if is_large else col('market_cap_basic').between(300_000_000, 10_000_000_000)
    return (
        Query().set_markets('america').select(*MOM_COLS, perf_col, vol_col)
        .where(col('type') == 'stock', col('exchange').isin(EXCHANGES), mcap,
               col('average_volume_60d_calc') > 300_000, col('volume') > 100_000,
               col(vol_col) > vol_min, col(perf_col) > perf_min)
        .order_by('change', ascending=False).limit(300)
    )


momentum_scans = {}
for _win, (_tf, _perf, _pmin, _vol, _vmin) in MOMENTUM_RULES.items():
    for _large in (False, True):
        momentum_scans[f"Mom_{_win}_{'Large' if _large else 'Small'}"] = {
            "mcap_group": "> $10B" if _large else "$300M - $10B",
            "timeframe": _tf, "is_large": _large,
            "query": _momentum_query(_perf, _pmin, _vol, _vmin, _large),
        }

# ──────────────────────────────────────────────────────────────
# 3. Jeff Sun's 2025 post-market Finviz screens (X post 1982678925483684325).
#    TradingView's screener has no short-interest or institutional-transaction
#    data, so the ticker universe comes from Finviz using his filters, then each
#    ticker is enriched through TradingView so it gets the same columns and the
#    same tightness filter as every other scan. His multi-industry filter is a
#    Finviz Elite feature (ignored on the free site), so it is left out.
# ──────────────────────────────────────────────────────────────
FINVIZ_SCANS = {
    "Hottest_Stock": {
        "label": "Hottest Stock",
        "filters": "cap_0.15to,sh_avgvol_o2000,sh_curvol_o1000,sh_float_to500x,sh_insttrans_pos,sh_short_high,ta_perf_13w30o,ta_volatility_wo5",
    },
    "Highest_Short_Float": {
        "label": "Highest Short Float",
        "filters": "cap_smallover,sh_avgvol_o1000,sh_float_u100,sh_short_o30",
    },
    "Beaten_Down_Bases": {
        "label": "Bases at Beaten-Down Levels",
        "filters": "cap_smallover,sh_avgvol_o1000,sh_curvol_o1000,sh_insttrans_pos,sh_price_o1,ta_alltime_b70h,ta_highlow50d_a15h,ta_highlow52w_b30h,ta_perf_ytddown,ta_sma200_-20to20-a,ta_volatility_wo4",
    },
}
FINVIZ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
FINVIZ_MAX_PAGES = 15

# Explicit logical display order for the final printout / frontend tab
LOGICAL_ORDER = [
    # Strong Movers <$10B watchlist
    "Mom_1W_Small", "Mom_1M_Small", "Mom_3M_Small", "Mom_6M_Small", "4_Strongest_Stock_JK",
    # Strong Movers >$10B watchlist
    "Mom_1W_Large", "Mom_1M_Large", "Mom_3M_Large", "Mom_6M_Large", "5_Strongest_Stock_10B_Rev_30_JK",
    # Fundamental (CANSLIM), Post Earnings Continuation Base, Daily Tightness
    "1_Fundamental_Growth", "3_Post_Earnings_Cont_Base", "Daily_Tightness_Swing",
    # Jeff Sun's 2025 Finviz screens
    "Hottest_Stock", "Highest_Short_Float", "Beaten_Down_Bases",
]

# Display label + the column holding this scan's defining performance metric
# (momentum scans: the lookback-window % move; operational scans: no single
# defining metric, so fall back to day change).
SCAN_META = {
    "Mom_1W_Small":                   {"label": "1W Momentum — Small Cap",  "group": "momentum",    "perf_col": "Perf.W"},
    "Mom_1M_Small":                   {"label": "1M Momentum — Small Cap",  "group": "momentum",    "perf_col": "Perf.1M"},
    "Mom_3M_Small":                   {"label": "3M Momentum — Small Cap",  "group": "momentum",    "perf_col": "Perf.3M"},
    "Mom_6M_Small":                   {"label": "6M Momentum — Small Cap",  "group": "momentum",    "perf_col": "Perf.6M"},
    "Mom_1W_Large":                   {"label": "1W Momentum — Large Cap",  "group": "momentum",    "perf_col": "Perf.W"},
    "Mom_1M_Large":                   {"label": "1M Momentum — Large Cap",  "group": "momentum",    "perf_col": "Perf.1M"},
    "Mom_3M_Large":                   {"label": "3M Momentum — Large Cap",  "group": "momentum",    "perf_col": "Perf.3M"},
    "Mom_6M_Large":                   {"label": "6M Momentum — Large Cap",  "group": "momentum",    "perf_col": "Perf.6M"},
    "1_Fundamental_Growth":           {"label": "Fundamental Growth",              "group": "operational", "perf_col": None},
    "3_Post_Earnings_Cont_Base":      {"label": "Post-Earnings Continuation Base", "group": "operational", "perf_col": None},
    "4_Strongest_Stock_JK":           {"label": "Strongest Stock ($300M–$10B)",    "group": "operational", "perf_col": None},
    "5_Strongest_Stock_10B_Rev_30_JK":{"label": "Strongest Stock (>$10B)",         "group": "operational", "perf_col": None},
    "Daily_Tightness_Swing":          {"label": "Daily Tightness Swing",           "group": "operational", "perf_col": None},
    "Hottest_Stock":                  {"label": "Hottest Stock",                   "group": "operational", "perf_col": None},
    "Highest_Short_Float":            {"label": "Highest Short Float",             "group": "operational", "perf_col": None},
    "Beaten_Down_Bases":              {"label": "Bases at Beaten-Down Levels",     "group": "operational", "perf_col": None},
}


def within_tightness(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the selected tightness variant (see TIGHTNESS)."""
    if TIGHTNESS == 'compression':
        chg_open = (df['close'] / df['open'] - 1).abs() * 100
        return df[(df['close'] > df['EMA20']) & (df['high'] < df['High.1M']) & (chg_open <= COMPRESSION_OPEN_BAND)].copy()
    ema = df['EMA5']
    return df[(ema > 0) & ((df['close'] / ema - 1).abs() <= EMA5_BAND)].copy()


def _with_tightness_cols(query):
    """Make sure every column the tightness filters need is selected."""
    query.query['columns'] = list(dict.fromkeys([*query.query['columns'], *TIGHTNESS_COLS]))
    return query


def run_individual(name: str, query) -> tuple[str, pd.DataFrame]:
    try:
        _, df = _with_tightness_cols(query).get_scanner_data()
        if not df.empty:
            # Post-filter tightness bands TradingView's query language can't express directly
            # (ratios between two returned columns rather than a column vs. a constant).
            if name == "4_Strongest_Stock_JK":
                df = df[(df['close'] >= df['price_52_week_low'] * 1.70) & (df['SMA10'] <= df['close']) & (df['SMA10'] >= df['close'] * 0.90)].copy()
            elif name == "5_Strongest_Stock_10B_Rev_30_JK":
                df = df[(df['close'] >= df['price_52_week_low'] * 1.70) & (df['SMA10'] <= df['close']) & (df['SMA10'] >= df['close'] * 0.97)].copy()
            elif name == "Daily_Tightness_Swing":
                df = df[
                    (df['close'] >= df['price_52_week_low'] * 1.50) &
                    (df['EMA5'] <= df['close']) &
                    (df['EMA5'] >= df['close'] * 0.97) &
                    (df['SMA10'] > df['SMA20'])
                ].copy()
            df = within_tightness(df)
            df.insert(0, 'Source_Scan', name)
        return name, df
    except Exception as e:
        logger.error("Error in %s: %s", name, e)
        return name, pd.DataFrame()


def run_momentum(key: str, info: dict) -> tuple[str, pd.DataFrame]:
    try:
        _, df = _with_tightness_cols(info['query']).get_scanner_data()
        if not df.empty:
            # Small caps get a looser tightness band (0.80x SMA10) than large caps (0.90x)
            # since small caps are naturally more volatile day to day.
            low_mult = 0.80 if not info['is_large'] else 0.90
            df = df[(df['close'] >= df['price_52_week_low'] * 1.50) & (df['SMA10'] <= df['close']) & (df['SMA10'] >= df['close'] * low_mult)].copy()
            df = within_tightness(df)
            df.insert(0, 'Source_Scan', key)
            df.insert(1, 'Market_Cap_Group', info['mcap_group'])
            df.insert(2, 'Timeframe', info['timeframe'])
            return key, df
    except Exception as e:
        logger.error("Error in momentum %s: %s", key, e)
    return key, pd.DataFrame()


def fetch_finviz_tickers(filters: str) -> list[str]:
    """Tickers matching a Finviz screener filter string (free site, 20 rows/page)."""
    import re
    import time
    import requests
    from bs4 import BeautifulSoup
    tickers: list[str] = []
    for page in range(FINVIZ_MAX_PAGES):
        url = f"https://finviz.com/screener.ashx?v=111&ft=4&f={filters}&r={page * 20 + 1}"
        r = requests.get(url, headers=FINVIZ_HEADERS, timeout=20)
        r.raise_for_status()
        found = []
        for t in BeautifulSoup(r.text, "html.parser").find_all("table"):
            rows = t.find_all("tr")
            if len(rows) < 2:
                continue
            header = [c.get_text(strip=True) for c in rows[0].find_all(["td", "th"])]
            if "No." not in header or "Ticker" not in header:
                continue
            for row in rows[1:]:
                tds = row.find_all("td")
                link = tds[1].find("a", href=True) if len(tds) > 1 else None
                m = re.search(r"[?&]t=([A-Za-z0-9.\-]+)", link["href"]) if link else None
                if m:
                    found.append(m.group(1).upper())
        found = [t for t in dict.fromkeys(found) if t not in tickers]  # Finviz repeats the table
        tickers.extend(found)
        if len(found) < 20:
            break
        time.sleep(1.0 if os.environ.get("CI") else 1.5)
    return tickers


def run_finviz(key: str, info: dict) -> tuple[str, pd.DataFrame]:
    try:
        finviz_tickers = fetch_finviz_tickers(info['filters'])
        logger.info("%s: Finviz returned %d tickers.", key, len(finviz_tickers))
        if not finviz_tickers:
            return key, pd.DataFrame()
        tv_names = [t.replace('-', '.') for t in finviz_tickers]  # BRK-B -> BRK.B
        query = (
            Query().set_markets('america')
            .select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'EMA5',
                    'average_volume_10d_calc', 'price_52_week_low', 'SMA10')
            .where(col('type') == 'stock', col('exchange').isin(EXCHANGES), col('name').isin(tv_names))
            .order_by('change', ascending=False).limit(500)
        )
        _, df = _with_tightness_cols(query).get_scanner_data()
        if not df.empty:
            df = within_tightness(df)
            df.insert(0, 'Source_Scan', key)
        return key, df
    except Exception as e:
        logger.error("Error in Finviz scan %s: %s", key, e)
        return key, pd.DataFrame()


def build_scan_result(master_all_scans: pd.DataFrame) -> pd.DataFrame:
    """Consolidate every scan's (already EMA5-filtered) matches into one
    de-duplicated "Scan Result" list — the equivalent of Jeff Sun's rebuilt
    weekly Scan Result watchlist. Ranked by number of scans a ticker hit
    (confluence), then ADR x $Vol proxy (ATR x avg 10d volume) as tie-break."""
    df = master_all_scans.dropna(subset=['name']).copy()
    scans_by_ticker = df.groupby('name')['Source_Scan'].apply(
        lambda x: [k for k in LOGICAL_ORDER if k in set(x)]
    )
    df['_adr_dvol'] = df['ATR'] * df['average_volume_10d_calc']  # == ADR% x avg $ volume, up to a constant
    df = df.sort_values('_adr_dvol', ascending=False).drop_duplicates('name', keep='first')
    df['Scans'] = df['name'].map(scans_by_ticker)
    df['Hits'] = df['Scans'].apply(len)
    df = df.sort_values(['Hits', '_adr_dvol'], ascending=[False, False]).drop(columns='_adr_dvol')
    return df.reset_index(drop=True)


def main() -> None:
    global TIGHTNESS
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--tightness', choices=list(TIGHTNESS_LABELS), default=TIGHTNESS)
    TIGHTNESS = ap.parse_args().tightness
    logger.info("Executing all %d scans concurrently (tightness: %s)…", len(individual_scans) + len(momentum_scans) + len(FINVIZ_SCANS), TIGHTNESS)
    results_dict: dict[str, pd.DataFrame] = {}
    momentum_dfs: list[pd.DataFrame] = []
    all_collected_dfs: list[pd.DataFrame] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        ind_futures = {executor.submit(run_individual, name, q): name for name, q in individual_scans.items()}
        mom_futures = {executor.submit(run_momentum, key, info): key for key, info in momentum_scans.items()}
        fv_futures  = {executor.submit(run_finviz, key, info): key for key, info in FINVIZ_SCANS.items()}

        for future in concurrent.futures.as_completed(ind_futures):
            name, df = future.result()
            results_dict[name] = df
            if not df.empty:
                all_collected_dfs.append(df)
            logger.info("Finished %s: %d matches.", name, len(df))

        for future in concurrent.futures.as_completed(fv_futures):
            name, df = future.result()
            results_dict[name] = df
            if not df.empty:
                all_collected_dfs.append(df)
            logger.info("Finished %s: %d matches.", name, len(df))

        for future in concurrent.futures.as_completed(mom_futures):
            key, df = future.result()
            if not df.empty:
                momentum_dfs.append(df)
                all_collected_dfs.append(df)
            logger.info("Finished %s: %d matches.", key, len(df))

    if momentum_dfs:
        master_momentum = pd.concat(momentum_dfs, ignore_index=True)
        master_momentum.sort_values(by=['Market_Cap_Group', 'Timeframe', 'change'], ascending=[True, True, False], inplace=True)
        results_dict['Momentum'] = master_momentum

    if all_collected_dfs:
        master_all_scans = pd.concat(all_collected_dfs, ignore_index=True)
        results_dict['All_Scans'] = master_all_scans

        print("\n" + "=" * 60)
        print(" COPYABLE COMMA-SEPARATED TICKER LISTS (LOGICAL ORDER)")
        print("=" * 60)
        if 'Source_Scan' in master_all_scans.columns and 'name' in master_all_scans.columns:
            for scan_name in LOGICAL_ORDER:
                group_df = master_all_scans[master_all_scans['Source_Scan'] == scan_name]
                if not group_df.empty:
                    scan_tickers = sorted(group_df['name'].dropna().unique())
                    print(f"\n[{scan_name}] ({len(scan_tickers)} tickers):")
                    print(", ".join(scan_tickers))

        if 'name' in master_all_scans.columns:
            unique_tickers = sorted(master_all_scans['name'].dropna().unique())
            print("\n" + "=" * 60)
            print(f" MASTER COMMA-SEPARATED LIST (ALL SCANS - {len(unique_tickers)} tickers):")
            print("=" * 60)
            print(", ".join(unique_tickers))
            print("=" * 60 + "\n")

        scan_result = build_scan_result(master_all_scans)
        results_dict['Scan_Result'] = scan_result.assign(Scans=scan_result['Scans'].apply(", ".join))
        logger.info("Scan Result: %d unique tickers across all scans.", len(scan_result))
        write_json(master_all_scans, scan_result)
    else:
        logger.warning("No scans returned any matches — leaving %s untouched.", OUTPUT_JSON)

    if not os.environ.get("CI"):
        excel_path = "Focus_List.xlsx"
        try:
            with pd.ExcelWriter(excel_path) as writer:
                for sheet_name, df in results_dict.items():
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
            logger.info("Done! Saved to: %s", excel_path)
        except PermissionError:
            alt_path = "Focus_List_NEW.xlsx"
            with pd.ExcelWriter(alt_path) as writer:
                for sheet_name, df in results_dict.items():
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
            logger.warning("Excel file was locked. Saved to: %s", alt_path)


def _et_day(ts: datetime) -> str:
    """Calendar day of a timestamp in US Eastern (falls back to UTC if tzdata is missing)."""
    try:
        from zoneinfo import ZoneInfo
        return ts.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return ts.strftime("%Y-%m-%d")


def _previous_scan_baseline(today: str) -> tuple[set[str], str | None]:
    """Scan Result tickers from the last scan on an earlier ET day, so today's list can
    flag what is new. A re-run on the same day keeps the baseline already stored in the file."""
    try:
        old = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
        old_day = old.get("scan_day") or _et_day(datetime.fromisoformat(old["scan_time"]))
        if old_day != today:
            return {s["ticker"] for s in (old.get("scan_result") or {}).get("stocks", [])}, old_day
        return set(old.get("prev_tickers", [])), old.get("prev_scan_day")
    except Exception:
        return set(), None


def write_json(master_all_scans: pd.DataFrame, scan_result: pd.DataFrame | None = None) -> None:
    """Build public/focus_list.json for the frontend's Focus List tab."""
    def stock_rows(df: pd.DataFrame, perf_col: str | None) -> list[dict]:
        rows = []
        for _, r in df.iterrows():
            perf  = r.get(perf_col) if perf_col else None
            close = r.get("close")
            atr   = r.get("ATR")
            avg_vol10 = r.get("average_volume_10d_calc")

            # ADR% = ATR / close × 100, and ADR×$Vol = ADR% × avg dollar volume —
            # same formula screener_builder.py uses for the main Stock Screener table.
            adr_pct = None
            if pd.notna(atr) and pd.notna(close) and float(close):
                adr_pct = round(float(atr) / float(close) * 100, 2)
            adr_dvol = None
            if adr_pct is not None and pd.notna(avg_vol10) and pd.notna(close):
                adr_dvol = round(adr_pct * float(avg_vol10) * float(close))

            ema5 = r.get("EMA5")
            ema5_pct = None
            if pd.notna(ema5) and pd.notna(close) and float(ema5):
                ema5_pct = round((float(close) / float(ema5) - 1) * 100, 2)

            # Extension (Jeff Sun): ATR% multiple from the 50-MA = (% above 50-MA) / ATR% — same as screener_builder.py.
            sma50 = r.get("SMA50")
            extension = None
            if pd.notna(sma50) and pd.notna(atr) and pd.notna(close) and float(sma50) > 0 and float(atr) > 0:
                extension = round(((float(close) / float(sma50)) - 1) / (float(atr) / float(close)), 2)

            # Average dollar volume = 10-day avg shares × price (the "Avg $ Vol" column; ADR×$Vol is this × ADR%).
            avg_dollar_volume = None
            if pd.notna(avg_vol10) and pd.notna(close):
                avg_dollar_volume = round(float(avg_vol10) * float(close))

            row = {
                "ticker":     r.get("name"),
                "industry":   r.get("industry") if pd.notna(r.get("industry")) else None,
                "close":      round(float(close), 2) if pd.notna(close) else None,
                "change":     round(float(r["change"]), 2) if pd.notna(r.get("change")) else None,
                "volume":     int(r["volume"]) if pd.notna(r.get("volume")) else None,
                "market_cap": int(r["market_cap_basic"]) if pd.notna(r.get("market_cap_basic")) else None,
                "perf":       round(float(perf), 2) if perf is not None and pd.notna(perf) else None,
                "adr_pct":    adr_pct,
                "adr_dvol":   adr_dvol,
                "avg_dollar_volume": avg_dollar_volume,
                "ema5_pct":   ema5_pct,
                "extension":  extension,
            }
            if "Scans" in r.index:
                row["scans"] = list(r["Scans"])
                row["hits"]  = len(row["scans"])
            rows.append(row)
        if rows and "hits" in rows[0]:
            return rows  # Scan Result arrives pre-ranked (hits, then ADR x $Vol)
        rows.sort(key=lambda x: (x["perf"] if x["perf"] is not None else x["change"] or 0), reverse=True)
        return rows

    scans = []
    for key in LOGICAL_ORDER:
        meta = SCAN_META[key]
        group_df = master_all_scans[master_all_scans["Source_Scan"] == key]
        # Empty scans are kept (stocks: []) so the frontend still shows the section.
        entry = {
            "key":   key,
            "label": meta["label"],
            "group": meta["group"],
        }
        if key in momentum_scans:
            entry["mcap_group"] = momentum_scans[key]["mcap_group"]
            entry["timeframe"]  = momentum_scans[key]["timeframe"]
        entry["stocks"] = stock_rows(group_df, meta["perf_col"])
        scans.append(entry)

    scan_result_entry = None
    if scan_result is not None and not scan_result.empty:
        scan_result_entry = {
            "key":    "Scan_Result",
            "label":  "Scan Result",
            "group":  "result",
            "stocks": stock_rows(scan_result, None),
        }

    now = datetime.now(tz=timezone.utc)
    today = _et_day(now)
    prev_tickers, prev_day = _previous_scan_baseline(today)
    if scan_result_entry:
        # None (not False) when there is no earlier scan to compare against.
        for row in scan_result_entry["stocks"]:
            row["is_new"] = (row["ticker"] not in prev_tickers) if prev_tickers else None

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(
        json.dumps({
            "scan_time": now.isoformat(),
            "scan_day": today,
            "prev_scan_day": prev_day,
            "prev_tickers": sorted(prev_tickers),
            "tightness": {"mode": TIGHTNESS, "label": TIGHTNESS_LABELS[TIGHTNESS]},
            "scan_result": scan_result_entry,
            "scans": scans,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Wrote %s (%d scans)", OUTPUT_JSON, len(scans))


if __name__ == "__main__":
    main()
