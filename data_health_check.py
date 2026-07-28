import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows encoding fix
"""
data_health_check.py — content-aware staleness + completeness check
===================================================================
Why this exists: the old check looked at FILE MTIME, which a daily builder
refreshes even when it silently emits stale/empty content (e.g. the Finviz quote
parser broke and thematic_data.json shipped empty `themes` for days while its
mtime stayed current — the monitor saw "fresh" and never alerted).

This checks what actually matters:
  1. INTERNAL date  — the timestamp embedded in the file (last_updated /
     generated_at / scan_time / rows[0].date), parsed across the formats the
     builders use. Catches "rewritten but stale".
  2. CONTENT completeness — required arrays are non-empty / above a floor.
     Catches "fresh timestamp but empty payload" (partial pipeline failures).
  3. MTIME fallback — only for files that genuinely have no internal timestamp,
     with a warning so we add one over time.

Exit code 1 + machine-readable issues when anything is stale/incomplete/missing,
so CI can open an alert. Run locally any time: `python data_health_check.py`.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PUBLIC = Path(__file__).parent / "public"
NOW = datetime.now(timezone.utc)

# ── Each check: file, internal-date field(s), max age (h), content rules ──────
# date_paths : dotted paths tried in order (supports list index, e.g. "rows.0.date")
# max_hours  : alert if the internal date is older than this
# content    : list of (dotted_path_to_list, min_len) that must hold
# weekday_only: if True, the freshness window only applies Tue–Sat UTC (data
#               produced after a US trading day); skipped right after weekends.
CHECKS = [
    # file,                         date_paths,                         max_h, content,                     weekday_only
    ("thematic_data.json",          ["generated_at", "last_updated"],    30,   [("themes", 1)],             True),
    ("screener_stocks.json",        ["last_updated", "generated_at"],    30,   [("stocks", 100)],           True),
    ("gapper_data.json",            ["scan_time"],                       30,   [],                          True),
    ("breadth_monitor.json",        ["rows.0.date"],                     60,   [("rows", 1)],               True),
    ("etf_rs.json",                 ["generated_at"],                    30,   [("etfs", 50)],              True),
    ("etf_trendline.json",          ["generated_at", "last_updated"],    30,   [],                          True),
    ("universe.json",               ["generated_at", "last_updated"],    30,   [],                          True),
    ("market_internals.json",       ["generated_at", "date"],            48,   [],                          True),
    ("econ_calendar.json",          ["generated_at", "last_updated"],    72,   [],                          False),
    ("earnings_calendar.json",      ["generated_at", "last_updated"],    72,   [],                          False),
    ("breaking_news.json",          ["generated_at", "last_updated"],    6,    [],                          False),  # mtime fallback
]


def _get(d, dotted):
    cur = d
    for part in dotted.split("."):
        if isinstance(cur, list):
            try: cur = cur[int(part)]
            except (ValueError, IndexError): return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None: return None
    return cur


def _parse_dt(s):
    """Parse the date formats the builders emit → aware UTC datetime, or None."""
    if not s: return None
    s = str(s).strip()
    # Strip a trailing " ET" / timezone word the gapper/screener use
    s_clean = s.replace(" ET", "").strip()
    fmts = ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]
    # ISO with offset / microseconds
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        pass
    for f in fmts:
        try:
            return datetime.strptime(s_clean[:len(datetime.now().strftime(f))], f).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def main():
    issues, warnings = [], []
    weekend = NOW.weekday() >= 5  # Sat/Sun UTC — builders may legitimately lag

    for fn, date_paths, max_h, content, weekday_only in CHECKS:
        p = PUBLIC / fn
        if not p.exists():
            issues.append(f"MISSING: {fn}")
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            issues.append(f"PARSE ERROR: {fn} ({e})")
            continue

        # 1) content completeness (always checked — empty payload is never OK)
        for path, min_len in content:
            val = _get(d, path) if isinstance(d, dict) else None
            n = len(val) if isinstance(val, list) else 0
            if n < min_len:
                issues.append(f"INCOMPLETE: {fn} — '{path}' has {n} items (need ≥{min_len})")

        # 2) freshness from internal date (fallback to mtime if absent)
        if weekday_only and weekend:
            continue  # don't nag about trading-day data on weekends
        raw = next((_get(d, dp) for dp in date_paths if isinstance(d, dict) and _get(d, dp)), None)
        dt = _parse_dt(raw)
        if dt is None:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            age_h = (NOW - mtime).total_seconds() / 3600
            warnings.append(f"NO INTERNAL DATE: {fn} — using mtime ({age_h:.0f}h old); add a timestamp to its builder")
            if age_h > max_h:
                issues.append(f"STALE {age_h:.0f}h (mtime): {fn} (max {max_h}h)")
        else:
            age_h = (NOW - dt).total_seconds() / 3600
            if age_h > max_h:
                issues.append(f"STALE {age_h:.0f}h: {fn} — internal date {raw} (max {max_h}h)")

    # ── Thematic stock-count floor ───────────────────────────────────────────
    # themes[] can be non-empty yet under-populated (per-stock detail step
    # degraded), which silently blanks Leading/Laggard Themes + Clean Bases.
    # A healthy scrape has ~250 theme stocks; alert below 100.
    tp = PUBLIC / "thematic_data.json"
    if tp.exists():
        try:
            td = json.loads(tp.read_text(encoding="utf-8"))
            n_stocks = sum(len(s.get("stocks", []))
                           for t in td.get("themes", [])
                           for s in t.get("subthemes", []))
            if td.get("themes") and n_stocks < 100:
                issues.append(f"UNDER-POPULATED: thematic_data.json has only {n_stocks} theme "
                              "stocks (need ≥100) — per-stock detail step degraded")
        except Exception:
            pass

    print(f"Data health check @ {NOW:%Y-%m-%d %H:%M} UTC  (weekend={weekend})\n" + "-" * 60)
    if warnings:
        print("WARNINGS:")
        for w in warnings: print(f"  · {w}")
    if issues:
        print("ISSUES:")
        for i in issues: print(f"  ✗ {i}")
        # machine-readable output for the workflow
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write("has_issues=true\n")
                f.write(f"issue_body={'%0A'.join(issues)}\n")
        sys.exit(1)
    print("✅ All data files fresh and complete.")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("has_issues=false\n")


if __name__ == "__main__":
    main()
