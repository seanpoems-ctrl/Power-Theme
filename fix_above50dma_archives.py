"""
fix_above50dma_archives.py
==========================
Rectifies all breadth history archives by filtering the above50dma bucket
to only include stocks where dma >= 50 (≥50% above their 50-day SMA).

The original archives stored the full unfiltered universe (all ~500 stocks)
in the above50dma bucket. The live builder correctly filters with:
    above50dma_pct = (close - SMA50) / SMA50 * 100 >= 50

This script applies the same filter retroactively to every archive date.

Run once:
    python fix_above50dma_archives.py
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HISTORY_DIR = Path("public/breadth_history")
MIN_DMA = 50.0   # ≥50% above 50-day SMA (matches breadth_stocks_builder.py line 745)


def _safe_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> None:
    files = sorted(HISTORY_DIR.glob("*.json"))
    files = [f for f in files if " " not in f.stem]  # skip "YYYY-MM-DD 2.json" duplicates

    total_fixed = 0
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read %s: %s", path.name, exc)
            continue

        filters = data.get("filters", {})
        stocks = filters.get("above50dma")
        if stocks is None:
            continue

        before = len(stocks)

        # Filter: keep only stocks where dma >= 50
        filtered = [s for s in stocks if (_safe_float(s.get("dma")) or 0) >= MIN_DMA]

        # Sort descending by dma (highest % above 50DMA first)
        filtered.sort(key=lambda s: _safe_float(s.get("dma")) or 0, reverse=True)

        after = len(filtered)

        if before != after:
            filters["above50dma"] = filtered
            data["filters"] = filters
            path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            logger.info("%-14s  above50dma: %d → %d stocks", path.stem, before, after)
            total_fixed += 1
        else:
            logger.info("%-14s  above50dma: %d stocks (no change)", path.stem, before)

    logger.info("Done — %d archive(s) updated.", total_fixed)


if __name__ == "__main__":
    main()
