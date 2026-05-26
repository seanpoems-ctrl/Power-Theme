"""
backfill_atr_ext.py
====================
Patches breadth_monitor.json with atr_10x_ext counts derived from
public/breadth_history/YYYY-MM-DD.json archive files.

Each archive file stores the actual stocks that were 10x ATR extended on
that day under the "atr_ext" filter key.  The breadth_monitor row field
atr_10x_ext is simply the count of those stocks.

Run once:
    python backfill_atr_ext.py
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MONITOR_FILE  = Path("public") / "breadth_monitor.json"
HISTORY_DIR   = Path("public") / "breadth_history"


def main() -> None:
    if not MONITOR_FILE.exists():
        logger.error("breadth_monitor.json not found at %s", MONITOR_FILE)
        return

    monitor = json.loads(MONITOR_FILE.read_text(encoding="utf-8"))
    rows = monitor.get("rows", [])

    patched = 0
    skipped_no_archive = 0

    for row in rows:
        # Only patch rows where atr_10x_ext is currently null
        if row.get("atr_10x_ext") is not None:
            continue

        date = row.get("date")  # "YYYY-MM-DD"
        if not date:
            continue

        archive_path = HISTORY_DIR / f"{date}.json"
        if not archive_path.exists():
            skipped_no_archive += 1
            continue

        try:
            archive = json.loads(archive_path.read_text(encoding="utf-8"))
            atr_stocks = archive.get("filters", {}).get("atr_ext", [])
            count = len(atr_stocks)
            row["atr_10x_ext"] = count
            logger.info("  %s  atr_10x_ext = %d", date, count)
            patched += 1
        except Exception as exc:
            logger.warning("  %s  could not read archive: %s", date, exc)

    if patched:
        MONITOR_FILE.write_text(
            json.dumps(monitor, separators=(",", ":")),
            encoding="utf-8",
        )
        logger.info("Done: patched %d rows, %d skipped (no archive).", patched, skipped_no_archive)
    else:
        logger.info("Nothing to patch (%d rows already had atr_10x_ext, %d had no archive).",
                    len(rows) - skipped_no_archive, skipped_no_archive)


if __name__ == "__main__":
    main()
