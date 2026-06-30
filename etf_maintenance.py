import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows encoding fix
"""
etf_maintenance.py — keep the ETF master list fresh, automatically
==================================================================
Two jobs, run together:

  1. AUTO-PRUNE stale tickers
     - Tracks a per-ticker fail streak in etf_health.json.
     - A ticker is "stale" for a run if it returns no price bar within
       STALE_DAYS (delisted / closed fund / dead ticker).
     - After PRUNE_THRESHOLD consecutive stale runs it is REMOVED from
       etf_master.json automatically. Category anchors are never auto-pruned
       (they only get a loud warning) so RS anchoring can't silently break.

  2. REVIEW-ADD new ETFs (never auto-added)
     - Discovers liquid US ETFs not already in the master via the TradingView
       screener, pre-filtered to thematic/niche names (broad / bond / leveraged
       funds excluded).
     - Classifies each with Gemini into one of the 12 canonical categories
       (+ label, pure_sector/beta_booster, liquid).
     - Writes them to etf_candidates.json for you to review and paste into
       etf_master.json. Nothing is added to the master without your approval.

Usage:
    python etf_maintenance.py              # dry run — report only, writes nothing
    python etf_maintenance.py --apply      # apply prunes to master + write candidates
    python etf_maintenance.py --apply --no-discover   # prune only
    python etf_maintenance.py --no-prune              # discover candidates only (dry)

After --apply, downstream files are regenerated via build_etf_metadata.main().
Run etf_rs_builder.py afterwards to refresh rankings.
"""
import argparse
import json
import logging
import os
from datetime import datetime, timezone, date
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

ROOT = Path(__file__).parent
MASTER_PATH     = ROOT / "etf_master.json"
HEALTH_PATH     = ROOT / "etf_health.json"
CANDIDATES_PATH = ROOT / "public" / "etf_candidates.json"  # served to the dashboard

STALE_DAYS       = 10   # no price bar within this many days → stale this run
PRUNE_THRESHOLD  = 3    # consecutive stale runs before auto-removal
MIN_AVG_DOLLAR_VOL = 25_000_000   # discovery liquidity floor ($/day)
MIN_PRICE          = 5.0
DISCOVER_LIMIT     = 400  # top liquid ETFs to scan

CANONICAL_CATEGORIES = [
    "Technology & Digital Disruption",
    "Energy, Metals & Commodities",
    "Industrials, Transportation & Infrastructure",
    "Consumer, Gaming & E-Commerce",
    "Healthcare & Biotech",
    "Finance & Capital Markets",
    "Real Estate & Utilities",
    "Geographic / Country Specific",
    "Crypto & Digital Assets",
    "Telecom & Communication",
    "Quantitative Factors & Volatility",
    "Space Exploration",
]

# Pure-sector anchors that must never be auto-pruned (mirror etf_rs_builder.CATEGORY_ANCHORS)
PROTECTED_ANCHORS = {"XLK", "XLE", "XLV", "XLP", "XLI", "XLF", "XLRE", "XLC", "EWZ", "GBTC", "SPMO", "SPY"}

# Names we never want as thematic candidates (broad market / bonds / leverage / options-income)
EXCLUDE_NAME_RE = None
def _exclude_re():
    global EXCLUDE_NAME_RE
    if EXCLUDE_NAME_RE is None:
        import re
        EXCLUDE_NAME_RE = re.compile(
            r"\b(bond|treasury|t-bill|tbill|aggregate|muni|municipal|corporate|high.yield|"
            r"government|ultrashort|ultra.short|floating.rate|money.market|"
            r"2x|3x|leveraged|inverse|bear|ultra(pro)?|daily|covered.call|buffer|"
            r"income|dividend|total.market|s&p.500|russell|total.bond|core.bond|"
            r"target.date|equal.weight.s&p|premium.income|option)\b",
            re.IGNORECASE,
        )
    return EXCLUDE_NAME_RE


# ─────────────────────────────────────────────────────────────────────────────
# Shared IO
# ─────────────────────────────────────────────────────────────────────────────
def load_master() -> dict:
    return json.loads(MASTER_PATH.read_text(encoding="utf-8"))

def save_master(master: dict) -> None:
    MASTER_PATH.write_text(json.dumps(master, ensure_ascii=False, indent=2), encoding="utf-8")

def load_health() -> dict:
    if HEALTH_PATH.exists():
        try:
            return json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_health(health: dict) -> None:
    HEALTH_PATH.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# 1) Stale detection + auto-prune
# ─────────────────────────────────────────────────────────────────────────────
def detect_stale(tickers: list[str]) -> set[str]:
    """Return the set of tickers with NO price bar within STALE_DAYS."""
    import pandas as pd
    import yfinance as yf

    fresh: set[str] = set()
    try:
        raw = yf.download(tickers, period="1mo", interval="1d",
                          auto_adjust=True, progress=False, group_by="ticker")
    except Exception as e:
        logger.error("yfinance batch download failed (%s) — skipping prune this run", e)
        return set()  # never prune on a total fetch failure

    cutoff = pd.Timestamp.now(tz=None).normalize() - pd.Timedelta(days=STALE_DAYS)
    for t in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                s = raw[t]["Close"].dropna() if t in raw.columns.get_level_values(0) else pd.Series(dtype=float)
            else:
                s = raw["Close"].dropna()
            if len(s) and s.index[-1].tz_localize(None) >= cutoff:
                fresh.add(t)
        except Exception:
            pass  # treated as stale this run
    stale = set(tickers) - fresh
    return stale


def run_prune(master: dict, apply: bool) -> tuple[dict, list[str]]:
    """Update health streaks, auto-prune tickers past threshold. Returns (master, pruned)."""
    health = load_health()
    today = date.today().isoformat()
    tickers = list(master.keys())

    logger.info("Checking %d tickers for staleness…", len(tickers))
    stale = detect_stale(tickers)
    if not stale and not any(health.get(t, {}).get("fail_streak") for t in tickers):
        logger.info("All tickers fresh.")

    pruned: list[str] = []
    for t in tickers:
        h = health.get(t, {"fail_streak": 0, "last_ok": None})
        if t in stale:
            h["fail_streak"] = h.get("fail_streak", 0) + 1
        else:
            h["fail_streak"] = 0
            h["last_ok"] = today
        health[t] = h

        if h["fail_streak"] >= PRUNE_THRESHOLD:
            if t in PROTECTED_ANCHORS:
                logger.warning("⚠️  ANCHOR %s stale for %d runs — NOT auto-pruned (manual review needed)",
                               t, h["fail_streak"])
                continue
            pruned.append(t)

    # report current stale watch
    watch = {t: health[t]["fail_streak"] for t in tickers if health[t].get("fail_streak")}
    if watch:
        logger.info("Stale watch (fail streak): %s", ", ".join(f"{t}×{n}" for t, n in sorted(watch.items(), key=lambda x: -x[1])))

    if pruned:
        logger.info("%s %d stale ticker(s): %s",
                    "PRUNING" if apply else "WOULD PRUNE", len(pruned), " ".join(pruned))
        if apply:
            for t in pruned:
                master.pop(t, None)
                health.pop(t, None)
    else:
        logger.info("No tickers past prune threshold (%d runs).", PRUNE_THRESHOLD)

    # clean orphan health entries (ticker removed by hand)
    for t in list(health.keys()):
        if t not in master:
            health.pop(t, None)

    if apply:
        save_health(health)
    return master, pruned


# ─────────────────────────────────────────────────────────────────────────────
# 2) Discover + classify new ETFs (review-add)
# ─────────────────────────────────────────────────────────────────────────────
def discover_candidates(master: dict) -> list[dict]:
    """Liquid US ETFs not in master, thematic-filtered. Returns [{ticker, name, avg_dollar_vol}]."""
    try:
        from tradingview_screener import Query, col
    except ImportError:
        logger.error("tradingview_screener not installed — skipping discovery")
        return []

    try:
        _, df = (
            Query()
            .set_markets("america")
            .select("name", "description", "close", "average_volume_10d_calc", "type")
            .where(
                col("type") == "fund",
                col("close") > MIN_PRICE,
                col("average_volume_10d_calc") > (MIN_AVG_DOLLAR_VOL / 50),  # rough vol floor
            )
            .order_by("average_volume_10d_calc", ascending=False)
            .limit(DISCOVER_LIMIT)
            .get_scanner_data()
        )
    except Exception as e:
        logger.error("TradingView ETF discovery failed: %s", e)
        return []

    have = set(master.keys())
    exclude = _exclude_re()
    out: list[dict] = []
    for _, row in df.iterrows():
        tkr = str(row.get("name", "")).strip()
        desc = str(row.get("description", "")).strip()
        if not tkr or tkr in have:
            continue
        close = row.get("close") or 0
        vol = row.get("average_volume_10d_calc") or 0
        adv = close * vol
        if adv < MIN_AVG_DOLLAR_VOL:
            continue
        if exclude.search(desc) or exclude.search(tkr):
            continue
        out.append({"ticker": tkr, "name": desc, "avg_dollar_vol": round(adv)})
    logger.info("Discovery: %d liquid thematic ETF candidates not in master", len(out))
    return out


def classify_candidates(candidates: list[dict]) -> list[dict]:
    """Use Gemini to assign category/label/type/liquid. Drops non-thematic (category null)."""
    if not candidates:
        return []
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        logger.warning("No GEMINI_API_KEY — emitting candidates UNCLASSIFIED for manual tagging")
        return [{**c, "category": None, "label": None, "type": None, "liquid": None} for c in candidates]

    try:
        from google import genai
        from google.genai import types as gt
        client = genai.Client(api_key=api_key)
    except Exception as e:
        logger.error("Gemini init failed (%s) — emitting unclassified", e)
        return [{**c, "category": None, "label": None, "type": None, "liquid": None} for c in candidates]

    listing = "\n".join(f"{c['ticker']}: {c['name']} (avg $vol ${c['avg_dollar_vol']:,})" for c in candidates)
    prompt = (
        "You are categorising US thematic ETFs for a swing-trading dashboard.\n"
        f"Assign each ETF below to EXACTLY ONE of these categories (or null if it is a plain "
        f"broad-market / bond / leveraged / income fund that is NOT a thematic or sector play):\n"
        + "\n".join(f"- {c}" for c in CANONICAL_CATEGORIES) + "\n\n"
        "For each ETF return: category (exact string above or null), label (short unique theme "
        "name, e.g. 'Photonics', 'Uranium Miners'), type ('pure_sector' for a broad liquid sector "
        "proxy, else 'beta_booster' for a higher-beta thematic basket), liquid (true if avg $vol > "
        "$100M else false).\n"
        "Return ONLY a JSON array of objects with keys: ticker, category, label, type, liquid.\n\n"
        f"ETFs:\n{listing}"
    )
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=gt.GenerateContentConfig(thinking_config=gt.ThinkingConfig(thinking_budget=1024)),
        )
        text = (resp.text or "").strip()
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1:
            raise ValueError("no JSON array in response")
        parsed = json.loads(text[start:end + 1])
    except Exception as e:
        logger.error("Gemini classification failed (%s) — emitting unclassified", e)
        return [{**c, "category": None, "label": None, "type": None, "liquid": None} for c in candidates]

    by_tkr = {c["ticker"]: c for c in candidates}
    out: list[dict] = []
    for r in parsed:
        tkr = str(r.get("ticker", "")).strip()
        if tkr not in by_tkr:
            continue
        cat = r.get("category")
        if cat is not None and cat not in CANONICAL_CATEGORIES:
            cat = None  # reject hallucinated categories
        out.append({
            "ticker": tkr,
            "name": by_tkr[tkr]["name"],
            "avg_dollar_vol": by_tkr[tkr]["avg_dollar_vol"],
            "category": cat,
            "label": r.get("label"),
            "type": r.get("type") if r.get("type") in ("pure_sector", "beta_booster") else "beta_booster",
            "liquid": bool(r.get("liquid")),
        })
    classified = [c for c in out if c["category"]]
    logger.info("Classified %d / %d candidates into thematic categories", len(classified), len(out))
    return out


def run_discover(master: dict, apply: bool) -> list[dict]:
    candidates = discover_candidates(master)
    classified = classify_candidates(candidates)
    keep = [c for c in classified if c["category"]]  # only thematic ones worth reviewing
    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "note": "Review these, then paste accepted entries into etf_master.json and run build_etf_metadata.py.",
        "candidates": sorted(keep, key=lambda c: -c["avg_dollar_vol"]),
    }
    if apply:  # we still only WRITE the review file; never auto-add to master
        CANDIDATES_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Wrote %d candidates → %s (review before adding)", len(keep), CANDIDATES_PATH.name)
    else:
        logger.info("[dry-run] would write %d candidates to %s", len(keep), CANDIDATES_PATH.name)
        for c in keep[:15]:
            logger.info("   + %-6s %-32s → %s / %s", c["ticker"], c["name"][:32], c["category"], c["label"])
    return keep


# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="ETF master maintenance: auto-prune stale + discover new")
    ap.add_argument("--apply", action="store_true", help="write changes (prune master, write candidates)")
    ap.add_argument("--no-prune", action="store_true", help="skip stale detection / pruning")
    ap.add_argument("--no-discover", action="store_true", help="skip new-ETF discovery")
    args = ap.parse_args()

    master = load_master()
    n0 = len(master)
    pruned: list[str] = []

    if not args.no_prune:
        master, pruned = run_prune(master, args.apply)

    if not args.no_discover:
        run_discover(master, args.apply)

    if args.apply and pruned:
        save_master(master)
        logger.info("Master updated: %d → %d ETFs (pruned %d)", n0, len(master), len(pruned))
        # keep downstream files in sync
        try:
            import build_etf_metadata
            build_etf_metadata.main()
            logger.info("Regenerated downstream metadata/map files.")
        except Exception as e:
            logger.error("Downstream regen failed (%s) — run build_etf_metadata.py manually", e)

    logger.info("Done.%s", "" if args.apply else "  (dry run — use --apply to write)")


if __name__ == "__main__":
    main()
