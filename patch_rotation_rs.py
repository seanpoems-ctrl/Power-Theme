# One-off: patch public/thematic_data.json with rotation ranks (#3) and
# broadened-universe RS scores (#4) so the dashboard shows live data before
# the next nightly scrape. Safe to delete after running.
import json

from scraper import (
    PREF_MIN_ADR_PCT,
    PREF_MIN_AVG_DOLLAR_VOL,
    _add_rotation_ranks,
    _build_sp500_rs_universe,
    _extend_rs_universe_midsmall,
    logger,
)

PATH = "public/thematic_data.json"

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)

# ── #3: rotation/acceleration ranks ──────────────────────────────────────
for key in ("theme_rankings", "finviz_theme_rankings", "industry_rankings"):
    rankings = data.get(key) or []
    _add_rotation_ranks(rankings)
    acc = [t["name"] for t in rankings if t.get("accelerating")]
    logger.info(f"{key}: {len(rankings)} entries, accelerating: {acc}")

# ── #4: RS vs broadened universe (S&P 1500 + scanned cohort) ─────────────
rs_universe = _build_sp500_rs_universe()[0]
logger.info(f"S&P 500 universe: {len(rs_universe)}")
rs_universe = _extend_rs_universe_midsmall(rs_universe)

all_stocks_flat = []
for th in (data.get("themes") or []) + (data.get("heatmap_themes") or []):
    for sub in th.get("subthemes", []):
        all_stocks_flat.extend(sub["stocks"])

if rs_universe and all_stocks_flat:
    rank_universe = dict(rs_universe)
    for stock in all_stocks_flat:
        tkr = stock.get("ticker")
        perf = stock.get("perf_6m") or stock.get("perf_3m")
        if tkr and perf is not None and tkr not in rank_universe:
            rank_universe[tkr] = perf
    sorted_perfs = sorted(rank_universe.values())
    n = len(sorted_perfs)
    changed = 0
    for stock in all_stocks_flat:
        perf = stock.get("perf_6m") or stock.get("perf_3m") or 0
        rank = sum(1 for v in sorted_perfs if v <= perf)
        new_rs = max(1, min(99, int((rank / max(n, 1)) * 98) + 1))
        if stock.get("rs_52w") != new_rs:
            changed += 1
        stock["rs_52w"] = new_rs
    logger.info(f"RS recomputed vs {n}-stock universe; {changed}/{len(all_stocks_flat)} scores changed")

    for th in (data.get("themes") or []) + (data.get("heatmap_themes") or []):
        for sub in th.get("subthemes", []):
            sub["stocks"].sort(
                key=lambda s: (
                    (s.get("adr_pct") or 0) >= PREF_MIN_ADR_PCT
                    and (s.get("avg_dollar_volume") or s.get("dollar_volume") or 0) >= PREF_MIN_AVG_DOLLAR_VOL,
                    s.get("rs_52w", 0),
                ),
                reverse=True,
            )

with open(PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
logger.info("patched " + PATH)
