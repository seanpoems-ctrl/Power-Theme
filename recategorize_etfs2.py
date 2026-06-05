import json, sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

p = Path("public/etf_map.json")
d = json.loads(p.read_text(encoding="utf-8"))

# Build reverse map: ticker -> current theme
ticker_to_theme = {v: k for k, v in d.items()}

# New category assignments per screenshots
# Format: ticker -> (new_category_prefix, short_label)
assignments = {
    # ── Technology & Digital Disruption ──────────────────────────────
    "AIQ":   ("Technology & Digital Disruption", "AI & Data Processing"),
    "BAI":   ("Technology & Digital Disruption", "AI Active (BAI)"),
    "BUG":   ("Technology & Digital Disruption", "Cybersecurity Alt (BUG)"),
    "CIBR":  ("Technology & Digital Disruption", "Cybersecurity (CIBR)"),
    "CLOU":  ("Technology & Digital Disruption", "Cloud SaaS (CLOU)"),
    "DRAM":  ("Technology & Digital Disruption", "Memory Chips"),
    "FDN":   ("Technology & Digital Disruption", "Internet Giants (FDN)"),
    "FINX":  ("Technology & Digital Disruption", "FinTech"),
    "FIVG":  ("Technology & Digital Disruption", "5G"),
    "HACK":  ("Technology & Digital Disruption", "Cybersecurity Alt2 (HACK)"),
    "IDGT":  ("Technology & Digital Disruption", "Data Centers"),
    "IGN":   ("Technology & Digital Disruption", "Networking"),
    "IGV":   ("Technology & Digital Disruption", "Software Giants (IGV)"),
    "QTUM":  ("Technology & Digital Disruption", "Quantum Computing"),
    "SKYY":  ("Technology & Digital Disruption", "Cloud Alt (SKYY)"),
    "SMH":   ("Technology & Digital Disruption", "Semiconductors (SMH)"),
    "SNSR":  ("Technology & Digital Disruption", "IoT"),
    "SOXX":  ("Technology & Digital Disruption", "Semiconductors Broad (SOXX)"),
    "WCLD":  ("Technology & Digital Disruption", "Cloud (WCLD)"),
    "XLK":   ("Technology & Digital Disruption", "Sector (XLK)"),
    "XLSR":  ("Technology & Digital Disruption", "Software & Services (XLSR)"),
    "XSD":   ("Technology & Digital Disruption", "Semiconductors Equal (XSD)"),
    "XSW":   ("Technology & Digital Disruption", "Software Equal (XSW)"),
    "XWEB":  ("Technology & Digital Disruption", "Internet Equal (XWEB)"),
    "ARKF":  ("Technology & Digital Disruption", "ARK FinTech"),
    "ARKK":  ("Technology & Digital Disruption", "ARK Innovation"),
    "ARKQ":  ("Technology & Digital Disruption", "ARK Robotics"),
    "ARKW":  ("Technology & Digital Disruption", "ARK Internet"),
    "BLOK":  ("Technology & Digital Disruption", "Blockchain (BLOK)"),  # moved from Crypto

    # ── Consumer, Gaming & E-Commerce ────────────────────────────────
    "XLP":   ("Consumer, Gaming & E-Commerce", "Staples Sector (XLP)"),
    "XLY":   ("Consumer, Gaming & E-Commerce", "Discretionary Sector (XLY)"),
    "XRT":   ("Consumer, Gaming & E-Commerce", "Retail"),
    "IYC":   ("Consumer, Gaming & E-Commerce", "Discretionary US"),
    "IYK":   ("Consumer, Gaming & E-Commerce", "Staples US"),
    "PBJ":   ("Consumer, Gaming & E-Commerce", "Food & Beverage"),
    "FTXG":  ("Consumer, Gaming & E-Commerce", "Food & Beverage Alt"),
    "RSPS":  ("Consumer, Gaming & E-Commerce", "Staples Equal Weight"),
    "RSPD":  ("Consumer, Gaming & E-Commerce", "Discretionary Equal Weight"),
    "MJ":    ("Consumer, Gaming & E-Commerce", "Cannabis & Sin Stocks"),
    "BETZ":  ("Consumer, Gaming & E-Commerce", "Sports Betting"),
    "BJK":   ("Consumer, Gaming & E-Commerce", "Global Gaming & Casinos"),
    "BUZZ":  ("Consumer, Gaming & E-Commerce", "Social Media (BUZZ)"),
    "ESPO":  ("Consumer, Gaming & E-Commerce", "Esports & Gaming"),
    "HERO":  ("Consumer, Gaming & E-Commerce", "Digital Entertainment"),
    "IBUY":  ("Consumer, Gaming & E-Commerce", "E-Commerce"),
    "MEME":  ("Consumer, Gaming & E-Commerce", "Meme Stocks"),
    "METV":  ("Consumer, Gaming & E-Commerce", "AR/VR"),
    "PEJ":   ("Consumer, Gaming & E-Commerce", "Leisure & Entertainment"),
    "SOCL":  ("Consumer, Gaming & E-Commerce", "Social Media Alt (SOCL)"),

    # ── Healthcare & Biotech ──────────────────────────────────────────
    "XLV":   ("Healthcare & Biotech", "Sector (XLV)"),
    "IBB":   ("Healthcare & Biotech", "Biotech (IBB)"),
    "XBI":   ("Healthcare & Biotech", "Biotech Alt (XBI)"),
    "XHS":   ("Healthcare & Biotech", "Services"),
    "XHE":   ("Healthcare & Biotech", "Equipment Equal"),
    "XPH":   ("Healthcare & Biotech", "Pharma Equal"),
    "PPH":   ("Healthcare & Biotech", "Pharmaceuticals"),
    "PBE":   ("Healthcare & Biotech", "Biotech Equal (PBE)"),
    "IHI":   ("Healthcare & Biotech", "Medical Devices"),
    "IHF":   ("Healthcare & Biotech", "Aging & Longevity"),
    "GNOM":  ("Healthcare & Biotech", "Genomics"),
    "ARKG":  ("Healthcare & Biotech", "ARK Genomics"),
    "KURE":  ("Healthcare & Biotech", "China Healthcare"),

    # ── Industrials, Transportation & Infrastructure ──────────────────
    "XLI":   ("Industrials, Transportation & Infrastructure", "Sector (XLI)"),
    "ITA":   ("Industrials, Transportation & Infrastructure", "Aerospace & Defense"),
    "XAR":   ("Industrials, Transportation & Infrastructure", "Aerospace & Defense Equal"),
    "SHLD":  ("Industrials, Transportation & Infrastructure", "Global Defense Tech"),
    "IYT":   ("Industrials, Transportation & Infrastructure", "Transportation"),
    "XTN":   ("Industrials, Transportation & Infrastructure", "Trucking"),
    "JETS":  ("Industrials, Transportation & Infrastructure", "Airlines"),
    "ITB":   ("Industrials, Transportation & Infrastructure", "Homebuilders"),
    "XHB":   ("Industrials, Transportation & Infrastructure", "Homebuilders Equal"),
    "LIT":   ("Industrials, Transportation & Infrastructure", "Electric Vehicles (LIT)"),
    "DRIV":  ("Industrials, Transportation & Infrastructure", "EV & Autonomous (DRIV)"),
    "FDRV":  ("Industrials, Transportation & Infrastructure", "EV Future Transport"),
    "BOTZ":  ("Industrials, Transportation & Infrastructure", "Robotics (BOTZ)"),
    "ROBO":  ("Industrials, Transportation & Infrastructure", "Robotics Alt (ROBO)"),
    "BOAT":  ("Industrials, Transportation & Infrastructure", "Maritime Shipping"),
    "IGF":   ("Industrials, Transportation & Infrastructure", "Global Infrastructure"),
    "PHO":   ("Industrials, Transportation & Infrastructure", "Water Resources"),
    "VPN":   ("Industrials, Transportation & Infrastructure", "Digital Infrastructure"),
    "PAVE":  ("Industrials, Transportation & Infrastructure", "Infrastructure (PAVE)"),  # moved from Other

    # ── Space Exploration ─────────────────────────────────────────────
    "NASA":  ("Space Exploration", "Space Economy"),
    "UFO":   ("Space Exploration", "Space Industry"),
    "ARKX":  ("Space Exploration", "ARK Space"),

    # ── Energy, Metals & Commodities ─────────────────────────────────
    "XLE":   ("Energy, Metals & Commodities", "Traditional Oil & Gas (XLE)"),
    "XOP":   ("Energy, Metals & Commodities", "Oil & Gas Equal (XOP)"),
    "OIH":   ("Energy, Metals & Commodities", "Oil Services"),
    "UNG":   ("Energy, Metals & Commodities", "Natural Gas (UNG)"),
    "USO":   ("Energy, Metals & Commodities", "Crude Oil (USO)"),
    "GDX":   ("Energy, Metals & Commodities", "Gold Miners (GDX)"),
    "GLD":   ("Energy, Metals & Commodities", "Gold (GLD)"),
    "TAN":   ("Energy, Metals & Commodities", "Solar"),
    "ICLN":  ("Energy, Metals & Commodities", "Clean Energy (ICLN)"),
    "FAN":   ("Energy, Metals & Commodities", "Wind"),
    "HYDR":  ("Energy, Metals & Commodities", "Hydrogen"),
    "GRID":  ("Energy, Metals & Commodities", "Smart Grid"),
    "PICK":  ("Energy, Metals & Commodities", "Metals & Mining (PICK)"),
    "REMX":  ("Energy, Metals & Commodities", "Rare Earth Metals"),
    "SIL":   ("Energy, Metals & Commodities", "Silver Miners (SIL)"),
    "SILJ":  ("Energy, Metals & Commodities", "Silver Miners Junior"),
    "COPX":  ("Energy, Metals & Commodities", "Copper Miners"),
    "SLX":   ("Energy, Metals & Commodities", "Steel (SLX)"),
    "URA":   ("Energy, Metals & Commodities", "Uranium & Nuclear (URA)"),
    "NLR":   ("Energy, Metals & Commodities", "Nuclear (NLR)"),
    "NUKZ":  ("Energy, Metals & Commodities", "Nuclear Alt (NUKZ)"),
    "URNM":  ("Energy, Metals & Commodities", "Uranium Miners (URNM)"),
    "MOO":   ("Energy, Metals & Commodities", "Agriculture & FoodTech"),
    "DBA":   ("Energy, Metals & Commodities", "Commodities Agriculture (DBA)"),
    "VEGI":  ("Energy, Metals & Commodities", "Agriculture Producers"),
    "WOOD":  ("Energy, Metals & Commodities", "Timber & Forestry"),
    # Keep remaining energy ETFs already categorized
    "AMLP":  ("Energy, Metals & Commodities", "MLP & Midstream"),
    "ENFR":  ("Energy, Metals & Commodities", "Energy Infrastructure"),
    "ERTH":  ("Energy, Metals & Commodities", "Clean Energy ESG"),
    "EVX":   ("Energy, Metals & Commodities", "Environmental Services"),
    "FCG":   ("Energy, Metals & Commodities", "Natural Gas Producers"),
    "GNR":   ("Energy, Metals & Commodities", "Natural Resources Global"),
    "KRBN":  ("Energy, Metals & Commodities", "Carbon Credits"),
    "PBW":   ("Energy, Metals & Commodities", "Clean Energy Alt"),
    "RING":  ("Energy, Metals & Commodities", "Gold Miners Alt"),
    "XES":   ("Energy, Metals & Commodities", "Energy Equipment"),
    "XLB":   ("Energy, Metals & Commodities", "Materials & Mining (XLB)"),
    "XME":   ("Energy, Metals & Commodities", "Metals & Mining Equal"),
    "ESGU":  ("Energy, Metals & Commodities", "ESG Sustainability"),

    # ── Finance & Capital Markets ─────────────────────────────────────
    "XLF":   ("Finance & Capital Markets", "Sector (XLF)"),
    "KRE":   ("Finance & Capital Markets", "Regional Banks"),
    "KBE":   ("Finance & Capital Markets", "Banking Equal"),
    "KCE":   ("Finance & Capital Markets", "Capital Markets Equal"),
    "KIE":   ("Finance & Capital Markets", "Insurance Equal"),
    "IAI":   ("Finance & Capital Markets", "Broker-Dealers"),
    "IAK":   ("Finance & Capital Markets", "Insurance"),
    "IYG":   ("Finance & Capital Markets", "Financial Services"),

    # ── Real Estate & Utilities ───────────────────────────────────────
    "XLRE":  ("Real Estate & Utilities", "Real Estate Sector"),
    "XLU":   ("Real Estate & Utilities", "Utilities Sector"),
    "VNQ":   ("Real Estate & Utilities", "REITs Broad (VNQ)"),
    "FRI":   ("Real Estate & Utilities", "REIT Index"),
    "SCHH":  ("Real Estate & Utilities", "Schwab REIT"),

    # ── Geographic / Country Specific ────────────────────────────────
    "FXI":   ("Geographic / Country Specific", "China - Large Cap"),
    "KWEB":  ("Geographic / Country Specific", "China - Internet"),
    "CHIQ":  ("Geographic / Country Specific", "China - Consumer"),
    "CHIU":  ("Geographic / Country Specific", "China - Utilities"),
    "CHIH":  ("Geographic / Country Specific", "China - Healthcare"),
    "EWZ":   ("Geographic / Country Specific", "Brazil"),
    "ARGT":  ("Geographic / Country Specific", "Argentina"),
    "EWY":   ("Geographic / Country Specific", "South Korea"),

    # ── Crypto & Digital Assets ───────────────────────────────────────
    "GBTC":  ("Crypto & Digital Assets", "Bitcoin (GBTC)"),
    "BITW":  ("Crypto & Digital Assets", "Multi Asset Index"),
    "WGMI":  ("Crypto & Digital Assets", "Mining (WGMI)"),
    # BLOK moved to Technology above

    # ── Telecom & Communication ───────────────────────────────────────
    "XLC":   ("Telecom & Communication", "Sector (XLC)"),
    "IYZ":   ("Telecom & Communication", "Services (IYZ)"),
    "FCOM":  ("Telecom & Communication", "Broad (FCOM)"),
    "XTL":   ("Telecom & Communication", "Equal (XTL)"),

    # ── Quantitative Factors & Volatility ────────────────────────────
    "MTUM":  ("Quantitative Factors & Volatility", "Momentum (MTUM)"),
    "SPMO":  ("Quantitative Factors & Volatility", "S&P Momentum"),
    "FFTY":  ("Quantitative Factors & Volatility", "IBD 50 Growth"),
    "SVIX":  ("Quantitative Factors & Volatility", "Short Volatility"),
    "DXYZ":  ("Quantitative Factors & Volatility", "Pre-IPO & Unicorn"),
}

# Rebuild the entire map with new theme names
new_map = {}
processed = set()

for ticker, (cat, label) in assignments.items():
    new_theme = f"{cat} - {label}"
    new_map[new_theme] = ticker
    processed.add(ticker)

# Keep any tickers not in our assignments (shouldn't be any, but safety net)
for theme, ticker in d.items():
    if ticker not in processed:
        new_map[theme] = ticker
        print(f"KEPT AS-IS: {ticker} -> {theme}")

p.write_text(json.dumps(new_map, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

# Summary
from collections import defaultdict
groups = defaultdict(list)
for theme, ticker in new_map.items():
    cat = theme.split(" - ")[0]
    groups[cat].append(ticker)

print(f"\nTotal ETFs: {len(new_map)}\n")
for cat in sorted(groups):
    tickers = sorted(groups[cat])
    print(f"{cat} ({len(tickers)})")
    print(f"  {' '.join(tickers)}\n")
