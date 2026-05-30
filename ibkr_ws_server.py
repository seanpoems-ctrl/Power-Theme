#!/usr/bin/env python3
# Fix for Python 3.10+: ib_insync's eventkit calls get_event_loop() at import time
# which raises RuntimeError if no loop exists. Create one before importing ib_insync.
import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())

"""
IBKR TWS WebSocket Bridge — ibkr_ws_server.py
==============================================
Connects to IBKR TWS/Gateway via ib_insync, subscribes to real-time market data
for all tickers in public/thematic_data.json + key market internals, then fans out
live price updates to any browser client via WebSocket on port 5003.

Frontend connects: ws://localhost:5003
Message format (server → client):
  { "type": "snapshot" | "prices", "data": { "TICKER": { "price": float, "change_pct": float, "source": "ibkr" } } }

Usage:
  python ibkr_ws_server.py                      # TWS live (port 7497)
  TWS_PORT=7496 python ibkr_ws_server.py        # TWS paper
  TWS_PORT=4002 python ibkr_ws_server.py        # IB Gateway paper
  TWS_PORT=4001 python ibkr_ws_server.py        # IB Gateway live
"""

import asyncio
import json
import logging
import math
import os
import time

# Load .env file automatically (python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, rely on shell env vars
from pathlib import Path

import websockets
from ib_insync import IB, Stock, Index

# ── Config ───────────────────────────────────────────────────────────────────
TWS_HOST   = os.getenv("TWS_HOST",      "127.0.0.1")
TWS_PORT   = int(os.getenv("TWS_PORT",  "7497"))   # 7497=TWS paper | 7496=TWS live | 4001=GW live | 4002=GW paper
CLIENT_ID  = int(os.getenv("TWS_CLIENT_ID", "20")) # must differ from other scripts (ibkr_client uses 1)
WS_PORT    = int(os.getenv("WS_PORT",   "5003"))
DATA_PATH   = Path(os.getenv("THEMATIC_JSON", "public/thematic_data.json"))
GAPPER_PATH = Path(os.getenv("GAPPER_JSON",   "public/gapper_data.json"))
MAX_TICKERS = 80  # max stock ticker subscriptions (IBKR limit=100; 80 stocks + 3 internals + buffer = ~95)
BROADCAST_INTERVAL = 1.0  # seconds between price broadcasts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Market internals to always subscribe ─────────────────────────────────────
# Note: $TICK and $TRIN require a separate IBKR market data subscription — removed for now
INTERNALS = [
    {"key": "SPY",  "symbol": "SPY", "secType": "STK", "exchange": "ARCA",  "currency": "USD"},
    {"key": "QQQ",  "symbol": "QQQ", "secType": "STK", "exchange": "NASDAQ","currency": "USD"},
    {"key": "$VIX", "symbol": "VIX", "secType": "IND", "exchange": "CBOE",  "currency": "USD"},
]

# ── Global state (single-threaded asyncio — no locks needed) ─────────────────
price_cache:    dict[str, dict] = {}    # key → { price, change_pct, source, ts }
dirty:          set[str]        = set() # keys updated since last broadcast
CLIENTS:        set             = set() # connected WebSocket objects
ibkr_connected: bool            = False # True only while IB Gateway session is live
ib = IB()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _valid(v) -> bool:
    return v is not None and not math.isnan(float(v)) and float(v) != 0.0

def _best_price(ticker) -> float | None:
    """Return the best available price for an ib_insync Ticker object."""
    # Regular hours: last traded price is authoritative
    if _valid(ticker.last) and ticker.last > 0:
        return float(ticker.last)
    # Pre/post-market: midpoint is more reliable than stale last
    if _valid(ticker.bid) and _valid(ticker.ask) and ticker.bid > 0 and ticker.ask > 0:
        return (float(ticker.bid) + float(ticker.ask)) / 2.0
    # Bid alone
    if _valid(ticker.bid) and ticker.bid > 0:
        return float(ticker.bid)
    # Prior close as last resort (will show 0% change)
    if _valid(ticker.close) and ticker.close > 0:
        return float(ticker.close)
    return None

def _change_pct(price: float, prev_close: float | None) -> float | None:
    if prev_close and _valid(prev_close) and prev_close > 0:
        return (price - float(prev_close)) / float(prev_close) * 100.0
    return None


def load_tickers_from_json() -> list[tuple[str, int]]:
    """Return [(ticker, rs_52w), ...] sorted by rs_52w desc from thematic_data.json."""
    if not DATA_PATH.exists():
        log.warning("thematic_data.json not found at %s — subscribing to internals only", DATA_PATH)
        return []
    with DATA_PATH.open() as f:
        data = json.load(f)
    seen = {}
    for theme in data.get("themes", []):
        for sub in theme.get("subthemes", []):
            for stock in sub.get("stocks", []):
                tk = stock.get("ticker", "")
                rs = stock.get("rs_52w", 0) or 0
                if tk and (tk not in seen or rs > seen[tk]):
                    seen[tk] = rs
    ranked = sorted(seen.items(), key=lambda x: x[1], reverse=True)
    log.info("Loaded %d unique tickers from thematic_data.json", len(ranked))
    return ranked


def load_gapper_tickers() -> list[tuple[str, int]]:
    """
    Return [(ticker, priority), ...] from today's gapper_data.json.

    Gappers are pre-market movers — they MUST be subscribed for live prices
    on the Pre-Market Gappers tab at 9 AM ET. They get the highest priority
    (score 1000 + conviction) so they're always subscribed ahead of thematic stocks.
    Returns [] silently if file missing or from a different day.
    """
    if not GAPPER_PATH.exists():
        log.info("gapper_data.json not found — no gapper tickers added")
        return []
    try:
        with GAPPER_PATH.open(encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        gappers = data.get("gappers", [])
        result = []
        for g in gappers:
            tk = g.get("ticker", "").strip().upper()
            conviction = int(g.get("conviction") or 50)
            if tk:
                result.append((tk, 1000 + conviction))  # score >999 guarantees first-slot priority
        log.info("Loaded %d gapper tickers from gapper_data.json (scan: %s)",
                 len(result), data.get("scan_time", "unknown"))
        return result
    except Exception as exc:
        log.warning("Could not load gapper_data.json: %s", exc)
        return []


def merge_tickers(
    gappers: list[tuple[str, int]],
    thematic: list[tuple[str, int]],
) -> list[tuple[str, int]]:
    """
    Merge gapper + thematic ticker lists with deduplication.

    Gappers always come first (they have priority score >999).
    Thematic stocks fill remaining slots up to MAX_TICKERS.
    Returns the combined list, capped at MAX_TICKERS.
    """
    seen: set[str] = set()
    merged: list[tuple[str, int]] = []

    # 1. Gappers first — always included (small list, ~10–20 tickers)
    for tk, score in gappers:
        if tk not in seen:
            seen.add(tk)
            merged.append((tk, score))

    # 2. Thematic stocks fill remaining capacity
    remaining = MAX_TICKERS - len(merged)
    for tk, score in thematic:
        if remaining <= 0:
            break
        if tk not in seen:
            seen.add(tk)
            merged.append((tk, score))
            remaining -= 1

    gapper_n   = len(gappers)
    thematic_n = len(merged) - gapper_n
    log.info("Subscription list: %d gappers + %d thematic = %d total (cap=%d)",
             gapper_n, thematic_n, len(merged), MAX_TICKERS)
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# IBKR event callback — called by ib_insync when any subscribed ticker updates
# ─────────────────────────────────────────────────────────────────────────────

def _on_pending_tickers(tickers):
    """Synchronous ib_insync callback — safe to mutate price_cache (single-threaded asyncio)."""
    for tk in tickers:
        key = tk.contract.symbol
        price = _best_price(tk)
        if price is None:
            continue
        chg = _change_pct(price, tk.close)
        entry = {
            "price":      round(price, 4),
            "change_pct": round(chg, 4) if chg is not None else None,
            "source":     "ibkr",
            "ts":         time.time(),
        }
        price_cache[key] = entry
        dirty.add(key)


# ─────────────────────────────────────────────────────────────────────────────
# IBKR connect + subscribe
# ─────────────────────────────────────────────────────────────────────────────

async def subscribe_all(symbols_ranked: list[tuple[str, int]]) -> None:
    """Subscribe to market data for all symbols. Respects the ~100-slot limit."""
    global ib

    # Register the batch callback (more efficient than per-ticker updateEvent)
    ib.pendingTickersEvent += _on_pending_tickers

    # Force delayed data (type 3) for ALL subscriptions — avoids "competing live session" error
    ib.reqMarketDataType(3)

    # 1. Market internals
    for spec in INTERNALS:
        try:
            if spec["secType"] == "STK":
                contract = Stock(spec["symbol"], spec["exchange"], spec["currency"])
            else:
                contract = Index(spec["symbol"], spec["exchange"], spec["currency"])
            ib.reqMktData(contract, "", False, False)
            log.info("  ✓ internal: %s", spec["key"])
        except Exception as exc:
            log.warning("  ✗ internal %s: %s", spec["key"], exc)
        await asyncio.sleep(0.05)

    # 2. Stock tickers
    all_tickers = [s for s, _ in symbols_ranked[:MAX_TICKERS]]
    log.info("Subscribing %d tickers (delayed data, limit=%d)…", len(all_tickers), MAX_TICKERS)
    for i, symbol in enumerate(all_tickers):
        try:
            contract = Stock(symbol, "SMART", "USD")
            ib.reqMktData(contract, "", False, False)
        except Exception as exc:
            log.warning("  ✗ %s: %s", symbol, exc)
        if i % 25 == 24:
            await asyncio.sleep(0.3)
        else:
            await asyncio.sleep(0.02)

    log.info("All subscriptions placed (%d tickers + internals).", len(all_tickers))


async def ibkr_connect(symbols_ranked: list[tuple[str, int]]) -> bool:
    """Try connecting to TWS. Returns True on success."""
    global ibkr_connected
    # Try configured port first, then all common IBKR ports as fallback
    # 7497=TWS paper, 7496=TWS live, 4002=Gateway paper, 4001=Gateway live
    all_ports = [TWS_PORT, 7497, 7496, 4002, 4001]
    ports_to_try = list(dict.fromkeys(all_ports))  # deduplicate, preserve order

    for port in ports_to_try:
        try:
            log.info("Connecting to IBKR at %s:%d (clientId=%d)…", TWS_HOST, port, CLIENT_ID)
            await ib.connectAsync(TWS_HOST, port, clientId=CLIENT_ID, readonly=True, timeout=10)
            log.info("✅ Connected to IBKR TWS/Gateway")
            ibkr_connected = True
            await _broadcast_ibkr_status(True)
            await subscribe_all(symbols_ranked)
            return True
        except Exception as exc:
            log.warning("Connection failed on port %d: %s", port, exc)

    log.error("❌ Could not connect to IBKR. Running in offline mode (WebSocket server still up).")
    return False


def _setup_reconnect(symbols_ranked: list[tuple[str, int]]) -> None:
    """Register a disconnected callback that retries after 5 s."""
    async def _reconnect():
        global ibkr_connected
        log.warning("IBKR disconnected — retrying in 5 s…")
        ibkr_connected = False
        await _broadcast_ibkr_status(False)
        await asyncio.sleep(5)
        await ibkr_connect(symbols_ranked)

    ib.disconnectedEvent += lambda: asyncio.ensure_future(_reconnect())


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket server
# ─────────────────────────────────────────────────────────────────────────────

async def _broadcast_ibkr_status(connected: bool) -> None:
    """Push ibkr_status message to all connected WebSocket clients."""
    if not CLIENTS:
        return
    msg = json.dumps({"type": "ibkr_status", "connected": connected})
    dead = set()
    for ws in list(CLIENTS):
        try:
            await ws.send(msg)
        except websockets.exceptions.ConnectionClosed:
            dead.add(ws)
    CLIENTS.difference_update(dead)


async def _ws_handler(ws) -> None:
    """Handle one WebSocket client connection."""
    CLIENTS.add(ws)
    log.info("Client connected (%d total)", len(CLIENTS))
    try:
        # Send full snapshot immediately so client has prices before first broadcast
        if price_cache:
            await ws.send(json.dumps({"type": "snapshot", "data": price_cache, "ibkr_connected": ibkr_connected}))
        else:
            await ws.send(json.dumps({"type": "snapshot", "data": {}, "ibkr_connected": ibkr_connected, "status": "waiting_for_ibkr"}))
        # Keep connection alive; ignore any incoming messages
        async for _ in ws:
            pass
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        CLIENTS.discard(ws)
        log.info("Client disconnected (%d remaining)", len(CLIENTS))


async def _broadcast_loop() -> None:
    """Every BROADCAST_INTERVAL seconds, push dirty price updates to all clients."""
    global CLIENTS, dirty
    while True:
        await asyncio.sleep(BROADCAST_INTERVAL)
        if not CLIENTS or not dirty:
            continue
        # Snapshot the dirty set and clear it
        keys = list(dirty)
        dirty.clear()
        payload = {k: price_cache[k] for k in keys if k in price_cache}
        if not payload:
            continue
        msg = json.dumps({"type": "prices", "data": payload})
        dead = set()
        for ws in list(CLIENTS):
            try:
                await ws.send(msg)
            except websockets.exceptions.ConnectionClosed:
                dead.add(ws)
        CLIENTS.difference_update(dead)  # in-place, avoids Python treating CLIENTS as local


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    # Load both sources and merge — gappers always get first-slot priority
    gapper_tickers   = load_gapper_tickers()
    thematic_tickers = load_tickers_from_json()
    symbols_ranked   = merge_tickers(gapper_tickers, thematic_tickers)

    # Start WebSocket server — use serve_forever() for compatibility with websockets 13/14+
    log.info("Starting WebSocket server on ws://localhost:%d …", WS_PORT)
    server = await websockets.serve(_ws_handler, "0.0.0.0", WS_PORT)
    log.info("WebSocket server ready ✅  (ws://localhost:%d)", WS_PORT)

    # Set up reconnect handler before first connect
    _setup_reconnect(symbols_ranked)

    # Connect in background — keeps retrying every 10s until IB Gateway / TWS opens.
    # This allows the script to auto-start at Windows login and connect the moment
    # the user opens IB Gateway, with no manual intervention.
    async def _connect_with_retry():
        while True:
            connected = await ibkr_connect(symbols_ranked)
            if connected:
                return
            log.info("IB Gateway / TWS not open yet — retrying in 10 s…")
            await asyncio.sleep(10)

    asyncio.ensure_future(_connect_with_retry())

    # Broadcast loop runs forever; server stays alive until Ctrl+C
    try:
        await _broadcast_loop()
    finally:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped.")
