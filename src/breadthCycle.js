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
