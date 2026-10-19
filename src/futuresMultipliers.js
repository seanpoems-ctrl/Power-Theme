// Contract multipliers for futures in the Trade Journal. The journal stores P&L as (exit − entry) × contracts — correct for stocks, but a futures
// contract moves dollars per POINT (e.g. one MNQ point = $2), so the dollar figures for micro / standard futures were far too small. Rather than
// rewriting stored trades, the journal applies the multiplier when it DISPLAYS or sums P&L (`pnlOf` in App.js); R multiples and P&L % are unaffected
// because risk and profit scale together.
//
// A symbol counts as a futures contract only when it looks like ROOT + month code + year digit(s) ("MNQZ9", "M6BH0", "ESH26"); bare roots are NOT
// matched because many collide with stock tickers (ES, CL, GC...). Prices are assumed to be quoted the way IBKR's trade report quotes them
// (FX in USD per unit of foreign currency, metals / energy in USD, indices in points).

export const FUTURES = {
  // equity index — micro, then standard
  MES: [5, "Micro E-mini S&P 500"], MNQ: [2, "Micro E-mini Nasdaq-100"], MYM: [0.5, "Micro E-mini Dow"], M2K: [5, "Micro E-mini Russell 2000"],
  ES: [50, "E-mini S&P 500"], NQ: [20, "E-mini Nasdaq-100"], YM: [5, "E-mini Dow"], RTY: [50, "E-mini Russell 2000"],
  // metals
  MGC: [10, "Micro Gold (10 oz)"], GC: [100, "Gold (100 oz)"], SIL: [1000, "Micro Silver (1,000 oz)"], SI: [5000, "Silver (5,000 oz)"],
  MHG: [2500, "Micro Copper (2,500 lb)"], HG: [25000, "Copper (25,000 lb)"], PL: [50, "Platinum (50 oz)"], PA: [100, "Palladium (100 oz)"],
  // energy
  MCL: [100, "Micro WTI Crude (100 bbl)"], CL: [1000, "WTI Crude (1,000 bbl)"], NG: [10000, "Natural Gas (10,000 mmBtu)"],
  // currencies (USD per unit of foreign currency)
  M6E: [12500, "Micro EUR/USD"], M6B: [6250, "Micro GBP/USD"], M6A: [10000, "Micro AUD/USD"], MCD: [10000, "Micro CAD/USD"], MSF: [12500, "Micro CHF/USD"],
  "6E": [125000, "Euro FX"], "6B": [62500, "British Pound"], "6A": [100000, "Australian Dollar"], "6C": [100000, "Canadian Dollar"], "6S": [125000, "Swiss Franc"],
  // rates
  ZN: [1000, "10-Year T-Note"], ZB: [1000, "30-Year T-Bond"], ZF: [1000, "5-Year T-Note"], ZT: [2000, "2-Year T-Note"], UB: [1000, "Ultra T-Bond"],
  // crypto
  MBT: [0.1, "Micro Bitcoin (0.1 BTC)"], BTC: [5, "Bitcoin (5 BTC)"], MET: [0.1, "Micro Ether (0.1 ETH)"], ETH: [50, "Ether (50 ETH)"],
};

const MONTHS = "FGHJKMNQUVXZ";
const ROOTS = Object.keys(FUTURES).sort((a, b) => b.length - a.length).join("|");
const RE = new RegExp(`^(${ROOTS})([${MONTHS}])(\\d{1,2})$`);

/** { root, mult, name } for a futures contract symbol such as "MNQZ9", else null. */
export function futuresInfo(ticker) {
  const m = RE.exec(String(ticker || "").trim().toUpperCase());
  return m ? { root: m[1], mult: FUTURES[m[1]][0], name: FUTURES[m[1]][1] } : null;
}

/** Dollars per 1.00 move in the quoted price for one contract (1 for stocks and anything unrecognised). */
export const futuresMultiplier = ticker => futuresInfo(ticker)?.mult ?? 1;
