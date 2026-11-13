// "ADR× Avg$Vol" = ADR% × average daily dollar volume (the same number the Stock Screener ranks by; ADR as the raw percent, e.g. 10.4, not 0.104).
// One helper so every table computes and prints it the same way.

/** ADR% × avg daily $ volume, or null when either input is missing. */
export const adrDollarVol = (adrPct, dollarVol) => {
  const a = Number(adrPct), d = Number(dollarVol);
  return adrPct != null && dollarVol != null && Number.isFinite(a) && Number.isFinite(d) && a > 0 && d > 0 ? a * d : null;
};

/** 162.6M / 9.45B style text (no $ sign, same as the Stock Screener column); "—" when missing. */
export const fmtAdrDollarVol = v => {
  if (v == null || !Number.isFinite(v)) return "—";
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  return `${(v / 1e3).toFixed(0)}K`;
};

/** Header text used on every table. */
export const ADR_DVOL_LABEL = "ADR× Avg$Vol";
export const ADR_DVOL_TIP = "ADR% × Avg Daily $ Vol (same as the Stock Screener)";
