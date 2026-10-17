import React from "react";
import { STAGES, loadStageStocks } from "./stageDefs";

// "Stage Movers" table under the Stage Analysis chart: which stocks just entered (or sit in) each stage, with TraderLion's breakout-volume test.
// Data: public/stage_stocks.json (stage_analysis_builder.py). `group` = the card's scope ("All" or a sector).

const STAGE_BY = Object.fromEntries(STAGES.map(x => [x.k, x]));
const StageChip = ({ k }) => (k && STAGE_BY[k]
  ? <span className="inline-block min-w-[26px] text-center text-[11px] font-bold px-1 py-0.5 rounded" style={{ background: `${STAGE_BY[k].color}33`, color: "#e4e4e7", border: `1px solid ${STAGE_BY[k].color}` }}>S{k}</span>
  : <span className="text-zinc-600">—</span>);

const PRESETS = [
  { id: "new2", label: "New Stage 2", stage: 2, within: 4, minVx: 0, from: 0, below: 0, tip: "Entered Stage 2 in the last 4 weeks" },
  { id: "brk", label: "Breakouts on volume", stage: 2, within: 4, minVx: 2, from: 0, below: 0, tip: "Entered Stage 2 in the last 4 weeks with a day of at least 2× the prior 30-day average volume" },
  { id: "top", label: "Topping (S2 → S3)", stage: 3, within: 4, minVx: 0, from: 2, below: 0, tip: "Slipped from Stage 2 into Stage 3 in the last 4 weeks" },
  { id: "warn", label: "⚠ 10W MA break (S2)", stage: 2, within: 0, minVx: 1.5, from: 0, below: 4, tip: "Stage 2 stocks whose weekly close slipped below the 10-week MA in the last 4 weeks, on at least 1.5× volume — TraderLion's first warning that an advance is rolling into Stage 3" },
  { id: "new4", label: "New Stage 4", stage: 4, within: 4, minVx: 0, from: 0, below: 0, tip: "Entered Stage 4 in the last 4 weeks" },
  { id: "base", label: "Basing (S1)", stage: 1, within: 0, minVx: 0, from: 0, below: 0, tip: "Everything currently in Stage 1, longest bases first" },
];

const num = (v, d = 1) => (v == null ? "—" : v.toFixed(d));
const money = v => (v == null ? "—" : v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : `$${Math.round(v / 1e6)}M`);
const field = "text-[12px] bg-zinc-800/60 border border-zinc-700/50 rounded-md px-1.5 py-0.5 text-zinc-200 focus:outline-none focus:border-blue-500/50";

export default function StageMovers({ group, StockCell = null, onMiniCharts = null }) {
  const [stk, setStk] = React.useState(null);
  React.useEffect(() => { let dead = false; loadStageStocks().then(d => { if (!dead) setStk(d); }); return () => { dead = true; }; }, []);
  const [f, setF] = React.useState({ preset: "new2", stage: 2, within: 4, minVx: 0, from: 0, below: 0, minDvM: 10 });
  const [sort, setSort] = React.useState({ col: null, dir: "desc" });
  const [copied, setCopied] = React.useState(false);
  const pick = p => { setF(prev => ({ ...prev, preset: p.id, stage: p.stage, within: p.within, minVx: p.minVx, from: p.from, below: p.below })); setSort({ col: null, dir: "desc" }); };
  const set = patch => setF(prev => ({ ...prev, preset: "custom", ...patch }));

  const rows = React.useMemo(() => {
    if (!stk) return [];
    const out = stk.rows.filter(r => r.st === f.stage
      && (group === "All" || r.sec === group)
      && (!f.within || r.w <= f.within)
      && (!f.from || r.pv === f.from)
      && (!f.below || (r.bw >= 1 && r.bw <= f.below))
      && (!f.minVx || ((f.below ? r.bvx : r.vx) ?? 0) >= f.minVx)
      && (f.minDvM <= 0 || (r.dv ?? 0) >= f.minDvM * 1e6));
    // default order: breakout volume for fresh S2/S4 entries, longest base first for S1, otherwise newest first
    const col = sort.col || (f.below ? "bvx" : f.stage === 1 ? "w" : f.stage === 3 ? "w" : "vx");
    const dir = sort.col ? sort.dir : (col === "w" && f.stage === 3 ? "asc" : "desc");
    return [...out].sort((a, b) => {
      const av = a[col], bv = b[col];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string") return dir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      return dir === "asc" ? av - bv : bv - av;
    });
  }, [stk, f, group, sort]);

  if (!stk) return null;
  const warn = f.below > 0;     // 10-week MA warning mode: show weeks-below and the break volume instead of stage age / entry volume
  const th = (col, label, align = "right", tip) => (
    <th onClick={() => setSort(p => ({ col, dir: p.col === col && p.dir === "desc" ? "asc" : "desc" }))} title={tip}
      className={`px-2 py-1.5 font-medium cursor-pointer select-none hover:text-zinc-300 whitespace-nowrap ${align === "left" ? "text-left" : "text-right"} ${sort.col === col ? "text-zinc-200" : ""}`}>
      {label}<span className="text-[9px] ml-0.5 opacity-60">{sort.col === col ? (sort.dir === "desc" ? "▼" : "▲") : "⬍"}</span>
    </th>
  );
  const copy = () => { (navigator.clipboard?.writeText(rows.map(r => r.t).join(",")) || Promise.reject()).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1400); }).catch(() => {}); };

  return (
    <div className="mt-4 pt-3 border-t border-zinc-800/80">
      <div className="flex items-center gap-2 flex-wrap mb-2">
        <span className="text-[12px] font-semibold text-zinc-300">Stage Movers</span>
        <span className="text-[11px] font-mono text-zinc-500 bg-zinc-800 px-1.5 py-0.5 rounded">{rows.length}</span>
        <span className="text-[11px] text-zinc-600">{group === "All" ? "all sectors" : group}</span>
        <div className="ml-auto flex items-center gap-2">
          {onMiniCharts && rows.length > 0 && (
            <button onClick={() => onMiniCharts(rows.map(r => r.t), `Stage ${f.stage}${f.within ? ` · last ${f.within}w` : ""}`)}
              className="text-[11px] font-medium px-2 py-1 rounded border border-zinc-700 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors">▦ Mini Charts</button>
          )}
          <button onClick={copy} disabled={!rows.length} className="text-[11px] px-2 py-1 rounded border border-zinc-700 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 disabled:opacity-30 transition-colors">{copied ? "Copied ✓" : "Copy"}</button>
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {PRESETS.map(p => (
          <button key={p.id} onClick={() => pick(p)} title={p.tip}
            className={`px-2.5 py-1 text-[11px] font-medium rounded-md border transition-colors ${f.preset === p.id ? "bg-blue-500/20 text-blue-300 border-blue-500/30" : "text-zinc-500 hover:text-zinc-300 border-zinc-800"}`}>{p.label}</button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 mb-2 text-[11px] text-zinc-400">
        <label className="flex items-center gap-1.5">Stage
          <select value={f.stage} onChange={e => set({ stage: +e.target.value })} className={field}>{STAGES.map(x => <option key={x.k} value={x.k}>S{x.k} {x.name}</option>)}</select></label>
        <label className="flex items-center gap-1.5">Entered within
          <select value={f.within} onChange={e => set({ within: +e.target.value })} className={field}>
            <option value={1}>1 week</option><option value={2}>2 weeks</option><option value={4}>4 weeks</option><option value={8}>8 weeks</option><option value={12}>12 weeks</option><option value={0}>any time</option></select></label>
        <label className="flex items-center gap-1.5" title="Weekly close under the 10-week MA (≈ 50-day) — Stage 3 early warning. Volume ≥ then measures the break">Below 10W MA
          <select value={f.below} onChange={e => set({ below: +e.target.value })} className={field}>
            <option value={0}>any</option><option value={1}>1 week</option><option value={2}>≤ 2 weeks</option><option value={4}>≤ 4 weeks</option><option value={8}>≤ 8 weeks</option></select></label>
        <label className="flex items-center gap-1.5" title={warn ? "Highest daily volume since the stock slipped below the 10-week MA ÷ the 30-day average before it" : "Highest daily volume since the stage began ÷ the 30-day average before it"}>Volume ≥
          <input type="number" min="0" step="0.5" value={f.minVx} onChange={e => set({ minVx: Math.max(0, parseFloat(e.target.value) || 0) })} className={`${field} w-14 text-right`} /><span className="text-zinc-600">×</span></label>
        <label className="flex items-center gap-1.5">Avg $ Vol ≥
          <input type="number" min="0" step="5" value={f.minDvM} onChange={e => set({ minDvM: Math.max(0, parseFloat(e.target.value) || 0) })} className={`${field} w-14 text-right`} /><span className="text-zinc-600">M</span></label>
      </div>
      {rows.length === 0 ? (
        <p className="text-[12px] text-zinc-600 italic py-3">No stocks match these filters.</p>
      ) : (
        <div className="max-h-[420px] overflow-y-auto rounded-lg border border-zinc-800">
          <table className="w-full text-[12px] border-collapse">
            <thead className="sticky top-0 bg-zinc-900 text-zinc-500 text-[10px] uppercase tracking-wide">
              <tr>
                {th("t", "Ticker", "left")}
                {th("n", "Company", "left")}
                {th("sec", "Sector", "left")}
                {warn
                  ? th("bw", "Wks below 10W", "right", "Consecutive weeks the weekly close has been under the 10-week moving average")
                  : th("w", "Wks in stage", "right", "Consecutive weeks in this stage (1 = entered this week)")}
                {!warn && th("pv", "From", "right", "The stage it came from")}
                {th("px", "Price")}
                {th("d10", "vs 10W MA", "right", "Close vs the 10-week moving average (≈ 50-day). Negative = below it.")}
                {th("d30", "vs 30W MA", "right", "Close vs the 30-week moving average")}
                {warn
                  ? th("bvx", "Break volume ×", "right", "Highest daily volume since the stock slipped below the 10-week MA ÷ average daily volume of the 30 days before. Heavy volume (1.5×+) on the break is the warning TraderLion describes.")
                  : th("vx", "Volume ×", "right", "Highest daily volume since the stage began ÷ average daily volume of the 30 days before (only for changes in the last 12 weeks). 2×+ = a high-volume break.")}
                {th("dv", "Avg $ Vol")}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.t} className={`border-t border-zinc-800/60 hover:bg-zinc-800/30 ${i % 2 ? "bg-zinc-900/20" : ""}`}>
                  <td className="px-2 py-1 text-left">{StockCell ? <StockCell ticker={r.t} /> : <span className="font-mono font-bold text-cyan-400">{r.t}</span>}</td>
                  <td className="px-2 py-1 text-left text-zinc-400 max-w-[200px] truncate" title={r.n}>{r.n || "—"}</td>
                  <td className="px-2 py-1 text-left text-zinc-500 whitespace-nowrap">{r.sec || "—"}</td>
                  <td className="px-2 py-1 text-right font-mono text-zinc-300">{warn ? r.bw : r.w}</td>
                  {!warn && <td className="px-2 py-1 text-right"><StageChip k={r.pv} /></td>}
                  <td className="px-2 py-1 text-right font-mono text-zinc-300">${num(r.px, 2)}</td>
                  <td className={`px-2 py-1 text-right font-mono ${r.d10 == null ? "text-zinc-600" : r.d10 >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{r.d10 == null ? "—" : `${r.d10 > 0 ? "+" : ""}${r.d10.toFixed(1)}%`}</td>
                  <td className={`px-2 py-1 text-right font-mono ${r.d30 == null ? "text-zinc-600" : r.d30 >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{r.d30 == null ? "—" : `${r.d30 > 0 ? "+" : ""}${r.d30.toFixed(1)}%`}</td>
                  <td className={`px-2 py-1 text-right font-mono ${(warn ? r.bvx : r.vx) == null ? "text-zinc-600" : (warn ? r.bvx : r.vx) >= 3 ? "text-amber-300 font-bold" : (warn ? r.bvx : r.vx) >= 1.5 + (warn ? 0 : 0.5) ? "text-amber-300" : "text-zinc-400"}`}>{(warn ? r.bvx : r.vx) == null ? "—" : `${(warn ? r.bvx : r.vx).toFixed(1)}×`}</td>
                  <td className="px-2 py-1 text-right font-mono text-zinc-400">{money(r.dv)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-1.5 text-[11px] text-zinc-600 leading-snug">Per TraderLion's guide, the best Stage 2 entries break out on at least twice average volume and sit in strong sectors (use the scope menu above).
        Volume × needs a stage change in the last 12 weeks. Stage 2 entries come from the base (S1) or a re-acceleration (S3). The ⚠ 10W MA break preset lists Stage 2 stocks that have slipped under the 10-week average (≈ 50-day) — the guide's first warning that an advance is losing steam; it is a prompt to tighten stops or trim, not a sell signal on its own.</p>
    </div>
  );
}
