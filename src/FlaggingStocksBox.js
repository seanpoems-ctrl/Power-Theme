import React, { useState, useEffect, useRef, useMemo } from 'react';
import { createChart, ColorType, CandlestickSeries, LineSeries, HistogramSeries } from 'lightweight-charts';
import { X, BarChart2 } from 'lucide-react';

// ─── Triangle / Pennant Detection ─────────────────────────────────────────────

function linReg(pts) {
  const n = pts.length;
  if (n < 2) return null;
  let sx = 0, sy = 0, sxy = 0, sx2 = 0;
  for (const p of pts) { sx += p.x; sy += p.y; sxy += p.x * p.y; sx2 += p.x * p.x; }
  const d = n * sx2 - sx * sx;
  if (Math.abs(d) < 1e-10) return null;
  const slope = (n * sxy - sx * sy) / d;
  const intercept = (sy - slope * sx) / n;
  return { slope, intercept, at: x => slope * x + intercept };
}

function findSwings(bars, win = 2) {
  const highs = [], lows = [];
  for (let i = win; i < bars.length - win; i++) {
    let hi = true, lo = true;
    for (let j = i - win; j <= i + win; j++) {
      if (j === i) continue;
      if (bars[j].h >= bars[i].h) hi = false;
      if (bars[j].l <= bars[i].l) lo = false;
    }
    if (hi) highs.push({ x: i, y: bars[i].h });
    if (lo) lows.push({ x: i, y: bars[i].l });
  }
  return { highs, lows };
}

function detectTriangle(bars) {
  if (!bars || bars.length < 12) return null;
  const { highs, lows } = findSwings(bars, 2);
  if (highs.length < 2 || lows.length < 2) return null;

  const rh = highs.slice(-3);
  const rl = lows.slice(-3);
  const upper = linReg(rh);
  const lower = linReg(rl);
  if (!upper || !lower) return null;

  const last = bars.length - 1;
  const avgPrice = (bars[last].h + bars[last].l + bars[last].c) / 3;

  const slopeTol = avgPrice * 0.002;
  if (upper.slope > slopeTol) return null;
  if (lower.slope < -slopeTol) return null;

  const uLast = upper.at(last);
  const lLast = lower.at(last);
  if (uLast <= lLast) return null;
  if (upper.slope - lower.slope >= 0) return null;

  const startIdx = Math.min(rh[0].x, rl[0].x);
  const rangeAtStart = upper.at(startIdx) - lower.at(startIdx);
  const rangeAtEnd = uLast - lLast;
  if (rangeAtEnd >= rangeAtStart * 0.90) return null;

  if (last - startIdx < 5) return null;

  const apexX = (lower.intercept - upper.intercept) / (upper.slope - lower.slope);
  const barsToApex = apexX - last;
  if (barsToApex < 1 || barsToApex > 60) return null;

  const close = bars[last].c;
  const lastHigh = bars[last].h;
  if (close > uLast * 1.01 || close < lLast * 0.99) return null;
  if (lastHigh > uLast * 1.03) return null;

  return { upper, lower, startIdx, barsToApex: Math.round(barsToApex) };
}

// ─── Chart Modal ───────────────────────────────────────────────────────────────

const BASE_TIME = 1700000000;

// Build auto trendline endpoints as { time, price } pairs.
// getT(barIdx) converts a bars_30d index → full-chart Unix timestamp.
function buildAutoEndpoints(bars, triangle, getT) {
  const last = bars.length - 1;
  if (triangle) {
    const { upper, lower, startIdx } = triangle;
    return {
      upper: [
        { time: getT(startIdx), price: upper.at(startIdx) },
        { time: getT(last),     price: upper.at(last) },
      ],
      lower: [
        { time: getT(startIdx), price: lower.at(startIdx) },
        { time: getT(last),     price: lower.at(last) },
      ],
    };
  }
  const start = Math.max(0, last - 19);
  const slice = bars.slice(start);
  const topS = Math.max(...slice.slice(0, 5).map(b => b.h));
  const botS = Math.min(...slice.slice(0, 5).map(b => b.l));
  const prRange = Math.max(...bars.map(b => b.h)) - Math.min(...bars.map(b => b.l));
  return {
    upper: [
      { time: getT(start), price: topS },
      { time: getT(last),  price: bars[last].c + prRange * 0.02 },
    ],
    lower: [
      { time: getT(start), price: botS },
      { time: getT(last),  price: bars[last].c - prRange * 0.02 },
    ],
  };
}

function TriangleChartModal({ stock, onClose }) {
  const containerRef = useRef(null);
  const timesRef = useRef([]);
  const [chartBars, setChartBars] = useState(null);

  const bars = useMemo(() => stock.bars_30d || [], [stock]);
  const triangle = useMemo(() => detectTriangle(bars), [bars]);

  // ── Fetch 6-month Yahoo Finance data on open ──────────────────────────────
  useEffect(() => {
    let cancelled = false;
    const ticker = stock.ticker;
    const url = `https://corsproxy.io/?${encodeURIComponent(
      `https://query1.finance.yahoo.com/v8/finance/chart/${ticker}?range=6mo&interval=1d`
    )}`;
    Promise.race([
      fetch(url).then(r => r.ok ? r.json() : null),
      new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 8000)),
    ])
      .then(data => {
        if (cancelled) return;
        const result = data?.chart?.result?.[0];
        const ts = result?.timestamp;
        const q = result?.indicators?.quote?.[0];
        if (ts?.length && q) {
          const full = ts.map((t, i) => ({
            t, h: q.high[i], l: q.low[i], c: q.close[i],
            o: q.open?.[i] ?? (i > 0 ? q.close[i - 1] : q.close[i]),
            v: q.volume?.[i] ?? 0,
          })).filter(b => b.h != null && b.l != null && b.c != null && b.h > 0 && b.l > 0);
          if (full.length >= 10) { setChartBars(full); return; }
        }
        setChartBars(bars.map((b, i) => ({
          t: BASE_TIME + i * 86400,
          o: i > 0 ? bars[i - 1].c : b.c, h: b.h, l: b.l, c: b.c, v: b.v ?? 0,
        })));
      })
      .catch(() => {
        if (cancelled) return;
        setChartBars(bars.map((b, i) => ({
          t: BASE_TIME + i * 86400,
          o: i > 0 ? bars[i - 1].c : b.c, h: b.h, l: b.l, c: b.c, v: b.v ?? 0,
        })));
      });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stock.ticker]);

  // ── Build chart once chartBars are ready ─────────────────────────────────
  useEffect(() => {
    if (!chartBars || !containerRef.current) return;

    const offset = Math.max(0, chartBars.length - bars.length);
    timesRef.current = chartBars.map(b => b.t);

    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: '#18181b' }, textColor: '#a1a1aa' },
      grid: { vertLines: { color: '#27272a' }, horzLines: { color: '#27272a' } },
      width: containerRef.current.clientWidth,
      height: 440,
      rightPriceScale: { borderColor: '#3f3f46' },
      timeScale: { borderColor: '#3f3f46', timeVisible: true, secondsVisible: false },
    });

    const cSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    });
    cSeries.setData(chartBars.map(b => ({
      time: b.t, open: b.o, high: b.h, low: b.l, close: b.c,
    })));

    // Auto trendline endpoints
    const getT = idx => timesRef.current[idx + offset] ?? BASE_TIME + (idx + offset) * 86400;
    const { upper: uPts, lower: lPts } = buildAutoEndpoints(bars, triangle, getT);

    chart.addSeries(LineSeries, {
      color: '#ef4444', lineWidth: 2,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    }).setData([
      { time: uPts[0].time, value: uPts[0].price },
      { time: uPts[1].time, value: uPts[1].price },
    ]);

    chart.addSeries(LineSeries, {
      color: '#22c55e', lineWidth: 2,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    }).setData([
      { time: lPts[0].time, value: lPts[0].price },
      { time: lPts[1].time, value: lPts[1].price },
    ]);

    // Volume histogram (pane 1) — separator locked via enableResize: false
    const hasVolume = chartBars.some(b => (b.v ?? 0) > 0);
    if (hasVolume) {
      const volSer = chart.addSeries(HistogramSeries, {
        priceFormat: { type: 'volume' },
        priceScaleId: 'vol',
        lastValueVisible: false,
        priceLineVisible: false,
      }, 1);
      volSer.priceScale().applyOptions({ scaleMargins: { top: 0.1, bottom: 0 } });
      volSer.setData(chartBars.map(b => ({
        time: b.t,
        value: b.v ?? 0,
        color: (b.c >= b.o) ? 'rgba(34,197,94,0.45)' : 'rgba(239,68,68,0.45)',
      })));
      const panes = chart.panes();
      if (panes.length >= 2) {
        panes[0].setHeight(350);
        panes[1].setHeight(90);
      }
      // Inject CSS to block separator drag (cursor + pointer-events)
      if (!document.getElementById('lwc-no-resize')) {
        const s = document.createElement('style');
        s.id = 'lwc-no-resize';
        s.textContent = 'tr[style*="height: 1px"] td { pointer-events: none !important; cursor: default !important; }';
        document.head.appendChild(s);
      }
    }

    chart.timeScale().fitContent();

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    });
    ro.observe(containerRef.current);

    return () => { ro.disconnect(); chart.remove(); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartBars]);

  // body { zoom: 1.15 } causes LWC to use visual-px offsets as CSS-px chart coordinates
  // (clientX - rect.left is visual px, but chart coordinate space is CSS px).
  // Applying inverse zoom to the chart wrapper makes net effective zoom = 1,
  // so visual px === CSS px — crosshair aligns with the mouse cursor.
  const bodyZoom = parseFloat(getComputedStyle(document.body).zoom) || 1;


  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75" onClick={onClose}>
      <div
        className="bg-zinc-900 border border-zinc-700/60 rounded-xl shadow-2xl w-[740px] max-w-[95vw]"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-white font-bold text-[15px]">{stock.ticker}</span>
            <span className="text-zinc-400 text-sm truncate max-w-[180px]">{stock.company}</span>
            {triangle && (
              <span className="px-2 py-0.5 rounded text-[11px] bg-amber-500/20 text-amber-400 border border-amber-500/30">
                Triangle · apex ~{triangle.barsToApex}d
              </span>
            )}
          </div>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300 p-1 ml-2">
            <X size={16}/>
          </button>
        </div>

        {/* Chart area */}
        <div className="p-3">
          <div className="relative rounded-lg overflow-hidden" style={{ minHeight: 440, zoom: 1 / bodyZoom }}>
            {/* Loading spinner */}
            {!chartBars && (
              <div className="absolute inset-0 flex items-center justify-center bg-zinc-900/80 rounded-lg z-20">
                <div className="flex flex-col items-center gap-2">
                  <div className="w-6 h-6 border-2 border-zinc-600 border-t-blue-400 rounded-full animate-spin"/>
                  <span className="text-[12px] text-zinc-500">Loading 6-month data…</span>
                </div>
              </div>
            )}
            <div ref={containerRef} className="w-full"/>
          </div>

          {/* Legend */}
          <div className="mt-2 flex items-center gap-4 text-[11px] text-zinc-500">
            <span className="flex items-center gap-1.5">
              <span className="w-4 h-0.5 bg-red-500 rounded inline-block"/>Resistance
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-4 h-0.5 bg-green-500 rounded inline-block"/>Support
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Flagging Stocks Box ───────────────────────────────────────────────────────

export default function FlaggingStocksBox({ data }) {
  const [selectedStock, setSelectedStock] = useState(null);

  const flagging = useMemo(() => {
    if (!data?.themes) return [];
    const seen = new Set();
    const results = [];
    for (const theme of data.themes) {
      for (const sub of (theme.subthemes || [])) {
        for (const stock of (sub.stocks || [])) {
          if (seen.has(stock.ticker)) continue;
          seen.add(stock.ticker);
          if ((stock.bars_30d?.length ?? 0) >= 12) {
            const tri = detectTriangle(stock.bars_30d);
            if (tri) results.push({ ...stock, _triangle: tri, _theme: theme.name, _sub: sub.name });
          }
        }
      }
    }
    return results.sort((a, b) => a._triangle.barsToApex - b._triangle.barsToApex);
  }, [data]);

  if (!data) return null;

  return (
    <>
      <div className="bg-zinc-900/60 border border-zinc-700/40 rounded-xl overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-2.5">
          <BarChart2 size={13} className="text-amber-400"/>
          <span className="text-[13px] font-semibold text-zinc-200">Flagging</span>
          <span className="px-1.5 py-0.5 rounded text-[11px] bg-amber-500/20 text-amber-400 font-mono">
            {flagging.length}
          </span>
        </div>

        <div className="px-2 pb-2 flex flex-col gap-0.5 max-h-[420px] overflow-y-auto custom-scrollbar">
          {flagging.length === 0 ? (
            <p className="text-[12px] text-zinc-600 px-2 py-3 text-center">No triangles detected</p>
          ) : (
            flagging.map(s => (
              <button
                key={s.ticker}
                onClick={() => setSelectedStock(s)}
                className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-zinc-800/60 transition-colors group"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-[13px] font-mono font-semibold text-zinc-200 group-hover:text-white shrink-0">
                      {s.ticker}
                    </span>
                    <span className="text-[11px] text-zinc-500 truncate">{s._sub}</span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-[11px] text-amber-400/80 font-mono">~{s._triangle.barsToApex}d</span>
                    <span className={`text-[12px] font-mono ${(s.perf_1d ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {(s.perf_1d ?? 0) >= 0 ? '+' : ''}{(s.perf_1d ?? 0).toFixed(1)}%
                    </span>
                  </div>
                </div>
              </button>
            ))
          )}
        </div>
      </div>

      {selectedStock && (
        <TriangleChartModal stock={selectedStock} onClose={() => setSelectedStock(null)}/>
      )}
    </>
  );
}
