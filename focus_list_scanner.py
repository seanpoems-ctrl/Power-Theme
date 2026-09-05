import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows encoding fix
"""
focus_list_scanner.py — Jeff Sun-style Focus List screener battery
Runs 13 TradingView screener queries concurrently (8 momentum scans bucketed
by lookback window x market-cap size, plus 5 operational/tightness scans),
each filtered down to stocks tight above their SMA10/EMA5 (Minervini-style
"near the highs, not extended" tightness band).

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
OUTPUT_JSON = ROOT / "public" / "focus_list.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# 1. Individual standalone operational & tightness scan tabs
# ──────────────────────────────────────────────────────────────
individual_scans = {
    "1_Fundamental_Growth": (
        Query().set_markets('america')
        .select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'average_volume_10d_calc')
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
        .select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'average_volume_10d_calc')
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
        .select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'average_volume_10d_calc', 'price_52_week_low', 'SMA10', 'SMA50', 'earnings_per_share_diluted_yoy_growth_fq', 'total_revenue_yoy_growth_fq')
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
        .select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'average_volume_10d_calc', 'price_52_week_low', 'SMA10', 'SMA50', 'earnings_per_share_diluted_yoy_growth_fq', 'total_revenue_yoy_growth_fq')
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
        .select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'average_volume_10d_calc', 'price_52_week_low', 'EMA5', 'SMA10', 'SMA20', 'Volatility.M', 'Perf.W')
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
momentum_scans = {
    "Mom_1W_Small": {
        "mcap_group": "$300M - $10B", "timeframe": "1 Week", "is_large": False,
        "query": Query().set_markets('america').select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'average_volume_10d_calc', 'price_52_week_low', 'SMA10', 'Perf.W', 'Volatility.M')
        .where(col('type') == 'stock', col('exchange').isin(['NASDAQ', 'NYSE', 'AMEX']), col('market_cap_basic').between(300_000_000, 10_000_000_000), col('average_volume_60d_calc') > 300_000, col('volume') > 100_000, col('float_shares_outstanding') < 50_000_000, col('Volatility.M') > 3, col('Perf.W') > 20).order_by('change', ascending=False).limit(300)
    },
    "Mom_1M_Small": {
        "mcap_group": "$300M - $10B", "timeframe": "1 Month", "is_large": False,
        "query": Query().set_markets('america').select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'average_volume_10d_calc', 'price_52_week_low', 'SMA10', 'Perf.1M', 'Volatility.M')
        .where(col('type') == 'stock', col('exchange').isin(['NASDAQ', 'NYSE', 'AMEX']), col('market_cap_basic').between(300_000_000, 10_000_000_000), col('average_volume_60d_calc') > 300_000, col('volume') > 100_000, col('float_shares_outstanding') < 50_000_000, col('Volatility.M') > 3, col('Perf.1M') > 30).order_by('change', ascending=False).limit(300)
    },
    "Mom_3M_Small": {
        "mcap_group": "$300M - $10B", "timeframe": "3 Months", "is_large": False,
        "query": Query().set_markets('america').select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'average_volume_10d_calc', 'price_52_week_low', 'SMA10', 'Perf.3M', 'Volatility.M')
        .where(col('type') == 'stock', col('exchange').isin(['NASDAQ', 'NYSE', 'AMEX']), col('market_cap_basic').between(300_000_000, 10_000_000_000), col('average_volume_60d_calc') > 300_000, col('volume') > 100_000, col('float_shares_outstanding') < 50_000_000, col('Volatility.M') > 3, col('Perf.3M') > 70).order_by('change', ascending=False).limit(300)
    },
    "Mom_6M_Small": {
        "mcap_group": "$300M - $10B", "timeframe": "6 Months", "is_large": False,
        "query": Query().set_markets('america').select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'average_volume_10d_calc', 'price_52_week_low', 'SMA10', 'Perf.6M', 'Volatility.M')
        .where(col('type') == 'stock', col('exchange').isin(['NASDAQ', 'NYSE', 'AMEX']), col('market_cap_basic').between(300_000_000, 10_000_000_000), col('average_volume_60d_calc') > 300_000, col('volume') > 100_000, col('float_shares_outstanding') < 50_000_000, col('Volatility.M') > 3, col('Perf.6M') > 100).order_by('change', ascending=False).limit(300)
    },
    "Mom_1W_Large": {
        "mcap_group": "> $10B", "timeframe": "1 Week", "is_large": True,
        "query": Query().set_markets('america').select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'average_volume_10d_calc', 'price_52_week_low', 'SMA10', 'Perf.W')
        .where(col('type') == 'stock', col('exchange').isin(['NASDAQ', 'NYSE', 'AMEX']), col('market_cap_basic') > 10_000_000_000, col('average_volume_60d_calc') > 300_000, col('volume') > 100_000, col('float_shares_outstanding') < 150_000_000, col('Perf.W') > 20).order_by('change', ascending=False).limit(300)
    },
    "Mom_1M_Large": {
        "mcap_group": "> $10B", "timeframe": "1 Month", "is_large": True,
        "query": Query().set_markets('america').select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'average_volume_10d_calc', 'price_52_week_low', 'SMA10', 'Perf.1M')
        .where(col('type') == 'stock', col('exchange').isin(['NASDAQ', 'NYSE', 'AMEX']), col('market_cap_basic') > 10_000_000_000, col('average_volume_60d_calc') > 300_000, col('volume') > 100_000, col('float_shares_outstanding') < 150_000_000, col('Perf.1M') > 30).order_by('change', ascending=False).limit(300)
    },
    "Mom_3M_Large": {
        "mcap_group": "> $10B", "timeframe": "3 Months", "is_large": True,
        "query": Query().set_markets('america').select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'average_volume_10d_calc', 'price_52_week_low', 'SMA10', 'Perf.3M')
        .where(col('type') == 'stock', col('exchange').isin(['NASDAQ', 'NYSE', 'AMEX']), col('market_cap_basic') > 10_000_000_000, col('average_volume_60d_calc') > 300_000, col('volume') > 100_000, col('float_shares_outstanding') < 150_000_000, col('Perf.3M') > 70).order_by('change', ascending=False).limit(300)
    },
    "Mom_6M_Large": {
        "mcap_group": "> $10B", "timeframe": "6 Months", "is_large": True,
        "query": Query().set_markets('america').select('name', 'close', 'change', 'volume', 'market_cap_basic', 'industry', 'ATR', 'average_volume_10d_calc', 'price_52_week_low', 'SMA10', 'Perf.6M')
        .where(col('type') == 'stock', col('exchange').isin(['NASDAQ', 'NYSE', 'AMEX']), col('market_cap_basic') > 10_000_000_000, col('average_volume_60d_calc') > 300_000, col('volume') > 100_000, col('float_shares_outstanding') < 150_000_000, col('Perf.6M') > 100).order_by('change', ascending=False).limit(300)
    },
}

# Explicit logical display order for the final printout / frontend tab
LOGICAL_ORDER = [
    "Mom_1W_Small", "Mom_1M_Small", "Mom_3M_Small", "Mom_6M_Small",
    "Mom_1W_Large", "Mom_1M_Large", "Mom_3M_Large", "Mom_6M_Large",
    "1_Fundamental_Growth", "3_Post_Earnings_Cont_Base",
    "4_Strongest_Stock_JK", "5_Strongest_Stock_10B_Rev_30_JK",
    "Daily_Tightness_Swing",
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
}


def run_individual(name: str, query) -> tuple[str, pd.DataFrame]:
    try:
        _, df = query.get_scanner_data()
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
            df.insert(0, 'Source_Scan', name)
        return name, df
    except Exception as e:
        logger.error("Error in %s: %s", name, e)
        return name, pd.DataFrame()


def run_momentum(key: str, info: dict) -> tuple[str, pd.DataFrame]:
    try:
        _, df = info['query'].get_scanner_data()
        if not df.empty:
            # Small caps get a looser tightness band (0.80x SMA10) than large caps (0.90x)
            # since small caps are naturally more volatile day to day.
            low_mult = 0.80 if not info['is_large'] else 0.90
            df = df[(df['close'] >= df['price_52_week_low'] * 1.50) & (df['SMA10'] <= df['close']) & (df['SMA10'] >= df['close'] * low_mult)].copy()
            df.insert(0, 'Source_Scan', key)
            df.insert(1, 'Market_Cap_Group', info['mcap_group'])
            df.insert(2, 'Timeframe', info['timeframe'])
            return key, df
    except Exception as e:
        logger.error("Error in momentum %s: %s", key, e)
    return key, pd.DataFrame()


def main() -> None:
    logger.info("Executing all %d scans concurrently…", len(individual_scans) + len(momentum_scans))
    results_dict: dict[str, pd.DataFrame] = {}
    momentum_dfs: list[pd.DataFrame] = []
    all_collected_dfs: list[pd.DataFrame] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        ind_futures = {executor.submit(run_individual, name, q): name for name, q in individual_scans.items()}
        mom_futures = {executor.submit(run_momentum, key, info): key for key, info in momentum_scans.items()}

        for future in concurrent.futures.as_completed(ind_futures):
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

        write_json(master_all_scans)
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


def write_json(master_all_scans: pd.DataFrame) -> None:
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

            rows.append({
                "ticker":     r.get("name"),
                "industry":   r.get("industry") if pd.notna(r.get("industry")) else None,
                "close":      round(float(close), 2) if pd.notna(close) else None,
                "change":     round(float(r["change"]), 2) if pd.notna(r.get("change")) else None,
                "volume":     int(r["volume"]) if pd.notna(r.get("volume")) else None,
                "market_cap": int(r["market_cap_basic"]) if pd.notna(r.get("market_cap_basic")) else None,
                "perf":       round(float(perf), 2) if perf is not None and pd.notna(perf) else None,
                "adr_pct":    adr_pct,
                "adr_dvol":   adr_dvol,
            })
        rows.sort(key=lambda x: (x["perf"] if x["perf"] is not None else x["change"] or 0), reverse=True)
        return rows

    scans = []
    for key in LOGICAL_ORDER:
        meta = SCAN_META[key]
        group_df = master_all_scans[master_all_scans["Source_Scan"] == key]
        if group_df.empty:
            continue
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

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(
        json.dumps({
            "scan_time": datetime.now(tz=timezone.utc).isoformat(),
            "scans": scans,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Wrote %s (%d scans)", OUTPUT_JSON, len(scans))


if __name__ == "__main__":
    main()
