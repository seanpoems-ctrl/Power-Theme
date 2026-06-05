"""
build_etf_metadata.py — Generate public/etf_metadata.json
Maps every ETF ticker to: category, label, type (pure_sector|beta_booster), liquid (bool)
Single source of truth — edit this file, then run it to regenerate.
"""
import json, sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

# fmt: (category, label, type, liquid)
# type:   "pure_sector" | "beta_booster"
# liquid: True = Liquid Basket (direct trading), False = Illiquid Vector (ticker mining)

ETF_META = {
    # ── Technology & Digital Disruption ──────────────────────────────────
    "XLK":  ("Technology & Digital Disruption", "Sector",                        "pure_sector",   True),
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
    "XWEB": ("Technology & Digital Disruption", "Internet Equal",                "pure_sector",   False),
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
    "NUKZ": ("Energy, Metals & Commodities", "Nuclear Alt",                      "beta_booster",  False),
    "MOO":  ("Energy, Metals & Commodities", "Agriculture & FoodTech",           "beta_booster",  False),
    # Remaining energy ETFs not shown explicitly → illiquid beta boosters
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

    # ── Industrials, Transportation & Infrastructure ──────────────────────
    "XLI":  ("Industrials, Transportation & Infrastructure", "Sector",                   "pure_sector",   True),
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

    # ── Consumer, Gaming & E-Commerce ────────────────────────────────────
    "XLP":  ("Consumer, Gaming & E-Commerce", "Staples Sector",              "pure_sector",   True),
    "XLY":  ("Consumer, Gaming & E-Commerce", "Discretionary Sector",        "pure_sector",   True),
    "XRT":  ("Consumer, Gaming & E-Commerce", "Retail",                      "pure_sector",   True),
    "BETZ": ("Consumer, Gaming & E-Commerce", "Sports Betting",              "beta_booster",  True),
    "BJK":  ("Consumer, Gaming & E-Commerce", "Global Gaming & Casinos",     "beta_booster",  True),
    "SOCL": ("Consumer, Gaming & E-Commerce", "Social Media",                "beta_booster",  True),
    "XHS":  ("Consumer, Gaming & E-Commerce", "Health Care Services",        "beta_booster",  True),
    "IYC":  ("Consumer, Gaming & E-Commerce", "Discretionary US",            "pure_sector",   False),
    "IYK":  ("Consumer, Gaming & E-Commerce", "Staples US",                  "pure_sector",   False),
    "PBJ":  ("Consumer, Gaming & E-Commerce", "Food & Beverage",             "pure_sector",   False),
    "FTXG": ("Consumer, Gaming & E-Commerce", "Food & Beverage Alt",         "pure_sector",   False),
    "RSPS": ("Consumer, Gaming & E-Commerce", "Staples Equal Weight",        "pure_sector",   False),
    "RSPD": ("Consumer, Gaming & E-Commerce", "Discretionary Equal Weight",  "pure_sector",   False),
    "MJ":   ("Consumer, Gaming & E-Commerce", "Cannabis & Sin Stocks",       "beta_booster",  False),
    "BUZZ": ("Consumer, Gaming & E-Commerce", "Social Media Alt",            "beta_booster",  False),
    "ESPO": ("Consumer, Gaming & E-Commerce", "Esports & Gaming",            "beta_booster",  False),
    "HERO": ("Consumer, Gaming & E-Commerce", "Digital Entertainment",       "beta_booster",  False),
    "IBUY": ("Consumer, Gaming & E-Commerce", "E-Commerce",                  "beta_booster",  False),
    "MEME": ("Consumer, Gaming & E-Commerce", "Meme Stocks",                 "beta_booster",  False),
    "METV": ("Consumer, Gaming & E-Commerce", "AR/VR",                       "beta_booster",  False),
    "PEJ":  ("Consumer, Gaming & E-Commerce", "Leisure & Entertainment",     "beta_booster",  False),

    # ── Healthcare & Biotech ──────────────────────────────────────────────
    "XLV":  ("Healthcare & Biotech", "Sector",              "pure_sector",  True),
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

    # ── Finance & Capital Markets ─────────────────────────────────────────
    "XLF":  ("Finance & Capital Markets", "Sector",                 "pure_sector",  True),
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
    "CHIU": ("Geographic / Country Specific", "China Utilities",   "beta_booster", False),
    "CHIH": ("Geographic / Country Specific", "China Healthcare",  "beta_booster", False),
    "ARGT": ("Geographic / Country Specific", "Argentina",         "beta_booster", False),

    # ── Crypto & Digital Assets ───────────────────────────────────────────
    "GBTC": ("Crypto & Digital Assets", "Bitcoin",          "pure_sector",  True),
    "BITW": ("Crypto & Digital Assets", "Multi Asset",      "pure_sector",  True),
    "WGMI": ("Crypto & Digital Assets", "Mining",           "beta_booster", False),

    # ── Telecom & Communication ───────────────────────────────────────────
    "XLC":  ("Telecom & Communication", "Sector",           "pure_sector",  True),
    "IYZ":  ("Telecom & Communication", "Services",         "pure_sector",  False),
    "FCOM": ("Telecom & Communication", "Broad",            "pure_sector",  False),
    "XTL":  ("Telecom & Communication", "Equal",            "beta_booster", False),

    # ── Quantitative Factors & Volatility ────────────────────────────────
    "MTUM": ("Quantitative Factors & Volatility", "Momentum",          "pure_sector",  True),
    "SPMO": ("Quantitative Factors & Volatility", "S&P Momentum",      "pure_sector",  True),
    "SVIX": ("Quantitative Factors & Volatility", "Short Volatility",  "pure_sector",  True),
    "FFTY": ("Quantitative Factors & Volatility", "IBD 50 Growth",     "beta_booster", False),
    "DXYZ": ("Quantitative Factors & Volatility", "Pre-IPO & Unicorn", "beta_booster", False),

    # ── Space Exploration ─────────────────────────────────────────────────
    "NASA": ("Space Exploration", "Space Economy",  "beta_booster", False),
    "UFO":  ("Space Exploration", "Space Industry", "beta_booster", False),
    "ARKX": ("Space Exploration", "ARK Space",      "beta_booster", False),
}

# Build output
output = []
for ticker, (cat, label, typ, liquid) in ETF_META.items():
    output.append({
        "ticker":   ticker,
        "category": cat,
        "label":    label,
        "type":     typ,
        "liquid":   liquid,
    })

output.sort(key=lambda x: (x["category"], not x["liquid"], x["type"], x["ticker"]))

out_path = Path("public/etf_metadata.json")
out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

# Summary
from collections import defaultdict
cats = defaultdict(lambda: {"liquid_pure": [], "liquid_beta": [], "illiquid_pure": [], "illiquid_beta": []})
for e in output:
    key = ("liquid" if e["liquid"] else "illiquid") + "_" + ("pure" if e["type"] == "pure_sector" else "beta")
    cats[e["category"]][key].append(e["ticker"])

print(f"Written {len(output)} ETFs to public/etf_metadata.json\n")
for cat in sorted(cats):
    c = cats[cat]
    print(f"{cat}")
    if c["liquid_pure"]:   print(f"  Liquid Baskets   | Pure Sectors:   {' '.join(c['liquid_pure'])}")
    if c["liquid_beta"]:   print(f"  Liquid Baskets   | Beta Boosters:  {' '.join(c['liquid_beta'])}")
    if c["illiquid_pure"]: print(f"  Illiquid Vectors | Pure Sectors:   {' '.join(c['illiquid_pure'])}")
    if c["illiquid_beta"]: print(f"  Illiquid Vectors | Beta Boosters:  {' '.join(c['illiquid_beta'])}")
    print()
