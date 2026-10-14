import React from "react";

// Weinstein Stage Analysis card for the Breadth tab. Data: public/stage_analysis.json (stage_analysis_builder.py — ~2,800 US stocks ≥ $500M,
// weekly stages from the 30-week MA). Shows the share of stocks in each stage now (stacked bar + tiles) and over time (line chart),
// for the whole market or one sector.

// Stage colours follow the reference layout (grey / blue / amber / pink). S1 is deliberately neutral — it is the "base" stage — so it is always
// identified by its label and legend, never by colour alone.
export const STAGES = [
  { k: 1, key: "s1", label: "S1", name: "Basing",    color: "#8b93a1", tip: "Stage 1 — basing: after a decline the 30-week MA flattens and price chops sideways around it; volume dries up." },
  { k: 2, key: "s2", label: "S2", name: "Advancing", color: "#3b82f6", tip: "Stage 2 — advancing: price above BOTH the 30-week and 200-day MAs, with the 30-week rising and the 200-day not falling. The stage to buy; it starts with a high-volume breakout from the base." },
  { k: 3, key: "s3", label: "S3", name: "Topping",   color: "#bf8a0b", tip: "Stage 3 — topping: after an advance the 30-week / 200-day MAs flatten and price churns around them (erratic swings, failed breakouts, slicing below the 10-week MA)." },
  { k: 4, key: "s4", label: "S4", name: "Declining", color: "#ec4899", tip: "Stage 4 — declining: price below BOTH the 30-week and 200-day MAs, both falling, with lower highs and lower lows. Avoid longs." },
];

let _stageP = null;
export const loadStageAnalysis = () => {
  if (!_stageP) {
    _stageP = fetch(`${process.env.PUBLIC_URL}/stage_analysis.json?v=${Date.now()}`).then(r => (r.ok ? r.json() : null)).catch(() => null);
  }
  return _stageP;
};


/**
 * Condensed stage reading for the Market Situation (Gemini) prompt: market-wide shares now, their 1- and 4-week change, where S2 sits in its
 * range for the loaded history, and the strongest / weakest sectors by S2 share. Returns null when the file is unavailable.
 */
export function buildStageBrief(data) {
  const all = data?.groups?.All;
  if (!all) return null;
  const last = data.weeks.length - 1;
  const share = (g, k, i) => { const tot = STAGES.reduce((a, s) => a + g[s.key][i], 0); return tot ? (g[`s${k}`][i] / tot) * 100 : 0; };
  const r1 = v => Math.round(v * 10) / 10;
  const at = (g, k, back) => share(g, k, Math.max(0, last - back));
  const row = g => ({
    s1_pct: r1(share(g, 1, last)), s2_pct: r1(share(g, 2, last)), s3_pct: r1(share(g, 3, last)), s4_pct: r1(share(g, 4, last)),
    s2_change_1w_pts: r1(share(g, 2, last) - at(g, 2, 1)), s2_change_4w_pts: r1(share(g, 2, last) - at(g, 2, 4)),
    s4_change_1w_pts: r1(share(g, 4, last) - at(g, 4, 1)), s4_change_4w_pts: r1(share(g, 4, last) - at(g, 4, 4)),
  });
  const s2Hist = data.weeks.map((_, i) => share(all, 2, i));
  const s4Hist = data.weeks.map((_, i) => share(all, 4, i));
  const sectors = Object.entries(data.groups).filter(([k]) => k !== "All").map(([name, g]) => ({ name, stocks: g.n, ...row(g) }));
  const bySector = [...sectors].sort((a, b) => b.s2_pct - a.s2_pct);
  const slim = x => ({ sector: x.name, s2_pct: x.s2_pct, s4_pct: x.s4_pct, s2_change_4w_pts: x.s2_change_4w_pts });
  const worstTrend = [...sectors].sort((a, b) => a.s2_change_4w_pts - b.s2_change_4w_pts).slice(0, 2).map(slim);
  return {
    as_of: data.as_of, stocks_classified: all.n,
    market: row(all),
    s2_pct_range_over_history: {
      low: r1(Math.min(...s2Hist)), high: r1(Math.max(...s2Hist)), weeks: data.weeks.length,
      // where today's S2 share sits between that low (0) and high (100) — so the model need not judge "near the low / high" itself
      current_position_0_to_100: Math.round(((s2Hist[last] - Math.min(...s2Hist)) / ((Math.max(...s2Hist) - Math.min(...s2Hist)) || 1)) * 100),
    },
    s4_pct_range_over_history: { low: r1(Math.min(...s4Hist)), high: r1(Math.max(...s4Hist)) },
    strongest_sectors_by_s2: bySector.slice(0, 3).map(slim),
    weakest_sectors_by_s2: bySector.slice(-3).reverse().map(slim),
    sectors_losing_s2_fastest_4w: worstTrend,
  };
}

const RANGES = [["6M", 26], ["1Y", 52], ["2Y", 104]];

const useWidth = () => {
  const ref = React.useRef(null);
  const [w, setW] = React.useState(640);
  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const set = () => setW(Math.max(280, Math.round(el.getBoundingClientRect().width)));
    set();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
};

const pct = (n, tot) => (tot > 0 ? (n / tot) * 100 : 0);
const monthLabel = iso => new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short" });
const fullDate = iso => new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });

const StageChart = ({ weeks, series, hidden, onToggle }) => {
  const [wrapRef, W] = useWidth();
  const [hover, setHover] = React.useState(null);
  const H = 190, L = 34, R = 10, T = 10, B = 22;
  const n = weeks.length;
  const visible = STAGES.filter(s => !hidden.has(s.key));
  const maxV = Math.max(10, ...visible.flatMap(s => series[s.key]));
  const top = Math.min(100, Math.ceil((maxV + 2) / 10) * 10);
  const x = i => L + (n <= 1 ? 0 : (i / (n - 1)) * (W - L - R));
  const y = v => T + (1 - v / top) * (H - T - B);
  const path = vals => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const ticks = [];
  for (let v = 0; v <= top; v += top > 60 ? 20 : 10) ticks.push(v);
  // month labels: first week of each month (thinned when the range is long)
  const monthIdx = [];
  let prev = "";
  weeks.forEach((d, i) => { const m = d.slice(0, 7); if (m !== prev) { monthIdx.push(i); prev = m; } });
  const step = monthIdx.length > 13 ? 3 : monthIdx.length > 8 ? 2 : 1;
  const onMove = e => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const i = Math.round(((px - L) / (W - L - R)) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  };
  const tipLeft = hover != null && x(hover) > W * 0.62;

  return (
    <div ref={wrapRef} className="relative">
      <div className="flex items-center justify-end gap-3 mb-1 text-[12px]" role="group" aria-label="Show or hide stages">
        {STAGES.map(s => {
          const off = hidden.has(s.key);
          return (
            <button key={s.key} onClick={() => onToggle(s.key)} aria-pressed={!off} title={`${off ? "Show" : "Hide"} ${s.label} — ${s.name}`}
              className={`flex items-center gap-1.5 font-semibold transition-opacity ${off ? "opacity-40 hover:opacity-70" : ""}`}>
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: s.color }} />
              <span className="text-zinc-300">{s.label}</span>
            </button>
          );
        })}
      </div>
      <svg width={W} height={H} role="img" aria-label="Share of stocks in each stage over time" onMouseMove={onMove} onMouseLeave={() => setHover(null)} className="block">
        {ticks.map(v => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="#3f3f46" strokeOpacity={v === 0 ? 0.9 : 0.35} strokeWidth="1" />
            <text x={L - 6} y={y(v) + 3.5} textAnchor="end" fontSize="10" fill="#71717a" fontFamily="ui-monospace, monospace">{v}%</text>
          </g>
        ))}
        {monthIdx.filter((_, j) => j % step === 0).map(i => (
          <text key={i} x={x(i)} y={H - 6} textAnchor="middle" fontSize="10" fill="#71717a">{monthLabel(weeks[i])}</text>
        ))}
        {visible.map(s => (
          <path key={s.key} d={path(series[s.key])} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        ))}
        {hover != null && (
          <g pointerEvents="none">
            <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} stroke="#a1a1aa" strokeOpacity="0.45" strokeWidth="1" />
            {visible.map(s => (
              <circle key={s.key} cx={x(hover)} cy={y(series[s.key][hover])} r="4" fill={s.color} stroke="#18181b" strokeWidth="2" />
            ))}
          </g>
        )}
        {/* transparent hit area so hovering works anywhere on the plot */}
        <rect x={L} y={T} width={Math.max(0, W - L - R)} height={H - T - B} fill="transparent" />
      </svg>
      {hover != null && visible.length > 0 && (
        <div className="absolute pointer-events-none rounded-lg border border-zinc-700 bg-zinc-900/95 px-3 py-2 text-[12px] shadow-xl"
          style={{ top: 34, ...(tipLeft ? { right: W - x(hover) + 12 } : { left: x(hover) + 12 }) }}>
          <div className="text-zinc-400 mb-1">{fullDate(weeks[hover])}</div>
          {visible.map(s => (
            <div key={s.key} className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full" style={{ background: s.color }} />
              <span className="text-zinc-300">{s.label}:</span>
              <span className="font-bold text-zinc-100 tabular-nums">{series[s.key][hover].toFixed(0)}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const StageAnalysisCard = () => {
  const [data, setData] = React.useState(null);
  const [state, setState] = React.useState("loading");     // loading | ready | missing
  const [group, setGroup] = React.useState("All");
  const [range, setRange] = React.useState("1Y");
  const [hidden, setHidden] = React.useState(() => new Set(["s1", "s3"]));
  React.useEffect(() => {
    let dead = false;
    loadStageAnalysis().then(d => { if (dead) return; if (d?.groups?.All) { setData(d); setState("ready"); } else setState("missing"); });
    return () => { dead = true; };
  }, []);
  const toggle = key => setHidden(prev => { const nx = new Set(prev); if (nx.has(key)) nx.delete(key); else if (nx.size < 3) nx.add(key); return nx; });   // keep ≥ 1 stage visible

  const view = React.useMemo(() => {
    if (!data) return null;
    const g = data.groups[group] || data.groups.All;
    const len = data.weeks.length;
    const take = Math.min(len, RANGES.find(r => r[0] === range)?.[1] ?? 52);
    const from = len - take;
    const weeks = data.weeks.slice(from);
    const series = {};
    for (const s of STAGES) series[s.key] = g[s.key].slice(from).map((v, i) => pct(v, STAGES.reduce((a, t) => a + g[t.key][from + i], 0)));
    const last = len - 1;
    const total = STAGES.reduce((a, s) => a + g[s.key][last], 0);
    const prevIdx = Math.max(0, last - 1);
    const prevTotal = STAGES.reduce((a, s) => a + g[s.key][prevIdx], 0);
    const now = STAGES.map(s => ({ ...s, count: g[s.key][last], share: pct(g[s.key][last], total), delta: pct(g[s.key][last], total) - pct(g[s.key][prevIdx], prevTotal) }));
    return { weeks, series, now, total };
  }, [data, group, range]);

  if (state === "missing") return null;       // file not built yet — no card rather than a broken one
  const names = data ? Object.keys(data.groups).filter(k => k !== "All") : [];

  return (
    <div className="bg-zinc-900/60 border border-zinc-800/60 rounded-xl overflow-hidden mb-5">
      <div className="px-4 py-2.5 border-b border-zinc-800/60 flex items-center gap-2 flex-wrap">
        <span className="text-[13px] font-semibold text-zinc-100">Stage Analysis</span>
        <span className="text-[11px] text-zinc-600">Weinstein · 30-week MA{data ? ` · ${data.universe.toLocaleString()} stocks ≥ $500M · as of ${fullDate(data.as_of)}` : ""}</span>
        <div className="ml-auto flex items-center gap-2">
          <div className="flex bg-zinc-800/60 rounded-lg p-0.5 border border-zinc-700/40">
            {RANGES.map(([l]) => (
              <button key={l} onClick={() => setRange(l)}
                className={`px-2 py-0.5 text-[11px] font-medium rounded-md transition-all ${range === l ? "bg-blue-500/20 text-blue-400 border border-blue-500/30" : "text-zinc-500 hover:text-zinc-300 border border-transparent"}`}>{l}</button>
            ))}
          </div>
          <select value={group} onChange={e => setGroup(e.target.value)} aria-label="Stage analysis scope"
            className="text-[12px] bg-zinc-800/60 border border-zinc-700/50 rounded-md px-2 py-1 text-zinc-200 focus:outline-none focus:border-blue-500/50">
            <option value="All">All stocks</option>
            {names.map(nm => <option key={nm} value={nm}>{nm} ({data.groups[nm].n})</option>)}
          </select>
        </div>
      </div>

      {state === "loading" || !view ? (
        <div className="px-4 py-8 text-[12px] text-zinc-600 italic">Loading stage analysis…</div>
      ) : (
        <div className="px-4 py-3">
          <div className="text-[12px] font-semibold text-zinc-300 mb-2">Market Overview{group !== "All" ? ` — ${group}` : ""}</div>
          {/* Stacked bar: one segment per stage, 2px gaps */}
          <div className="flex h-2.5 gap-[2px] rounded-full overflow-hidden" role="img"
            aria-label={view.now.map(s => `${s.label} ${s.share.toFixed(0)}%`).join(", ")}>
            {view.now.map(s => s.share > 0 && <div key={s.key} title={`${s.label} ${s.name}: ${s.count} (${s.share.toFixed(1)}%)`} style={{ width: `${s.share}%`, background: s.color }} />)}
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-3">
            {view.now.map(s => (
              <div key={s.key} className="text-center" title={s.tip}>
                <div className="flex items-center justify-center gap-1.5 text-[13px] font-bold text-zinc-100">
                  <span className="w-2.5 h-2.5 rounded-full" style={{ background: s.color }} />{s.label}
                  <span className="text-[11px] font-normal text-zinc-500">{s.name}</span>
                </div>
                <div className="text-[13px] text-zinc-400 tabular-nums">{s.count.toLocaleString()} · {s.share.toFixed(0)}%</div>
                <div className={`text-[11px] tabular-nums ${Math.abs(s.delta) < 0.05 ? "text-zinc-600" : s.delta > 0 ? "text-zinc-300" : "text-zinc-500"}`}>
                  {Math.abs(s.delta) < 0.05 ? "unch. vs last wk" : `${s.delta > 0 ? "▲" : "▼"} ${Math.abs(s.delta).toFixed(1)} pts vs last wk`}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-3">
            <StageChart weeks={view.weeks} series={view.series} hidden={hidden} onToggle={toggle} />
          </div>
          <p className="mt-2 text-[11px] text-zinc-600 leading-snug">
            S2 = price above rising 30-week and 200-day MAs · S4 = below falling ones · S1 / S3 = transition after a decline / after an advance (flat MAs, price chopping around them).
            Rules-based reading of Weinstein following TraderLion's stage guide (the stages are really read by eye); stocks need 45 weeks of history. The universe is today's list, so earlier weeks cover the same names, not delisted ones.
          </p>
        </div>
      )}
    </div>
  );
};

export default React.memo(StageAnalysisCard);
