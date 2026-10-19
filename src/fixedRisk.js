// Trade Journal — fixed-risk R estimate.
//
// A trade's R multiple needs its stop. Many trades have none on record (IBKR's statement doesn't list a stop that never executed). If every trade is sized
// to risk the same fraction of the account (e.g. 0.15 %), then R ≈ P&L ÷ (equity × that fraction). This turns that into an ESTIMATED R for trades that
// have no real one. A real stop-based R is never replaced, and the estimate is applied only when the journal is displayed — nothing is stored on the trade.

const KEY = "power_theme_fixed_risk_v1";
const DEFAULTS = { on: false, equity: "", pct: "0.15" };

export function loadFixedRisk() {
  try {
    const o = JSON.parse(localStorage.getItem(KEY) || "null");
    if (o && typeof o === "object") return { on: !!o.on, equity: String(o.equity ?? ""), pct: String(o.pct ?? DEFAULTS.pct) };
  } catch { /* corrupt / blocked storage */ }
  return { ...DEFAULTS };
}

export function saveFixedRisk(fr) {
  try { localStorage.setItem(KEY, JSON.stringify(fr)); } catch { /* quota / private mode */ }
}

/** Dollars risked per trade = equity × pct / 100; 0 when the estimate is off or an input isn't a positive number. */
export function fixedRiskDollars(fr) {
  const equity = parseFloat(String(fr.equity).replace(/[$,\s]/g, ""));
  const pct = parseFloat(fr.pct);
  return fr.on && equity > 0 && pct > 0 ? (equity * pct) / 100 : 0;
}

/**
 * Copy of `trades` where each CLOSED trade with no real R gets r_multiple = P&L ÷ riskDollars and r_est = true.
 * `pnlOf(trade)` must return dollar P&L (futures multiplier applied) or NaN. Returns the same array when riskDollars is 0.
 */
export function withEstimatedR(trades, riskDollars, pnlOf) {
  if (!(riskDollars > 0)) return trades;
  return trades.map(t => {
    if (t.r_multiple !== "" && t.r_multiple != null && Number.isFinite(parseFloat(t.r_multiple))) return t;   // a real R always wins
    if (t.exit_price === "" || t.exit_price == null) return t;                                              // still open
    const pnl = pnlOf(t);
    if (!Number.isFinite(pnl)) return t;
    return { ...t, r_multiple: (pnl / riskDollars).toFixed(2), r_est: true };
  });
}
