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
TWS_PORT   = int(os.getenv("TWS_PORT",  "7497"))   # 7497=TWS live | 7496=paper | 4001=GW live | 4002=GW paper
CLIENT_ID  = int(os.getenv("TWS_CLIENT_ID", "20")) # must differ from other scripts (ibkr_client uses 1)
WS_PORT    = int(os.getenv("WS_PORT",   "5003"))
DATA_PATH  = Path(os.getenv("THEMATIC_JSON", "public/thematic_data.json"))
MAX_LIVE   = 95   # live subscriptions for stock tickers (internals use ~5 slots)
BROADCAST_INTERVAL = 1.0  # seconds between price broadcasts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Market internals to always subscribe ─────────────────────────────────────
INTERNALS = [
    {"key": "SPY",   "symbol": "SPY",  "secType": "STK", "exchange": "ARCA",  "currency": "USD"},
    {"key": "QQQ",   "symbol": "QQQ",  "secType": "STK", "exchange": "NASDAQ","currency": "USD"},
    {"key": "$VIX",  "symbol": "VIX",  "secType": "IND", "exchange": "CBOE",  "currency": "USD"},
    {"key": "$TICK", "symbol": "TICK", "secType": "IND", "exchange": "NYSE",  "currency": "USD"},
    {"key": "$TRIN", "symbol": "TRIN", "secType": "IND", "exchange": "NYSE",  "currency": "USD"},
]

# ── Global state (single-threaded asyncio — no locks needed) ─────────────────
price_cache: dict[str, dict] = {}   # key → { price, change_pct, source, ts }
dirty:       set[str]        = set() # keys updated since last broadcast
CLIENTS:     set             = set() # connected WebSocket objects
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

    # 2. Stock tickers: top MAX_LIVE by RS get live data; remainder get delayed
    live_slots  = MAX_LIVE
    live_tickers  = [s for s, _ in symbols_ranked[:live_slots]]
    delay_tickers = [s for s, _ in symbols_ranked[live_slots:]]

    # Request live data type (1=live, 3=delayed)
    ib.reqMarketDataType(1)

    log.info("Subscribing %d LIVE + %d DELAYED stock tickers…", len(live_tickers), len(delay_tickers))
    for i, symbol in enumerate(live_tickers):
        try:
            contract = Stock(symbol, "SMART", "USD")
            ib.reqMktData(contract, "", False, False)
        except Exception as exc:
            log.warning("  ✗ live %s: %s", symbol, exc)
        if i % 25 == 24:
            await asyncio.sleep(0.3)
        else:
            await asyncio.sleep(0.02)

    # Switch to delayed for the remaining tickers
    if delay_tickers:
        ib.reqMarketDataType(3)
        for i, symbol in enumerate(delay_tickers):
            try:
                contract = Stock(symbol, "SMART", "USD")
                ib.reqMktData(contract, "", False, False)
            except Exception as exc:
                log.warning("  ✗ delayed %s: %s", symbol, exc)
            if i % 25 == 24:
                await asyncio.sleep(0.3)
            else:
                await asyncio.sleep(0.02)
        ib.reqMarketDataType(1)  # restore default

    log.info("All subscriptions placed.")


async def ibkr_connect(symbols_ranked: list[tuple[str, int]]) -> bool:
    """Try connecting to TWS. Returns True on success."""
    ports_to_try = [TWS_PORT]
    # If default isn't 4002, also try gateway paper as fallback
    if TWS_PORT != 4002:
        ports_to_try.append(4002)

    for port in ports_to_try:
        try:
            log.info("Connecting to IBKR at %s:%d (clientId=%d)…", TWS_HOST, port, CLIENT_ID)
            await ib.connectAsync(TWS_HOST, port, clientId=CLIENT_ID, readonly=True, timeout=10)
            log.info("✅ Connected to IBKR TWS/Gateway")
            await subscribe_all(symbols_ranked)
            return True
        except Exception as exc:
            log.warning("Connection failed on port %d: %s", port, exc)

    log.error("❌ Could not connect to IBKR. Running in offline mode (WebSocket server still up).")
    return False


def _setup_reconnect(symbols_ranked: list[tuple[str, int]]) -> None:
    """Register a disconnected callback that retries after 5 s."""
    async def _reconnect():
        log.warning("IBKR disconnected — retrying in 5 s…")
        await asyncio.sleep(5)
        await ibkr_connect(symbols_ranked)

    ib.disconnectedEvent += lambda: asyncio.ensure_future(_reconnect())


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket server
# ─────────────────────────────────────────────────────────────────────────────

async def _ws_handler(ws) -> None:
    """Handle one WebSocket client connection."""
    CLIENTS.add(ws)
    log.info("Client connected (%d total)", len(CLIENTS))
    try:
        # Send full snapshot immediately so client has prices before first broadcast
        if price_cache:
            await ws.send(json.dumps({"type": "snapshot", "data": price_cache}))
        else:
            await ws.send(json.dumps({"type": "snapshot", "data": {}, "status": "waiting_for_ibkr"}))
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
    symbols_ranked = load_tickers_from_json()

    # Start WebSocket server — use serve_forever() for compatibility with websockets 13/14+
    log.info("Starting WebSocket server on ws://localhost:%d …", WS_PORT)
    server = await websockets.serve(_ws_handler, "0.0.0.0", WS_PORT)
    log.info("WebSocket server ready ✅  (ws://localhost:%d)", WS_PORT)

    # Set up reconnect handler before first connect
    _setup_reconnect(symbols_ranked)

    # Connect to IBKR (non-fatal if TWS is offline)
    await ibkr_connect(symbols_ranked)

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
