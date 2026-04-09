"""
ibkr_themes.py — IBKR Account & Position Snapshot
Connects to TWS/IB Gateway, fetches account equity + open positions.
Falls back gracefully when TWS is not running (CI / offline mode).
Outputs public/ibkr_themes.json
"""

import json
import os
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
OUT_PATH = Path("public/ibkr_themes.json")

IBKR_HOST = os.environ.get("IBKR_HOST", "127.0.0.1") or "127.0.0.1"
IBKR_PORT = int(os.environ.get("IBKR_PORT", "7497") or "7497")  # 7497 = paper, 7496 = live
IBKR_CLIENT_ID = 10  # distinct client ID to avoid conflicts with trading bot


# ──────────────────────────────────────────────────────────────
# Fallback output (when TWS is offline)
# ──────────────────────────────────────────────────────────────

def write_fallback(reason: str) -> None:
    payload = {
        "generated_at": datetime.now(ET).strftime("%Y-%m-%dT%H:%M"),
        "connected": False,
        "account_equity": None,
        "scanner": [],
        "note": reason,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2))
    logger.info("Wrote fallback ibkr_themes.json: %s", reason)


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def run() -> None:
    try:
        from ib_insync import IB, util
        util.logToConsole(logging.WARNING)  # suppress ib_insync verbose logs
    except ImportError:
        write_fallback("ib_insync not installed")
        return

    ib = IB()
    try:
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID, timeout=8, readonly=True)
    except Exception as e:
        write_fallback(f"TWS not reachable ({e})")
        return

    try:
        # ── Account equity ──────────────────────────────────────
        account_equity = None
        try:
            summary = ib.accountSummary()
            for item in summary:
                if item.tag == "NetLiquidation" and item.currency == "USD":
                    account_equity = round(float(item.value), 2)
                    break
        except Exception as e:
            logger.warning("Could not fetch account summary: %s", e)

        # ── Open positions ──────────────────────────────────────
        scanner = []
        try:
            positions = ib.positions()
            tickers_needed = [p.contract for p in positions if p.contract.secType == "STK"]

            # Batch-request market data snapshots for all positions
            price_map = {}
            if tickers_needed:
                reqs = [ib.reqMktData(c, "", True, False) for c in tickers_needed]
                ib.sleep(2)  # wait for snapshots
                for contract, ticker in zip(tickers_needed, reqs):
                    sym = contract.symbol
                    last = ticker.last if ticker.last and ticker.last > 0 else ticker.close
                    chg_pct = None
                    if ticker.close and ticker.close > 0 and ticker.last and ticker.last > 0:
                        chg_pct = round((ticker.last - ticker.close) / ticker.close * 100, 2)
                    price_map[sym] = {"price": last, "change_pct": chg_pct}
                    ib.cancelMktData(contract)

            for pos in positions:
                if pos.contract.secType != "STK":
                    continue
                sym = pos.contract.symbol
                qty = pos.position
                avg_cost = round(pos.avgCost, 2)
                info = price_map.get(sym, {})
                price = info.get("price") or avg_cost
                change_pct = info.get("change_pct")

                unrealized_pnl_pct = None
                if avg_cost and avg_cost > 0 and price:
                    unrealized_pnl_pct = round((price - avg_cost) / avg_cost * 100, 2)

                # signal: Long/Short based on qty sign
                signal = "Long" if qty > 0 else "Short"

                scanner.append({
                    "ticker": sym,
                    "signal": signal,
                    "price": round(price, 2) if price else None,
                    "change_pct": change_pct,
                    "qty": int(qty),
                    "avg_cost": avg_cost,
                    "unrealized_pnl_pct": unrealized_pnl_pct,
                })

            # Sort: biggest unrealized gain first
            scanner.sort(key=lambda x: (x.get("unrealized_pnl_pct") or 0), reverse=True)

        except Exception as e:
            logger.warning("Could not fetch positions: %s", e)

        # ── Write output ────────────────────────────────────────
        payload = {
            "generated_at": datetime.now(ET).strftime("%Y-%m-%dT%H:%M"),
            "connected": True,
            "account_equity": account_equity,
            "scanner": scanner,
        }
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(payload, indent=2))
        logger.info(
            "ibkr_themes.json written — equity: %s, positions: %d",
            account_equity, len(scanner)
        )

    except Exception as e:
        logger.error("Unexpected error: %s", e)
        write_fallback(f"Runtime error: {e}")
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    run()
