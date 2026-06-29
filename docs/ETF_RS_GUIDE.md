# ETF Relative Strength System — Usage Guide

How to read and trade off the **ETF RS** view (Watchlist tab → 📊 ETF RS).
Three stacked panels, designed to be read **top-down**:

1. 🏆 **Category Leaderboard** — *where* is money rotating? (12 theme-categories)
2. ⚡ **RS Flip Scanner** — *which basket* just turned up inside a hot category?
3. **ETF Relative Strength table** — *the full detail* on every ETF.

---

## The mental model

```
Category Leaderboard   →   pick the 1-3 leading CATEGORIES
        ↓
RS Flip Scanner        →   inside those, find the basket whose RS just FLIPPED up
        ↓
RS Table / Holdings    →   open the basket, run your stock screen on its names
```

You are **not** buying the ETF. The ETF is a *thermometer* — it tells you which
pocket of the market is heating up so you run your VCP / breakout stock screen
**there** instead of guessing.

---

## Two ETF roles (the `type` field)

| Type | What it is | How to use |
|------|-----------|-----------|
| **Pure Sector** (anchor) | Broad, liquid sector proxy — XLK, XLE, XLV, XLF… | The **benchmark**. Everything in the category is measured against it. |
| **Beta Booster** | Higher-octane thematic basket — SOXX→**SMH**, XLV→**XBI**, etc. | The **signal**. When a booster outruns its anchor, risk appetite is ON in that theme. |

And the `liquid` flag:

| Flag | Name | Meaning |
|------|------|---------|
| `true` | **Liquid Basket** | Tradeable, stable — always-on benchmark + leaderboard. ~55 ETFs. |
| `false` | **Niche Scout** | Thin/new (FOTO, HUMN, SLIM…). Watched for **flips only** — surfaces exactly when money rotates in, not every day. |

---

## Panel 1 — 🏆 Category Leaderboard

Ranks the 12 categories. Columns:

| Column | Meaning | What "good" looks like |
|--------|---------|------------------------|
| **Category Score** | Median RS Score of all baskets in the category (0–100 percentile vs *every* ETF) | **70+** = category is broadly leading the whole market |
| **1W / 1M / 3M** | Median performance of the category's baskets | 1W green while 3M green = sustained; 1W green + 3M red = *early rotation in* |
| **Leader** | Highest-scoring basket in the category (click → holdings) | Your first drill-down candidate |
| **Anchor** | The pure-sector benchmark + its 1M move | Context for the whole category |
| **Flips** | ⚡N = active RS flips in the category | ⚡ = something just turned, go look |

**Sort toggle (top-right):**
- **Score** → durable leadership (use for *what to focus on this week*).
- **1W** → freshest momentum (use to *catch rotation early*).
- **1M** → the established trend.

**The key read:** a category that is **low on Score but jumping on 1W**, especially
with a ⚡ flip, is **rotating in** — the highest-alpha, earliest signal.

---

## Panel 2 — ⚡ RS Flip Scanner

For every **beta booster**, compares its RS line vs its category **anchor**.
A **flip** fires when the booster's *1-week* excess return is running ≥1.5× faster
than its *1-month* average weekly pace — i.e. RS is **accelerating**, not just high.

- **Active Flip Signals** (left) — sorted by strength (×). `3.2×` = this week is
  3.2× faster than the monthly pace. **These are your "screen now" alerts.**
- **Weekly Scanning Checklist** — every category's best booster vs its anchor.
  ▶ + "SCREEN NOW" badge = has an active flip.
- **Top 3 High-Beta Momentum** (right) — biggest 1-week excess vs anchor.

**How to act on a flip:** click the ticker → holdings modal → those are the
individual stocks. Run your VCP/breakout screen on them *that day*.

> A flip is a **timing** signal (entry window opening). Category Score is a
> **selection** signal (what deserves attention). Use them together.

---

## Panel 3 — ETF Relative Strength table

Every ETF, fully sortable. The two most important columns:

| Column | Use it for |
|--------|-----------|
| **Score** | **Theme selection** — durable 0–100 percentile rank. Sort desc to see overall leaders. |
| **RS%** | **Tactical timing** — where today's RS sits in its 25-day range. 80%+ = RS at local highs (strong); <20% = RS rolling over. |

Other columns: Day/Wk/Mth/Qtr/HY/Yr **%** (rolling performance), matching **RS ranks**
(1–99 percentile per timeframe), 1-month sparkline, 1-month RS histogram, and
**% Off 52W High** (how extended/beaten-down).

**Rule of thumb:** high **Score** *and* high **RS%** = strong theme, RS confirming
→ green light. High Score but **falling RS%** = leader losing steam → tighten/avoid new entries.

---

## A complete weekly routine

1. **Monday — top-down.** Open Category Leaderboard, sort by **1W**. Note the top 2-3
   categories and anything with a ⚡ flip that's also climbing.
2. **Confirm durability.** Switch sort to **Score**. Categories that are top on *both*
   1W and Score = your core focus. 1W-only = early-rotation watchlist.
3. **Find the basket.** In the Flip Scanner, look at those categories' rows. Click any
   "SCREEN NOW" / high-strength flip.
4. **Get the names.** Holdings modal → the individual stocks (enriched with their own
   RS/perf). Run your VCP / Stage-2 screen on them.
5. **Time the entry.** Back in the RS table, check the basket's **RS%** — 50%+ and rising
   confirms; <20% means wait.
6. **Avoid the laggards.** Bottom of the leaderboard / negative anchor moves = don't fight
   the rotation there.

---

## Maintaining the ETF list (single source of truth)

**The master list lives in one JSON file: [`etf_master.json`](../etf_master.json).**

Entry shape:
```json
"TICKER": {"category": "...", "label": "Unique Label", "type": "pure_sector"|"beta_booster", "liquid": true|false}
```

To **add / remove / re-categorise** by hand, edit `etf_master.json`, then run:

```bash
python build_etf_metadata.py     # regenerates all 3 map/metadata files (validates uniqueness)
python etf_rs_builder.py         # recomputes RS rankings → public/etf_rs.json
```

`build_etf_metadata.py` regenerates **all** downstream files in one shot:
- `public/etf_metadata.json` — ticker → category/label/type/liquid
- `public/etf_map.json` — used by `scraper.py` + `etf_rs_builder.py`
- `src/etf_map.json` — leaderboard theme→ETF links for the frontend

It **fails fast** if two ETFs share a label or a category has no pure-sector anchor.

### Notes / gotchas
- **Labels must be unique** — they are the display name *and* the map key.
- **Brand-new ETFs** (launched < ~1 month ago) may not rank until they accrue ~10+ trading
  days of history. They'll appear automatically once they do.
- New **categories** must also get an anchor in `etf_rs_builder.py` → `CATEGORY_ANCHORS`.
- `etf_universe.json` is a **separate, intentional** small list for the trendline scanner
  (`etf_trendline_service.py`) — *not* generated here.

---

## Automated maintenance (`etf_maintenance.py`)

Runs **weekly** via the `ETF Maintenance` GitHub Action (Sat 13:00 UTC). It keeps the
master list fresh so you rarely touch it by hand. Two jobs:

### 1. Auto-prune stale ETFs
- Each run checks every ticker for a recent price bar (within **10 days**).
- A miss increments a fail streak tracked in [`etf_health.json`](../etf_health.json);
  a hit resets it.
- After **3 consecutive** stale runs the ticker is **removed automatically** from
  `etf_master.json` (downstream files regenerate in the same run).
- **Category anchors are never auto-pruned** — they only log a loud warning, so RS
  anchoring can't silently break.
- *Why 3 runs:* guards against a one-off yfinance outage deleting a good ETF. An ETF that
  truly can't be priced for 3 weeks is useless in the RS engine anyway and can be
  re-added later from the candidates file.

### 2. Review-add new ETFs (never auto-added)
- Discovers liquid US ETFs (avg $vol > $25M) **not** already in the master via the
  TradingView screener, pre-filtered to thematic/niche names (broad-market, bond,
  leveraged, and option-income funds excluded).
- **Gemini** classifies each into one of the 12 categories (+ label, type, liquid),
  rejecting anything non-thematic.
- Writes them to [`etf_candidates.json`](../etf_candidates.json) for you to review.
  **Nothing is added to the master automatically** — you decide what's worth keeping.

### Your part: reviewing candidates
1. Open `etf_candidates.json` (refreshed weekly).
2. For any ETF you want, copy its `ticker` + `category`/`label`/`type`/`liquid` into
   `etf_master.json`.
3. Run `python build_etf_metadata.py && python etf_rs_builder.py` (or just let the next
   scheduled run pick it up).

### Running it manually
```bash
python etf_maintenance.py                 # dry run — report only, writes nothing
python etf_maintenance.py --apply         # prune stale + refresh candidates
python etf_maintenance.py --apply --no-discover   # prune only
python etf_maintenance.py --no-prune              # discover candidates only (dry)
```
