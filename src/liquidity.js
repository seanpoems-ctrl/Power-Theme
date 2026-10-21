// Minimum average dollar volume for the ETF holdings lists (EtfHoldingsModal, ThemeGroupPopup). The user only trades liquid stocks, so holdings under this
// are hidden by default; the minimum (in $ millions) is editable in the popups and remembered here. 0 = show everything.

const KEY = "etf_holdings_min_dvol_m_v1";
export const DEFAULT_MIN_DVOL_M = 100;

export function loadMinDollarVolM() {
  try {
    const v = parseFloat(localStorage.getItem(KEY));
    return Number.isFinite(v) && v >= 0 ? v : DEFAULT_MIN_DVOL_M;
  } catch { return DEFAULT_MIN_DVOL_M; }
}

export function saveMinDollarVolM(v) {
  try { localStorage.setItem(KEY, String(v)); } catch { /* private mode / quota */ }
}

/** True when a holding's avg dollar volume clears the minimum (in $M). A missing value can't be verified, so it does not pass while a minimum is set. */
export const isLiquid = (dollarVolume, minM) => !(minM > 0) || (Number.isFinite(dollarVolume) && dollarVolume >= minM * 1e6);
