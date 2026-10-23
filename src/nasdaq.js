// Nasdaq share of an ETF's holdings, for the "Nasdaq %" column / filter in the ETF tables.
// Exchanges come from public/stock_lookup.json ({ticker: [market cap $B, industry, exchange]}, built nightly from TradingView). The share is the part of the
// ETF's stored top holdings that are listed on NASDAQ, counting only holdings that clear the liquidity minimum (liquidity.js) and whose exchange is known.
// Fewer than NASDAQ_MIN_KNOWN such holdings → null (too little to say anything; a filter hides those ETFs rather than guess).

import { isLiquid } from "./liquidity";

export const NASDAQ_MIN_KNOWN = 3;
const KEY = "etf_nasdaq_min_pct_v1";

export function loadNasdaqMin() {
  try {
    const v = parseFloat(localStorage.getItem(KEY));
    return Number.isFinite(v) && v >= 0 && v <= 100 ? v : 0;
  } catch { return 0; }
}
export function saveNasdaqMin(v) {
  try { localStorage.setItem(KEY, String(v)); } catch { /* private mode / quota */ }
}

/** Exchange ("NASDAQ" / "NYSE" / "AMEX") for a ticker, tolerating class-share spelling (BRK-B ↔ BRK.B); null when unknown. */
export function exchangeOf(lookup, ticker) {
  if (!lookup || !ticker) return null;
  const row = lookup[ticker] || lookup[ticker.replace("-", ".")] || lookup[ticker.replace(".", "-")];
  return row?.[2] || null;
}

/** { pct (0-100, rounded), n (holdings counted), nas (how many on Nasdaq), tickers (the Nasdaq ones) } or null when fewer than NASDAQ_MIN_KNOWN count. */
export function nasdaqShare(holdings, lookup, minDvolM) {
  const known = [];
  for (const h of holdings || []) {
    if (!isLiquid(h.dollar_volume, minDvolM)) continue;
    const ex = exchangeOf(lookup, h.ticker);
    if (ex) known.push([h.ticker, ex]);
  }
  if (known.length < NASDAQ_MIN_KNOWN) return null;
  const tickers = known.filter(([, ex]) => ex === "NASDAQ").map(([t]) => t);
  return { pct: Math.round((tickers.length / known.length) * 100), n: known.length, nas: tickers.length, tickers };
}
