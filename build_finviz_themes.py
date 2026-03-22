"""
build_finviz_themes.py — Scrape Finviz themes map to get authoritative
ticker → (theme, subtheme) mappings.
Output: public/finviz_theme_tickers.json
Run: python3 build_finviz_themes.py
"""
import json
import re
import time
import logging
from pathlib import Path

import requests
from scraper import HEADERS, _THEME_MAP_PREFIXES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _node_to_theme_subtheme(node_id: str) -> tuple[str, str]:
    """Convert a Finviz node ID (e.g. 'ev_selfdriving') to (theme, subtheme)."""
    # Match longest prefix first
    theme = ""
    for prefix, theme_name in sorted(_THEME_MAP_PREFIXES.items(), key=lambda x: -len(x[0])):
        if node_id.startswith(prefix):
            theme = theme_name
            # Subtheme = remainder after prefix (strip leading underscore)
            remainder = node_id[len(prefix):].lstrip("_")
            subtheme = _SUBTHEME_LABELS.get(node_id) or _prettify(remainder)
            return theme, subtheme
    return "", node_id


def _prettify(s: str) -> str:
    """Convert snake_case or camelCase node suffix to Title Case label."""
    # Insert space before uppercase runs
    s = re.sub(r'([a-z])([A-Z])', r'\1 \2', s)
    s = s.replace("_", " ").replace("-", " ")
    return s.title().strip()


# Explicit label overrides for known sub-theme node suffixes
_SUBTHEME_LABELS = {
    # Electric Vehicles
    "ev_manufacturers":   "Manufacturers",
    "ev_charging":        "Charging",
    "ev_suppliers":       "Suppliers",
    "ev_selfdriving":     "Self-Driving",
    "ev_batteries":       "Batteries",
    "ev_chips":           "Chips",
    "ev_fleets":          "Fleets",
    # Artificial Intelligence
    "ai_compute":         "Compute",
    "ai_enterprise":      "Enterprise",
    "ai_networking":      "Networking",
    "ai_security":        "Security",
    "ai_cloud":           "Cloud",
    "ai_edge":            "Edge",
    "ai_applications":    "Applications",
    "ai_adssearch":       "Ads & Search",
    "ai_models":          "Models",
    "ai_robotics":        "Robotics",
    "ai_energy":          "Energy",
    "ai_agi":             "AGI",
    "ai_data":            "Data",
    # Cloud Computing
    "cloud_hyperscalers": "Hyperscalers",
    "cloud_datacenters":  "Data Centers",
    "cloud_databases":    "Databases",
    "cloud_devops":       "DevOps",
    "cloud_multicloud":   "Multi Cloud",
    "cloud_serverless":   "Serverless",
    "cloud_security":     "Security",
    "cloud_paas":         "PaaS",
    "cloud_edge":         "Edge",
    "cloud_hybridcloud":  "Hybrid Cloud",
    "cloud_hardware":     "Hardware",
    "cloud_hsaas":        "H-SaaS",
    # Semiconductors
    "semi_compute":       "Compute",
    "semi_analog":        "Analog",
    "semi_foundries":     "Foundries",
    "semi_designtools":   "Design Tools",
    "semi_memory":        "Memory",
    "semi_wireless":      "Wireless",
    "semi_lithography":   "Lithography",
    "semi_packaging":     "Packaging",
    "semi_nextgen":       "Next-Gen",
    # Software
    "soft_os":            "OS",
    "soft_collaboration": "Collaboration",
    "soft_gaming":        "Gaming",
    "soft_ecommerce":     "E-Commerce",
    "soft_enterprise":    "Enterprise",
    "soft_design":        "Design",
    "soft_devops":        "DevOps",
    "soft_dataanalytics": "Data Analytics",
    "soft_crm":           "CRM",
    "soft_security":      "Security",
    "soft_hsaas":         "H-SaaS",
    "soft_vsaas":         "V-SaaS",
    # Cybersecurity
    "cyber_zerotrust":    "ZeroTrust",
    "cyber_network":      "Network",
    "cyber_identityiam":  "Identity IAM",
    "cyber_threatops":    "ThreatOps",
    "cyber_endpoint":     "Endpoint",
    "cyber_cloud":        "Cloud",
    "cyber_siem":         "SIEM",
    "cyber_appsecurity":  "App Security",
    # Defense & Aerospace
    "defense_drones":     "Drones",
    "defense_spacetech":  "SpaceTech",
    "defense_weapons":    "Weapons",
    "defense_missiles":   "Missiles",
    "defense_cyberdefense": "CyberDefense",
    "defense_aviation":   "Aviation",
    "defense_manufacturing": "Manufacturing",
    # Healthcare & Biotech
    "health_nextgen":     "Next-Gen",
    "health_diagnostics": "Diagnostics",
    "health_genomics":    "Genomics",
    "health_oncology":    "Oncology",
    "health_devices":     "Devices",
    "health_metabolic":   "Metabolic",
    "health_telemedicine": "Telemedicine",
    "health_therapeutics": "Therapeutics",
    # Fintech
    "fintech_payments":   "Payments",
    "fintech_trading":    "Trading",
    "fintech_blockchain": "Blockchain",
    "fintech_neobanks":   "Neobanks",
    "fintech_itdata":     "IT & Data",
    "fintech_lending":    "Lending",
    "fintech_exchanges":  "Exchanges",
    "fintech_insurance":  "Insurance",
    # Transportation & Logistics
    "transport_rail":     "Rail",
    "transport_aircargo": "Air Cargo",
    "transport_warehousing": "Warehousing",
    "transport_infrastructure": "Infrastructure",
    "transport_trucking": "Trucking",
    "transport_maritime": "Maritime",
    "transport_airtravel": "Air Travel",
    "transport_nextgen":  "Next-Gen",
    # Energy Renewable
    "energyclean_solar":  "Solar",
    "energyclean_geothermal": "Geothermal",
    "energyclean_wind":   "Wind",
    "energyclean_batteries": "Batteries",
    "energyclean_hydrogen": "Hydrogen",
    "energyclean_smartgrid": "Smart Grid",
    "energyclean_biofuels": "Biofuels",
    "energyclean_utilities": "Utilities",
    "energyclean_materials": "Materials",
    # Energy Traditional
    "energybase_oilproduction": "Oil Production",
    "energybase_thermal": "Thermal",
    "energybase_nuclear": "Nuclear",
    "energybase_oilrefining": "Oil Refining",
    "energybase_oilservices": "Oil Services",
    "energybase_majors":  "Majors",
    "energybase_utilities": "Utilities",
    # Industrial Automation
    "industauto_robotics": "Robotics",
    "industauto_machinevision": "Machine Vision",
    "industauto_iot":     "IoT",
    "industauto_logistics": "Logistics",
    "industauto_software": "Software",
    "industauto_automation": "Automation",
    "industauto_3dprinting": "3D Printing",
    # Space Tech
    "space_launch":       "Launch",
    "space_defense":      "Defense",
    "space_satellites":   "Satellites",
    "space_infrastructure": "Infrastructure",
    # Robotics
    "robotics_automation": "Automation",
    "robotics_avmobility": "AV & Mobility",
    "robotics_machinevision": "Machine Vision",
    "robotics_logistics": "Logistics",
    "robotics_medical":   "Medical",
    "robotics_consumer":  "Consumer",
    # Telecom
    "telecom_wireless":   "Wireless",
    "telecom_cloudedge":  "Cloud & Edge",
    "telecom_5g":         "5G",
    "telecom_satcom":     "Satcom",
    "telecom_enterprise": "Enterprise",
    # E-Commerce
    "ecomm_marketplaces": "Marketplaces",
    "ecomm_platforms":    "Platforms",
    "ecomm_adsmedia":     "Ads & Media",
    "ecomm_logistics":    "Logistics",
    "ecomm_omnichannel":  "Omnichannel",
    "ecomm_grocery":      "Grocery",
    "ecomm_social":       "Social",
    # Digital Entertainment
    "entertainment_video": "Video",
    "entertainment_gaming": "Gaming",
    "entertainment_infrastructure": "Infrastructure",
    "entertainment_music": "Music",
    "entertainment_betting": "Betting",
    "entertainment_gambling": "Gambling",
    "entertainment_dtc":  "DTC",
    # Consumer Goods
    "consumer_food":      "Food",
    "consumer_apparel":   "Apparel",
    "consumer_luxury":    "Luxury",
    "consumer_household": "Household",
    "consumer_farmdirect": "Farm-Direct",
    "consumer_secondhand": "Secondhand",
    # Smart Home
    "smarthome_devices":  "Devices",
    "smarthome_automation": "Automation",
    "smarthome_security": "Security",
    "smarthome_networking": "Networking",
    "smarthome_voiceai":  "Voice & AI",
    "smarthome_energy":   "Energy",
    # Commodities Metals
    "commmetals_gold":    "Gold",
    "commmetals_silver":  "Silver",
    "commmetals_precious": "Precious",
    "commmetals_industrial": "Industrial",
    "commmetals_rareearth": "Rare Earth",
    "commmetals_battery": "Battery",
    "commmetals_recycling": "Recycling",
    # Commodities Energy
    "commenergy_oil":     "Oil",
    "commenergy_gaslng":  "Gas & LNG",
    "commenergy_uranium": "Uranium",
    "commenergy_biofuels": "Biofuels",
    # Commodities Agriculture
    "commagri_grains":    "Grains",
    "commagri_softs":     "Softs",
    "commagri_livestock": "Livestock",
    "commagri_smartfarming": "Smart Farming",
    "commagri_cropinputs": "Crop Inputs",
    "commagri_biofuels":  "Biofuels",
    # Agriculture & FoodTech
    "agriculture_indoorfarming": "Indoor Farming",
    "agriculture_processing": "Processing",
    "agriculture_altprotein": "Alt Protein",
    "agriculture_fertilizers": "Fertilizers",
    # Environmental Sustainability
    "environmental_water": "Water",
    "environmental_waste": "Waste",
    "environmental_airquality": "Air Quality",
    "environmental_climate": "Climate",
    "environmental_agriculture": "Agriculture",
    # Real Estate & REITs
    "realestate_residential": "Residential",
    "realestate_office":  "Office",
    "realestate_retail":  "Retail",
    "realestate_industrial": "Industrial",
    "realestate_datacenter": "Data Center",
    "realestate_healthcare": "Healthcare",
    "realestate_hotel":   "Hotel",
    "realestate_mortgage": "Mortgage",
    # Nanotechnology
    "nano_materials":     "Materials",
    "nano_medicine":      "Medicine",
    "nano_electronics":   "Electronics",
    "nano_energy":        "Energy",
    "nano_hardware":      "Hardware",
    "nano_software":      "Software",
    "nano_products":      "Products",
    "nano_researchtools": "Research Tools",
    "nano_enterprise":    "Enterprise",
    "nano_security":      "Security",
    # Internet of Things
    "iot_edgedevices":    "Edge Devices",
    "iot_networking":     "Networking",
    # Autonomous Systems
    "autosys_avmobility": "AV & Mobility",
    "autosys_defense":    "Defense",
    "autosys_software":   "Software",
    "autosys_industrial": "Industrial",
    "autosys_machinevision": "Machine Vision",
    "autosys_specialized": "Specialized",
    # Quantum Computing
    "quantum_hardware":   "Hardware",
    "quantum_software":   "Software",
    "quantum_networking": "Networking",
    "quantum_cloud":      "Cloud",
    "quantum_enablingtech": "Enabling Tech",
    "quantum_applications": "Applications",
    # Virtual & Augmented Reality
    "vr_hardware":        "Hardware",
    "vr_applications":    "Applications",
    "vr_software":        "Software",
    "vr_infrastructure":  "Infrastructure",
    "vr_enterprise":      "Enterprise",
    # Crypto & Blockchain
    "blockchain_bitcoin": "Bitcoin",
    "blockchain_defi":    "DeFi",
    "blockchain_nft":     "NFT",
    "blockchain_web3":    "Web3",
    "blockchain_mining":  "Mining",
    "blockchain_exchange": "Exchange",
}


def fetch_finviz_theme_tickers() -> dict[str, dict]:
    """
    Scrape finviz.com/map.ashx?t=themes and extract ticker → {theme, subtheme}.
    Returns dict keyed by ticker.
    """
    url = "https://finviz.com/map.ashx?t=themes"
    logger.info(f"Fetching {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    html = resp.text

    # Extract the FinvizInitCanvas data blob
    # The canvas data contains something like: d=[{...ticker data...}, ...]
    # Try to find stock data in various formats

    # Pattern 1: Look for ticker/node data in the JS
    # Finviz map uses: d=[{"t":"NVDA","n":"ev_selfdriving",...},...]
    matches = re.findall(
        r'\{"t"\s*:\s*"([A-Z]{1,6})"\s*,\s*[^}]*"n"\s*:\s*"([a-z0-9_]+)"',
        html
    )

    if not matches:
        # Pattern 2: different field ordering
        matches = re.findall(
            r'"n"\s*:\s*"([a-z0-9_]+)"[^}]*"t"\s*:\s*"([A-Z]{1,6})"',
            html
        )
        matches = [(t, n) for n, t in matches]

    if not matches:
        # Pattern 3: look for any ticker-to-node associations
        matches = re.findall(
            r'"([A-Z]{1,6})"\s*:\s*\{[^}]*"node"\s*:\s*"([a-z0-9_]+)"',
            html
        )

    logger.info(f"Found {len(matches)} ticker-node pairs from map page")

    result = {}
    for ticker, node_id in matches:
        theme, subtheme = _node_to_theme_subtheme(node_id)
        if theme:
            result[ticker] = {"theme": theme, "subtheme": subtheme, "node": node_id}

    return result


def build():
    ticker_map = fetch_finviz_theme_tickers()

    if not ticker_map:
        logger.warning("No ticker-node data found from map page. Trying screener approach...")
        ticker_map = fetch_via_screener()

    logger.info(f"Total ticker→theme mappings: {len(ticker_map)}")

    out = Path("public/finviz_theme_tickers.json")
    out.write_text(json.dumps(ticker_map, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Saved to {out}")

    # Spot checks
    for t in ["AUR", "NVDA", "TSLA", "APTV", "GOOGL"]:
        if t in ticker_map:
            logger.info(f"  {t}: {ticker_map[t]}")
        else:
            logger.info(f"  {t}: NOT FOUND in themes map")


def fetch_via_screener() -> dict[str, dict]:
    """
    Fallback: for each known sub-theme node ID, scrape the Finviz screener.
    Uses URL: https://finviz.com/screener.ashx?v=111&f=<node_id>
    """
    from bs4 import BeautifulSoup

    result = {}
    node_ids = list(_SUBTHEME_LABELS.keys())
    logger.info(f"Scraping screener for {len(node_ids)} sub-themes...")

    for node_id in node_ids:
        theme, subtheme = _node_to_theme_subtheme(node_id)
        if not theme:
            continue

        offset = 1
        while True:
            url = f"https://finviz.com/screener.ashx?v=111&f={node_id}&r={offset}"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

                # Parse table rows
                page_tickers = []
                for t in soup.find_all("table"):
                    rows = t.find_all("tr")
                    if len(rows) < 3:
                        continue
                    headers = [td.get_text(strip=True) for td in rows[1].find_all(["td", "th"])]
                    if "Ticker" not in headers:
                        continue
                    idx = {h: i for i, h in enumerate(headers)}
                    for row in rows[2:]:
                        cells = [td.get_text(strip=True) for td in row.find_all("td")]
                        if len(cells) < 2:
                            continue
                        ticker = cells[idx.get("Ticker", 1)]
                        if ticker and ticker not in ("Ticker", "No."):
                            page_tickers.append(ticker)

                if not page_tickers:
                    break

                for ticker in page_tickers:
                    if ticker not in result:  # first assignment wins (most specific)
                        result[ticker] = {"theme": theme, "subtheme": subtheme, "node": node_id}

                logger.info(f"  {node_id} offset={offset}: +{len(page_tickers)}")
                if len(page_tickers) < 20:
                    break
                offset += 20
                time.sleep(0.4)

            except Exception as e:
                logger.warning(f"  Error {node_id} offset={offset}: {e}")
                break

    return result


if __name__ == "__main__":
    build()
