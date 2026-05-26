"""
backfill_breadth_archives.py
=============================
Retroactively fixes known bugs in public/breadth_history/YYYY-MM-DD.json archives.

Fixes applied per archive:
  1. up50m / dn50m  — re-filter using stored "mtd" field so only stocks
                       actually meeting ±50% monthly threshold are kept.
                       Previously the archives had up50m == up25m (bug).
  2. up4  (partial)  — for dates where up4 = 0 but other buckets have stocks,
                       reconstruct a partial list from any stock in the archive
                       that has c (change%) >= 4, sorted desc by c.
  3. above50dma      — strip stocks that do NOT have "dma" field (old archives
                       pre-2026-05-27 lack it); future archives will carry the
                       dma field and will already be pre-filtered to >= 50%.

Run once:
    python backfill_breadth_archives.py
"""

import json
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

HISTORY_DIR = Path("public") / "breadth_history"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fix_up50m(stocks: list[dict]) -> list[dict]:
    """Keep only stocks with mtd >= 50 (or mtd is None, to avoid silently dropping)."""
    fixed = []
    for s in stocks:
        mtd = _safe_float(s.get("mtd"))
        if mtd is None or mtd >= 50:
            fixed.append(s)
    return fixed


def _fix_dn50m(stocks: list[dict]) -> list[dict]:
    """Keep only stocks with mtd <= -50 (or mtd is None)."""
    fixed = []
    for s in stocks:
        mtd = _safe_float(s.get("mtd"))
        if mtd is None or mtd <= -50:
            fixed.append(s)
    return fixed


def _reconstruct_up4(filters: dict) -> list[dict]:
    """
    Collect all unique stocks across all filter buckets that have c (change%) >= 4.
    Sorted descending by change%.  Used only when the stored up4 bucket is empty.
    """
    seen: set[str] = set()
    candidates: list[dict] = []
    for key, stocks in filters.items():
        for s in stocks:
            tkr = s.get("t", "")
            chg = _safe_float(s.get("c"))
            if tkr and chg is not None and chg >= 4.0 and tkr not in seen:
                seen.add(tkr)
                candidates.append(s)
    candidates.sort(key=lambda s: _safe_float(s.get("c")) or 0, reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    files = sorted(HISTORY_DIR.glob("*.json"))
    files = [f for f in files if " " not in f.stem]  # skip "2026-05-04 2.json" etc.

    if not files:
        logger.error("No archive files found in %s", HISTORY_DIR)
        return

    total_updated = 0
    stats = {"up50m": 0, "dn50m": 0, "up4_reconstructed": 0, "above50dma": 0}

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            filters: dict = data.get("filters", {})
            changed = False

            # ── Fix 1: up50m ─────────────────────────────────────────────────
            if "up50m" in filters:
                before = len(filters["up50m"])
                filters["up50m"] = _fix_up50m(filters["up50m"])
                after = len(filters["up50m"])
                if after != before:
                    logger.info("  %s  up50m: %d → %d stocks", path.stem, before, after)
                    stats["up50m"] += 1
                    changed = True

            # ── Fix 2: dn50m ─────────────────────────────────────────────────
            if "dn50m" in filters:
                before = len(filters["dn50m"])
                filters["dn50m"] = _fix_dn50m(filters["dn50m"])
                after = len(filters["dn50m"])
                if after != before:
                    logger.info("  %s  dn50m: %d → %d stocks", path.stem, before, after)
                    stats["dn50m"] += 1
                    changed = True

            # ── Fix 3: up4 = 0 → reconstruct partial ─────────────────────────
            if "up4" in filters and len(filters["up4"]) == 0:
                reconstructed = _reconstruct_up4(filters)
                if reconstructed:
                    filters["up4"] = reconstructed
                    logger.info("  %s  up4: 0 → %d stocks (partial, reconstructed from c%%)",
                                path.stem, len(reconstructed))
                    stats["up4_reconstructed"] += 1
                    changed = True

            # ── Fix 4: above50dma — strip stocks without dma field ────────────
            # New archives (≥2026-05-27) are pre-filtered to dma >= 50.
            # Old archives have no dma field; leave them as-is (the modal
            # normalizer returns above50dma_pct: null for those stocks).
            if "above50dma" in filters:
                stocks_dma = filters["above50dma"]
                with_dma = [s for s in stocks_dma if s.get("dma") is not None]
                if with_dma and len(with_dma) != len(stocks_dma):
                    # Some stocks have dma (mixed archive) — keep only those with dma >= 50
                    filtered = [s for s in stocks_dma
                                if s.get("dma") is None or s["dma"] >= 50]
                    if len(filtered) != len(stocks_dma):
                        logger.info("  %s  above50dma: %d → %d stocks (dma>=50 filter)",
                                    path.stem, len(stocks_dma), len(filtered))
                        filters["above50dma"] = filtered
                        stats["above50dma"] += 1
                        changed = True

            if changed:
                data["filters"] = filters
                path.write_text(
                    json.dumps(data, separators=(",", ":")),
                    encoding="utf-8",
                )
                total_updated += 1

        except Exception as exc:
            logger.warning("Could not process %s: %s", path.name, exc)

    logger.info(
        "Done: %d/%d archives updated | up50m=%d dn50m=%d up4=%d above50dma=%d",
        total_updated, len(files),
        stats["up50m"], stats["dn50m"], stats["up4_reconstructed"], stats["above50dma"],
    )


if __name__ == "__main__":
    main()
