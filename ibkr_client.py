"""
ibkr_client.py — Data-fetch-only IBKR module for GitHub Actions.

No live socket management. Connects to IB Gateway, fetches data, writes JSON.
All functions return None or [] on any error — caller decides how to handle.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

IS_LIVE = False
_ib = None

_HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
_PORT = int(os.environ.get("IBKR_PORT", "4002"))

try:
    from ib_insync import IB, Stock, Index, ScannerSubscription, util

    _ib = IB()
    _ib.connect(_HOST, _PORT, clientId=1, readonly=True, timeout=10)
    IS_LIVE = True
    logger.info("Connected to IB Gateway at %s:%s", _HOST, _PORT)
except Exception as _conn_err:
    IS_LIVE = False
    logger.warning(
        "Could not connect to IB Gateway at %s:%s — running in fallback mode. Error: %s",
        _HOST,
        _PORT,
        _conn_err,
    )


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_data_source() -> str:
    """Return 'ibkr' if connected to IB Gateway, else 'fallback'."""
    return "ibkr" if IS_LIVE else "fallback"


# ---------------------------------------------------------------------------
# Data-fetch functions
# ---------------------------------------------------------------------------


def get_account_equity() -> float | None:
    """
    Return the NetLiquidation value from the account summary.
    Returns None if not connected or on any error.
    """
    if not IS_LIVE or _ib is None:
        return None
    try:
        summary = _ib.reqAccountSummary()
        for item in summary:
            if item.tag == "NetLiquidation":
                return float(item.value)
        logger.warning("NetLiquidation tag not found in account summary")
        return None
    except Exception as exc:
        logger.error("get_account_equity failed: %s", exc)
        return None


def get_premarket_scanner() -> list[dict]:
    """
    Run an IBKR scanner for top pre-market gap-up stocks.

    Criteria:
      - ScanCode: TOP_PERC_GAIN
      - locationCode: STK.US
      - abovePrice: 12
      - aboveVolume: 100,000
      - marketCapAbove: 2,000,000,000
      - session: PRE_MKT

    Returns a list of dicts:
      {ticker, last, change_pct, volume, rs_placeholder}
    """
    if not IS_LIVE or _ib is None:
        return []
    try:
        sub = ScannerSubscription(
            instrument="STK",
            locationCode="STK.US",
            scanCode="TOP_PERC_GAIN",
            abovePrice=12,
            aboveVolume=100_000,
            marketCapAbove=2_000_000_000,
        )
        # Session filter is passed as a scanner subscription option tag
        options = [("session", "PRE_MKT")]
        results = _ib.reqScannerSubscription(sub, [], options)

        output = []
        for item in results:
            contract = item.contractDetails.contract
            output.append(
                {
                    "ticker": contract.symbol,
                    "last": None,          # populated by reqMktData if needed
                    "change_pct": None,    # scanner result has rankValue; real price needs mkt data
                    "volume": None,
                    "rs_placeholder": None,
                }
            )
        return output
    except Exception as exc:
        logger.error("get_premarket_scanner failed: %s", exc)
        return []


def get_earnings_calendar() -> list[dict]:
    """
    Return earnings events for the next 7 days using reqFundamentalData (CalendarReport).

    Returns a list of dicts:
      {ticker, date, time_of_day, eps_estimate}
    """
    if not IS_LIVE or _ib is None:
        return []
    try:
        # CalendarReport requires a specific contract — use a broad index as proxy
        # to retrieve the XML calendar, then parse it.
        import xml.etree.ElementTree as ET

        spy = Stock("SPY", "ARCA", "USD")
        _ib.qualifyContracts(spy)
        xml_data = _ib.reqFundamentalData(spy, "CalendarReport")
        if not xml_data:
            return []

        root = ET.fromstring(xml_data)
        cutoff = datetime.now(tz=timezone.utc) + timedelta(days=7)
        output = []

        for event in root.iter("EarningsEvent"):
            ticker_el = event.find("Ticker")
            date_el = event.find("Date")
            time_el = event.find("TimeOfDay")
            eps_el = event.find("EPSEstimate")

            if ticker_el is None or date_el is None:
                continue

            try:
                event_date = datetime.strptime(date_el.text.strip(), "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue

            if event_date > cutoff:
                continue

            output.append(
                {
                    "ticker": ticker_el.text.strip() if ticker_el.text else "",
                    "date": date_el.text.strip() if date_el.text else "",
                    "time_of_day": time_el.text.strip() if (time_el is not None and time_el.text) else "",
                    "eps_estimate": float(eps_el.text.strip()) if (eps_el is not None and eps_el.text) else None,
                }
            )

        return output
    except Exception as exc:
        logger.error("get_earnings_calendar failed: %s", exc)
        return []


def get_news_headlines(ticker: str) -> list[dict]:
    """
    Return news headlines for *ticker* from the last 24 hours.

    Providers: BRFG (Briefing.com), DJNL (Dow Jones Newswire)

    Returns a list of dicts:
      {headline, source, timestamp}
    """
    if not IS_LIVE or _ib is None:
        return []
    try:
        contract = Stock(ticker, "SMART", "USD")
        _ib.qualifyContracts(contract)

        end_dt = datetime.now(tz=timezone.utc)
        start_dt = end_dt - timedelta(hours=24)

        start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

        articles = _ib.reqHistoricalNews(
            contract.conId,
            providerCodes="BRFG+DJNL",
            startDateTime=start_str,
            endDateTime=end_str,
            totalResults=50,
        )

        output = []
        for article in articles:
            output.append(
                {
                    "headline": article.headline,
                    "source": article.providerCode,
                    "timestamp": article.time,
                }
            )
        return output
    except Exception as exc:
        logger.error("get_news_headlines(%s) failed: %s", ticker, exc)
        return []


def get_vix() -> float | None:
    """
    Return the current VIX spot price via reqMktData on the CBOE VIX index contract.
    Returns None if not connected or on any error.
    """
    if not IS_LIVE or _ib is None:
        return None
    try:
        vix_contract = Index("VIX", "CBOE", "USD")
        _ib.qualifyContracts(vix_contract)

        ticker = _ib.reqMktData(vix_contract, "", False, False)
        _ib.sleep(2)  # allow snapshot to arrive

        last = ticker.last
        if last is None or (isinstance(last, float) and last != last):  # NaN guard
            last = ticker.close
        if last is None or (isinstance(last, float) and last != last):
            logger.warning("VIX market data returned no valid price")
            return None

        _ib.cancelMktData(vix_contract)
        return float(last)
    except Exception as exc:
        logger.error("get_vix failed: %s", exc)
        return None


def disconnect() -> None:
    """Cleanly disconnect from IB Gateway if connected."""
    global IS_LIVE
    if _ib is not None and _ib.isConnected():
        try:
            _ib.disconnect()
            logger.info("Disconnected from IB Gateway")
        except Exception as exc:
            logger.warning("disconnect() encountered an error: %s", exc)
    IS_LIVE = False
