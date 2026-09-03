import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows encoding fix
"""
gapper_service.py — Pre-Market Gapper Intelligence Scanner
Scans for gap-up stocks 08:00–09:29 AM ET using TradingView Screener
Categorizes catalysts and generates trade hypotheses via Gemini 2.5 Flash
Outputs public/gapper_data.json
"""

import sys
sys.dont_write_bytecode = True  # Prevent stale .pyc cache issues

import json
import os
import re
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
ET = ZoneInfo("America/New_York")

# ──────────────────────────────────────────────────────────────
# Opinion Article Filtering
# ──────────────────────────────────────────────────────────────

OPINION_KEYWORDS = {
    # Prediction / analysis / commentary words
    "why", "analysis", "bullish", "bearish", "outlook", "opinion",
    "could", "should", "might", "expected to", "could surge", "could fall",
    "prediction", "expert says", "top reasons", "what to know", "here's why",
    "could rally", "analyst view", "will likely", "analyst expects",
    "could drop", "could rise", "market sentiment", "investor outlook",
    "here's what we see", "what we see in our data", "stay in focus",
    "chatter", "narrative stay", "like move", "like a", "reminds us",
    "looks like", "is it", "is this", "watch out", "time to buy",
    "time to sell", "worth watching", "keep an eye", "what investors",
    "what you need", "everything you need", "3 reasons", "5 reasons",
    "top picks", "best stocks", "worst stocks",
    # Question-style opinion titles
    "room to run", "more room", "have more", "do shares",
    "what's going on with", "what is going on with", "going on with",
    "is this the", "is now the time", "time to own", "should you buy",
    "should you sell", "worth buying", "worth owning",
    "here's what", "here is what",
}

# Roundup/list articles — mention multiple stocks, not company-specific news
ROUNDUP_KEYWORDS = {
    "biggest moves", "biggest movers", "biggest gainers", "biggest losers",
    "top movers", "midday movers", "premarket movers", "pre-market movers",
    "after-hours movers", "afterhours movers", "making moves midday",
    "stocks on the move", "stocks moving", "movers today", "stocks to watch",
    "what's moving", "whats moving", "market movers", "hot stocks",
    "notable movers", "unusual movers", "notable gainers", "notable losers",
    "stocks making the biggest", "making the biggest moves",
    " and more", "& more",   # roundup "HPE, MRVL, Coherent & more"
}

OPINION_SOURCES = {
    # Financial media opinion / commentary sites
    "seeking alpha", "motley fool", "investor's business daily", "investopedia",
    "tradingview blog", "yahoo finance opinion", "cnbc opinion", "forbes opinion",
    "marketwatch opinion", "fool.com", "benzinga opinion", "zacks opinion",
    # Sources confirmed causing duplicate/opinion noise
    "barron", "barrons",                         # Barron's — mostly opinion
    "quiver quantitative", "quiverquant",        # Quiver — data commentary, not hard news
    "thestreet", "the street",                   # TheStreet — analysis/commentary
    "kiplinger",                                 # Kiplinger — opinion
    "schaeffers", "schaeffer",                   # Schaeffer's — derivatives opinion
    "barchart",                                  # Barchart — screener commentary
    "tipranks",                                  # TipRanks — analyst opinion
    "simply wall st", "simplywallst",            # Simply Wall St — analysis
    "gurufocus",                                 # GuruFocus — value analysis
    "stockanalysis",                             # Stock Analysis — commentary
    "wsj opinion", "wall street journal opinion",
    "bloomberg opinion",
    "marketbeat",                                # MarketBeat — options volume alerts, not fundamental news
    "benzinga",                                  # Benzinga — mostly commentary/alerts
    "stocktwits",                                # StockTwits — social/sentiment
    "finbold",                                   # Finbold — crypto/stock commentary
    "wallstreetmojo",                            # WSM — educational/analysis
    "qz.com", "quartz",                          # Quartz — business commentary
    "thefly.com", "the fly",                     # The Fly — options/flow alerts
    "trefis",                                    # Trefis — valuation commentary
    "tradingview",                               # TradingView articles — analysis/opinion
    "nasdaq.com",                                # Nasdaq.com editorial — mostly commentary
    "fool.com",                                  # Motley Fool
}

# ──────────────────────────────────────────────────────────────
# Curated Peer Stocks & Leverage/Inverse Mappings
# ──────────────────────────────────────────────────────────────

PEER_MAPPING = {
    # ── Storage & Data Center ──────────────────────────────────
    "HPE":  ["SMCI", "NTAP", "STX"],
    "SMCI": ["HPE",  "NTAP", "WDC"],
    "NTAP": ["HPE",  "SMCI", "STX"],
    "STX":  ["WDC",  "NTAP", "HPE"],
    "WDC":  ["STX",  "SMCI", "NTAP"],
    "HPO":  ["SMCI", "NTAP", "ANET"],
    "SNDK": ["STX",  "WDC",  "SMCI"],

    # ── Networking & Infrastructure ───────────────────────────
    "ANET": ["HPE",  "CSCO", "JNPR"],
    "CSCO": ["ANET", "IBM",  "HPE"],
    "IBM":  ["CSCO", "ANET", "HPE"],
    "JNPR": ["ANET", "CSCO", "HPE"],
    "GLW":  ["COHR", "LITE", "FNSR"],     # Corning — optical fiber

    # ── AI / Large-Cap Semis ──────────────────────────────────
    "NVDA": ["AMD",  "AVGO", "MRVL"],
    "AMD":  ["NVDA", "AVGO", "QCOM"],
    "AVGO": ["MRVL", "QCOM", "NVDA"],
    "QCOM": ["AMD",  "AVGO", "MRVL"],
    "MRVL": ["AVGO", "QCOM", "NVDA"],

    # ── Analog / Embedded / Power Semis ──────────────────────
    "STM":  ["ON",   "NXPI", "MCHP"],
    "ON":   ["STM",  "NXPI", "MCHP"],
    "NXPI": ["STM",  "ON",   "MCHP"],
    "MCHP": ["STM",  "ON",   "NXPI"],
    "NVTS": ["ON",   "STM",  "WOLF"],     # Navitas — power semis
    "WOLF": ["NVTS", "STM",  "ON"],       # Wolfspeed — SiC

    # ── Networking / Data-Center Semis ───────────────────────
    "MXL":  ["MRVL", "COHR", "AVGO"],    # MaxLinear
    "SMTC": ["MRVL", "AVGO", "MXL"],     # Semtech
    "AXTI": ["COHR", "LITE", "MRVL"],    # AXT Inc — III-V substrates

    # ── Photonics / Optical ───────────────────────────────────
    "COHR": ["AXTI", "LITE", "GLW"],     # Coherent
    "LITE": ["COHR", "AXTI", "GLW"],     # Lumentum
    "LWLG": ["COHR", "AXTI", "LITE"],    # Lightwave Logic

    # ── Semiconductor Test & Equipment ───────────────────────
    "AEHR": ["COHU", "ONTO", "MKSI"],    # Aehr Test Systems
    "COHU": ["AEHR", "ONTO", "MKSI"],
    "ONTO": ["AEHR", "COHU", "MKSI"],    # Onto Innovation

    # ── China Tech / ADR ─────────────────────────────────────
    "BABA": ["JD",   "PDD",  "BIDU"],    # Alibaba
    "JD":   ["BABA", "PDD",  "BIDU"],    # JD.com
    "PDD":  ["BABA", "JD",   "BIDU"],    # Pinduoduo
    "BIDU": ["BABA", "JD",   "PDD"],     # Baidu

    # ── Chinese EV ────────────────────────────────────────────
    "LI":   ["NIO",  "XPEV", "TSLA"],    # Li Auto
    "NIO":  ["LI",   "XPEV", "TSLA"],
    "XPEV": ["LI",   "NIO",  "TSLA"],    # XPeng
    "HSAI": ["OUST", "AEYE", "LIDR"],    # Hesai — LiDAR

    # ── Biotech / Oncology ────────────────────────────────────
    "RLAY": ["RVMD", "KRYS", "RXDX"],    # Relay Therapeutics
    "RVMD": ["RLAY", "KRYS", "RXDX"],    # Revolution Medicines
    "KRYS": ["RLAY", "RVMD", "RXDX"],    # Krystal Biotech

    # ── Uranium / Nuclear ─────────────────────────────────────
    "URG":  ["UEC",  "DNN",  "NXE"],     # Ur-Energy
    "UEC":  ["URG",  "DNN",  "NXE"],     # Uranium Energy
    "DNN":  ["URG",  "UEC",  "NXE"],     # Denison Mines

    # ── Lithium / Battery Metals ──────────────────────────────
    "LAC":  ["ALB",  "SQM",  "LTHM"],    # Lithium Americas
    "ALB":  ["LAC",  "SQM",  "LTHM"],    # Albemarle
    "SQM":  ["LAC",  "ALB",  "LTHM"],    # SQM

    # ── AI Edge / Small-Cap Tech ──────────────────────────────
    "GRRR": ["SOUN", "BBAI", "CEVA"],    # Gorilla Technology
    "SOUN": ["GRRR", "BBAI", "CEVA"],    # SoundHound
    "BBAI": ["GRRR", "SOUN", "CEVA"],    # BigBear.ai
    "PENG": ["SMCI", "NVDA", "ANET"],    # Penguin Solutions

    # ── Drones / UAM ──────────────────────────────────────────
    "UMAC": ["JOBY", "ACHR", "LILM"],    # Unusual Machines
    "JOBY": ["UMAC", "ACHR", "LILM"],

    # ── Industrials ───────────────────────────────────────────
    "CNH":  ["DE",   "AGCO", "CNHI"],    # CNH Industrial
}

# Single-stock leverage/inverse ETF mapping
# Format: "STOCK": (LONG_ETF, LONG_MULT, SHORT_ETF, SHORT_MULT)
# SHORT_ETF = None when no liquid single-stock inverse exists → Inv row left blank
# All ETF volumes verified 2026-06-03 via yfinance. Threshold: >$2M daily.
SINGLE_STOCK_LEVERAGE = {
    # ── Mega-cap Tech (single-stock ETFs, all liquid) ─────────────────────────
    "NVDA":  ("NVDL",  "2x", "NVDS",  "1x"),   # $1.4B / verified
    "AAPL":  ("AAPU",  "2x", "AAPD",  "1x"),
    "TSLA":  ("TSLL",  "2x", "TSLS",  "1x"),   # $904M
    "AMZN":  ("AMZU",  "2x", "AMZD",  "1x"),
    "MSFT":  ("MSFU",  "2x", "MSFD",  "1x"),
    "META":  ("METU",  "2x", "METD",  "1x"),
    "GOOGL": ("GGLL",  "2x", "GGLS",  "1x"),
    "GOOG":  ("GGLL",  "2x", "GGLS",  "1x"),
    "NFLX":  ("NFXL",  "2x", None,    None),   # NFXL $22.7M / NFXS $0.6M (too low)
    "ORCL":  ("ORCX",  "2x", None,    None),   # ORCX $217M / no liquid inverse

    # ── Semiconductors (single-stock, volume-verified) ────────────────────────
    "AMD":   ("AMDL",  "2x", "AMDD",  "1x"),   # $327M / $36.6M
    "AVGO":  ("AVGX",  "2x", "AVS",   "1x"),   # $165.8M / $144.6M
    "MRVL":  ("MRVU",  "2x", None,    None),   # $256.9M / no liquid inverse
    "SMCI":  ("SMCX",  "2x", "SMCZ",  "1x"),   # $105.9M / $16.4M
    "INTC":  ("INTW",  "2x", None,    None),   # $254.7M / no liquid inverse
    "MU":    ("MUU",   "2x", "MUD",   "1x"),   # MU single-stock ETFs

    # ── Semiconductors (sector ETF — no liquid single-stock ETF) ─────────────
    "MXL":   ("SOXL",  "3x", "SOXS",  "3x"),
    "MCHP":  ("SOXL",  "3x", "SOXS",  "3x"),
    "NVTS":  ("SOXL",  "3x", "SOXS",  "3x"),
    "STM":   ("SOXL",  "3x", "SOXS",  "3x"),
    "SMTC":  ("SOXL",  "3x", "SOXS",  "3x"),
    "AEHR":  ("SOXL",  "3x", "SOXS",  "3x"),
    "AXTI":  ("SOXL",  "3x", "SOXS",  "3x"),
    "COHR":  ("SOXL",  "3x", "SOXS",  "3x"),
    "LWLG":  ("SOXL",  "3x", "SOXS",  "3x"),
    "STX":   ("SOXL",  "3x", "SOXS",  "3x"),
    "WDC":   ("SOXL",  "3x", "SOXS",  "3x"),
    "SNDK":  ("SOXL",  "3x", "SOXS",  "3x"),
    "WOLF":  ("SOXL",  "3x", "SOXS",  "3x"),
    "HSAI":  ("SOXL",  "3x", "SOXS",  "3x"),

    # ── Fintech / Trading Apps ────────────────────────────────────────────────
    "COIN":  ("CONL",  "2x", None,    None),   # $137M / no liquid inverse
    "HOOD":  ("ROBN",  "2x", "HOOZ",  "1x"),   # $47.4M / $1.3M
    "PLTR":  ("PLTU",  "2x", "PLTD",  "1x"),   # $195.7M / $220.7M

    # ── AI / Software ─────────────────────────────────────────────────────────
    "PLTR":  ("PLTU",  "2x", "PLTD",  "1x"),   # Palantir $195.7M / $220.7M
    "APP":   ("APPX",  "2x", None,    None),   # AppLovin $18.6M
    "RDDT":  ("RDTL",  "2x", None,    None),   # Reddit $12M
    "NBIS":  ("NEBX",  "2x", None,    None),   # Nebius $94.5M
    "UPST":  ("UPSX",  "2x", None,    None),   # Upstart $1.9M
    "HIMS":  ("HIMZ",  "2x", None,    None),   # Hims $16.2M
    "SOFI":  ("SOFX",  "2x", None,    None),   # SoFi $16M
    "TEM":   ("TEMT",  "2x", None,    None),   # Tempus AI $8.7M

    # ── Crypto / Bitcoin ──────────────────────────────────────────────────────
    "MSTR":  ("MSTU",  "2x", "SMST",  "1x"),   # $274.6M / $23M
    "MARA":  ("MRAL",  "2x", None,    None),   # $10.1M
    "RIOT":  ("RIOX",  "2x", None,    None),   # $16.2M

    # ── Quantum Computing ─────────────────────────────────────────────────────
    "IONQ":  ("IONX",  "2x", "IONZ",  "1x"),   # $115.7M / $39.7M
    "RGTI":  ("RGTX",  "2x", "RGTZ",  "1x"),   # $95.1M / $63.2M
    "QBTS":  ("QBTX",  "2x", "QBTZ",  "1x"),   # $70.2M / $21.2M
    "QUBT":  ("QUBX",  "2x", None,    None),   # $10.9M

    # ── Space / Aerospace ─────────────────────────────────────────────────────
    "RKLB":  ("RKLX",  "2x", "RKLZ",  "1x"),   # $227.7M / $35.4M
    "ASTS":  ("ASTX",  "2x", None,    None),   # $381.1M / no inverse

    # ── Nuclear / Clean Energy ────────────────────────────────────────────────
    "OKLO":  ("OKLL",  "2x", "OKLS",  "1x"),   # $224.5M / $16.4M
    "SMR":   ("SMU",   "2x", None,    None),   # $40.7M / no inverse
    "IREN":  ("IREX",  "2x", None,    None),   # $39.3M / no inverse

    # ── Healthcare / Pharma ───────────────────────────────────────────────────
    "LLY":   ("LLYX",  "2x", None,    None),   # $10.4M / LLYZ delisted
    "UNH":   ("UNHG",  "2x", None,    None),   # $14.6M

    # ── AI Small-Cap ──────────────────────────────────────────────────────────
    "BBAI":  ("BAIG",  "2x", None,    None),   # $3.3M
    "SOUN":  ("SOUX",  "2x", None,    None),   # $4.1M

    # ── China Tech ────────────────────────────────────────────────────────────
    "BABA":  ("BABX",  "2x", None,    None),   # $21M / no liquid inverse

    # ── EV / Auto ─────────────────────────────────────────────────────────────
    "LI":    ("TSLL",  "2x", "TSLS",  "1x"),   # Tesla proxy (most liquid EV ETF)

    # ── Cloud / Networking (sector ETF — no liquid single-stock) ─────────────
    "ANET":  ("TECL",  "3x", "TECS",  "3x"),
    "HPE":   ("TECL",  "3x", "TECS",  "3x"),
    "CSCO":  ("TECL",  "3x", "TECS",  "3x"),
    "IBM":   ("TECL",  "3x", "TECS",  "3x"),
    "GLW":   ("TECL",  "3x", "TECS",  "3x"),
    "NTAP":  ("TECL",  "3x", "TECS",  "3x"),
    "PENG":  ("TECL",  "3x", "TECS",  "3x"),
    "GRRR":  ("TECL",  "3x", "TECS",  "3x"),
    "UMAC":  ("TECL",  "3x", "TECS",  "3x"),

    # ── Biotech ───────────────────────────────────────────────────────────────
    "RLAY":  ("LABU",  "3x", "LABD",  "3x"),

    # ── Materials / Energy / Uranium ──────────────────────────────────────────
    "LAC":   ("REMX",  "1x", None,    None),
    "URG":   ("URA",   "1x", "URNM",  "1x"),
    "CNH":   ("DXJS",  "1x", None,    None),
}

# ETFs always liquid (>$2M daily) — skip live yfinance volume check
# All volumes verified 2026-06-03
ALWAYS_LIQUID_ETFS = {
    # Sector 3x ETFs
    "SOXL", "SOXS", "TECL", "TECS", "LABU", "LABD",
    "TQQQ", "SQQQ", "UPRO", "SPXU",
    # Single-stock — verified liquid
    "NVDL", "NVDS",                    # NVIDIA  $1.4B / verified
    "TSLL", "TSLS",                    # Tesla   $904M
    "METU", "METD",                    # Meta
    "AAPU", "AAPD",                    # Apple
    "AMZU", "AMZD",                    # Amazon
    "MSFU", "MSFD",                    # Microsoft
    "GGLL", "GGLS",                    # Alphabet
    "AMDL", "AMDD",                    # AMD     $327M / $36.6M
    "PLTU", "PLTD",                    # Palantir $195.7M / $220.7M
    "CONL",                            # Coinbase $137M
    "MSTU", "SMST",                    # MicroStrategy $274.6M / $23M
    "NFXL",                            # Netflix $22.7M (inverse NFXS too low)
    "AVGX", "AVS",                     # Broadcom $165.8M / $144.6M
    "MRVU",                            # Marvell  $256.9M
    "SMCX",                            # Super Micro $105.9M
    "INTW",                            # Intel $254.7M
    "IONX", "IONZ",                    # IonQ $115.7M / $39.7M
    "RGTX", "RGTZ",                    # Rigetti $95.1M / $63.2M
    "QBTX", "QBTZ",                    # D-Wave $70.2M / $21.2M
    "RKLX", "RKLZ",                    # Rocket Lab $227.7M / $35.4M
    "ASTX",                            # AST SpaceMobile $381.1M
    "OKLL",                            # Oklo $224.5M
    "SMU",                             # NuScale $40.7M
    "IREX",                            # IREN $39.3M
    "ROBN",                            # Robinhood $47.4M
    "NEBX",                            # Nebius $94.5M
    "BABX",                            # Alibaba $21M
    "ORCX",                            # Oracle $217M
    # Previously below $20M but above $2M — now included
    "SMCZ",                            # SMCI inverse $16.4M
    "OKLS",                            # Oklo inverse $16.4M
    "HOOZ",                            # Hood inverse $1.3M  ← borderline, include
    "RDTL",                            # Reddit $12M
    "APPX",                            # AppLovin $18.6M
    "MRAL",                            # Marathon Digital $10.1M
    "RIOX",                            # Riot $16.2M
    "SOFX",                            # SoFi $16M
    "HIMZ",                            # Hims $16.2M
    "TEMT",                            # Tempus AI $8.7M
    "UNHG",                            # UnitedHealth $14.6M
    "UPSX",                            # Upstart $1.9M  ← borderline
    "LLYX",                            # Eli Lilly $10.4M
    "QUBX",                            # Quantum Computing $10.9M
    "BAIG",                            # BigBear.ai $3.3M
    "SOUX",                            # SoundHound $4.1M
}


def get_peer_stocks(ticker: str) -> list:
    """Get curated peer stocks for a given ticker."""
    return PEER_MAPPING.get(ticker.upper(), [])


def get_leverage_inverse_stocks(ticker: str) -> dict:
    """
    Return leverage (long) and inverse (short) ETFs with multiplier labels.
    Returns: {
      "long":  [{"ticker": ETF, "mult": "3x"}],
      "short": [{"ticker": ETF, "mult": "3x"}]
    }
    """
    t = ticker.upper()
    if t not in SINGLE_STOCK_LEVERAGE:
        return {"long": [], "short": []}

    long_etf, long_mult, short_etf, short_mult = SINGLE_STOCK_LEVERAGE[t]

    def _qualifies(etf):
        if not etf:
            return False
        if etf in ALWAYS_LIQUID_ETFS:
            return True
        try:
            vol = _fetch_dollar_volume(etf)
            return bool(vol and vol > 2_000_000)
        except Exception as e:
            logger.debug(f"  Vol check failed for {etf}: {e}")
            return False

    return {
        "long":  [{"ticker": long_etf,  "mult": long_mult}]  if _qualifies(long_etf)  else [],
        "short": [{"ticker": short_etf, "mult": short_mult}] if short_etf and _qualifies(short_etf) else [],
    }


def _fetch_dollar_volume(ticker: str) -> float | None:
    """Fetch latest dollar volume via yfinance (1-day, most recent close × volume)."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period="2d", interval="1d")
        if hist.empty:
            return None
        row = hist.iloc[-1]
        close = float(row["Close"])
        vol   = float(row["Volume"])
        return close * vol if close > 0 and vol > 0 else None
    except Exception:
        return None

# ──────────────────────────────────────────────────────────────
# RC1: SEC EDGAR 8-K Detection — free public API, no key required
# ──────────────────────────────────────────────────────────────

_CIK_MAP: dict = {}   # ticker → zero-padded CIK string, loaded once per run

_EDGAR_HEADERS = {"User-Agent": "PowerTheme/1.0 sean.poems@gmail.com"}

# 8-K item numbers → catalyst category (priority order: Earnings > Contract > Others)
_8K_ITEM_TO_CATEGORY = {
    "2.02": "Earnings",                  # Results of Operations
    "8.02": "Earnings",                  # Results of Operations (older)
    "1.01": "New Contract/Partnership",  # Entry into Material Agreement
    "5.02": "Others",                    # Director/Officer Change
    "8.01": "Others",                    # Other Events
    "1.05": "Others",                    # Cybersecurity Incident
    "2.05": "Others",                    # Exit Activities / Restructuring
    "2.06": "Others",                    # Material Impairment
}
_8K_ITEM_LABELS = {
    "2.02": "Earnings/Financial Results",
    "8.02": "Earnings/Financial Results",
    "1.01": "Material Contract or Agreement",
    "5.02": "Leadership Change (Director/Officer)",
    "8.01": "Other Material Event",
    "1.05": "Cybersecurity Incident",
    "2.05": "Restructuring / Exit Activity",
    "2.06": "Material Impairment",
}


def _load_cik_map() -> None:
    """Download SEC ticker→CIK mapping once per process. No API key required."""
    global _CIK_MAP
    if _CIK_MAP:
        return
    try:
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_EDGAR_HEADERS, timeout=15,
        )
        resp.raise_for_status()
        for item in resp.json().values():
            t   = (item.get("ticker") or "").upper()
            cik = str(item.get("cik_str", "")).zfill(10)
            if t and cik:
                _CIK_MAP[t] = cik
        logger.info(f"  EDGAR CIK map loaded: {len(_CIK_MAP)} tickers")
    except Exception as e:
        logger.warning(f"  EDGAR CIK map load failed: {e}")


def fetch_edgar_8k(ticker: str, days: int = 2) -> dict | None:
    """
    Check SEC EDGAR for 8-K filings within the last `days` days.
    Returns filing metadata + inferred catalyst category, or None.
    No API key required — EDGAR is a free government public API.
    """
    try:
        cik = _CIK_MAP.get(ticker.upper())
        if not cik:
            return None
        resp = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=_EDGAR_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        data   = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms       = recent.get("form", [])
        dates       = recent.get("filingDate", [])
        accessions  = recent.get("accessionNumber", [])
        items_field = recent.get("items", [])
        primary_doc = recent.get("primaryDocument", [])

        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        for i, (form, date) in enumerate(zip(forms, dates)):
            if form in ("8-K", "8-K/A") and date >= cutoff:
                acc_clean = accessions[i].replace("-", "") if i < len(accessions) else ""
                cik_int   = int(cik)
                doc       = primary_doc[i] if i < len(primary_doc) else ""
                url       = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{doc}"

                # Parse comma-separated item numbers (e.g. "2.02, 9.01")
                raw_items = items_field[i] if i < len(items_field) else ""
                item_nums = [x.strip() for x in str(raw_items).split(",") if x.strip()]

                # Highest-priority category from item numbers
                category, label = "Others", "8-K Material Event"
                for item in item_nums:
                    cat = _8K_ITEM_TO_CATEGORY.get(item)
                    lbl = _8K_ITEM_LABELS.get(item, f"Item {item}")
                    if cat == "Earnings":
                        category, label = cat, lbl
                        break
                    if cat == "New Contract/Partnership" and category != "Earnings":
                        category, label = cat, lbl
                    elif not category or category == "Others":
                        category, label = (cat or "Others"), lbl

                logger.info(f"  EDGAR 8-K: {ticker} filed {form} on {date} — {label} (items: {item_nums})")
                return {
                    "filing_date": date,
                    "form":        form,
                    "items":       item_nums,
                    "category":    category,
                    "label":       label,
                    "url":         url,
                }
        return None
    except Exception as e:
        logger.debug(f"  EDGAR 8-K check failed for {ticker}: {e}")
        return None


# ──────────────────────────────────────────────────────────────
# RC2: Article Body Snippet Scraping
# ──────────────────────────────────────────────────────────────

def fetch_article_snippet(url: str, max_chars: int = 450) -> str | None:
    """
    Scrape the first ~450 chars of article body text for Gemini context.
    Skips Google News redirect URLs (too many hops); works best on direct links.
    Returns None on paywall, error, or insufficient content.
    """
    if not url or "news.google.com" in url:
        return None
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(url, timeout=6, allow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        })
        if not resp.ok or "text/html" not in resp.headers.get("Content-Type", ""):
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer",
                         "aside", "form", "iframe", "noscript", "button"]):
            tag.decompose()
        # Try semantic selectors first, then fall back to raw <p> tags
        for selector in [
            "article", "[class*='article-body']", "[class*='story-body']",
            "[class*='post-content']", "[class*='entry-content']", "main",
        ]:
            els = soup.select(selector)
            if els:
                text = " ".join(e.get_text(" ", strip=True) for e in els[:4])
                text = " ".join(text.split())
                if len(text) >= 120:
                    return text[:max_chars]
        # Raw paragraph fallback
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")
                 if len(p.get_text(strip=True)) > 50]
        if paras:
            text = " ".join(text.split())  # collapse whitespace
            text = " ".join(paras[:3])
            if len(text) >= 120:
                return text[:max_chars]
        return None
    except Exception:
        return None


def _fetch_snippets_parallel(headlines: list[dict]) -> None:
    """In-place: add 'snippet' key to top 3 headlines that have scrapable URLs."""
    targets = [(i, h) for i, h in enumerate(headlines[:3]) if h.get("url")]
    if not targets:
        return
    with ThreadPoolExecutor(max_workers=3) as pool:
        future_to_idx = {pool.submit(fetch_article_snippet, h["url"]): i
                         for i, h in targets}
        try:
            for future in as_completed(future_to_idx, timeout=15):
                idx = future_to_idx[future]
                try:
                    snippet = future.result()
                    if snippet:
                        headlines[idx]["snippet"] = snippet
                except Exception:
                    pass
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────
# RC3: Ticker Mention Filter
# ──────────────────────────────────────────────────────────────

def _mentions_ticker(title: str, ticker: str) -> bool:
    """
    Returns True if the headline title directly names this stock
    (ticker symbol or NASDAQ:/NYSE: prefix pattern).
    Used to deprioritise articles that mention the stock only tangentially.
    """
    title_upper = title.upper()
    t = ticker.upper()
    # Direct symbol match (word-boundary style)
    if re.search(rf'\b{re.escape(t)}\b', title_upper):
        return True
    # "NASDAQ: NBIS" or "(NBIS)" style
    if f":{t}" in title_upper or f"({t})" in title_upper:
        return True
    return False


def _is_opinion_article(title: str, source: str = "") -> bool:
    """
    Filter out non-company-specific news. Keep only hard news directly about the
    scanned ticker: earnings, guidance, contracts, regulatory, leadership changes.
    Reject: opinion, predictions, roundup lists, multi-company articles, data commentary.
    """
    title_lower = title.lower()
    source_lower = source.lower()

    # 1. Reject known opinion/commentary sources
    for opinion_src in OPINION_SOURCES:
        if opinion_src in source_lower:
            return True

    # 2. Reject if source name appears inline at end of title (e.g. "... - Barron's")
    if " - " in title:
        inline_source = title.split(" - ")[-1].lower().strip()
        for opinion_src in OPINION_SOURCES:
            if opinion_src in inline_source:
                return True

    # 3. Reject roundup / multi-stock list articles
    for kw in ROUNDUP_KEYWORDS:
        if kw in title_lower:
            return True

    # 4. Reject roundup/list articles — commas OR semicolons separating multiple companies
    #    e.g. "5 big analyst moves: Intel upgrade; SpaceX Buy; AMD target raised"
    if title.count(",") >= 2 or title.count(";") >= 1:
        return True
    # Also reject titles starting with a number + "big/top/best/worst/key" (numbered lists)
    if re.match(r"^\d+\s+(big|top|best|worst|key|major|notable|important)", title_lower):
        return True

    # 5. Reject opinion keyword phrases in title
    for keyword in OPINION_KEYWORDS:
        if f" {keyword} " in f" {title_lower} " or title_lower.startswith(keyword):
            return True

    return False

def filter_headlines(headlines: list[dict], ticker: str = "",
                     max_headlines: int = 5, skip_opinion: bool = True) -> list[dict]:
    """
    Filter headlines: remove opinion/roundup articles, keep hard news catalysts.
    Headlines that directly name the ticker are sorted to the top.
    Returns up to max_headlines of quality, fact-based news.
    """
    if not skip_opinion:
        return headlines[:max_headlines]

    filtered = []
    for headline in headlines:
        title  = headline.get("title", "")
        source = headline.get("source", "")
        if not _is_opinion_article(title, source):
            filtered.append(headline)
        else:
            logger.debug(f"  Skipped opinion: {title[:60]} ({source})")

    # RC3: Sort so headlines that name the ticker come first — better Gemini signal
    if ticker:
        filtered.sort(key=lambda h: 0 if _mentions_ticker(h.get("title", ""), ticker) else 1)

    return filtered[:max_headlines]

# ──────────────────────────────────────────────────────────────
# TradingView Screener
# ──────────────────────────────────────────────────────────────

def fetch_gappers() -> list[dict]:
    """Fetch pre-market gap-up stocks from TradingView screener."""
    try:
        from tradingview_screener import Query, col
        (_, df) = (
            Query()
            .select(
                "name", "close", "premarket_change", "premarket_volume",
                "market_cap_basic", "average_volume_10d_calc", "relative_volume_intraday|5"
            )
            .where(
                col("premarket_change") >= 5,
                col("premarket_volume") >= 50000,
                col("market_cap_basic") >= 5e8,
                col("average_volume_10d_calc") >= 100000,
                col("close") >= 2,
            )
            .order_by("premarket_change", ascending=False)
            .limit(100)
            .get_scanner_data()
        )
        if df is None or df.empty:
            return []
        results = []
        for _, row in df.iterrows():
            rvol     = float(row.get("relative_volume_intraday|5") or 0)
            price    = round(float(row.get("close") or 0), 2)
            avg_vol  = int(row.get("average_volume_10d_calc") or 0)
            avg_dvol = round(price * avg_vol)
            # Minimum avg $ vol: $5M (UI filter handles stricter thresholds)
            if avg_dvol < 5_000_000:
                continue
            results.append({
                "ticker":       str(row.get("name", "")),
                "price":        price,
                "gap_pct":      round(float(row.get("premarket_change") or 0), 2),
                "pm_volume":    int(row.get("premarket_volume") or 0),
                "avg_vol_10d":  avg_vol,
                "avg_dollar_vol": avg_dvol,
                "mkt_cap":      int(row.get("market_cap_basic") or 0),
                "rvol":         round(rvol, 2),
            })
        return results
    except Exception as e:
        logger.error(f"TradingView screener failed: {e}")
        return []


# ──────────────────────────────────────────────────────────────
# Finviz Theme Map (heatmap-based ticker → theme)
# ──────────────────────────────────────────────────────────────

def fetch_finviz_theme_map() -> dict:
    """Scrape Finviz themes heatmap to build {ticker: theme_name} mapping."""
    ticker_theme = {}
    try:
        url = "https://finviz.com/map.ashx?t=themes"
        resp = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://finviz.com/",
        })
        resp.raise_for_status()
        html = resp.text

        # Finviz embeds map data as a JS variable:
        # var d=[{t:"NVDA",n:"NVIDIA",v:5.2,g:"Artificial Intelligence"}, ...]
        patterns = [
            r'var\s+d\s*=\s*(\[.+?\])\s*;',
            r'var\s+mapData\s*=\s*(\[.+?\])\s*;',
        ]
        raw = None
        for pattern in patterns:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                raw = m.group(1)
                break

        if raw:
            # Normalize JS unquoted keys to valid JSON
            fixed = re.sub(r'(?<=[{,\[]\s*)([a-zA-Z_]\w*)\s*:', r'"\1":', raw)
            try:
                items = json.loads(fixed)
                for item in items:
                    t = item.get("t") or item.get("ticker", "")
                    g = item.get("g") or item.get("group", "") or item.get("theme", "")
                    if t and g:
                        ticker_theme[t] = g
                logger.info(f"  Finviz theme map: {len(ticker_theme)} tickers mapped")
            except json.JSONDecodeError as je:
                logger.warning(f"  Finviz theme map JSON parse failed: {je}")
        else:
            logger.warning("  Finviz theme map: data pattern not found in page HTML")
    except Exception as e:
        logger.warning(f"  Finviz theme map fetch failed: {e}")
    return ticker_theme


# ──────────────────────────────────────────────────────────────
# Finviz Fundamentals (float, short interest, daily %)
# ──────────────────────────────────────────────────────────────

def fetch_finviz_data(ticker: str) -> dict:
    """Fetch Float, Short Interest %, Daily %, and Prev Close from Finviz quote page."""
    result = {"float_shares": None, "short_float": None, "daily_pct": None, "prev_close": None}
    try:
        from bs4 import BeautifulSoup
        url = f"https://finviz.com/quote.ashx?t={ticker}&ty=c&p=d&b=1"
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # Finviz splits the snapshot data across several <table class="snapshot-table2">
        # chunks now — reading only the first one silently dropped every field below
        # "IPO" (Float, Short Float, Change, Prev Close all live in later chunks).
        tables = soup.find_all("table", class_="snapshot-table2")
        if not tables:
            return result
        cells = [td for t in tables for td in t.find_all("td")]
        for i in range(0, len(cells) - 1, 2):
            label = cells[i].get_text(strip=True)
            value = cells[i + 1].get_text(strip=True)
            if label in ("Shs Float", "Float"):
                result["float_shares"] = value
            elif label == "Short Float":
                result["short_float"] = value
            elif label in ("Change %", "Change"):
                try:
                    result["daily_pct"] = float(value.replace("%", "").replace("+", ""))
                except ValueError:
                    result["daily_pct"] = None
            elif label == "Prev Close":
                try:
                    result["prev_close"] = float(value.replace(",", ""))
                except ValueError:
                    result["prev_close"] = None
        return result
    except Exception as e:
        logger.warning(f"  Finviz data failed for {ticker}: {e}")
        return result


# ──────────────────────────────────────────────────────────────
# Ticker Fundamentals: ADR% + Last Earnings Date (yfinance)
# ──────────────────────────────────────────────────────────────

def fetch_ticker_fundamentals(ticker: str) -> dict:
    """Fetch ADR%(20d) and last earnings date in one yfinance call."""
    result = {"adr_pct": None, "last_earnings_date": None}
    try:
        import yfinance as yf
        from datetime import date as _date
        t = yf.Ticker(ticker)

        # ADR% — avg of (High-Low)/Close over last 20 sessions
        hist = t.history(period="25d", interval="1d", auto_adjust=True)
        if len(hist) >= 10:
            adr = ((hist["High"] - hist["Low"]) / hist["Close"] * 100).tail(20).mean()
            result["adr_pct"] = round(float(adr), 2)

        # Last earnings date
        try:
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                today = _date.today()
                past  = ed[ed.index.date < today]
                if not past.empty:
                    result["last_earnings_date"] = past.index[0].strftime("%Y-%m-%d")
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"  yfinance fundamentals failed for {ticker}: {e}")
    return result


# ──────────────────────────────────────────────────────────────
# Hard Gates
# ──────────────────────────────────────────────────────────────

_HIGH_IMPACT_CATS = {"Earnings", "New Contract/Partnership", "FDA"}

def _compute_gates(stock: dict) -> tuple[int, dict, bool]:
    """
    Evaluate 5 hard gates against a stock dict.
    rs_52w defaults to True (pass) when not present in TV data.
    Returns (gates_passed, gates_detail, meets_all_gates).
    """
    gates = {
        "gate_rs":         stock.get("rs_52w") is None or stock.get("rs_52w", 0) > 85,
        "gate_price":      (stock.get("price") or 0) > 12,
        "gate_dollar_vol": (stock.get("avg_dollar_vol") or 0) > 100_000_000,
        "gate_mkt_cap":    (stock.get("mkt_cap") or 0) > 2_000_000_000,
        "gate_adr":        (stock.get("adr_pct") or 0) >= 4.0,
    }
    passed = sum(gates.values())
    return passed, gates, passed == 5


def _compute_tier(grade: str, category: str, meets_all_gates: bool, hypothesis: str) -> tuple[str, str]:
    """
    Map grade + context to a tier label.
    Priority: T1 → T2 → Fail → T3.
    """
    if grade in ("A+", "A") and category in _HIGH_IMPACT_CATS:
        return "T1", "Major Catalyst"
    high_conviction = "High Conviction" in hypothesis or grade == "A+"
    if grade in ("A", "B") and high_conviction:
        return "T2", "Strong Catalyst"
    if grade == "C" and not meets_all_gates:
        return "Fail", "Excluded"
    return "T3", "Minor Catalyst"


def _apply_technical_floor(analysis: dict, avg_dollar_vol: float, adr_pct: float | None) -> tuple[dict, str]:
    """Enforce the Hard Technical Floor on grade and return (updated_analysis, technical_status)."""
    dvol_m  = (avg_dollar_vol or 0) / 1_000_000
    adr     = adr_pct or 0.0
    grade   = analysis.get("grade", "C")

    failures = []
    if dvol_m < 100:
        failures.append(f"Avg $Vol ${dvol_m:.0f}M < $100M")
    if adr < 4:
        failures.append(f"ADR {adr:.1f}% < 4%")

    technical_status = "Pass" if not failures else "Fail (" + ", ".join(failures) + ")"

    # Hard grade cap
    if dvol_m < 100 or adr < 2:
        analysis["grade"] = "C"
    elif adr < 4 and grade in ("A+", "A"):
        analysis["grade"] = "B"

    return analysis, technical_status


# ──────────────────────────────────────────────────────────────
# Google News RSS + Finviz News
# ──────────────────────────────────────────────────────────────

def _fetch_google_news(ticker: str, cutoff, limit: int = 5) -> list[dict]:
    """Fetch up to `limit` headlines from Google News RSS within the last 24h."""
    try:
        from xml.etree import ElementTree as ET_xml
        from email.utils import parsedate_to_datetime
        url = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET_xml.fromstring(resp.content)
        results = []
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            link  = item.findtext("link", "")
            pub_date_str = item.findtext("pubDate", "")
            if not title:
                continue
            try:
                pub_dt = parsedate_to_datetime(pub_date_str)
                if pub_dt < cutoff:
                    continue
                date_label = pub_dt.strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                continue
            results.append({"title": title, "date": date_label, "source": "Google News", "url": link})
            if len(results) >= limit:
                break
        return results
    except Exception as e:
        logger.warning(f"  Google News fetch failed for {ticker}: {e}")
        return []


def _fetch_finviz_news(ticker: str, cutoff, limit: int = 5) -> list[dict]:
    """Fetch up to `limit` headlines from Finviz quote page news table within the last 24h."""
    try:
        from bs4 import BeautifulSoup
        url = f"https://finviz.com/quote.ashx?t={ticker}&p=d"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", id="news-table")
        if not table:
            return []
        results = []
        last_date = None
        today = datetime.now(timezone.utc).date()
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            date_str = cells[0].get_text(strip=True)
            title_tag = cells[1].find("a")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            # Finviz date cell: either "Mar-20-26 07:30AM" or just "07:30AM" (same day)
            if len(date_str) > 8:
                last_date = date_str
            full_date_str = last_date or date_str
            try:
                from datetime import timedelta
                import re
                # Parse "Mar-20-26 07:30AM" or "Mar-20-2026 07:30AM"
                match = re.match(r"(\w{3}-\d{2}-\d{2,4})\s+(\d{1,2}:\d{2}(?:AM|PM))", full_date_str)
                if not match:
                    continue
                date_part, time_part = match.group(1), match.group(2)
                year_part = date_part.split("-")[2]
                if len(year_part) == 2:
                    date_part = date_part[:-2] + "20" + year_part
                pub_dt = datetime.strptime(f"{date_part} {time_part}", "%b-%d-%Y %I:%M%p")
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
                date_label = pub_dt.strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                continue
            link = title_tag.get("href", "") if title_tag else ""
            results.append({"title": title, "date": date_label, "source": "Finviz", "url": link})
            if len(results) >= limit:
                break
        return results
    except Exception as e:
        logger.warning(f"  Finviz news fetch failed for {ticker}: {e}")
        return []


def verify_catalyst_accuracy(
    ticker: str,
    category: str,
    reasoning: str,
    google_headlines: list[dict],
    finviz_headlines: list[dict],
) -> dict:
    """Second Gemini call: Skeptical Auditor cross-checks facts across two independent sources."""
    fallback = {
        "status": "Unconfirmed",
        "confidence_score": 50,
        "sources_consulted": ["Finviz", "Google News"],
        "primary_claim": reasoning,
        "discrepancy_note": "",
    }
    if not GEMINI_API_KEY:
        return fallback

    def fmt(items):
        return "\n".join(f"  [{i+1}] ({h['source']}) {h['title']}" for i, h in enumerate(items)) or "  (none)"

    prompt = f"""You are a Skeptical Auditor fact-checking pre-market news for {ticker}.

PRIMARY CATALYST CLAIM (from initial analysis):
Category: {category}
Summary: {reasoning}

SOURCE A — Finviz News:
{fmt(finviz_headlines)}

SOURCE B — Google News:
{fmt(google_headlines)}

Your job:
1. Identify the single most specific factual claim (a dollar amount, percentage, date, approval decision, etc.) in the catalyst summary.
2. Check whether BOTH sources confirm this claim with matching specifics. Exact number agreement is required for "Verified".
3. If the news mentions a specific number (e.g. "$1.6T deal" or "beat by 22%"), look carefully — is it consistent across sources, or could it be an old headline, typo, or exaggeration?
4. If only ONE source mentions the claim or sources give conflicting numbers, mark as Discrepancy.
5. If neither source contains enough detail to confirm the claim, mark as Unconfirmed.

Respond ONLY with this JSON (no extra text):
{{"status": "Verified|Discrepancy|Unconfirmed", "confidence_score": <0-100>, "sources_consulted": ["Finviz", "Google News"], "primary_claim": "<the specific claim being verified>", "discrepancy_note": "<only populated when status=Discrepancy, e.g. Source A says $200M, Source B says $20M>"}}"""

    try:
        from google import genai
        from google.genai import types as _gt
        client = genai.Client(api_key=GEMINI_API_KEY)
        _thinking_cfg = _gt.GenerateContentConfig(
            thinking_config=_gt.ThinkingConfig(thinking_budget=1024)
        )
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt, config=_thinking_cfg)
        text = response.text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
        result = json.loads(text)
        status = result.get("status", "Unconfirmed")
        if status not in ("Verified", "Discrepancy", "Unconfirmed"):
            status = "Unconfirmed"
        return {
            "status": status,
            "confidence_score": max(0, min(100, int(result.get("confidence_score", 50)))),
            "sources_consulted": result.get("sources_consulted", ["Finviz", "Google News"]),
            "primary_claim": result.get("primary_claim", reasoning),
            "discrepancy_note": result.get("discrepancy_note", ""),
        }
    except Exception as e:
        logger.warning(f"  Verification failed for {ticker}: {e}")
        return fallback


def fetch_news_headlines(ticker: str, skip_opinion: bool = True) -> list[dict]:
    """Fetch news headlines from the last 24h via Google News RSS + Finviz, deduped and sorted.

    Args:
        ticker: Stock symbol
        skip_opinion: If True, filter out opinion/analysis articles (keep hard news only)

    Returns:
        List of filtered headlines, newest first
    """
    import datetime as dt
    cutoff = datetime.now(timezone.utc) - dt.timedelta(hours=24)
    google = _fetch_google_news(ticker, cutoff, limit=5)
    finviz = _fetch_finviz_news(ticker, cutoff, limit=5)
    # Merge, deduplicate by title similarity, sort newest first
    seen = set()
    merged = []
    for item in google + finviz:
        key = item["title"][:60].lower()
        if key not in seen:
            seen.add(key)
            merged.append(item)
    merged.sort(key=lambda x: x["date"], reverse=True)

    # Filter opinion articles if requested
    if skip_opinion:
        merged = filter_headlines(merged, max_headlines=10, skip_opinion=True)
        logger.info(f"  {ticker}: {len(merged)} hard-news headlines after opinion filter")

    return merged[:8]


# ──────────────────────────────────────────────────────────────
# Gemini Analysis
# ──────────────────────────────────────────────────────────────

CATEGORIES = ["Earnings", "Upgrade", "FDA", "Thematic Narratives", "Government Policy", "New Contract/Partnership", "Institutional Buying", "Insider Buying", "Others"]

HYPOTHESIS_RULES = {
    "Earnings":                 ("High Conviction (Gap & Go)",  "Watch for 5-min ORB above PM High."),
    "New Contract/Partnership": ("High Conviction (Gap & Go)",  "Watch for 5-min ORB above PM High."),
    "Thematic Narratives":      ("Medium Conviction (RS Hold)", "Look for dip-buy at 9-EMA/VWAP."),
    "Government Policy":        ("Medium Conviction (RS Hold)", "Look for dip-buy at 9-EMA/VWAP."),
    "Institutional Buying":     ("Medium Conviction (RS Hold)", "Large fund accumulation; watch for continuation."),
    "Insider Buying":           ("Medium Conviction (RS Hold)", "Insider conviction signal; look for base breakout."),
    "Upgrade":                  ("Caution (Fade Candidate)",    "Low institutional conviction; likely gap-fill."),
    "FDA":                      ("High Risk (Volatility Trap)", "Expect 2nd-half mean reversion; avoid open chase."),
    "Others":                   ("Medium Conviction (RS Hold)", "Monitor price action at open."),
}

ANALYSIS_FORMAT_INSTRUCTIONS = """
Return analysis_details as 2-3 structured sections using EXACTLY this format:

• **[Section Title]**
[Body: 2-4 sentences. Extract and bold specific facts from the headlines — numbers, percentages, product names, deal values, company names. Use **bold** for all key figures and outcomes. e.g. "HPE reported **solid Q2 earnings**, with a **record backlog** and **booming AI server business**, driving the stock **+29% premarket**." Never write generic filler — every sentence must contain a specific fact from the headlines.]

• **[Section Title 2]**
[Body: Forward implication — what does this catalyst mean for price momentum, competitive positioning, or sector rotation? Be specific, not vague.]

Section titles to use by category:
- Earnings: "The Beats (Surprise Factor)" | "The Growth (Momentum)" | "The Guide"
- Upgrade: "The Firm & Rating" | "The Thesis"
- FDA: "The Drug" | "The Significance" | "Risk Profile"
- Thematic Narratives: "The Narrative" | "Explosiveness"
- Government Policy: "The Policy" | "Direct Impact"
- New Contract/Partnership: "Impact" | "Strategic Value"
- Institutional Buying / Insider Buying: "The Buyer" | "Conviction Signal"
- Others: "What Happened" | "Key Consideration"

Rules: No emojis. No markdown headers (#, ##). Start every section with • **Title** on its own line. Separate sections with a blank line. Every claim must come from the provided headlines — never invent numbers.
"""


def analyze_with_gemini(
    ticker: str,
    headlines: list[str],
    rvol: float,
    last_earnings_date: str | None = None,
    avg_dollar_vol: float = 0,
    adr_pct: float | None = None,
    edgar_event: dict | None = None,
) -> dict:
    """Use Gemini 2.5 Flash — Momentum Catalyst Intelligence with Hard Technical Floor."""
    if not GEMINI_API_KEY:
        return _fallback_analysis(ticker, headlines, rvol)

    dvol_m = (avg_dollar_vol or 0) / 1_000_000
    adr    = adr_pct or 0.0

    if not headlines and not edgar_event:
        return {
            "category":        "Others",
            "theme":           "Technical / Flow",
            "reasoning":       "No immediate fundamental catalyst found; price action likely driven by technical breakout or institutional flow.",
            "hypothesis":      "Caution (Gap & Trap Risk) — Avoid open chase; wait for 15-min base confirmation.",
            "conviction":      25,
            "grade":           "C",
            "finviz_theme":    "—",
            "analysis_source": "no_news",
            "analysis_detail": {"catalyst": "No fundamental catalyst identified in the last 24 hours.", "impact": "Speculative. Significant price move on no news suggests Low Float squeeze or technical stop-running. High risk of Gap and Trap without fundamental backing."},
            "analysis_details": "• **What Happened**\nNo news catalyst identified within the last 24 hours. The gap is likely technical or flow-driven.\n\n• **Key Consideration**\nLow Float squeezes and overnight program flows can create sizable gaps with no fundamental backing. These are typically Gap and Trap setups — the stock often fades to fill the gap by end of day.",
            "peer_tickers":    get_peer_stocks(ticker),
            "leverage_etfs":   get_leverage_inverse_stocks(ticker).get("long", []),
            "inverse_etfs":    get_leverage_inverse_stocks(ticker).get("short", []),
        }
    try:
        from google import genai
        from google.genai import types as _gt
        client = genai.Client(api_key=GEMINI_API_KEY)

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        def _clean_title(h):
            """Strip trailing ' - Source Name' from headline to prevent Gemini echoing it."""
            title = h['title'] if isinstance(h, dict) else h
            if ' - ' in title:
                # Remove everything after the last ' - ' if it looks like a source name
                # (source names are short, < 40 chars, no numbers/dates)
                parts = title.rsplit(' - ', 1)
                if len(parts[1]) < 40 and not any(c.isdigit() for c in parts[1]):
                    title = parts[0].strip()
            return title

        # RC2: Format headlines with article snippets when available
        def _fmt_headline(h):
            if not isinstance(h, dict):
                return f"- {h}"
            title   = _clean_title(h)
            date    = h.get("date", "")
            source  = h.get("source", "SEC EDGAR" if "sec.gov" in h.get("url","") else h.get("source",""))
            snippet = h.get("snippet", "")
            line    = f"- [{date}] ({source}) {title}"
            if snippet:
                line += f"\n  Article excerpt: \"{snippet}\""
            return line

        headlines_text = "\n".join(_fmt_headline(h) for h in headlines)

        # RC5: SEC EDGAR verified event block (highest-priority context)
        edgar_block = ""
        edgar_category_override = ""
        if edgar_event:
            edgar_block = f"""
═══ VERIFIED SEC FILING — HIGHEST PRIORITY SOURCE ═══
{ticker} filed a {edgar_event['form']} with the SEC on {edgar_event['filing_date']}.
Event type: {edgar_event['label']}
Item numbers: {', '.join(edgar_event['items'])}
SEC URL: {edgar_event['url']}

This is an official government disclosure — it overrides any ambiguity in news headlines.
Use this to set the category. For Item 2.02 (Earnings), extract actual figures from headlines.
"""
            edgar_category_override = edgar_event["category"]

        earnings_note = ""
        if last_earnings_date:
            from datetime import date as _date
            last_dt  = _date.fromisoformat(last_earnings_date)
            days_ago = (_date.today() - last_dt).days
            if days_ago > 5:
                earnings_note = f"\nIMPORTANT: {ticker}'s last earnings report was on {last_earnings_date} ({days_ago} days ago). Do NOT classify as Earnings — that is too old to be today's catalyst."
            else:
                earnings_note = f"\nNote: {ticker}'s last earnings report was on {last_earnings_date} ({days_ago} days ago) — recent enough to be a valid Earnings catalyst."

        prompt = f"""You are a Senior Momentum Equity Analyst. Today is {today_str}.{earnings_note}
{edgar_block}
Technical context for {ticker}:
  Avg $ Vol (20d): ${dvol_m:.0f}M  |  ADR% (20d): {adr:.1f}%  |  RVOL: {rvol:.1f}x

News headlines (last 24h):
{headlines_text}

═══ STEP 1: CATALYST VERIFICATION ═══
Before grading, verify a specific catalyst occurred in the last 24h.

UNKNOWN CATALYST RULE — If no specific fundamental news found:
  category: "Others"  |  theme: "Technical / Flow"  |  grade: "C"
  reasoning: "No immediate fundamental catalyst found; likely technical breakout or institutional flow."
  analysis_detail: "Catalyst: Unknown | Impact: Speculative. Significant move on no news suggests Low Float squeeze. High risk of Gap and Trap."

SYMPATHY MOVE RULE — If ticker moves because a sector leader (e.g. NVDA) reported news:
  theme: "Sector Sympathy"  |  grade: "B" or "C"
  reasoning: "Moving in sympathy with [Leader] following [specific event]."
  analysis_detail: "Catalyst: Sector Tailwinds | Impact: Secondary. No company-specific news; move correlated to broader [Industry] trend."

DO NOT invent a story. DO NOT use "Market Volatility" as reasoning.

═══ STEP 2: CATEGORY ═══
Choose exactly one:
- Earnings | Upgrade | FDA | Thematic Narratives | Government Policy | New Contract/Partnership | Institutional Buying | Insider Buying | Others
{earnings_note}

═══ STEP 3: GRADE RUBRIC — STRICT HIERARCHY ═══
- A+ (Institutional Apex): Structural change ($1B+ contract, Tier-1 partnership, FDA Approval for $5B+ TAM, massive Beat+Raise)
- A  (High Conviction):    Earnings Beat+Raise, significant product launch, major re-rating with new PT
- B  (Exploitable):        Incremental news (PT hike, minor contract, index inclusion, unclear policy)
- C  (Avoid / Noise):      Sympathy move, vague rumor, Technical/Flow, unknown catalyst

═══ STEP 4: OUTPUT FORMAT ═══
{ANALYSIS_FORMAT_INSTRUCTIONS}

FINVIZ INDUSTRY THEME (use most specific):
AI Compute | AI Cloud | AI Models | AI Data & Analytics | AI Enterprise Software | AI Networking | AI Security | AI Edge & IoT | AI Robotics | AI Applications | AI Ads & Search | AI Energy | AI AGI |
Semiconductors - Compute | Semiconductors - Memory | Semiconductors - Analog | Semiconductors - Wireless | Semiconductors - Foundries | Semiconductors - Design Tools | Semiconductors - Lithography | Semiconductors - Packaging |
Cloud Hyperscalers | Cloud Data Centers | Cloud Databases | Cloud DevOps | Cloud Security | Cloud Hybrid | Cloud Multi-cloud | Cloud SaaS |
Cybersecurity - Zero Trust | Cybersecurity - Endpoint | Cybersecurity - Network | Cybersecurity - Cloud | Cybersecurity - Identity/IAM | Cybersecurity - Threat Ops |
Fintech - Payments | Fintech - Neobanks | Fintech - Lending | Fintech - Trading | Fintech - Blockchain/Crypto |
Clean Energy - Solar | Clean Energy - Wind | Clean Energy - Grid | Clean Energy - Nuclear | Clean Energy - Hydrogen |
Electric Vehicles | EV Batteries | EV Charging |
Biotech - Oncology | Biotech - Rare Disease | Biotech - Gene Therapy | Biotech - Immunology |
Pharma - Large Cap | Medical Devices | Digital Health |
Defense & Aerospace | Space | Drones |
Consumer - E-Commerce | Consumer - Streaming | Consumer - Social Media | Consumer - Gaming |
Energy - Oil & Gas | Energy - LNG | Materials - Metals & Mining |
Real Estate | REITs | Infrastructure | Others

PEER TICKERS: Pre-populated from curated mapping — ignore in your response.

CRITICAL — specificity rules (violations make the output useless for trading decisions):
reasoning (1 sentence, 15-25 words):
  - ALWAYS name the specific trigger: firm name for upgrades, policy name for govt, deal partner for contracts
  - GOOD: "Micron surged after Citi raised PT to $130 on DRAM demand recovery driven by AI server buildout"
  - BAD:  "MU received an analyst rating action or price target revision" ← NEVER write this
  - GOOD: "NBIS added to Nasdaq-100 effective Monday, triggering index-fund buying and momentum flows"
  - BAD:  "NBIS is affected by a recent government policy" ← NEVER write this
  - DO NOT copy a headline title verbatim. DO NOT include source names.

analysis_detail — "Catalyst: [...] | Impact: [...]":
  - Government Policy: name the SPECIFIC policy, bill, or executive order — not just "a policy"
  - Upgrade: name the SPECIFIC firm, old rating → new rating, and new price target
  - Earnings: include the SPECIFIC EPS beat/miss and revenue figure
  - Contract: name the SPECIFIC partner and deal scope/value
  - Impact: state the investment implication (guidance revision, TAM expansion, risk removal, etc.)

Respond ONLY with this JSON (no extra text, no markdown fences):
{{"category": "<category>", "theme": "<specific catalyst e.g. 'Beat & Raise', 'Nasdaq-100 Addition', 'Citi Upgrade to Buy'>", "reasoning": "<specific 15-25 word synthesis>", "grade": "<A+|A|B|C>", "finviz_theme": "<industry>", "analysis_detail": "Catalyst: [2-3 sentences **bolding** key facts] | Impact: [forward implication]", "analysis_details": "<detailed multi-section analysis>"}}"""

        for attempt in range(3):
            try:
                response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt, config=_gt.GenerateContentConfig(thinking_config=_gt.ThinkingConfig(thinking_budget=1024)))
                break
            except Exception as api_err:
                err_str = str(api_err)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    # Extract retry delay from error if available, else use 60s
                    import re as _re
                    delay_match = _re.search(r"retry[^\d]+(\d+)", err_str)
                    wait = int(delay_match.group(1)) + 2 if delay_match else 60
                    logger.warning(f"  Gemini 429 rate limit for {ticker} — waiting {wait}s before retry {attempt+1}/3...")
                    time.sleep(wait)
                elif attempt == 2:
                    raise
                else:
                    logger.warning(f"  Gemini API attempt {attempt+1} failed for {ticker}: {api_err} — retrying in 5s...")
                    time.sleep(5)

        text = response.text.strip()
        # Strip markdown code fences
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
        result = json.loads(text.strip())

        category = result.get("category", "Others")
        if category not in CATEGORIES:
            category = "Others"
        theme    = result.get("theme", category)
        reasoning = result.get("reasoning", "")
        grade    = result.get("grade", "B")
        if grade not in ("A+", "A", "B", "C"):
            grade = "B"
        finviz_theme = result.get("finviz_theme", "—")

        # analysis_detail: Gemini returns a string "Catalyst: ... | Impact: ..."
        # Split into object so frontend can use .catalyst and .impact
        raw_detail = result.get("analysis_detail", "")
        if isinstance(raw_detail, dict):
            analysis_detail = raw_detail
        elif " | Impact: " in raw_detail:
            parts = raw_detail.split(" | Impact: ", 1)
            catalyst_text = parts[0].replace("Catalyst: ", "").strip()
            impact_text   = parts[1].strip()
            analysis_detail = {"catalyst": catalyst_text, "impact": impact_text}
        else:
            analysis_detail = {"catalyst": raw_detail, "impact": ""}

        analysis_details = result.get("analysis_details", reasoning)

        # Curated peer stocks (sympathy plays)
        peer_tickers = get_peer_stocks(ticker)
        # Single-stock leverage/inverse ETFs — split into long vs short
        _lev         = get_leverage_inverse_stocks(ticker)
        leverage_etfs = _lev.get("long", [])
        inverse_etfs  = _lev.get("short", [])

        hypothesis_label, strategy = HYPOTHESIS_RULES.get(category, HYPOTHESIS_RULES["Others"])
        base_conviction = {"High Conviction (Gap & Go)": 80, "Medium Conviction (RS Hold)": 60,
                           "Caution (Fade Candidate)": 35, "High Risk (Volatility Trap)": 25}.get(hypothesis_label, 50)
        conviction = min(99, int(base_conviction + (rvol - 2) * 3))

        return {
            "category":        category,
            "theme":           theme,
            "reasoning":       reasoning,
            "hypothesis":      f"{hypothesis_label} — {strategy}",
            "conviction":      conviction,
            "grade":           grade,
            "finviz_theme":    finviz_theme,
            "analysis_source": "gemini+edgar" if edgar_event else "gemini",
            "analysis_detail": analysis_detail,
            "analysis_details": analysis_details,
            "peer_tickers":    peer_tickers,
            "leverage_etfs":   leverage_etfs,
            "inverse_etfs":    inverse_etfs,
        }
    except Exception as e:
        logger.warning(f"  Gemini failed for {ticker}: {e}")
        return _fallback_analysis(ticker, headlines, rvol)


def _fallback_analysis(ticker: str, headlines: list, rvol: float) -> dict:
    """Rule-based fallback if Gemini unavailable. Uses actual headline text when available."""
    titles = [h["title"] if isinstance(h, dict) else h for h in headlines]
    text = " ".join(titles).lower()

    # First non-trivial headline for specificity
    first_headline = next((t for t in titles if len(t) > 25), None)
    # All headlines joined for the catalyst block
    headlines_block = " | ".join(titles[:3]) if titles else ""

    if any(w in text for w in ["earnings", "beat", "revenue", "eps", "q1", "q2", "q3", "q4"]):
        cat = "Earnings"
        rsn = (
            f"{ticker} gapping on earnings: {first_headline[:90]}"
            if first_headline else
            f"{ticker} reported quarterly results; earnings catalyst detected."
        )
        catalyst_text = (
            f"Headlines: {headlines_block}"
            if headlines_block else
            "Earnings report detected. Review actual EPS/revenue figures for confirmation."
        )
        impact_text = "Strong earnings prints with guidance raises typically sustain the gap through the open. Watch for volume confirmation above pre-market high."
    elif any(w in text for w in ["fda", "clinical", "trial", "drug", "approval", "pdufa"]):
        cat = "FDA"
        rsn = (
            f"{ticker} FDA/regulatory event: {first_headline[:90]}"
            if first_headline else
            f"{ticker} has an active FDA/clinical regulatory event."
        )
        catalyst_text = (
            f"Headlines: {headlines_block}"
            if headlines_block else
            "FDA or clinical trial event detected. Binary outcome."
        )
        impact_text = "Binary event — expect extreme volatility. Mean reversion risk is high; avoid chasing the open. Wait for a 15-min base to form."
    elif any(w in text for w in ["upgrade", "price target", "raised", "initiated", "outperform", "overweight"]):
        cat = "Upgrade"
        rsn = (
            f"{ticker} analyst action: {first_headline[:90]}"
            if first_headline else
            f"{ticker} received an analyst rating action or price target revision."
        )
        catalyst_text = (
            f"Headlines: {headlines_block}"
            if headlines_block else
            "Analyst upgrade or price target change detected."
        )
        impact_text = "Analyst upgrades carry limited institutional conviction and often gap-fill by midday. Fade into strength or wait for a dip entry near VWAP."
    elif any(w in text for w in ["contract", "partnership", "deal", "agreement", "collaboration"]):
        cat = "New Contract/Partnership"
        rsn = (
            f"{ticker} deal announcement: {first_headline[:90]}"
            if first_headline else
            f"{ticker} announced a new contract or strategic partnership."
        )
        catalyst_text = (
            f"Headlines: {headlines_block}"
            if headlines_block else
            "Contract or partnership announcement detected. Evaluate deal size and strategic significance."
        )
        impact_text = "Contract wins with named clients and dollar values expand revenue visibility. Gap & Go above pre-market high if volume confirms at open."
    elif any(w in text for w in ["policy", "government", "regulation", "tariff", "executive order", "legislation"]):
        cat = "Government Policy"
        rsn = (
            f"{ticker} government policy catalyst: {first_headline[:90]}"
            if first_headline else
            f"{ticker} is affected by a recent government policy or regulatory development."
        )
        catalyst_text = (
            f"Headlines: {headlines_block}"
            if headlines_block else
            "Policy or regulatory catalyst detected. Monitor for further legislative developments."
        )
        impact_text = "Policy tailwinds can create sustained multi-day moves in direct beneficiaries. Watch for sector-wide sympathy moves and dip-buy at 9-EMA/VWAP."
    else:
        cat = "Others"
        rsn = (
            f"{ticker}: {first_headline[:90]}"
            if first_headline else
            f"{ticker} gap lacks a clear fundamental catalyst; likely technical breakout or flow-driven."
        )
        catalyst_text = (
            f"Headlines: {headlines_block}"
            if headlines_block else
            "No specific fundamental catalyst identified. High risk of Gap and Trap."
        )
        impact_text = "No clear catalyst — high risk of Gap and Trap. Wait for a 15-min base above pre-market high before considering entry."

    label, strategy = HYPOTHESIS_RULES.get(cat, HYPOTHESIS_RULES["Others"])

    section_titles = {
        "Earnings":               ("The Results", "Trade Implication"),
        "FDA":                    ("The Event", "Risk Profile"),
        "Upgrade":                ("The Analyst Action", "Trade Implication"),
        "New Contract/Partnership": ("The Announcement", "Trade Implication"),
        "Government Policy":      ("The Policy", "Trade Implication"),
        "Others":                 ("What Happened", "Key Consideration"),
    }
    s1, s2 = section_titles.get(cat, ("What Happened", "Key Consideration"))
    analysis_details = (
        f"• **{s1}**\n{catalyst_text}\n\n"
        f"• **{s2}**\n{impact_text}"
    )

    peer_tickers = get_peer_stocks(ticker)
    _lev         = get_leverage_inverse_stocks(ticker)

    return {
        "category":        cat,
        "theme":           cat,
        "reasoning":       rsn,
        "hypothesis":      f"{label} — {strategy}",
        "conviction":      50,
        "grade":           "B",
        "finviz_theme":    "—",
        "analysis_source": "fallback",
        "analysis_detail": {"catalyst": catalyst_text, "impact": impact_text},
        "analysis_details": analysis_details,
        "peer_tickers":    peer_tickers,
        "leverage_etfs":   _lev.get("long", []),
        "inverse_etfs":    _lev.get("short", []),
    }


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def _build_ibkr_scanner() -> list[dict]:
    """
    Mirror table: fetch pre-market movers from IBKR and apply gate logic.
    Returns [] when IBKR is unavailable or on any error.
    """
    try:
        import ibkr_client
        if not ibkr_client.IS_LIVE:
            return []
        raw = ibkr_client.get_premarket_scanner() or []
        results = []
        for item in raw:
            ticker = (item.get("ticker") or "").upper()
            if not ticker:
                continue
            stock = {
                "ticker":        ticker,
                "price":         item.get("last") or 0.0,
                "change_pct":    item.get("change_pct"),
                "volume":        item.get("volume"),
                "rs_placeholder": item.get("rs_placeholder"),
            }
            gates_passed, gates_detail, meets_all = _compute_gates(stock)
            results.append({
                **stock,
                "gates_passed":   gates_passed,
                "gates_detail":   gates_detail,
                "meets_all_gates": meets_all,
            })
        logger.info(f"  IBKR scanner: {len(results)} tickers")
        return results
    except Exception as e:
        logger.warning(f"  IBKR scanner mirror failed: {e}")
        return []


def _load_earnings_today() -> list[dict]:
    """
    Read public/earnings_calendar.json and return today's earnings list.
    Returns [] when file is absent or unreadable.
    """
    try:
        ec_path = Path("public/earnings_calendar.json")
        if not ec_path.exists():
            return []
        data = json.loads(ec_path.read_text(encoding="utf-8"))
        return data.get("today", [])
    except Exception as e:
        logger.warning(f"  Could not load earnings_calendar.json: {e}")
        return []


def main():
    now_et = datetime.now(ET)
    logger.info(f"Pre-Market Gapper Scanner — {now_et.strftime('%Y-%m-%d %H:%M ET')}")

    # ── Earnings strip (read before the slow per-ticker loop) ────────────────
    earnings_today = _load_earnings_today()
    logger.info(f"  Earnings today: {len(earnings_today)} events")

    # RC1: Load SEC EDGAR CIK map once (no API key needed — free public API)
    logger.info("Loading SEC EDGAR CIK map...")
    _load_cik_map()

    logger.info("Fetching gappers from TradingView...")
    gappers = fetch_gappers()[:25]
    logger.info(f"  Found {len(gappers)} gappers")

    output = []
    import datetime as dt

    # Uniform 5 headlines per ticker — quality analysis requires context
    stats = {"small_gap": 0, "large_gap": 0, "headlines_sent": 0}

    for stock in gappers:
        ticker = stock["ticker"]
        gap_pct = stock.get("gap_pct", 0)
        is_large_gap = gap_pct >= 15
        headline_limit = 5

        logger.info(f"  Analyzing {ticker} (gap={gap_pct:.1f}% rvol={stock['rvol']:.1f}x) → {headline_limit} headlines...")

        # Finviz fundamentals (float, short interest, daily %)
        fv = fetch_finviz_data(ticker)

        # News headlines — keep sources separate for verification
        cutoff = datetime.now(timezone.utc) - dt.timedelta(hours=24)
        google_headlines = _fetch_google_news(ticker, cutoff, limit=5)
        finviz_headlines = _fetch_finviz_news(ticker, cutoff, limit=5)
        seen = set()
        raw_headlines = []
        for h in google_headlines + finviz_headlines:
            key = h["title"][:60].lower()
            if key not in seen:
                seen.add(key)
                raw_headlines.append(h)
        raw_headlines.sort(key=lambda x: x["date"], reverse=True)

        # RC3: Filter opinion articles, sort ticker-mentioning headlines to top
        headlines = filter_headlines(raw_headlines, ticker=ticker, max_headlines=headline_limit, skip_opinion=True)
        stats["headlines_sent"] += len(headlines)
        stats["large_gap" if is_large_gap else "small_gap"] += 1

        # RC2: Scrape article body snippets in parallel (top 3 headlines, 15s budget)
        _fetch_snippets_parallel(headlines)

        # ADR% + last earnings date (single yfinance call)
        fundamentals       = fetch_ticker_fundamentals(ticker)
        last_earnings_date = fundamentals["last_earnings_date"]
        adr_pct            = fundamentals["adr_pct"]
        stock["adr_pct"]   = adr_pct

        # Hard gates (adr_pct now available)
        gates_passed, gates_detail, meets_all_gates = _compute_gates(stock)

        # RC1: Check SEC EDGAR for recent 8-K filings (free public API)
        edgar_event = fetch_edgar_8k(ticker, days=2)
        if edgar_event:
            logger.info(f"  EDGAR 8-K found for {ticker}: {edgar_event['label']} on {edgar_event['filing_date']}")

        # AI analysis — Momentum Catalyst Intelligence
        analysis = analyze_with_gemini(
            ticker, headlines, stock["rvol"], last_earnings_date,
            avg_dollar_vol=stock.get("avg_dollar_vol", 0),
            adr_pct=adr_pct,
            edgar_event=edgar_event,
        )

        # Hard Technical Floor — enforce grade cap + compute technical_status
        analysis, technical_status = _apply_technical_floor(
            analysis, stock.get("avg_dollar_vol", 0), adr_pct
        )

        # Tier label
        tier, tier_label = _compute_tier(
            analysis.get("grade", "C"),
            analysis.get("category", "Others"),
            meets_all_gates,
            analysis.get("hypothesis", ""),
        )

        # Fact-check verification (Skeptical Auditor — second Gemini call)
        logger.info(f"  Verifying catalyst for {ticker}...")
        verification = verify_catalyst_accuracy(
            ticker,
            analysis.get("category", "Others"),
            analysis.get("reasoning", ""),
            google_headlines,
            finviz_headlines,
        )

        time.sleep(1)  # rate limit
        output.append({
            **stock,
            **analysis,
            "headlines":        [{"title": h["title"], "source": h.get("source",""), "url": h.get("url","")} for h in headlines[:5]],
            "edgar_filing":     edgar_event,
            "float_shares":     fv.get("float_shares"),
            "short_float":      fv.get("short_float"),
            "daily_pct":        fv.get("daily_pct"),
            "prev_close":       fv.get("prev_close"),
            "industry":         analysis.get("finviz_theme", "—"),
            "technical_status": technical_status,
            "verification":     verification,
            "gates_passed":     gates_passed,
            "gates_detail":     gates_detail,
            "meets_all_gates":  meets_all_gates,
            "tier":             tier,
            "tier_label":       tier_label,
        })

    # ── Cost Analysis Reporting ────────────────────────────────────────────────
    logger.info(f"\n{'='*70}")
    logger.info(f"HYBRID ANALYSIS SUMMARY")
    logger.info(f"{'='*70}")
    logger.info(f"Small gaps (<15%):  {stats['small_gap']} gappers × 2 headlines")
    logger.info(f"Large gaps (≥15%):  {stats['large_gap']} gappers × 5 headlines")
    logger.info(f"Total headlines sent to Gemini: {stats['headlines_sent']}")
    logger.info(f"Opinion articles filtered out: Enabled (cost savings ✓)")
    logger.info(f"\nEstimated Gemini 2.5 Flash cost: ~$0.02/day (~$0.40/month)")
    logger.info(f"{'='*70}\n")

    # ── IBKR mirror table ────────────────────────────────────────────────────
    ibkr_scanner = _build_ibkr_scanner()

    result = {
        "scan_time":     now_et.strftime("%Y-%m-%d %H:%M ET"),
        "earnings_today": earnings_today,
        "gappers":       output,
        "ibkr_scanner":  ibkr_scanner,
        "cost_analysis": {
            "model": "gemini-2.0-flash-lite",
            "approach": "hybrid",
            "small_gaps": stats["small_gap"],
            "large_gaps": stats["large_gap"],
            "total_headlines": stats["headlines_sent"],
            "monthly_cost_usd": 0.01
        }
    }

    out_path = Path("public/gapper_data.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Done! {len(output)} gappers → {out_path}")


if __name__ == "__main__":
    main()
