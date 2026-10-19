import React from "react";
import { createChart, CandlestickSeries, LineSeries, HistogramSeries } from "lightweight-charts";
import { BREADTH_PHASES } from "./breadthCycle";
import { MINI_CHART_THEME, MINI_EMA_CONFIG, _emaSeries, _smaSeries, candleOptions, candleData } from "./chartTheme";

// "Market regime vs QQQ" (Breadth → Breadth Cycle): QQQ's daily candles — same look as the Mini / enlarged charts (grey canvas, white / grey outlined
// candles, orange inside bars, EMA 9 / 21 / 50 + SMA 200) — over the breadth-cycle phase of every session, so you can see when breadth said
// risk-on / risk-off. Phases are our own breadth-cycle scoring (breadthCycle.js); QQQ candles come from breadth_long_history.json
// (`qqq_dates` / `qqq_o` / `qqq_h` / `qqq_l` / `qqq`, written by breadth_history_builder.py).

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

const hexA = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${a})`; };
const fullDate = iso => new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
const pct = (v, d = 1) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
const px = v => (v == null ? "—" : v.toFixed(2));

// variant "card" = the panel on the Breadth Cycle page; "modal" = the same chart enlarged in an overlay (opened from the card's ⤢ button or by
// double-clicking the chart, like the Mini Charts).
export default function BreadthRegime({ enriched, hist, selDate, variant = "card", onClose = null, initialRange = "1Y" }) {
  const isModal = variant === "modal";
  const [open, setOpen] = React.useState(false);
  const [range, setRange] = React.useState(initialRange);
  const [hoverDate, setHoverDate] = React.useState(null);
  const containerRef = React.useRef(null);
  const chartRef = React.useRef(null);
  const bodyZoom = parseFloat(getComputedStyle(document.body).zoom) || 1;      // same wrapper fix as MiniChartCard (body { zoom } skews the crosshair)

  // phase + strength per breadth date
  const regimeByDate = React.useMemo(() => new Map((enriched || []).filter(r => r.strength != null).map(r => [r.date, { phase: r.phase, strength: r.strength }])), [enriched]);

  // QQQ candles (all dates, ascending)
  const bars = React.useMemo(() => {
    if (!hist?.qqq?.length || !hist.qqq_o?.length) return null;
    return hist.qqq_dates.map((d, i) => ({ time: d, open: hist.qqq_o[i], high: hist.qqq_h[i], low: hist.qqq_l[i], close: hist.qqq[i] }));
  }, [hist]);

  // series with both a QQQ close and a regime (for the statistics table)
  const series = React.useMemo(() => {
    if (!bars) return null;
    const out = [];
    for (const b of bars) { const r = regimeByDate.get(b.time); if (r) out.push({ date: b.time, close: b.close, phase: r.phase, strength: r.strength }); }
    return out.length > 60 ? out : null;
  }, [bars, regimeByDate]);

  // What QQQ did in each regime, over the whole history: share of sessions, annualised return when holding QQQ only in that regime
  // (the return of session i is credited to the regime known at the close of session i−1 — no look-ahead), volatility, worst day, and the
  // average 21-session forward return.
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

  React.useEffect(() => {
    if (!isModal) return undefined;
    const h = e => { if (e.key === "Escape") { e.stopPropagation(); onClose?.(); } };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [isModal, onClose]);

  // candles up to the Time Machine date
  const upTo = React.useMemo(() => (bars ? bars.filter(b => b.time <= (selDate || "9999")) : []), [bars, selDate]);
  const barIdx = React.useMemo(() => new Map(upTo.map((b, i) => [b.time, i])), [upTo]);

  // Build the chart. Rebuilt when the data (or Time Machine date) changes; the range buttons only move the visible window.
  React.useEffect(() => {
    if (!containerRef.current || upTo.length < 2) return undefined;
    const T = MINI_CHART_THEME;
    const chart = createChart(containerRef.current, {
      layout: { background: { color: "transparent" }, textColor: T.text, fontSize: 13 },
      grid: { vertLines: { color: "transparent" }, horzLines: { color: "transparent" } },
      // minBarSpacing: Lightweight Charts won't zoom out past 0.5 px per bar by default (≈ 1,400 candles in a 700 px chart); QQQ has ~6,900 daily bars since 1999.
      timeScale: { borderColor: T.axis, rightOffset: 4, minBarSpacing: 0.02 },
      rightPriceScale: { borderColor: T.axis },
      crosshair: { mode: 0, vertLine: { color: T.crosshair }, horzLine: { color: T.crosshair } },
      autoSize: true,
    });
    chartRef.current = chart;

    // Regime bands: a full-height histogram on its own hidden scale (drawn first, so the candles sit on top), plus a solid strip along the bottom.
    const bands = chart.addSeries(HistogramSeries, { priceScaleId: "regime", priceLineVisible: false, lastValueVisible: false, base: 0 });
    bands.priceScale().applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false });
    bands.setData(upTo.map(b => { const r = regimeByDate.get(b.time); return r ? { time: b.time, value: 1, color: hexA(REGIMES[r.phase].color, 0.3) } : { time: b.time, value: 0 }; }));
    const strip = chart.addSeries(HistogramSeries, { priceScaleId: "regimeStrip", priceLineVisible: false, lastValueVisible: false, base: 0 });
    strip.priceScale().applyOptions({ scaleMargins: { top: 0.955, bottom: 0 }, visible: false });
    strip.setData(upTo.map(b => { const r = regimeByDate.get(b.time); return r ? { time: b.time, value: 1, color: REGIMES[r.phase].color } : { time: b.time, value: 0 }; }));

    const candles = chart.addSeries(CandlestickSeries, candleOptions());
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.08 } });
    candles.setData(candleData(upTo.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close }))));

    const closes = upTo.map(b => b.close);
    MINI_EMA_CONFIG.forEach(cfg => {
      const s = chart.addSeries(LineSeries, { color: cfg.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      const vals = cfg.sma ? _smaSeries(closes, cfg.period) : _emaSeries(closes, cfg.period);
      s.setData(upTo.reduce((acc, b, i) => { if (vals[i] != null) acc.push({ time: b.time, value: vals[i] }); return acc; }, []));
    });

    chart.subscribeCrosshairMove(param => setHoverDate(param.time ? String(param.time) : null));
    return () => { chart.remove(); chartRef.current = null; };
  }, [upTo, regimeByDate]);

  // Visible window + log axis for long ranges (QQQ in 2009 would be a flat line next to today's price)
  React.useEffect(() => {
    const chart = chartRef.current;
    if (!chart || upTo.length < 2) return;
    const n = RANGES.find(r => r[0] === range)?.[1] ?? 252;
    chart.priceScale("right").applyOptions({ mode: n > 600 ? 1 : 0 });
    if (!Number.isFinite(n) || n >= upTo.length) chart.timeScale().fitContent();
    else chart.timeScale().setVisibleLogicalRange({ from: upTo.length - n, to: upTo.length + 4 });
  }, [range, upTo]);

  if (!bars || !series || upTo.length < 2) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
        <div className="text-[11px] font-semibold tracking-wider text-zinc-500">MARKET REGIME VS QQQ</div>
        <p className="text-[12px] text-zinc-600 italic mt-1">QQQ candle history isn't in the data file yet — it is added by the next nightly run (breadth_history_builder.py).</p>
      </div>
    );
  }

  const lastBar = upTo[upTo.length - 1];
  const shown = (hoverDate && barIdx.has(hoverDate)) ? hoverDate : lastBar.time;
  const i = barIdx.get(shown);
  const b = upTo[i], prev = upTo[i - 1];
  const chg = prev ? (b.close / prev.close - 1) * 100 : null;
  const reg = regimeByDate.get(shown);
  const cur = regimeByDate.get(lastBar.time);
  const curReg = cur ? REGIMES[cur.phase] : null;

  const panelCls = isModal
    ? "w-full max-w-[1700px] rounded-xl border border-zinc-700 bg-zinc-900 p-4 shadow-2xl"
    : "rounded-xl border border-zinc-800 bg-zinc-900/60 p-5";
  const content = (
    <div className={panelCls} onClick={isModal ? (e => e.stopPropagation()) : undefined}>
      <div className="flex items-center gap-3 flex-wrap mb-1">
        <div>
          <div className="text-[11px] font-semibold tracking-wider text-zinc-500">MARKET REGIME VS QQQ</div>
          {curReg && <div className="text-lg font-bold" style={{ color: curReg.color }}>{curReg.label} <span className="text-[12px] font-normal text-zinc-500">· {cur.phase} on {fullDate(lastBar.time)}</span></div>}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <div className="flex bg-zinc-800/60 rounded-lg p-0.5 border border-zinc-700/40">
            {RANGES.map(([l]) => (
              <button key={l} onClick={() => setRange(l)}
                className={`px-2.5 py-1 text-[11px] font-medium rounded-md transition-all ${range === l ? "bg-blue-500/20 text-blue-400 border border-blue-500/30" : "text-zinc-500 hover:text-zinc-300 border border-transparent"}`}>{l}</button>
            ))}
          </div>
          {isModal
            ? <button onClick={onClose} title="Close (Esc)" aria-label="Close" className="text-zinc-400 hover:text-zinc-100 text-lg leading-none px-2 py-1">✕</button>
            : <button onClick={() => setOpen(true)} title="Enlarge the chart (or double-click it)" aria-label="Enlarge"
                className="text-[11px] font-medium px-2 py-1 rounded border border-zinc-700 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors">⤢ Enlarge</button>}
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

      <div className="rounded-lg overflow-hidden border border-zinc-800/60">
        {/* OHLC legend in the same style as the Mini Chart header: hovered bar, or the latest bar when idle */}
        <div className="flex items-baseline gap-1.5 px-3 py-2 bg-zinc-900/80 text-[13px] font-mono whitespace-nowrap overflow-x-auto">
          <span className="text-[14px] font-bold text-cyan-400 mr-1">QQQ</span>
          <span className="text-zinc-500">{fullDate(shown)}</span>
          <span className="text-zinc-500 ml-2">O</span><span className="text-zinc-300">{px(b.open)}</span>
          <span className="text-zinc-500">H</span><span className="text-zinc-300">{px(b.high)}</span>
          <span className="text-zinc-500">L</span><span className="text-zinc-300">{px(b.low)}</span>
          <span className="text-zinc-500">C</span><span className="text-zinc-300">{px(b.close)}</span>
          {chg != null && <span className={chg >= 0 ? "text-emerald-400" : "text-red-400"}>{pct(chg, 2)}</span>}
          {reg && (
            <span className="ml-auto flex items-center gap-1.5 font-sans">
              <span className="w-2 h-2 rounded-full" style={{ background: REGIMES[reg.phase].color }} />
              <span className="font-bold text-zinc-100">{REGIMES[reg.phase].label}</span>
              <span className="text-zinc-500">{reg.phase} · strength {reg.strength.toFixed(0)}</span>
            </span>
          )}
        </div>
        <div style={{ height: isModal ? `calc((100vh - 270px) * ${bodyZoom})` : 400, zoom: 1 / bodyZoom, background: MINI_CHART_THEME.bg, cursor: isModal ? "default" : "zoom-in" }}
          onDoubleClick={isModal ? undefined : () => setOpen(true)} title={isModal ? undefined : "Double-click to enlarge"}>
          <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
        </div>
      </div>

      {stats && !isModal && (
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
  if (isModal) {
    return (
      <div className="fixed inset-0 z-[80] flex items-center justify-center p-4 overflow-y-auto" style={{ backgroundColor: "rgba(0,0,0,0.85)" }} onClick={onClose}>
        {content}
      </div>
    );
  }
  return (
    <>
      {content}
      {open && <BreadthRegime enriched={enriched} hist={hist} selDate={selDate} variant="modal" initialRange={range} onClose={() => setOpen(false)} />}
    </>
  );
}
