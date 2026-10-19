import React from "react";
import { BREADTH_PHASES } from "./breadthCycle";

// "Market regime vs QQQ" (Breadth → Breadth Cycle): QQQ's price drawn over the breadth-cycle phase of every session, so you can see when
// breadth said risk-on / risk-off. Phases are our own breadth-cycle scoring (breadthCycle.js); QQQ closes come from breadth_long_history.json
// (`qqq_dates` / `qqq`, written by breadth_history_builder.py).

// Breadth-cycle phase -> the risk mode it implies. Expansion = broad participation, not fading; Repair = recovering from weakness (early
// risk-on, not yet confirmed); Distribution = still decent but fading (take care); Contraction = downside participation dominant.
export const REGIMES = {
  Expansion:    { label: "Risk-on",  note: "Broad participation, not fading",           color: BREADTH_PHASES.Expansion.color },
  Repair:       { label: "Recovery", note: "Early risk-on — recovering, not confirmed", color: BREADTH_PHASES.Repair.color },
  Distribution: { label: "Caution",  note: "Participation fading — tighten up",         color: BREADTH_PHASES.Distribution.color },
  Contraction:  { label: "Risk-off", note: "Downside participation dominant",           color: BREADTH_PHASES.Contraction.color },
};
const ORDER = ["Expansion", "Repair", "Distribution", "Contraction"];
const RANGES = [["6M", 126], ["1Y", 252], ["3Y", 756], ["5Y", 1260], ["10Y", 2520], ["Max", Infinity]];

const useWidth = () => {
  const ref = React.useRef(null);
  const [w, setW] = React.useState(760);
  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const set = () => setW(Math.max(300, Math.round(el.getBoundingClientRect().width)));
    set();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
};

const fullDate = iso => new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
const pct = (v, d = 1) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);

export default function BreadthRegime({ enriched, hist, selDate }) {
  const [range, setRange] = React.useState("1Y");
  const [hover, setHover] = React.useState(null);
  const [wrapRef, W] = useWidth();

  // QQQ close per breadth date (ascending), only where both exist
  const series = React.useMemo(() => {
    if (!hist?.qqq?.length || !enriched?.length) return null;
    const q = new Map(hist.qqq_dates.map((d, i) => [d, hist.qqq[i]]));
    const out = [];
    for (const r of enriched) {
      const c = q.get(r.date);
      if (c != null && r.strength != null) out.push({ date: r.date, close: c, phase: r.phase, strength: r.strength });
    }
    return out.length > 60 ? out : null;
  }, [hist, enriched]);

  // What QQQ did in each regime, over the whole history: share of sessions, annualised return when holding QQQ only in that regime
  // (the return of session i is credited to the regime known at the close of session i−1 — no look-ahead), and the average 21-session forward return.
  const stats = React.useMemo(() => {
    if (!series) return null;
    const agg = Object.fromEntries(ORDER.map(p => [p, { n: 0, sumRet: 0, sumSq: 0, nRet: 0, worst: 0, sumFwd: 0, nFwd: 0, win: 0 }]));
    let totRet = 0, nTot = 0;
    for (let i = 1; i < series.length; i++) {
      const ret = series[i].close / series[i - 1].close - 1;
      const a = agg[series[i - 1].phase];
      if (a) { a.sumRet += ret; a.sumSq += ret * ret; a.nRet++; if (ret < a.worst) a.worst = ret; }
      totRet += ret; nTot++;
    }
    series.forEach((r, i) => {
      const a = agg[r.phase];
      if (!a) return;
      a.n++;
      if (i + 21 < series.length) { const f = series[i + 21].close / r.close - 1; a.sumFwd += f; a.nFwd++; if (f > 0) a.win++; }
    });
    return {
      rows: ORDER.map(p => ({ phase: p, share: (agg[p].n / series.length) * 100, ann: agg[p].nRet ? (agg[p].sumRet / agg[p].nRet) * 252 * 100 : null,
        vol: agg[p].nRet > 1 ? Math.sqrt(Math.max(0, agg[p].sumSq / agg[p].nRet - (agg[p].sumRet / agg[p].nRet) ** 2)) * Math.sqrt(252) * 100 : null,
        worst: agg[p].nRet ? agg[p].worst * 100 : null,
        fwd: agg[p].nFwd ? (agg[p].sumFwd / agg[p].nFwd) * 100 : null, win: agg[p].nFwd ? (agg[p].win / agg[p].nFwd) * 100 : null })),
      allAnn: nTot ? (totRet / nTot) * 252 * 100 : null, from: series[0].date, to: series[series.length - 1].date,
    };
  }, [series]);

  const view = React.useMemo(() => {
    if (!series) return null;
    const upTo = series.filter(r => r.date <= (selDate || "9999"));
    const n = RANGES.find(r => r[0] === range)?.[1] ?? 252;
    return upTo.slice(-Math.min(n, upTo.length));
  }, [series, range, selDate]);

  if (!series || !view || view.length < 2) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
        <div className="text-[11px] font-semibold tracking-wider text-zinc-500">MARKET REGIME VS QQQ</div>
        <p className="text-[12px] text-zinc-600 italic mt-1">QQQ history isn't in the data file yet — it is added by the next nightly run (breadth_history_builder.py).</p>
      </div>
    );
  }

  const H = 280, L = 52, R = 12, T = 12, B = 24;
  const n = view.length;
  const x = i => L + (n <= 1 ? 0 : (i / (n - 1)) * (W - L - R));
  const closes = view.map(r => r.close);
  const lo = Math.min(...closes), hi = Math.max(...closes);
  const pad = (hi - lo) * 0.06 || 1;
  const logScale = n > 600;                       // long ranges: log axis, so 2009's QQQ isn't a flat line next to today's
  const f = v => (logScale ? Math.log(v) : v);
  const fLo = f(lo - pad > 0 ? lo - pad : lo), fHi = f(hi + pad);
  const y = v => T + (1 - (f(v) - fLo) / (fHi - fLo)) * (H - T - B);

  // consecutive same-phase runs -> background bands and coloured line segments
  const runs = [];
  view.forEach((r, i) => {
    const last = runs[runs.length - 1];
    if (last && last.phase === r.phase) last.end = i; else runs.push({ phase: r.phase, start: i, end: i });
  });
  const segPath = run => { const from = Math.max(0, run.start - 1); let d = ""; for (let i = from; i <= run.end; i++) d += `${i === from ? "M" : "L"}${x(i).toFixed(1)},${y(view[i].close).toFixed(1)}`; return d; };
  const tickVals = (() => { const t = []; const steps = 5; for (let k = 0; k <= steps; k++) { const v = logScale ? Math.exp(fLo + ((fHi - fLo) * k) / steps) : lo - pad + ((hi + pad - (lo - pad)) * k) / steps; t.push(v); } return t; })();
  const monthIdx = []; let prevKey = "";
  const yearly = n > 400;
  view.forEach((r, i) => { const key = yearly ? r.date.slice(0, 4) : r.date.slice(0, 7); if (key !== prevKey) { monthIdx.push(i); prevKey = key; } });
  const thin = monthIdx.length > 12 ? Math.ceil(monthIdx.length / 10) : 1;
  const lab = i => (yearly ? view[i].date.slice(0, 4) : new Date(`${view[i].date}T00:00:00`).toLocaleDateString("en-US", { month: "short" }));
  const onMove = e => {
    const rect = e.currentTarget.getBoundingClientRect();
    const i = Math.round(((e.clientX - rect.left - L) / (W - L - R)) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  };
  const cur = view[n - 1];
  const curReg = REGIMES[cur.phase];
  const h = hover != null ? view[hover] : null;
  const tipRight = hover != null && x(hover) > W * 0.62;

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
      <div className="flex items-center gap-3 flex-wrap mb-1">
        <div>
          <div className="text-[11px] font-semibold tracking-wider text-zinc-500">MARKET REGIME VS QQQ</div>
          <div className="text-lg font-bold" style={{ color: curReg.color }}>{curReg.label} <span className="text-[12px] font-normal text-zinc-500">· {cur.phase} on {fullDate(cur.date)}</span></div>
        </div>
        <div className="ml-auto flex bg-zinc-800/60 rounded-lg p-0.5 border border-zinc-700/40">
          {RANGES.map(([l]) => (
            <button key={l} onClick={() => setRange(l)}
              className={`px-2.5 py-1 text-[11px] font-medium rounded-md transition-all ${range === l ? "bg-blue-500/20 text-blue-400 border border-blue-500/30" : "text-zinc-500 hover:text-zinc-300 border border-transparent"}`}>{l}</button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 mb-2" role="list" aria-label="Regime legend">
        {ORDER.map(p => (
          <span key={p} role="listitem" title={`${REGIMES[p].label} = breadth cycle "${p}": ${REGIMES[p].note}`} className="flex items-center gap-1.5 text-[12px] text-zinc-300">
            <span className="w-3 h-1.5 rounded-sm" style={{ background: REGIMES[p].color }} />
            <span className="font-semibold">{REGIMES[p].label}</span>
            <span className="text-zinc-600 text-[11px]">{p}</span>
          </span>
        ))}
      </div>

      <div ref={wrapRef} className="relative">
        <svg width={W} height={H} role="img" aria-label="QQQ price coloured by breadth-cycle regime" onMouseMove={onMove} onMouseLeave={() => setHover(null)} className="block">
          {runs.map((run, k) => (
            <rect key={k} x={x(Math.max(0, run.start - 0.5))} y={T} width={Math.max(0.6, x(Math.min(n - 1, run.end + 0.5)) - x(Math.max(0, run.start - 0.5)))} height={H - T - B}
              fill={REGIMES[run.phase].color} opacity="0.13" />
          ))}
          {tickVals.map((v, k) => (
            <g key={k}>
              <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="#3f3f46" strokeOpacity="0.35" />
              <text x={L - 6} y={y(v) + 3.5} textAnchor="end" fontSize="10" fill="#71717a" fontFamily="ui-monospace, monospace">{v >= 100 ? v.toFixed(0) : v.toFixed(1)}</text>
            </g>
          ))}
          {monthIdx.filter((_, j) => j % thin === 0).map(i => (
            <text key={i} x={x(i)} y={H - 7} textAnchor="middle" fontSize="10" fill="#71717a">{lab(i)}</text>
          ))}
          {runs.map((run, k) => (
            <path key={k} d={segPath(run)} fill="none" stroke={REGIMES[run.phase].color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {h && (
            <g pointerEvents="none">
              <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} stroke="#a1a1aa" strokeOpacity="0.5" />
              <circle cx={x(hover)} cy={y(h.close)} r="4.5" fill={REGIMES[h.phase].color} stroke="#18181b" strokeWidth="2" />
            </g>
          )}
          <rect x={L} y={T} width={Math.max(0, W - L - R)} height={H - T - B} fill="transparent" />
        </svg>
        {h && (
          <div className="absolute pointer-events-none rounded-lg border border-zinc-700 bg-zinc-900/95 px-3 py-2 text-[12px] shadow-xl"
            style={{ top: 28, ...(tipRight ? { right: W - x(hover) + 12 } : { left: x(hover) + 12 }) }}>
            <div className="text-zinc-400 mb-1">{fullDate(h.date)}</div>
            <div className="flex items-center gap-2"><span className="w-2 h-2 rounded-full" style={{ background: REGIMES[h.phase].color }} /><span className="font-bold text-zinc-100">{REGIMES[h.phase].label}</span><span className="text-zinc-500">{h.phase}</span></div>
            <div className="text-zinc-300 tabular-nums">QQQ <span className="font-bold text-zinc-100">{h.close.toFixed(2)}</span></div>
            <div className="text-zinc-300 tabular-nums">Breadth strength <span className="font-bold text-zinc-100">{h.strength.toFixed(0)}</span> / 100</div>
          </div>
        )}
      </div>

      {stats && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-[12px] border-collapse">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide text-zinc-500">
                <th className="py-1.5 pr-3 text-left font-medium">Regime</th>
                <th className="py-1.5 px-2 text-right font-medium" title="Share of all sessions">Time in regime</th>
                <th className="py-1.5 px-2 text-right font-medium" title="Average daily QQQ return earned while the PREVIOUS close was in this regime, annualised ×252 (no look-ahead)">QQQ return, annualised</th>
                <th className="py-1.5 px-2 text-right font-medium" title="Annualised standard deviation of QQQ's daily returns while the previous close was in this regime — how bumpy the ride was">Volatility</th>
                <th className="py-1.5 px-2 text-right font-medium" title="QQQ's worst single day while the previous close was in this regime">Worst day</th>
                <th className="py-1.5 px-2 text-right font-medium" title="Average QQQ return over the next 21 sessions after a day in this regime">Next 21 days (avg)</th>
                <th className="py-1.5 pl-2 text-right font-medium" title="How often QQQ was higher 21 sessions later">Up after 21 days</th>
              </tr>
            </thead>
            <tbody>
              {stats.rows.map(r => (
                <tr key={r.phase} className="border-t border-zinc-800/70">
                  <td className="py-1.5 pr-3"><span className="inline-flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full" style={{ background: REGIMES[r.phase].color }} /><span className="font-semibold text-zinc-200">{REGIMES[r.phase].label}</span><span className="text-zinc-600 text-[11px]">{r.phase}</span></span></td>
                  <td className="py-1.5 px-2 text-right font-mono text-zinc-400">{r.share.toFixed(0)}%</td>
                  <td className={`py-1.5 px-2 text-right font-mono ${r.ann == null ? "text-zinc-600" : r.ann >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{pct(r.ann, 0)}</td>
                  <td className="py-1.5 px-2 text-right font-mono text-zinc-300">{r.vol == null ? "—" : `${r.vol.toFixed(0)}%`}</td>
                  <td className="py-1.5 px-2 text-right font-mono text-rose-300">{pct(r.worst, 1)}</td>
                  <td className={`py-1.5 px-2 text-right font-mono ${r.fwd == null ? "text-zinc-600" : r.fwd >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{pct(r.fwd, 1)}</td>
                  <td className="py-1.5 pl-2 text-right font-mono text-zinc-300">{r.win == null ? "—" : `${r.win.toFixed(0)}%`}</td>
                </tr>
              ))}
              <tr className="border-t border-zinc-700/80 text-zinc-500">
                <td className="py-1.5 pr-3">All sessions (buy & hold)</td><td className="py-1.5 px-2 text-right font-mono">100%</td>
                <td className="py-1.5 px-2 text-right font-mono">{pct(stats.allAnn, 0)}</td><td /><td /><td /><td />
              </tr>
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-zinc-600 leading-snug">
            Statistics cover {fullDate(stats.from)} – {fullDate(stats.to)}. The regimes are our own breadth-cycle scoring (strength ≥ 55 and not falling = Risk-on; falling = Caution;
            low and falling = Risk-off; low but up ≥ 5 pts in 5 sessions = Recovery) — in-sample context for risk appetite, not a trading system. Note what the numbers
            say: Risk-off and Recovery are the BUMPY regimes (roughly twice the volatility and bigger worst days), but QQQ's average return after them has not been lower —
            washed-out breadth is also where many bounces begin. Read the regime as risk level and position-sizing context, not as a sell signal.
          </p>
        </div>
      )}
    </div>
  );
}
