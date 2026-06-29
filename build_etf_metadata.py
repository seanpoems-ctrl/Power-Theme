"""
build_etf_metadata.py — SINGLE SOURCE OF TRUTH for the ETF RS engine
====================================================================
Edit ETF_META below to add / remove / re-categorise an ETF. Then run:

    python build_etf_metadata.py

It regenerates ALL downstream ETF files in one shot:
  • public/etf_metadata.json  — ticker → category, label, type, liquid   (RS builder + UI)
  • public/etf_map.json       — label  → ticker  (scraper.py + etf_rs_builder.py ticker list)
  • src/etf_map.json          — leaderboard theme-name → ticker  (App.js theme→ETF links)

NOTE: etf_universe.json (the trendline scanner's curated list) is INTENTIONALLY
separate and is NOT generated here — it is a small hand-picked universe for a
different feature (etf_trendline_service.py).

ETF_META format:  ticker: (category, label, type, liquid)
  category : one of the 12 canonical categories (must have an anchor in
             etf_rs_builder.CATEGORY_ANCHORS, except "Space Exploration")
  label    : human-readable theme name — MUST be unique across the whole map
             (it becomes the display "theme" + the etf_map.json key)
  type     : "pure_sector"  = broad liquid sector proxy (RS anchor candidate)
             "beta_booster" = higher-beta thematic basket (gets RS-vs-anchor)
  liquid   : True  = Liquid Basket   — always-on benchmark + leaderboard
             False = Niche Scout     — RS / flip detection only (surfaced on flip)
"""
import json
import sys
from pathlib import Path
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

# fmt: off
ETF_META = {
    # ── Technology & Digital Disruption ──────────────────────────────────
    "XLK":  ("Technology & Digital Disruption", "Technology Sector",             "pure_sector",   True),
    "SOXX": ("Technology & Digital Disruption", "Semiconductors Broad",          "pure_sector",   True),
    "FDN":  ("Technology & Digital Disruption", "Internet Giants",               "pure_sector",   True),
    "SMH":  ("Technology & Digital Disruption", "Semiconductors Mega-Cap",       "pure_sector",   True),
    "CLOU": ("Technology & Digital Disruption", "Cloud SaaS",                    "beta_booster",  True),
    "BUG":  ("Technology & Digital Disruption", "Cybersecurity Alt",             "beta_booster",  True),
    "CIBR": ("Technology & Digital Disruption", "Cybersecurity",                 "beta_booster",  True),
    "HACK": ("Technology & Digital Disruption", "Cybersecurity Alt2",            "beta_booster",  True),
    "SKYY": ("Technology & Digital Disruption", "Cloud Alt",                     "beta_booster",  True),
    "ARKK": ("Technology & Digital Disruption", "ARK Innovation",                "beta_booster",  True),
    "ARKW": ("Technology & Digital Disruption", "ARK Internet",                  "beta_booster",  True),
    "XSW":  ("Technology & Digital Disruption", "Software Equal",                "pure_sector",   False),
    "IGN":  ("Technology & Digital Disruption", "Networking",                    "pure_sector",   False),
    "AIQ":  ("Technology & Digital Disruption", "AI & Data Processing",          "beta_booster",  False),
    "BAI":  ("Technology & Digital Disruption", "AI Active",                     "beta_booster",  False),
    "DRAM": ("Technology & Digital Disruption", "Memory Chips",                  "beta_booster",  False),
    "FINX": ("Technology & Digital Disruption", "FinTech",                       "beta_booster",  False),
    "FIVG": ("Technology & Digital Disruption", "5G",                            "beta_booster",  False),
    "IDGT": ("Technology & Digital Disruption", "Data Centers",                  "beta_booster",  False),
    "IGV":  ("Technology & Digital Disruption", "Software Giants",               "beta_booster",  False),
    "QTUM": ("Technology & Digital Disruption", "Quantum Computing",             "beta_booster",  False),
    "WCLD": ("Technology & Digital Disruption", "Cloud",                         "beta_booster",  False),
    "XLSR": ("Technology & Digital Disruption", "Software & Services",           "beta_booster",  False),
    "XSD":  ("Technology & Digital Disruption", "Semiconductors Equal",          "beta_booster",  False),
    "ARKF": ("Technology & Digital Disruption", "ARK FinTech",                   "beta_booster",  False),
    "ARKQ": ("Technology & Digital Disruption", "ARK Robotics",                  "beta_booster",  False),
    "BLOK": ("Technology & Digital Disruption", "Blockchain",                    "beta_booster",  False),
    "SNSR": ("Technology & Digital Disruption", "Internet of Things",            "beta_booster",  False),
    # New niche scouts (2025-2026 launches)
    "FOTO": ("Technology & Digital Disruption", "Photonics",                     "beta_booster",  False),
    "CHPX": ("Technology & Digital Disruption", "AI Semi & Quantum",             "beta_booster",  False),
    "DTCR": ("Technology & Digital Disruption", "Data Center Infrastructure",    "beta_booster",  False),
    "MOON": ("Technology & Digital Disruption", "Moonshot Innovators",           "beta_booster",  False),

    # ── Energy, Metals & Commodities ─────────────────────────────────────
    "XLE":  ("Energy, Metals & Commodities", "Traditional Oil & Gas",            "pure_sector",   True),
    "UNG":  ("Energy, Metals & Commodities", "Natural Gas",                      "pure_sector",   True),
    "USO":  ("Energy, Metals & Commodities", "Crude Oil",                        "pure_sector",   True),
    "GLD":  ("Energy, Metals & Commodities", "Gold",                             "pure_sector",   True),
    "XOP":  ("Energy, Metals & Commodities", "Oil & Gas Equal",                  "beta_booster",  True),
    "OIH":  ("Energy, Metals & Commodities", "Oil Services",                     "beta_booster",  True),
    "GDX":  ("Energy, Metals & Commodities", "Gold Miners",                      "beta_booster",  True),
    "TAN":  ("Energy, Metals & Commodities", "Solar",                            "beta_booster",  True),
    "ICLN": ("Energy, Metals & Commodities", "Clean Energy",                     "beta_booster",  True),
    "URNM": ("Energy, Metals & Commodities", "Uranium Miners",                   "beta_booster",  True),
    "URA":  ("Energy, Metals & Commodities", "Uranium & Nuclear",                "beta_booster",  True),
    "GNR":  ("Energy, Metals & Commodities", "Natural Resources Global",         "pure_sector",   False),
    "VEGI": ("Energy, Metals & Commodities", "Agriculture Producers",            "pure_sector",   False),
    "WOOD": ("Energy, Metals & Commodities", "Timber & Forestry",                "pure_sector",   False),
    "DBA":  ("Energy, Metals & Commodities", "Commodities Agriculture",          "pure_sector",   False),
    "FAN":  ("Energy, Metals & Commodities", "Wind",                             "beta_booster",  False),
    "HYDR": ("Energy, Metals & Commodities", "Hydrogen",                         "beta_booster",  False),
    "GRID": ("Energy, Metals & Commodities", "Smart Grid",                       "beta_booster",  False),
    "PICK": ("Energy, Metals & Commodities", "Metals & Mining",                  "beta_booster",  False),
    "REMX": ("Energy, Metals & Commodities", "Rare Earth Metals",                "beta_booster",  False),
    "SIL":  ("Energy, Metals & Commodities", "Silver Miners",                    "beta_booster",  False),
    "SILJ": ("Energy, Metals & Commodities", "Silver Miners Junior",             "beta_booster",  False),
    "COPX": ("Energy, Metals & Commodities", "Copper Miners",                    "beta_booster",  False),
    "SLX":  ("Energy, Metals & Commodities", "Steel",                            "beta_booster",  False),
    "NLR":  ("Energy, Metals & Commodities", "Nuclear",                          "beta_booster",  False),
    "NUKZ": ("Energy, Metals & Commodities", "Nuclear Renaissance",             "beta_booster",  False),
    "MOO":  ("Energy, Metals & Commodities", "Agriculture & FoodTech",           "beta_booster",  False),
    "AMLP": ("Energy, Metals & Commodities", "MLP & Midstream",                  "beta_booster",  False),
    "ENFR": ("Energy, Metals & Commodities", "Energy Infrastructure",            "beta_booster",  False),
    "ERTH": ("Energy, Metals & Commodities", "Clean Energy ESG",                 "beta_booster",  False),
    "EVX":  ("Energy, Metals & Commodities", "Environmental Services",           "beta_booster",  False),
    "FCG":  ("Energy, Metals & Commodities", "Natural Gas Producers",            "beta_booster",  False),
    "KRBN": ("Energy, Metals & Commodities", "Carbon Credits",                   "beta_booster",  False),
    "PBW":  ("Energy, Metals & Commodities", "Clean Energy Alt",                 "beta_booster",  False),
    "RING": ("Energy, Metals & Commodities", "Gold Miners Alt",                  "beta_booster",  False),
    "XES":  ("Energy, Metals & Commodities", "Energy Equipment",                 "beta_booster",  False),
    "XLB":  ("Energy, Metals & Commodities", "Materials & Mining",               "beta_booster",  False),
    "XME":  ("Energy, Metals & Commodities", "Metals & Mining Equal",            "beta_booster",  False),
    "ESGU": ("Energy, Metals & Commodities", "ESG Sustainability",               "beta_booster",  False),
    # New niche scouts
    "HTOO": ("Energy, Metals & Commodities", "Hydrogen Fuel Cells",              "beta_booster",  False),
    "CPER": ("Energy, Metals & Commodities", "Copper Futures",                   "beta_booster",  False),
    "GDXJ": ("Energy, Metals & Commodities", "Gold Miners Junior",              "beta_booster",  False),
    "LNGG": ("Energy, Metals & Commodities", "LNG Dominance",                    "beta_booster",  False),
    "ELEC": ("Energy, Metals & Commodities", "US Electrification",               "beta_booster",  False),
    "KROP": ("Energy, Metals & Commodities", "AgTech",                           "beta_booster",  False),

    # ── Industrials, Transportation & Infrastructure ──────────────────────
    "XLI":  ("Industrials, Transportation & Infrastructure", "Industrials Sector",       "pure_sector",   True),
    "IYT":  ("Industrials, Transportation & Infrastructure", "Transportation",           "pure_sector",   True),
    "ITA":  ("Industrials, Transportation & Infrastructure", "Aerospace & Defense",      "beta_booster",  True),
    "JETS": ("Industrials, Transportation & Infrastructure", "Airlines",                 "beta_booster",  True),
    "PAVE": ("Industrials, Transportation & Infrastructure", "Infrastructure",           "beta_booster",  True),
    "XTN":  ("Industrials, Transportation & Infrastructure", "Trucking",                 "pure_sector",   False),
    "IGF":  ("Industrials, Transportation & Infrastructure", "Global Infrastructure",    "pure_sector",   False),
    "PHO":  ("Industrials, Transportation & Infrastructure", "Water Resources",          "pure_sector",   False),
    "XAR":  ("Industrials, Transportation & Infrastructure", "Aerospace & Defense Equal","beta_booster",  False),
    "SHLD": ("Industrials, Transportation & Infrastructure", "Global Defense Tech",      "beta_booster",  False),
    "ITB":  ("Industrials, Transportation & Infrastructure", "Homebuilders",             "beta_booster",  False),
    "XHB":  ("Industrials, Transportation & Infrastructure", "Homebuilders Equal",       "beta_booster",  False),
    "LIT":  ("Industrials, Transportation & Infrastructure", "Electric Vehicles",        "beta_booster",  False),
    "DRIV": ("Industrials, Transportation & Infrastructure", "EV & Autonomous",          "beta_booster",  False),
    "FDRV": ("Industrials, Transportation & Infrastructure", "EV Future Transport",      "beta_booster",  False),
    "BOTZ": ("Industrials, Transportation & Infrastructure", "Robotics",                 "beta_booster",  False),
    "ROBO": ("Industrials, Transportation & Infrastructure", "Robotics Alt",             "beta_booster",  False),
    "BOAT": ("Industrials, Transportation & Infrastructure", "Maritime Shipping",        "beta_booster",  False),
    "VPN":  ("Industrials, Transportation & Infrastructure", "Digital Infrastructure",   "beta_booster",  False),
    # New niche scouts
    "HUMN": ("Industrials, Transportation & Infrastructure", "Humanoid Robotics",        "beta_booster",  False),
    "PRNT": ("Industrials, Transportation & Infrastructure", "3D Printing",              "beta_booster",  False),
    "SEA":  ("Industrials, Transportation & Infrastructure", "Global Shipping",          "beta_booster",  False),
    "BWET": ("Industrials, Transportation & Infrastructure", "Dry Bulk Shipping",        "beta_booster",  False),

    # ── Consumer, Gaming & E-Commerce ────────────────────────────────────
    "XLP":  ("Consumer, Gaming & E-Commerce", "Staples Sector",              "pure_sector",   True),
    "XLY":  ("Consumer, Gaming & E-Commerce", "Discretionary Sector",        "pure_sector",   True),
    "XRT":  ("Consumer, Gaming & E-Commerce", "Retail",                      "pure_sector",   True),
    "BETZ": ("Consumer, Gaming & E-Commerce", "Sports Betting",              "beta_booster",  True),
    "SOCL": ("Consumer, Gaming & E-Commerce", "Social Media",                "beta_booster",  True),
    "XHS":  ("Consumer, Gaming & E-Commerce", "Health Care Services",        "beta_booster",  True),
    "IYC":  ("Consumer, Gaming & E-Commerce", "Discretionary US",            "pure_sector",   False),
    "IYK":  ("Consumer, Gaming & E-Commerce", "Staples US",                  "pure_sector",   False),
    "PBJ":  ("Consumer, Gaming & E-Commerce", "Food & Beverage",             "pure_sector",   False),
    "FTXG": ("Consumer, Gaming & E-Commerce", "Food & Beverage Alt",         "pure_sector",   False),
    "RSPS": ("Consumer, Gaming & E-Commerce", "Staples Equal Weight",        "pure_sector",   False),
    "RSPD": ("Consumer, Gaming & E-Commerce", "Discretionary Equal Weight",  "pure_sector",   False),
    "MJ":   ("Consumer, Gaming & E-Commerce", "Cannabis & Sin Stocks",       "beta_booster",  False),
    "BUZZ": ("Consumer, Gaming & E-Commerce", "Social Sentiment",            "beta_booster",  False),
    "ESPO": ("Consumer, Gaming & E-Commerce", "Esports & Gaming",            "beta_booster",  False),
    "HERO": ("Consumer, Gaming & E-Commerce", "Digital Entertainment",       "beta_booster",  False),
    "IBUY": ("Consumer, Gaming & E-Commerce", "E-Commerce",                  "beta_booster",  False),
    "MEME": ("Consumer, Gaming & E-Commerce", "Meme Stocks",                 "beta_booster",  False),
    "METV": ("Consumer, Gaming & E-Commerce", "AR/VR Metaverse",             "beta_booster",  False),
    "PEJ":  ("Consumer, Gaming & E-Commerce", "Leisure & Entertainment",     "beta_booster",  False),
    # New niche scouts
    "VICE": ("Consumer, Gaming & E-Commerce", "Vice & Sin Stocks",           "beta_booster",  False),
    "PAWZ": ("Consumer, Gaming & E-Commerce", "Pet Care",                    "beta_booster",  False),
    "MILN": ("Consumer, Gaming & E-Commerce", "Millennial Consumer",         "beta_booster",  False),

    # ── Healthcare & Biotech ──────────────────────────────────────────────
    "XLV":  ("Healthcare & Biotech", "Healthcare Sector",   "pure_sector",  True),
    "IBB":  ("Healthcare & Biotech", "Biotech",             "beta_booster", True),
    "XBI":  ("Healthcare & Biotech", "Biotech Alt",         "beta_booster", True),
    "PPH":  ("Healthcare & Biotech", "Pharmaceuticals",     "pure_sector",  False),
    "XPH":  ("Healthcare & Biotech", "Pharma Equal",        "pure_sector",  False),
    "IHF":  ("Healthcare & Biotech", "Aging & Longevity",   "pure_sector",  False),
    "XHE":  ("Healthcare & Biotech", "Equipment Equal",     "beta_booster", False),
    "PBE":  ("Healthcare & Biotech", "Biotech Equal",       "beta_booster", False),
    "IHI":  ("Healthcare & Biotech", "Medical Devices",     "beta_booster", False),
    "GNOM": ("Healthcare & Biotech", "Genomics",            "beta_booster", False),
    "ARKG": ("Healthcare & Biotech", "ARK Genomics",        "beta_booster", False),
    "KURE": ("Healthcare & Biotech", "China Healthcare",    "beta_booster", False),
    # New niche scouts
    "SLIM": ("Healthcare & Biotech", "Obesity & GLP-1",     "beta_booster", False),
    "AGNG": ("Healthcare & Biotech", "Aging Population",    "beta_booster", False),
    "LNGR": ("Healthcare & Biotech", "Longevity",           "beta_booster", False),
    "EDOC": ("Healthcare & Biotech", "Telemedicine",        "beta_booster", False),
    "HEAL": ("Healthcare & Biotech", "HealthTech",          "beta_booster", False),

    # ── Finance & Capital Markets ─────────────────────────────────────────
    "XLF":  ("Finance & Capital Markets", "Financials Sector",      "pure_sector",  True),
    "KRE":  ("Finance & Capital Markets", "Regional Banks",         "pure_sector",  True),
    "KBE":  ("Finance & Capital Markets", "Banking Equal",          "pure_sector",  True),
    "IAI":  ("Finance & Capital Markets", "Broker-Dealers",         "beta_booster", True),
    "IAK":  ("Finance & Capital Markets", "Insurance",              "beta_booster", True),
    "IYG":  ("Finance & Capital Markets", "Financial Services",     "beta_booster", True),
    "KCE":  ("Finance & Capital Markets", "Capital Markets Equal",  "beta_booster", False),
    "KIE":  ("Finance & Capital Markets", "Insurance Equal",        "beta_booster", False),

    # ── Real Estate & Utilities ───────────────────────────────────────────
    "XLRE": ("Real Estate & Utilities", "Real Estate Sector",   "pure_sector",  True),
    "XLU":  ("Real Estate & Utilities", "Utilities Sector",     "pure_sector",  True),
    "VNQ":  ("Real Estate & Utilities", "REITs Broad",          "pure_sector",  True),
    "FRI":  ("Real Estate & Utilities", "REIT Index",           "beta_booster", False),
    "SCHH": ("Real Estate & Utilities", "Schwab REIT",          "beta_booster", False),

    # ── Geographic / Country Specific ────────────────────────────────────
    "FXI":  ("Geographic / Country Specific", "China Large Cap",   "pure_sector",  True),
    "KWEB": ("Geographic / Country Specific", "China Internet",    "pure_sector",  True),
    "EWZ":  ("Geographic / Country Specific", "Brazil",            "pure_sector",  True),
    "EWY":  ("Geographic / Country Specific", "South Korea",       "pure_sector",  True),
    "CHIQ": ("Geographic / Country Specific", "China Consumer",    "beta_booster", False),
    "ARGT": ("Geographic / Country Specific", "Argentina",         "beta_booster", False),

    # ── Crypto & Digital Assets ───────────────────────────────────────────
    "GBTC": ("Crypto & Digital Assets", "Bitcoin",          "pure_sector",  True),
    "BITW": ("Crypto & Digital Assets", "Multi Asset",      "pure_sector",  True),
    "WGMI": ("Crypto & Digital Assets", "Mining",           "beta_booster", False),

    # ── Telecom & Communication ───────────────────────────────────────────
    "XLC":  ("Telecom & Communication", "Telecom Sector",   "pure_sector",  True),
    "IYZ":  ("Telecom & Communication", "Services",         "pure_sector",  False),
    "FCOM": ("Telecom & Communication", "Broad",            "pure_sector",  False),
    "XTL":  ("Telecom & Communication", "Equal",            "beta_booster", False),

    # ── Quantitative Factors & Volatility ────────────────────────────────
    "MTUM": ("Quantitative Factors & Volatility", "Momentum",          "pure_sector",  True),
    "SPMO": ("Quantitative Factors & Volatility", "S&P Momentum",      "pure_sector",  True),
    "SVIX": ("Quantitative Factors & Volatility", "Short Volatility",  "pure_sector",  True),
    "FFTY": ("Quantitative Factors & Volatility", "IBD 50 Growth",     "beta_booster", False),
    "DXYZ": ("Quantitative Factors & Volatility", "Pre-IPO & Unicorn", "beta_booster", False),
    "GXTG": ("Quantitative Factors & Volatility", "Thematic Rotation", "beta_booster", False),

    # ── Space Exploration ─────────────────────────────────────────────────
    "NASA": ("Space Exploration", "Space Economy",   "beta_booster", False),
    "UFO":  ("Space Exploration", "Space Industry",  "beta_booster", False),
    "ARKX": ("Space Exploration", "ARK Space",       "beta_booster", False),
    "MARS": ("Space Exploration", "Space & Tech",    "beta_booster", False),
    "ORBX": ("Space Exploration", "Space Tech",      "beta_booster", False),
    "SPCX": ("Space Exploration", "SpaceX Exposure", "beta_booster", False),
}
# fmt: on

# Curated leaderboard aliases — extra theme-name → ticker keys for App.js so that
# Finviz/leaderboard theme names that don't match a label still resolve to an ETF.
# These supplement the auto-generated label→ticker entries in src/etf_map.json.
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
    "Genomics":                           "GNOM",
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
    "FinTech":                            "FINX",
    "Infrastructure":                     "PAVE",
    "Homebuilders":                       "ITB",
    "Airlines":                           "JETS",
}

ROOT = Path(__file__).parent


def _validate() -> None:
    """Fail fast on duplicate labels or unknown categories before writing."""
    labels = [m[1] for m in ETF_META.values()]
    dupes = {x for x in labels if labels.count(x) > 1}
    if dupes:
        raise SystemExit(f"❌ Duplicate labels (must be unique): {sorted(dupes)}")
    bad_type = {t: m[2] for t, m in ETF_META.items() if m[2] not in ("pure_sector", "beta_booster")}
    if bad_type:
        raise SystemExit(f"❌ Bad type values: {bad_type}")
    # Every category should have at least one pure_sector (anchor candidate),
    # except Space Exploration which intentionally has no anchor.
    cats = defaultdict(list)
    for t, (cat, _lbl, typ, _liq) in ETF_META.items():
        cats[cat].append(typ)
    for cat, types in cats.items():
        if cat != "Space Exploration" and "pure_sector" not in types:
            print(f"⚠️  Category '{cat}' has no pure_sector anchor candidate")


def main() -> None:
    _validate()

    # 1) public/etf_metadata.json — ticker → category/label/type/liquid
    metadata = [
        {"ticker": t, "category": cat, "label": lbl, "type": typ, "liquid": liq}
        for t, (cat, lbl, typ, liq) in ETF_META.items()
    ]
    metadata.sort(key=lambda x: (x["category"], not x["liquid"], x["type"], x["ticker"]))
    (ROOT / "public" / "etf_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 2) public/etf_map.json — label → ticker (scraper.py + etf_rs_builder.py)
    label_map = {lbl: t for t, (_cat, lbl, _typ, _liq) in ETF_META.items()}
    (ROOT / "public" / "etf_map.json").write_text(
        json.dumps(label_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 3) src/etf_map.json — leaderboard theme-name → ticker (App.js)
    #    = curated aliases (whose ticker still exists) ∪ label→ticker
    valid = set(ETF_META)
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
    print(f"   src/etf_map.json          ({len(leaderboard_map)} leaderboard keys)\n")
    for cat in sorted(cats):
        c = cats[cat]
        print(cat)
        if c["liquid_pure"]:   print(f"  Liquid   | Pure Sectors:  {' '.join(c['liquid_pure'])}")
        if c["liquid_beta"]:   print(f"  Liquid   | Beta Boosters: {' '.join(c['liquid_beta'])}")
        if c["illiquid_pure"]: print(f"  Niche    | Pure Sectors:  {' '.join(c['illiquid_pure'])}")
        if c["illiquid_beta"]: print(f"  Niche    | Beta Boosters: {' '.join(c['illiquid_beta'])}")
        print()


if __name__ == "__main__":
    main()
