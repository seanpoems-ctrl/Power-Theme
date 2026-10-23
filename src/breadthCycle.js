// Breadth Cycle scoring shared by the Breadth Cycle page, the Market Pulse chips and the Market Situation brief.
//
// Input rows come from public/breadth_monitor.json (the Stockbee market-monitor sheet). Everything here is OUR OWN scoring — the reference
// page that inspired it does not publish a formula:
//
//   Strength (0–100) = equal-weight mean of six 0–100 scores: daily up/(up+down) of ±4% movers; 5-day ratio; 10-day ratio
//   (ratio 1 → 50, 2 → 100, 0.5 → 0); quarter ±25% up share; 34-day ±13% up share; T2108.
//
//   Phase: Expansion = strength ≥ 55 and not falling over 5 sessions; Distribution = falling (strength ≥ 45, or ≥ 55);
//   Repair = strength < 55 and up ≥ 5 pts over 5 sessions; otherwise Contraction.
//
//   Divergence (breadthDivergence) compares the Market Pulse trend signal (green/yellow/orange/red, from SPY/QQQ/IWM vs their
//   EMAs) with the breadth phase, e.g. index uptrend + weak participation = "Narrow".
import React from "react";

export const BREADTH_PHASES = {
  Expansion:    { color: "#5fd4b0", text: "Upside participation is broad and not fading. Trend-following setups have the wind behind them." },
  Distribution: { color: "#e5b567", text: "Participation is still decent but weakening. Be selective and protect gains." },
  Contraction:  { color: "#ff6b8b", text: "Downside participation is dominant. Breadth remains under pressure until the short-term measures begin to repair." },
  Repair:       { color: "#6db0ff", text: "Breadth is recovering from weak levels. Early improvement — wait for the longer measures to confirm." },
};

export const breadthScore = (r) => {
  const share = (a, b) => (a + b) > 0 ? (a / (a + b)) * 100 : 50;
  const ratio = (x) => x > 0 ? Math.max(0, Math.min(100, 50 + 50 * Math.log2(x))) : 0;
  const parts = [
    share(r.up_4_pct, r.down_4_pct), ratio(r.ratio_5d), ratio(r.ratio_10d),
    share(r.up_25_q, r.down_25_q), share(r.up_13_34d, r.down_13_34d), r.t2108,
  ];
  const ok = parts.filter((p) => p != null && Number.isFinite(p));
  return ok.length ? ok.reduce((a, b) => a + b, 0) / ok.length : null;
};

export const breadthPhase = (strength, change) => {
  if (strength >= 55 && change >= 0) return "Expansion";
  if (strength >= 45 && change < 0) return "Distribution";
  if (strength >= 55) return "Distribution";
  if (change >= 5) return "Repair";
  return "Contraction";
};

// rows -> ascending-by-date rows, each with { strength, change (vs 5 sessions earlier), phase }.
export function computeBreadthCycle(rows) {
  const asc = [...(rows || [])].filter((r) => r && r.date).sort((a, b) => a.date.localeCompare(b.date));
  const withS = asc.map((r) => ({ ...r, strength: breadthScore(r) }));
  return withS.map((r, i) => {
    const prev = withS[i - 5];
    const change = prev && prev.strength != null && r.strength != null ? r.strength - prev.strength : 0;
    return { ...r, change, phase: r.strength != null ? breadthPhase(r.strength, change) : "Contraction" };
  });
}

// Confirmed regime: a new phase is only adopted after CONFIRM_SESSIONS sessions in a row. On 2009–2026 that cut colour changes by ~75 % (1,678 → 448),
// removed every one-day regime, and kept the volatility gap between the calm and the bumpy regimes (CLAUDE.md → "Market regime vs QQQ").
// Cost: a real change is shown two sessions late. Input rows ascending with `.phase`; adds `confirmed`, `daysInRegime` (counted from the first of the
// confirming sessions), and `pendingPhase` / `pendingCount` (a different raw phase that has not been confirmed yet).
export const CONFIRM_SESSIONS = 3;
export function addConfirmedPhase(rows, n = CONFIRM_SESSIONS) {
  let shown = rows?.[0]?.phase ?? null, cand = null, cnt = 0, start = 0;
  return (rows || []).map((r, i) => {
    if (r.phase === shown) { cand = null; cnt = 0; }
    else if (r.phase === cand) { cnt++; if (cnt >= n) { shown = r.phase; start = i - (n - 1); cand = null; cnt = 0; } }
    else { cand = r.phase; cnt = 1; if (n <= 1) { shown = r.phase; start = i; cand = null; cnt = 0; } }
    return { ...r, confirmed: shown, daysInRegime: i - start + 1, pendingPhase: cand, pendingCount: cnt };
  });
}

/**
 * Staged re-entry exposure series (0 / 0.5 / 1) from two daily flags: `riskOff[i]` = the confirmed regime is Risk-off, `below[i]` = QQQ closed under its 50-day EMA.
 * Out (0) the day BOTH are true. Once out, coming back is staged: half (0.5) as soon as EITHER clears, fully (1) once BOTH have cleared (a day with both true
 * again goes back to 0). While never out, exposure stays 1 even if only one flag is true — the rule only stages the way BACK in, not the way out.
 */
export function stagedReentryExposure(riskOff, below) {
  const out = [];
  let isOut = false;
  for (let i = 0; i < riskOff.length; i++) {
    const a = !!riskOff[i], b = !!below[i];
    if (a && b) { isOut = true; out.push(0); }
    else if (isOut && (a || b)) out.push(0.5);
    else { isOut = false; out.push(1); }
  }
  return out;
}

/**
 * Slow re-entry exposure series (0 / 1): same exit as the staged rule (confirmed Risk-off AND QQQ under its 50-day EMA), but once out you stay in cash until
 * BOTH flags have cleared. Fewest switches of the regime rules; costs a little return versus coming back at the first sign.
 */
export function slowReentryExposure(riskOff, below) {
  const out = [];
  let isOut = false;
  for (let i = 0; i < riskOff.length; i++) {
    const a = !!riskOff[i], b = !!below[i];
    if (a && b) isOut = true;
    else if (isOut && !a && !b) isOut = false;
    out.push(isOut ? 0 : 1);
  }
  return out;
}

// Exposure guide for a swing trader: the confirmed regime paired with QQQ's own trend (+ T2108). Built from the backtests in CLAUDE.md: the
// confirmed Risk-off regime AND QQQ below its 50-day EMA was the best pairing (it beat the regime alone in every window); the 9 / 21 EMAs were
// worse as market-level exits (too noisy), so the 21-day only tightens things up and the 200-day is the bear-market line.
//   confirmed: "Expansion" | "Repair" | "Distribution" | "Contraction";  qqq: { close, e21, e50, s200 } (any may be missing)
// Returns { key: full | build | reduce | defensive | bounce, label, tone: ok | info | warn | bad, line, why: [...] }.
export function exposureGuide({ confirmed, t2108, qqq } = {}) {
  const ge = (a, b) => (a != null && b != null ? a >= b : null);
  const a21 = ge(qqq?.close, qqq?.e21), a50 = ge(qqq?.close, qqq?.e50), a200 = ge(qqq?.close, qqq?.s200);
  const washed = t2108 != null && t2108 < 20;
  const res = (key, label, tone, line, why) => ({ key, label, tone, line, why });
  const trend = [];
  if (a21 === false) trend.push("QQQ below its 21-day EMA");
  if (a50 === false) trend.push("QQQ below its 50-day EMA");
  if (a200 === false) trend.push("QQQ below its 200-day average");
  if (confirmed === "Contraction") {
    if (washed) return res("bounce", "Bounce watch", "info", "Breadth is washed out (T2108 under 20). Small, short-term trades only — don't chase new lows.", ["Confirmed Risk-off", "T2108 under 20", ...trend]);
    if (a50 === false || a200 === false) return res("defensive", "Defensive", "bad", "No new longs except top-quality names. Smaller size, tight stops.", ["Confirmed Risk-off", ...trend]);
    return res("reduce", "Reduce", "warn", "Breadth is weak but QQQ still holds its 50-day. Half size, tighten stops, fewer new entries.", ["Confirmed Risk-off", "QQQ above its 50-day EMA"]);
  }
  if (confirmed === "Distribution") return res("reduce", "Reduce", "warn", "Participation is fading. Half size, tighten stops, don't add to extended names.", ["Confirmed Caution", ...trend]);
  if (confirmed === "Expansion" || confirmed === "Repair") {
    const name = confirmed === "Expansion" ? "Confirmed Risk-on" : "Confirmed Recovery";
    if (a50 === false) return res("reduce", "Reduce", "warn", "Breadth looks better, but QQQ is below its 50-day EMA. Keep size down until it reclaims it.", [name, ...trend]);
    if (a21 === false) return res("reduce", "Reduce", "warn", "QQQ has slipped under its 21-day EMA. Tighten stops and slow new entries until it recovers.", [name, ...trend]);
    if (confirmed === "Repair") return res("build", "Build", "info", "Breadth is recovering from weak levels and QQQ is above its 21 and 50-day. Starter positions; add as it turns Risk-on.", [name, "QQQ above its 21 and 50-day EMAs"]);
    return res("full", "Full size", "ok", "Broad participation and QQQ above its 21 and 50-day EMAs. Normal size — press quality breakouts.", [name, "QQQ above its 21 and 50-day EMAs"]);
  }
  return res("reduce", "No read", "warn", "Not enough data for a regime read.", []);
}

// Rows (ascending) in the Stockbee sheet's shape, built from public/breadth_long_history.json: the 5 / 10-day ratios are recomputed as
// sum(up 4 %) / sum(down 4 %), the S&P close comes from Yahoo. Shared by the Breadth Cycle page and the Trade Journal's regime tag.
export function rowsFromLongHistory(hist) {
  const c = hist.cols, spxJ = new Map(hist.spx_dates.map((d, i) => [d, i]));
  const cu = [0], cd = [0];
  hist.dates.forEach((_, i) => { cu.push(cu[i] + (c.up_4_pct[i] || 0)); cd.push(cd[i] + (c.down_4_pct[i] || 0)); });
  const ratio = (i, k) => (cd[i + 1] - cd[i + 1 - k] > 0 ? (cu[i + 1] - cu[i + 1 - k]) / (cd[i + 1] - cd[i + 1 - k]) : 1);
  return hist.dates.map((date, i) => ({
    date, up_4_pct: c.up_4_pct[i], down_4_pct: c.down_4_pct[i], ratio_5d: i >= 4 ? ratio(i, 5) : 1, ratio_10d: i >= 9 ? ratio(i, 10) : 1,
    up_25_q: c.up_25_q[i], down_25_q: c.down_25_q[i], up_25_m: c.up_25_m[i], down_25_m: c.down_25_m[i], up_50_m: c.up_50_m[i], down_50_m: c.down_50_m[i],
    up_13_34d: c.up_13_34d[i], down_13_34d: c.down_13_34d[i], t2108: c.t2108[i], sp_index: spxJ.has(date) ? hist.spx[spxJ.get(date)] : null,
  })).slice(10);       // the first days have no 10-day ratio
}

// Regime calendar for the Trade Journal: every session since 2009 with its confirmed regime (3 sessions in a row). One shared load.
let _calP = null;
export function loadRegimeCalendar() {
  if (!_calP) {
    _calP = fetch(`${process.env.PUBLIC_URL || ""}/breadth_long_history.json?v=${new Date().toISOString().slice(0, 13)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((h) => {
        if (!h?.dates?.length) return null;
        const rows = addConfirmedPhase(computeBreadthCycle(rowsFromLongHistory(h)));
        return { rows, dates: rows.map((r) => r.date) };
      })
      .catch(() => null);
    _calP.then((v) => { if (!v) _calP = null; });
  }
  return _calP;
}

// The regime a trader could SEE when entering on `iso` (YYYY-MM-DD): the confirmed regime at the close of the last session strictly BEFORE that
// date — the entry day's own breadth is only known after the close, so using it would be look-ahead. Null when there is no earlier session.
export function regimeBefore(cal, iso) {
  if (!cal?.dates?.length || !iso) return null;
  const d = cal.dates;
  let lo = 0, hi = d.length;                       // first index with date >= iso
  while (lo < hi) { const mid = (lo + hi) >> 1; if (d[mid] < iso) lo = mid + 1; else hi = mid; }
  const r = cal.rows[lo - 1];
  return r && r.confirmed ? { date: r.date, confirmed: r.confirmed, raw: r.phase, strength: r.strength, days: r.daysInRegime } : null;
}

// The most recent session's cycle reading (or null).
export function latestBreadthCycle(rows) {
  const all = computeBreadthCycle(rows);
  const r = all[all.length - 1];
  if (!r || r.strength == null) return null;
  return {
    date: r.date, strength: r.strength, change: r.change, phase: r.phase, t2108: r.t2108 ?? null,
    up4: r.up_4_pct, down4: r.down_4_pct, ratio5d: r.ratio_5d, ratio10d: r.ratio_10d,
  };
}

// Market Pulse trend signal vs breadth phase. Returns null when there is nothing worth flagging.
//   tone: "warn" (amber), "info" (blue), "ok" (green), "bad" (red)
export function breadthDivergence(signal, cycle) {
  if (!cycle || !signal) return null;
  const { phase, strength, t2108 } = cycle;
  const s = Math.round(strength);
  const t = t2108 != null ? `, T2108 ${t2108.toFixed(1)}%` : "";
  const weak = phase === "Contraction" || phase === "Repair";
  const up = signal === "green", pullback = signal === "yellow", down = signal === "orange" || signal === "red";
  if (up && weak)
    return { kind: "narrow", tone: "warn", label: "Narrow",
      text: `Index uptrend, but participation is weak (breadth ${phase}, strength ${s}${t}). Leadership is narrow — be selective and favor RS leaders.` };
  if (up && phase === "Distribution")
    return { kind: "fading", tone: "warn", label: "Fading",
      text: `Index uptrend, but participation is fading (breadth Distribution, strength ${s}${t}). Protect gains; don't chase extended names.` };
  if (up && phase === "Expansion")
    return { kind: "confirmed", tone: "ok", label: "Confirmed",
      text: `Breadth confirms the uptrend (Expansion, strength ${s}${t}).` };
  if (pullback && weak)
    return { kind: "no-support", tone: "warn", label: "No support",
      text: `Index pullback with weak participation (breadth ${phase}, strength ${s}${t}) — no internal support yet.` };
  if (down && (phase === "Repair" || (t2108 != null && t2108 <= 20)))
    return { kind: "bounce", tone: "info", label: "Bounce watch",
      text: `Weak tape, but breadth is oversold/repairing (${phase}, strength ${s}${t}) — relief-rally risk for shorts.` };
  if (down && phase === "Contraction")
    return { kind: "confirms-weakness", tone: "bad", label: "Confirms weakness",
      text: `Breadth confirms the weak tape (Contraction, strength ${s}${t}).` };
  return null;
}

// One shared load of breadth_monitor.json for every consumer (chips, brief, page).
let _promise = null;
export function loadBreadthCycle() {
  if (!_promise) {
    _promise = fetch(`${process.env.PUBLIC_URL || ""}/breadth_monitor.json?v=${new Date().toISOString().slice(0, 13)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => (d?.rows?.length ? latestBreadthCycle(d.rows) : null))
      .catch(() => null);
    _promise.then((v) => { if (!v) _promise = null; });   // don't cache a failure for the whole session
  }
  return _promise;
}

export function useBreadthCycle() {
  const [cycle, setCycle] = React.useState(null);
  React.useEffect(() => {
    let alive = true;
    loadBreadthCycle().then((c) => { if (alive) setCycle(c); });
    return () => { alive = false; };
  }, []);
  return cycle;
}
