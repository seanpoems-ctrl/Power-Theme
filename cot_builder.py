import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows encoding fix
"""
cot_builder.py — CFTC Commitment of Traders (COT) positioning for the COT tab.

Builds public/cot_data.json from the CFTC Public Reporting Environment
(Socrata dataset 6dca-aqww = "Legacy - Futures Only"). The Legacy report splits
every market the same way, which is what makes cross-market comparison possible:

  Large Specs  = non-commercial reportable traders (hedge funds / CTAs)
  Commercials  = hedgers (producers, dealers, the "smart money" in commodities)
  Small Specs  = non-reportable (retail)

Net = long - short per group. Each market keeps ~5 years of weekly history so the
frontend can compute 1Y / 3Y ranges and COT-index percentiles itself.

The CFTC publishes Fridays 15:30 ET with Tuesday's positions, so the file only
changes weekly; running nightly is harmless (the file is rewritten only when the
data changes, so no commit noise).

Row layout (oldest -> newest), kept as arrays to keep the file small:
  [date, open_interest, ls_long, ls_short, c_long, c_short, ss_long, ss_short]

Run: python cot_builder.py
"""

import sys
sys.dont_write_bytecode = True

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent
OUTPUT_JSON = ROOT / "public" / "cot_data.json"

API_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
HISTORY_YEARS = 5            # 3Y COT index + 51-week chart + a year of slack
MIN_MARKETS_FRACTION = 0.8   # refuse to publish if the pull is badly incomplete
MAX_STALE_DAYS = 21          # report_date older than this = the CFTC feed is stuck

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# (cftc contract code, symbol, display name, group, exchange)
# Groups render in this order in the market picker. Scope: indices, crypto, metals, energy, bonds
# (ags / softs / livestock were dropped on request — re-add rows here to bring them back).
MARKETS = [
    # ── Indices ──
    ("13874A", "ES",  "S&P 500 E-Mini",       "Indices", "CME"),
    ("209742", "NQ",  "Nasdaq 100 E-Mini",    "Indices", "CME"),
    ("124603", "YM",  "Dow Futures E-Mini",   "Indices", "CBOT"),
    ("239742", "RTY", "Russell 2000 E-Mini",  "Indices", "CME"),
    ("33874A", "EMD", "S&P Midcap E-Mini",    "Indices", "CME"),
    ("1170E1", "VX",  "VIX Futures",          "Indices", "CFE"),
    ("13874U", "MES", "S&P 500 Micro",        "Indices", "CME"),
    ("209747", "MNQ", "Nasdaq 100 Micro",     "Indices", "CME"),
    # ── Crypto ──
    ("133741", "BTC", "Bitcoin",              "Crypto", "CME"),
    ("133742", "MBT", "Micro Bitcoin",        "Crypto", "CME"),
    ("146021", "ETH", "Ether",                "Crypto", "CME"),
    ("177741", "SOL", "Solana",               "Crypto", "CME"),
    ("176740", "XRP", "XRP",                  "Crypto", "CME"),
    # ── Metals ──
    ("088691", "GC",  "Gold",                 "Metals", "COMEX"),
    ("084691", "SI",  "Silver",               "Metals", "COMEX"),
    ("085692", "HG",  "High Grade Copper",    "Metals", "COMEX"),
    ("076651", "PL",  "Platinum",             "Metals", "NYMEX"),
    ("075651", "PA",  "Palladium",            "Metals", "NYMEX"),
    # ── Energy ──
    ("067651", "CL",  "Crude Oil WTI",        "Energy", "NYMEX"),
    ("023651", "NG",  "Natural Gas",          "Energy", "NYMEX"),
    ("06765T", "BZ",  "Brent Crude",          "Energy", "NYMEX"),
    # ── Bonds ── (2Y / 10Y / 30Y only: CFTC has no 20Y contract; Ultra Bond stands for the 30Y)
    ("042601", "ZT",  "2-Year T-Note",        "Bonds", "CBOT"),
    ("043602", "ZN",  "10-Year T-Note",       "Bonds", "CBOT"),
    ("020604", "UB",  "Ultra T-Bond (30Y)",   "Bonds", "CBOT"),
]

_FIELDS = [
    "report_date_as_yyyy_mm_dd", "cftc_contract_market_code", "open_interest_all",
    "noncomm_positions_long_all", "noncomm_positions_short_all",
    "comm_positions_long_all", "comm_positions_short_all",
    "nonrept_positions_long_all", "nonrept_positions_short_all",
]


def _get(params, attempts=4):
    """GET with backoff — the CFTC endpoint occasionally 5xx's or times out."""
    last = None
    for i in range(attempts):
        try:
            r = requests.get(API_URL, params=params, timeout=90,
                             headers={"User-Agent": "thematic-scanner-cot/1.0"})
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 - any failure is retried then raised
            last = e
            wait = 5 * (i + 1) if not _in_ci() else 15 * (i + 1)
            logger.warning("CFTC request failed (%s) — retry %d/%d in %ds", e, i + 1, attempts, wait)
            time.sleep(wait)
    raise RuntimeError(f"CFTC API failed after {attempts} attempts: {last}")


def _in_ci():
    import os
    return bool(os.environ.get("CI"))


def fetch_rows(since):
    """All Legacy-Futures-Only rows for our contract codes since `since` (a date)."""
    codes = ",".join(f"'{m[0]}'" for m in MARKETS)
    where = (f"cftc_contract_market_code in({codes}) "
             f"AND report_date_as_yyyy_mm_dd >= '{since.isoformat()}T00:00:00.000'")
    out, offset, page = [], 0, 20000
    while True:
        batch = _get({
            "$select": ",".join(_FIELDS), "$where": where,
            "$order": "report_date_as_yyyy_mm_dd ASC", "$limit": page, "$offset": offset,
        })
        out.extend(batch)
        if len(batch) < page:
            return out
        offset += page


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def build_markets(raw):
    """Group raw API rows by contract code into the compact per-market structure."""
    by_code = {}
    for r in raw:
        vals = [_int(r.get(k)) for k in _FIELDS[2:]]
        if any(v is None for v in vals):
            continue  # a row with a missing field would corrupt the nets — skip it
        by_code.setdefault(r["cftc_contract_market_code"].strip(), {})[r["report_date_as_yyyy_mm_dd"][:10]] = vals

    markets = []
    for code, symbol, name, group, exchange in MARKETS:
        weeks = by_code.get(code)
        if not weeks:
            logger.warning("No data for %s (%s) — omitted", symbol, code)
            continue
        rows = [[d] + weeks[d] for d in sorted(weeks)]  # dict keyed by date: de-duplicates restatements
        markets.append({"code": code, "symbol": symbol, "name": name, "group": group,
                        "exchange": exchange, "rows": rows})
    return markets


def validate(markets, today):
    if len(markets) < len(MARKETS) * MIN_MARKETS_FRACTION:
        raise RuntimeError(f"only {len(markets)}/{len(MARKETS)} markets came back — not publishing")
    latest = max(m["rows"][-1][0] for m in markets)
    age = (today - datetime.strptime(latest, "%Y-%m-%d").date()).days
    if age > MAX_STALE_DAYS:
        raise RuntimeError(f"newest report date {latest} is {age} days old — CFTC feed looks stuck")
    return latest


def main():
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=int(365.25 * HISTORY_YEARS))
    markets = build_markets(fetch_rows(since))
    report_date = validate(markets, today)

    payload = {
        "report_date": report_date,
        "source": "CFTC Legacy Futures-Only (publicreporting.cftc.gov, dataset 6dca-aqww)",
        "columns": ["date", "open_interest", "ls_long", "ls_short", "c_long", "c_short", "ss_long", "ss_short"],
        "markets": markets,
    }

    # Weekly data: only rewrite when something changed so nightly runs don't create commits.
    if OUTPUT_JSON.exists():
        try:
            old = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
            if {k: v for k, v in old.items() if k != "generated_at"} == payload:
                logger.info("COT data unchanged (report date %s) — not rewriting", report_date)
                return
        except Exception:  # noqa: BLE001 - corrupt old file: just overwrite it
            pass

    payload["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUTPUT_JSON.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    logger.info("Wrote %s — %d markets, report date %s, %.0f KB", OUTPUT_JSON.name, len(markets),
                report_date, OUTPUT_JSON.stat().st_size / 1024)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        logger.error("cot_builder failed: %s", e)
        sys.exit(1)
