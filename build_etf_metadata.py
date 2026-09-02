"""
build_etf_metadata.py — generate downstream ETF files from the master list
==========================================================================
The master list of ETFs lives in **etf_master.json** (machine-editable so that
etf_maintenance.py can add/prune entries safely). This script reads it and
regenerates, in one shot:

  • public/etf_metadata.json  — ticker → category, label, type, liquid   (RS builder + UI)
  • public/etf_map.json       — label  → ticker  (scraper.py + etf_rs_builder.py ticker list)
  • src/etf_map.json          — leaderboard theme-name → ticker  (App.js theme→ETF links)

To add / remove / re-categorise an ETF by hand, edit etf_master.json then run:
    python build_etf_metadata.py

etf_master.json entry shape:
    "TICKER": {"category": "...", "label": "...", "type": "pure_sector|beta_booster", "liquid": bool}

  category : one of the 12 canonical categories (must have an anchor in
             etf_rs_builder.CATEGORY_ANCHORS, except "Space Exploration")
  label    : human-readable theme name — MUST be unique (display + map key)
  type     : "pure_sector" (broad liquid sector / RS anchor) | "beta_booster" (thematic basket)
  liquid   : True = Liquid Basket (always-on benchmark) | False = Niche Scout (flip-only)

NOTE: etf_universe.json (the trendline scanner's curated list) is INTENTIONALLY
separate and is NOT generated here.
"""
import json
import sys
from pathlib import Path
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
MASTER_PATH = ROOT / "etf_master.json"


def load_master() -> dict:
    """ticker -> (category, label, type, liquid, description) from etf_master.json."""
    raw = json.loads(MASTER_PATH.read_text(encoding="utf-8"))
    return {t: (m["category"], m["label"], m["type"], m["liquid"], m.get("description", "")) for t, m in raw.items()}


# Curated leaderboard aliases — extra theme-name → ticker keys for App.js so that
# Finviz/leaderboard theme names that don't match a label still resolve to an ETF.
LEADERBOARD_ALIASES = {
    "Artificial Intelligence":            "AIQ",
    "Semiconductors":                     "SOXX",
    "Memory & Semiconductors":            "DRAM",
    "Cloud Computing":                    "WCLD",
    "Software":                           "IGV",
    "Disruptive Innovation":              "ARKK",
    "Quantum Computing":                  "QTUM",
    "Energy Renewable":                   "ICLN",
    "Solar Energy":                       "TAN",
    "Wind Energy":                        "FAN",
    "Uranium & Nuclear":                  "URA",
    "Nuclear Energy":                     "NLR",
    "Electric Vehicles":                  "LIT",
    "Robotics":                           "BOTZ",
    "Defense & Aerospace":                "ITA",
    "Healthcare & Biotech":               "XBI",
    "Genomics":                           "ARKG",
    "Crypto & Blockchain":                "BLOK",
    "E-Commerce":                         "IBUY",
    "Social Media":                       "SOCL",
    "Space Tech":                         "UFO",
    "Agriculture & FoodTech":             "MOO",
    "Gold":                               "GLD",
    "Copper":                             "COPX",
    "Steel":                              "SLX",
    "Lithium":                            "LIT",
    "Cybersecurity":                      "CIBR",
    "FinTech":                            "ARKF",
    "Infrastructure":                     "PAVE",
    "Homebuilders":                       "ITB",
    "Airlines":                           "JETS",
}


def _validate(etf_meta: dict) -> None:
    """Fail fast on duplicate labels or bad types before writing."""
    labels = [m[1] for m in etf_meta.values()]
    dupes = {x for x in labels if labels.count(x) > 1}
    if dupes:
        raise SystemExit(f"❌ Duplicate labels (must be unique): {sorted(dupes)}")
    bad_type = {t: m[2] for t, m in etf_meta.items() if m[2] not in ("pure_sector", "beta_booster")}
    if bad_type:
        raise SystemExit(f"❌ Bad type values: {bad_type}")
    cats = defaultdict(list)
    for t, (cat, _lbl, typ, _liq, _desc) in etf_meta.items():
        cats[cat].append(typ)
    for cat, types in cats.items():
        if cat != "Space Exploration" and "pure_sector" not in types:
            print(f"⚠️  Category '{cat}' has no pure_sector anchor candidate")


def main() -> None:
    etf_meta = load_master()
    _validate(etf_meta)

    # 1) public/etf_metadata.json — ticker → category/label/type/liquid/description
    metadata = [
        {"ticker": t, "category": cat, "label": lbl, "type": typ, "liquid": liq, "description": desc}
        for t, (cat, lbl, typ, liq, desc) in etf_meta.items()
    ]
    metadata.sort(key=lambda x: (x["category"], not x["liquid"], x["type"], x["ticker"]))
    (ROOT / "public" / "etf_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 2) public/etf_map.json — label → ticker (scraper.py + etf_rs_builder.py)
    label_map = {lbl: t for t, (_cat, lbl, _typ, _liq, _desc) in etf_meta.items()}
    (ROOT / "public" / "etf_map.json").write_text(
        json.dumps(label_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 3) src/etf_map.json — leaderboard theme-name → ticker (App.js)
    valid = set(etf_meta)
    leaderboard_map = {k: v for k, v in LEADERBOARD_ALIASES.items() if v in valid}
    leaderboard_map.update(label_map)
    leaderboard_map = dict(sorted(leaderboard_map.items()))
    (ROOT / "src" / "etf_map.json").write_text(
        json.dumps(leaderboard_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ── Summary ──────────────────────────────────────────────────────────
    cats = defaultdict(lambda: {"liquid_pure": [], "liquid_beta": [], "illiquid_pure": [], "illiquid_beta": []})
    for e in metadata:
        key = ("liquid" if e["liquid"] else "illiquid") + "_" + ("pure" if e["type"] == "pure_sector" else "beta")
        cats[e["category"]][key].append(e["ticker"])

    n_liquid = sum(1 for e in metadata if e["liquid"])
    print(f"✅ {len(metadata)} ETFs ({n_liquid} liquid baskets, {len(metadata) - n_liquid} niche scouts)")
    print(f"   public/etf_metadata.json  ({len(metadata)} entries)")
    print(f"   public/etf_map.json       ({len(label_map)} labels)")
    print(f"   src/etf_map.json          ({len(leaderboard_map)} leaderboard keys)")


if __name__ == "__main__":
    main()
