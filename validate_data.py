"""
validate_data.py — Post-scrape data quality gate.

Checks that every breadth filter JSON file has a minimum viable stock count.
Exits with code 1 (fails the GitHub Actions step) if any critical check fails,
so the deployment is blocked and the team sees a loud failure instead of silent
empty data reaching production.

Run after breadth_stocks_builder.py:
    python validate_data.py
"""
import json
import sys
from pathlib import Path

PUBLIC_DIR = Path(__file__).parent / "public"

# Minimum stocks each filter must have for the data to be considered valid.
# These are conservative floors — on a normal trading day counts are much higher.
MIN_COUNTS: dict[str, int] = {
    "up4":        10,
    "dn4":        10,
    "up25q":       5,
    "dn25q":       5,
    "up25m":       3,
    "dn25m":       3,
    "up50m":       1,
    "dn50m":       1,
    "up13_34":     5,
    "dn13_34":     5,
    "atr_ext":    10,
    "above50dma": 10,
}


def main() -> None:
    failures: list[str] = []
    warnings: list[str] = []

    for fkey, min_n in MIN_COUNTS.items():
        path = PUBLIC_DIR / f"breadth_stocks_{fkey}.json"

        if not path.exists():
            failures.append(f"MISSING FILE: {path.name}")
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"CORRUPT JSON: {path.name}: {exc}")
            continue

        count  = data.get("count", 0)
        ok     = data.get("ok", False)
        source = data.get("source", "finviz")

        if not ok:
            failures.append(f"NOT OK: {fkey} — ok=False [source: {source}]")
            continue

        if count < min_n:
            failures.append(
                f"LOW COUNT: {fkey} has {count} stocks (min {min_n}) [source: {source}]"
            )
        else:
            src_tag = f" [{source}]" if source != "finviz" else ""
            print(f"  OK  {fkey:12s}: {count:4d} stocks{src_tag}")
            if source == "tv_fallback":
                warnings.append(
                    f"{fkey}: used TradingView fallback ({count} stocks) — "
                    "Finviz may be rate-limiting this runner"
                )

    if warnings:
        print("\nWARNINGS (non-fatal):")
        for w in warnings:
            print(f"  ⚠  {w}")

    if failures:
        print("\nDATA QUALITY FAILURES:")
        for f in failures:
            print(f"  ✗  {f}")
        print(
            "\nPossible causes: Finviz rate-limiting + TradingView fallback also failed, "
            "or yfinance outage. Check breadth_stocks_builder.py logs above."
        )
        sys.exit(1)
    else:
        print("\nAll data quality checks passed.")


if __name__ == "__main__":
    main()
