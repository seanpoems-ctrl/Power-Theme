import React from "react";
import { _emaSeries, _smaSeries } from "./chartTheme";
import { stagedReentryExposure } from "./breadthCycle";

// "Backtest" table (Breadth → Breadth Cycle): simple long / flat (or sized) QQQ rules driven by the breadth-cycle regime, QQQ's own trend and
// T2108, compared with buy-and-hold over 1 / 2 / 5 / 8 / 10 years and since 2009. Same method as the research behind the Market Status panel
// (CLAUDE.md → "Market regime vs QQQ"): QQQ close-to-close, the position for day t is set from the signal at the close of day t−1 (no look-ahead),
// no dividends, optional round-trip cost per change of exposure. In-sample, thresholds not optimised, QQQ only — context, not a forecast.

const WINDOWS = [[1, "1y"], [2, "2y"], [5, "5y"], [8, "8y"], [10, "10y"], [0, "Since 2009"]];

const addYears = (iso, y) => `${+iso.slice(0, 4) - y}${iso.slice(4)}`;
const pc = (v, d = 0) => (v == null || !Number.isFinite(v) ? "—" : `${v.toFixed(d)}%`);

export default function BreadthBacktest({ enriched, hist, selDate }) {
  const [detail, setDetail] = React.useState(10);          // window used for the Worst drop / Switches columns
  const [cost, setCost] = React.useState(0);               // basis points per full switch (in ↔ out); scales with the size of the change

  // aligned daily series
  const data = React.useMemo(() => {
    if (!hist?.qqq?.length || !enriched?.length) return null;
    const c = hist.qqq, e21 = _emaSeries(c, 21), e50 = _emaSeries(c, 50), s200 = _smaSeries(c, 200), e9 = _emaSeries(c, 9);
    const idx = new Map(hist.qqq_dates.map((d, i) => [d, i]));
    const rows = enriched.filter(r => r.confirmed && idx.has(r.date));
    return {
      dates: rows.map(r => r.date), px: rows.map(r => c[idx.get(r.date)]),
      e9: rows.map(r => e9[idx.get(r.date)]), e21: rows.map(r => e21[idx.get(r.date)]), e50: rows.map(r => e50[idx.get(r.date)]), s200: rows.map(r => s200[idx.get(r.date)]),
      t2108: rows.map(r => r.t2108), conf: rows.map(r => r.confirmed), raw: rows.map(r => r.phase),
    };
  }, [hist, enriched]);

  const rules = React.useMemo(() => {
    if (!data) return [];
    const { px, e9, e21, e50, s200, t2108, conf, raw } = data;
    const off = p => p === "Contraction";
    // staged re-entry: the same exit as the best pairing, but the way back is in two steps (path-dependent, so the whole series is built up front)
    const staged = stagedReentryExposure(conf.map(off), px.map((p, i) => e50[i] != null && p < e50[i]));
    return [
      { name: "Buy and hold QQQ", base: true, f: () => 1 },
      { name: "QQQ above its 200-day average", f: i => (s200[i] != null && px[i] > s200[i] ? 1 : 0) },
      { name: "QQQ above its 50-day EMA", f: i => (px[i] > e50[i] ? 1 : 0) },
      { name: "QQQ above its 21-day EMA", f: i => (px[i] > e21[i] ? 1 : 0), note: "faster = noisier" },
      { name: "QQQ above its 9-day EMA", f: i => (px[i] > e9[i] ? 1 : 0), note: "fastest = noisiest" },
      { name: "Confirmed regime: out in Risk-off", f: i => (off(conf[i]) ? 0 : 1) },
      { name: "Raw daily regime: out in Risk-off", f: i => (off(raw[i]) ? 0 : 1), note: "never trade the raw colour" },
      { name: "Confirmed Risk-off AND QQQ below 50-day EMA", hl: true, f: i => (off(conf[i]) && px[i] < e50[i] ? 0 : 1) },
      { name: "Staged re-entry: out when Risk-off AND below 50-day EMA; half back in when either clears, all in when both clear", hl2: true, f: i => staged[i] },
      { name: "Confirmed Risk-off AND QQQ below 21-day EMA", f: i => (off(conf[i]) && px[i] < e21[i] ? 0 : 1) },
      { name: "Confirmed Risk-off, but stay in if T2108 < 20", f: i => (off(conf[i]) && !(t2108[i] != null && t2108[i] < 20) ? 0 : 1) },
      { name: "Risk dial (confirmed): 100 / 75 / 50 / 75 % in Risk-on / Caution / Risk-off / Recovery",
        f: i => ({ Expansion: 1, Distribution: 0.75, Contraction: 0.5, Repair: 0.75 })[conf[i]] ?? 1 },
    ];
  }, [data]);

  const results = React.useMemo(() => {
    if (!data || !selDate) return null;
    const { dates, px } = data;
    let endIdx = dates.length - 1;
    while (endIdx > 0 && dates[endIdx] > selDate) endIdx--;
    const end = dates[endIdx];
    const run = (f, si) => {
      let eq = 1, pk = 1, mdd = 0, sw = 0, prev = null, days = 0;
      for (let i = si; i < endIdx; i++) {
        const e = f(i);                                   // exposure decided at the close of day i, earned on day i+1
        const chg = prev == null ? 0 : Math.abs(e - prev);
        if (prev != null && e !== prev) sw++;
        prev = e;
        eq *= 1 + e * (px[i + 1] / px[i] - 1) - chg * (cost / 10000);
        pk = Math.max(pk, eq); mdd = Math.min(mdd, eq / pk - 1); days++;
      }
      const yrs = days / 252;
      return days > 20 ? { cagr: (Math.pow(eq, 1 / yrs) - 1) * 100, mdd: mdd * 100, sw: sw / yrs, total: (eq - 1) * 100 } : null;
    };
    const starts = WINDOWS.map(([y]) => {
      if (y === 0) return 0;
      const target = addYears(end, y);
      return dates[0] > target ? null : Math.max(0, dates.findIndex(d => d >= target));
    });
    return { end, table: rules.map(r => ({ ...r, cells: starts.map(si => (si == null ? null : run(r.f, si))) })) };
  }, [data, rules, selDate, cost]);

  if (!results) return null;
  const dIdx = WINDOWS.findIndex(w => w[0] === detail);
  const bestIn = col => { let b = -1e9, k = -1; results.table.forEach((r, i) => { const c = r.cells[col]; if (c && !r.base && c.cagr > b) { b = c.cagr; k = i; } }); return k; };
  const best = WINDOWS.map((_, c) => bestIn(c));
  const th = "py-1.5 px-2 text-right font-medium";

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
      <div className="flex items-end gap-3 flex-wrap mb-2">
        <div>
          <div className="text-[11px] font-semibold tracking-wider text-zinc-500">BACKTEST · REGIME RULES ON QQQ</div>
          <div className="text-[12px] text-zinc-400">Annual return (CAGR) ending {results.end}. Position set from the previous close; QQQ price only; no dividends.</div>
        </div>
        <div className="ml-auto flex items-center gap-3 flex-wrap text-[11px] text-zinc-400">
          <label className="flex items-center gap-1.5" title="Round-trip cost in basis points for a full in↔out switch (partial changes are charged pro rata). 0 = ignore costs.">Cost per switch
            <input type="number" min="0" step="1" value={cost} onChange={e => setCost(Math.max(0, parseFloat(e.target.value) || 0))}
              className="w-14 px-1.5 py-0.5 text-[12px] font-mono bg-zinc-800/60 border border-zinc-700/50 rounded text-zinc-200 focus:outline-none focus:border-blue-500/50 text-right" /><span className="text-zinc-600">bps</span></label>
          <div className="flex items-center gap-1.5">Detail window
            <div className="flex bg-zinc-800/60 rounded-lg p-0.5 border border-zinc-700/40">
              {WINDOWS.map(([y, l]) => (
                <button key={l} onClick={() => setDetail(y)}
                  className={`px-2 py-0.5 text-[11px] font-medium rounded-md transition-all ${detail === y ? "bg-blue-500/20 text-blue-400 border border-blue-500/30" : "text-zinc-500 hover:text-zinc-300 border border-transparent"}`}>{l}</button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[12px] border-collapse min-w-[760px]">
          <thead>
            <tr className="text-[10px] uppercase tracking-wide text-zinc-500">
              <th className="py-1.5 pr-3 text-left font-medium">Rule (invested unless the rule says cash)</th>
              {WINDOWS.map(([, l]) => <th key={l} className={th}>{l}</th>)}
              <th className={`${th} border-l border-zinc-800`} title={`Largest peak-to-trough fall over the ${WINDOWS[dIdx][1]} window`}>Worst drop</th>
              <th className={th} title="Changes of exposure per year — whipsaw. Fewer is cheaper and calmer.">Switches / yr</th>
            </tr>
          </thead>
          <tbody>
            {results.table.map((r, ri) => {
              const d = r.cells[dIdx];
              return (
                <tr key={r.name} className={`border-t border-zinc-800/70 ${r.hl ? "bg-blue-500/5" : r.hl2 ? "bg-amber-500/5" : ""}`}>
                  <td className="py-1.5 pr-3">
                    <span className={r.base ? "font-semibold text-zinc-200" : "text-zinc-300"}>{r.name}</span>
                    {r.hl && <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded border border-blue-500/40 text-blue-300">best pairing</span>}
                    {r.hl2 && <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded border border-amber-500/40 text-amber-300">staged</span>}
                    {r.note && <span className="ml-2 text-[10px] text-zinc-600">{r.note}</span>}
                  </td>
                  {r.cells.map((c, ci) => (
                    <td key={ci} className={`py-1.5 px-2 text-right font-mono ${c == null ? "text-zinc-700" : c.cagr >= 0 ? "text-emerald-400" : "text-rose-400"} ${best[ci] === ri ? "font-bold underline decoration-dotted underline-offset-4" : ""}`}
                      title={c ? `Total return ${c.total.toFixed(0)}% · worst drop ${c.mdd.toFixed(0)}% · ${c.sw.toFixed(0)} switches a year` : "not enough history"}>
                      {c == null ? "—" : pc(c.cagr, 0)}
                    </td>
                  ))}
                  <td className="py-1.5 px-2 text-right font-mono text-rose-300 border-l border-zinc-800">{d ? pc(d.mdd, 0) : "—"}</td>
                  <td className="py-1.5 px-2 text-right font-mono text-zinc-300">{d ? (r.base ? "0" : d.sw.toFixed(0)) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-zinc-600 leading-snug">
        The amber "staged" row exits exactly like the best pairing but comes back in two steps (half when either condition clears, everything when both have) — it trades a little return for a smaller worst drop and a few more switches.
        Dotted underline = best non-buy-and-hold rule for that window. How to read it: none of these rules beat buy-and-hold QQQ on return over 5–10 years — their value is a much smaller worst drop
        (the pairing above roughly cuts it by a third at similar returns) and fewer whipsaws. The plain 200-day average is the bar a regime rule has to clear; the raw daily colour
        gives the deepest drops; the 9 / 21-day EMAs are too noisy as market-level exits (keep them for stock entries and trailing stops). Over the last year the confirmed rules lagged a V-shaped
        rebound — that two-session lag is the price of fewer whipsaws. In-sample, thresholds not optimised, QQQ only; past results are not a forecast. Hover a number for the total return,
        worst drop and switches of that window.
      </p>
    </div>
  );
}
