import json, sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

p = Path("public/etf_map.json")
d = json.loads(p.read_text(encoding="utf-8"))

renames = {
    # Healthcare
    "Healthcare & Biotech":                  "Healthcare - Biotech Alt (XBI)",
    "Biotech Equal Weight":                  "Healthcare - Biotech Equal (PBE)",
    "Medical Devices":                       "Healthcare - Medical Devices",
    "Healthcare Equipment Equal":            "Healthcare - Equipment Equal",
    "Pharma Equal Weight":                   "Healthcare - Pharma Equal",
    "Genomics":                              "Healthcare - Genomics",
    "ARK Genomics":                          "Healthcare - ARK Genomics",
    "Aging Population & Longevity":          "Healthcare - Aging & Longevity",
    # Technology
    "AI Software & Data Processing":         "Technology - AI & Data Processing",
    "AI & Tech Active":                      "Technology - AI Active (BAI)",
    "US Tech/Software":                      "Technology - Software Giants (IGV)",
    "Cloud Tech":                            "Technology - Cloud (WCLD)",
    "Cloud Infrastructure & SaaS":          "Technology - Cloud SaaS (CLOU)",
    "Cloud Computing Alt2":                  "Technology - Cloud Alt (SKYY)",
    "Cybersecurity":                         "Technology - Cybersecurity (CIBR)",
    "Cybersecurity Alt":                     "Technology - Cybersecurity Alt (BUG)",
    "Cybersecurity Alt2":                    "Technology - Cybersecurity Alt2 (HACK)",
    "Internet of Things":                    "Technology - IoT",
    "Quantum/AI":                            "Technology - Quantum Computing",
    "5G Connectivity":                       "Technology - 5G",
    "Semiconductors Giants":                 "Technology - Semiconductors (SMH)",
    "Broad Semiconductor":                   "Technology - Semiconductors Broad (SOXX)",
    "Semiconductors (Equal)":               "Technology - Semiconductors Equal (XSD)",
    "Software (Equal)":                      "Technology - Software Equal (XSW)",
    "ARK Innovation":                        "Technology - ARK Innovation",
    "ARK Internet & Next-Gen Tech":          "Technology - ARK Internet",
    "ARK Robotics":                          "Technology - ARK Robotics",
    "Memory":                                "Technology - Memory Chips",
    "Digital Infrastructure & Data Centers": "Technology - Data Centers",
    "US Internet Giants":                    "Technology - Internet Giants (FDN)",
    "FinTech":                               "Technology - FinTech",
    "ARK FinTech Innovation":                "Technology - ARK FinTech",
    # Energy / Resources
    "Energy Traditional":                    "Energy - Traditional (XLE)",
    "Clean Energy":                          "Energy - Clean Energy (ICLN)",
    "Clean Energy Alt":                      "Energy - Clean Energy Alt (PBW)",
    "Solar":                                 "Energy - Solar",
    "Wind Energy":                           "Energy - Wind",
    "Hydrogen Energy & Fuel Cells":          "Energy - Hydrogen",
    "Smart Grid":                            "Energy - Smart Grid",
    "Natural Gas":                           "Energy - Natural Gas (UNG)",
    "Natural Gas Producers":                 "Energy - Natural Gas Producers (FCG)",
    "Oil & Gas Equal Weight":               "Energy - Oil & Gas Equal (XOP)",
    "Energy Equipment":                      "Energy - Equipment (XES)",
    "Commodities Energy":                    "Energy - Commodities (USO)",
    "Commodities Metals":                    "Energy - Gold Miners (GDX)",
    "Gold":                                  "Energy - Gold (GLD)",
    "Gold Miners Alt":                       "Energy - Gold Miners Alt (RING)",
    "Copper Miners":                         "Energy - Copper Miners",
    "Metals & Mining":                       "Energy - Metals & Mining (PICK)",
    "Metals & Mining Equal":                 "Energy - Metals & Mining Equal (XME)",
    "Rare Earth & Strategic Metals":         "Energy - Rare Earth Metals",
    "Silver Miners":                         "Energy - Silver Miners (SIL)",
    "Junior Silver Miners":                  "Energy - Silver Miners Junior (SILJ)",
    "Steel":                                 "Energy - Steel (SLX)",
    "Uranium & Nuclear":                     "Energy - Uranium & Nuclear (URA)",
    "Nuclear Energy":                        "Energy - Nuclear (NLR)",
    "Nuclear Renaissance":                   "Energy - Nuclear Alt (NUKZ)",
    "MLP & Midstream":                       "Energy - MLP & Midstream",
    "Commodities Agriculture":               "Energy - Commodities Agriculture (DBA)",
    "Agriculture & FoodTech":               "Energy - Agriculture & FoodTech (MOO)",
    "Environmental Sustainability":          "Energy - ESG Sustainability",
    "Materials & Mining":                    "Energy - Materials & Mining (XLB)",
    # Consumer
    "Consumer Staples":                      "Consumer - Staples Sector (XLP)",
    "Consumer Discretionary":               "Consumer - Discretionary Sector (XLY)",
    "E-Commerce":                            "Consumer - E-Commerce",
    "Sports Betting & iGaming":             "Consumer - Sports Betting",
    "Esports & Gaming":                      "Consumer - Esports & Gaming",
    "Digital Entertainment":                 "Consumer - Digital Entertainment",
    "Leisure & Entertainment":               "Consumer - Leisure & Entertainment",
    "Social Media":                          "Consumer - Social Media (BUZZ)",
    "Social Media Alt":                      "Consumer - Social Media Alt (SOCL)",
    "Global Gaming & Casinos":               "Consumer - Global Gaming & Casinos",
    "Meme":                                  "Consumer - Meme Stocks",
    "Virtual & Augmented Reality":           "Consumer - AR/VR",
    # Finance
    "Financials":                            "Finance - Sector (XLF)",
    "Regional Banks":                        "Finance - Regional Banks",
    "Banking Equal Weight":                  "Finance - Banking Equal",
    "Capital Markets Equal":                 "Finance - Capital Markets Equal",
    "Insurance Equal Weight":                "Finance - Insurance Equal",
    # Industrials
    "Industrials":                           "Industrials - Sector (XLI)",
    "Aerospace & Defense":                   "Industrials - Aerospace & Defense",
    "Aerospace & Defense (Equal)":          "Industrials - Aerospace & Defense Equal",
    "Global Defense Tech":                   "Industrials - Global Defense Tech",
    "Transportation":                        "Industrials - Transportation",
    "Trucking":                              "Industrials - Trucking",
    "Airlines":                              "Industrials - Airlines",
    "Homebuilders":                          "Industrials - Homebuilders",
    "Homebuilders Equal":                    "Industrials - Homebuilders Equal",
    "ARK Space Exploration":                 "Industrials - ARK Space",
    "Space Economy":                         "Industrials - Space Economy",
    "Space Industry":                        "Industrials - Space Industry",
    "Robotics":                              "Industrials - Robotics (BOTZ)",
    "Robotics & Automation":                 "Industrials - Robotics Alt (ROBO)",
    "Electric Vehicles":                     "Industrials - Electric Vehicles (LIT)",
    "EV & Autonomous Vehicles":             "Industrials - EV & Autonomous (DRIV)",
    "EV & Future Transpo":                   "Industrials - EV Future Transport (FDRV)",
    "Maritime Shipping & Global Trade":      "Industrials - Maritime Shipping",
    # Real Estate
    "Real Estate & REITs":                  "Real Estate - REITs Broad (VNQ)",
    "Real Estate Schwab":                    "Real Estate - Schwab (SCHH)",
    # Telecom
    "Telecommunications Services":          "Telecom - Services (IYZ)",
    "Telecommunications":                   "Telecom - Sector (XLC)",
    "Telecom Broad":                         "Telecom - Broad (FCOM)",
    "Telecom":                               "Telecom - Equal (XTL)",
    # Crypto
    "Blockchain":                            "Crypto - Blockchain (BLOK)",
    "Crypto Mining":                         "Crypto - Mining (WGMI)",
    # Factor / Smart Beta
    "Smart Beta / Factor Investing":         "Factor - Momentum (MTUM)",
    "S&P 500 Momentum":                      "Factor - S&P Momentum (SPMO)",
    "IBD 50":                                "Factor - IBD 50 Growth",
    "Short VIX Futures (Volatility)":        "Factor - Short Volatility",
    "Pre-IPO & Private Unicorn Equity":      "Factor - Pre-IPO & Unicorn",
    # International
    "Argentinian Equities":                  "International - Argentina",
    "South Korea Equities":                  "International - South Korea",
}

count = 0
skipped = []
for old, new in renames.items():
    if old in d:
        ticker = d.pop(old)
        d[new] = ticker
        print(f"{ticker:8s}  {old}  ->  {new}")
        count += 1
    else:
        skipped.append(old)

p.write_text(json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
print(f"\nRenamed: {count}")
if skipped:
    print(f"Not found: {skipped}")
print(f"Total ETFs: {len(d)}")
