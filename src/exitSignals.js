// Exit / re-entry signals for the Market Status panel (Breadth → Breadth Cycle), built from the same rules as the Backtest table.
// Inputs: the enriched breadth-cycle rows (with the CONFIRMED regime) and breadth_long_history.json (QQQ closes). A signal is read at the close and applies to the
// NEXT session. States: 1 = fully invested, 0.5 = half, 0 = cash.

import { _emaSeries } from "./chartTheme";
import { stagedReentryExposure, slowReentryExposure } from "./breadthCycle";

/** QQQ closes + 9 / 21 / 50-day EMAs lined up with the confirmed regime, one entry per session that has both. null when data is missing. */
export function alignQqq(enriched, hist) {
  if (!hist?.qqq?.length || !enriched?.length) return null;
  const c = hist.qqq, idx = new Map(hist.qqq_dates.map((d, i) => [d, i]));
  const e9 = _emaSeries(c, 9), e21 = _emaSeries(c, 21), e50 = _emaSeries(c, 50);
  const rows = enriched.filter(r => r.confirmed && idx.has(r.date));
  if (rows.length < 2) return null;
  const at = a => rows.map(r => a[idx.get(r.date)]);
  const px = at(c), E9 = at(e9), E21 = at(e21), E50 = at(e50);
  return {
    dates: rows.map(r => r.date), px, e9: E9, e21: E21, e50: E50,
    riskOff: rows.map(r => r.confirmed === "Contraction"),
    below50: px.map((p, i) => E50[i] != null && p < E50[i]),
  };
}

/** Exposure series (0 / 0.5 / 1) for each rule over the aligned sessions. */
export function ruleSeries(a) {
  const both = a.riskOff.map((r, i) => r && a.below50[i]);
  return {
    staged: stagedReentryExposure(a.riskOff, a.below50),
    slow: slowReentryExposure(a.riskOff, a.below50),
    pairing: both.map(b => (b ? 0 : 1)),
    cross: a.e9.map((v, i) => (v > a.e21[i] ? 1 : 0)),
  };
}

/** Where a series stands at index i: its value, the date it last changed to that value, and how many sessions ago that was (0 = today). */
export function signalAt(series, dates, i) {
  let j = i;
  while (j > 0 && series[j - 1] === series[i]) j--;
  return { value: series[i], since: dates[j], sessions: i - j, firstSession: j === 0 };
}

/** Index of the last aligned session on or before `iso` (-1 when none). */
export function indexOnOrBefore(dates, iso) {
  let k = -1;
  for (let i = 0; i < dates.length; i++) { if (dates[i] <= iso) k = i; else break; }
  return k;
}
