import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
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

  // Try win=3 first for cleaner pivots; fall back to win=2 for tight consolidations
  let highs = [], lows = [];
  for (const win of [3, 2]) {
    const s = findSwings(bars, win);
    if (s.highs.length >= 2 && s.lows.length >= 2) {
      highs = s.highs; lows = s.lows; break;
    }
  }
  if (highs.length < 2 || lows.length < 2) return null;

  const last = bars.length - 1;
  const avgPrice = (bars[last].h + bars[last].l + bars[last].c) / 3;

  // Use up to 4 recent pivots for a more robust regression
  const rh = highs.slice(-4);
  const rl = lows.slice(-4);
  const upper = linReg(rh);
  const lower = linReg(rl);
  if (!upper || !lower) return null;

  const uLast = upper.at(last);
  const lLast = lower.at(last);
  if (uLast <= lLast) return null;
  // Lines must be converging — no restriction on individual slope direction
  if (upper.slope >= lower.slope) return null;

  const startIdx = Math.min(rh[0].x, rl[0].x);
  if (last - startIdx < 8) return null;

  const rangeAtStart = upper.at(startIdx) - lower.at(startIdx);
  const rangeAtEnd = uLast - lLast;
  if (rangeAtStart <= 0 || rangeAtEnd <= 0) return null;

  // Require at least 20% contraction over the visible pattern (was 10%)
  if (rangeAtEnd >= rangeAtStart * 0.80) return null;

  // Current range must be meaningfully tight relative to price
  if (rangeAtEnd / avgPrice > 0.25) return null;

  const apexX = (lower.intercept - upper.intercept) / (upper.slope - lower.slope);
  const barsToApex = apexX - last;
  // Allow apex upcoming ≤80 bars, or just-passed ≥−5 bars (breakout candidate)
  if (barsToApex < -5 || barsToApex > 80) return null;

  // Price should be inside or just touching the boundaries
  const close = bars[last].c;
  const tol = rangeAtEnd * 0.20;
  if (close > uLast + tol || close < lLast - tol) return null;
  if (bars[last].h > uLast * 1.05) return null;

  return { upper, lower, startIdx, barsToApex: Math.max(0, Math.round(barsToApex)) };
}

// ─── localStorage persistence (time-based offsets so positions survive bar rolls) ──

const lsKey = ticker => `flagging_lines_v2_${ticker}`;

function loadSavedLines(ticker, lastBarTime) {
  try {
    const raw = localStorage.getItem(lsKey(ticker));
    if (!raw) return null;
    const saved = JSON.parse(raw);
    return {
      upper: saved.upper.map(p => ({ time: lastBarTime + p.timeOffset, price: p.price })),
      lower: saved.lower.map(p => ({ time: lastBarTime + p.timeOffset, price: p.price })),
    };
  } catch { return null; }
}

function saveLines(ticker, lastBarTime, uPts, lPts) {
  if (!uPts || !lPts) return;
  localStorage.setItem(lsKey(ticker), JSON.stringify({
    upper: uPts.map(p => ({ timeOffset: p.time - lastBarTime, price: p.price })),
    lower: lPts.map(p => ({ timeOffset: p.time - lastBarTime, price: p.price })),
    savedAt: Date.now(),
  }));
}

function clearSavedLines(ticker) {
  localStorage.removeItem(lsKey(ticker));
  localStorage.removeItem(`flagging_lines_v1_${ticker}`);
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
  const outerRef = useRef(null);
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const upperSerRef = useRef(null);
  const lowerSerRef = useRef(null);
  const upperPtsRef = useRef(null);
  const lowerPtsRef = useRef(null);
  const timesRef = useRef([]);
  const chartBarsLenRef = useRef(0);
  const dragging = useRef(null);

  const [chartBars, setChartBars] = useState(null);
  const [upperPts, setUpperPts] = useState(null);
  const [lowerPts, setLowerPts] = useState(null);
  const [handlePx, setHandlePx] = useState(null);
  const [hasSaved, setHasSaved] = useState(() => !!localStorage.getItem(lsKey(stock.ticker)));
  const [dragOverride, setDragOverride] = useState(null);

  const bars = useMemo(() => stock.bars_30d || [], [stock]);
  const triangle = useMemo(() => detectTriangle(bars), [bars]);

  // ── Fetch 6-month Yahoo Finance data ─────────────────────────────────────
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

  // ── Recompute drag handle pixel positions ─────────────────────────────────
  const computeHandles = useCallback(() => {
    if (!chartRef.current || !upperSerRef.current || !lowerSerRef.current || dragging.current) return;
    const uPts = upperPtsRef.current;
    const lPts = lowerPtsRef.current;
    if (!uPts || !lPts) return;
    const ts = chartRef.current.timeScale();
    const px = (pt, ser) => {
      const x = ts.timeToCoordinate(pt.time);
      const y = ser.priceToCoordinate(pt.price);
      return (x != null && y != null) ? { x, y } : null;
    };
    const up0 = px(uPts[0], upperSerRef.current);
    const up1 = px(uPts[1], upperSerRef.current);
    const lo0 = px(lPts[0], lowerSerRef.current);
    const lo1 = px(lPts[1], lowerSerRef.current);
    if (up0 && up1 && lo0 && lo1) {
      setHandlePx(prev => {
        if (prev &&
          Math.abs(prev.upper[0].x - up0.x) < 0.5 && Math.abs(prev.upper[0].y - up0.y) < 0.5 &&
          Math.abs(prev.upper[1].x - up1.x) < 0.5 && Math.abs(prev.upper[1].y - up1.y) < 0.5 &&
          Math.abs(prev.lower[0].x - lo0.x) < 0.5 && Math.abs(prev.lower[0].y - lo0.y) < 0.5 &&
          Math.abs(prev.lower[1].x - lo1.x) < 0.5 && Math.abs(prev.lower[1].y - lo1.y) < 0.5
        ) return prev;
        return { upper: [up0, up1], lower: [lo0, lo1] };
      });
    }
  }, []);

  // ── Build chart once chartBars are ready ─────────────────────────────────
  useEffect(() => {
    if (!chartBars || !containerRef.current) return;

    const offset = Math.max(0, chartBars.length - bars.length);
    timesRef.current = chartBars.map(b => b.t);
    chartBarsLenRef.current = chartBars.length;

    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: '#18181b' }, textColor: '#a1a1aa' },
      grid: { vertLines: { color: '#27272a' }, horzLines: { color: '#27272a' } },
      width: containerRef.current.clientWidth,
      height: 440,
      rightPriceScale: { borderColor: '#3f3f46' },
      timeScale: { borderColor: '#3f3f46', timeVisible: true, secondsVisible: false },
    });
    chartRef.current = chart;

    const cSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    });
    cSeries.setData(chartBars.map(b => ({
      time: b.t, open: b.o, high: b.h, low: b.l, close: b.c,
    })));

    const getT = idx => timesRef.current[idx + offset] ?? BASE_TIME + (idx + offset) * 86400;
    const lastBarTime = timesRef.current[chartBars.length - 1] ?? BASE_TIME + (chartBars.length - 1) * 86400;

    const saved = loadSavedLines(stock.ticker, lastBarTime);
    const auto = buildAutoEndpoints(bars, triangle, getT);
    const initU = saved?.upper ?? auto.upper;
    const initL = saved?.lower ?? auto.lower;

    setUpperPts(initU); upperPtsRef.current = initU;
    setLowerPts(initL); lowerPtsRef.current = initL;

    const uSer = chart.addSeries(LineSeries, {
      color: '#ef4444', lineWidth: 2,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    });
    uSer.setData([
      { time: initU[0].time, value: initU[0].price },
      { time: initU[1].time, value: initU[1].price },
    ]);
    upperSerRef.current = uSer;

    const lSer = chart.addSeries(LineSeries, {
      color: '#22c55e', lineWidth: 2,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    });
    lSer.setData([
      { time: initL[0].time, value: initL[0].price },
      { time: initL[1].time, value: initL[1].price },
    ]);
    lowerSerRef.current = lSer;

    // Volume histogram (pane 1) — pane separator locked
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
      if (!document.getElementById('lwc-no-resize')) {
        const s = document.createElement('style');
        s.id = 'lwc-no-resize';
        s.textContent = 'tr[style*="height: 1px"] { display: none !important; }';
        document.head.appendChild(s);
      }
    }

    chart.timeScale().fitContent();

    let rafId;
    const rafLoop = () => { computeHandles(); rafId = requestAnimationFrame(rafLoop); };
    rafId = requestAnimationFrame(rafLoop);

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    });
    ro.observe(containerRef.current);

    return () => {
      cancelAnimationFrame(rafId);
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      upperSerRef.current = null;
      lowerSerRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartBars]);

  // body { zoom: 1.15 } causes LWC to use visual-px offsets as CSS-px chart coordinates
  // (clientX - rect.left is visual px, but chart coordinate space is CSS px).
  // Applying inverse zoom to the chart wrapper makes net effective zoom = 1,
  // so visual px === CSS px — crosshair aligns with the mouse cursor.
  const bodyZoom = parseFloat(getComputedStyle(document.body).zoom) || 1;

  // ── Sync series when endpoints change ─────────────────────────────────────
  useEffect(() => {
    if (!upperSerRef.current || !lowerSerRef.current || !upperPts || !lowerPts) return;
    upperPtsRef.current = upperPts;
    lowerPtsRef.current = lowerPts;
    upperSerRef.current.setData([
      { time: upperPts[0].time, value: upperPts[0].price },
      { time: upperPts[1].time, value: upperPts[1].price },
    ]);
    lowerSerRef.current.setData([
      { time: lowerPts[0].time, value: lowerPts[0].price },
      { time: lowerPts[1].time, value: lowerPts[1].price },
    ]);
    if (!dragging.current) computeHandles();
  }, [upperPts, lowerPts, computeHandles]);

  // ── Drag interaction ──────────────────────────────────────────────────────
  const onHandleMouseDown = useCallback((line, ptIdx, e) => {
    e.preventDefault();
    e.stopPropagation();
    dragging.current = { line, ptIdx };
    const key = `${line}-${ptIdx}`;
    if (chartRef.current) chartRef.current.applyOptions({ handleScroll: false, handleScale: false });

    const onMove = (ev) => {
      if (!dragging.current || !containerRef.current || !chartRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const mouseX = ev.clientX - rect.left;
      const mouseY = ev.clientY - rect.top;
      setDragOverride({ key, pos: { x: mouseX, y: mouseY } });
      const ser = dragging.current.line === 'upper' ? upperSerRef.current : lowerSerRef.current;
      if (!ser) return;
      const price = ser.coordinateToPrice(mouseY);
      const rawTime = chartRef.current.timeScale().coordinateToTime(mouseX);
      if (price == null || rawTime == null) return;
      const setter = dragging.current.line === 'upper' ? setUpperPts : setLowerPts;
      const serRef = dragging.current.line === 'upper' ? upperSerRef : lowerSerRef;
      setter(prev => {
        if (!prev) return prev;
        const next = [...prev];
        next[dragging.current.ptIdx] = { time: rawTime, price };
        if (serRef.current) serRef.current.setData([
          { time: next[0].time, value: next[0].price },
          { time: next[1].time, value: next[1].price },
        ]);
        return next;
      });
    };

    const onUp = () => {
      dragging.current = null;
      if (chartRef.current) chartRef.current.applyOptions({ handleScroll: true, handleScale: true });
      setDragOverride(null);
      computeHandles();
      const lastBarTime = timesRef.current[timesRef.current.length - 1]
        ?? BASE_TIME + (timesRef.current.length - 1) * 86400;
      saveLines(stock.ticker, lastBarTime, upperPtsRef.current, lowerPtsRef.current);
      setHasSaved(true);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [computeHandles, stock.ticker]);

  // ── Reset trendlines ──────────────────────────────────────────────────────
  const handleReset = useCallback(() => {
    clearSavedLines(stock.ticker);
    setHasSaved(false);
    const offset = Math.max(0, chartBarsLenRef.current - bars.length);
    const getT = idx => timesRef.current[idx + offset] ?? BASE_TIME + (idx + offset) * 86400;
    const auto = buildAutoEndpoints(bars, triangle, getT);
    setUpperPts(auto.upper);
    setLowerPts(auto.lower);
  }, [stock.ticker, bars, triangle]);

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
            {hasSaved && (
              <span className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] bg-blue-500/15 text-blue-400 border border-blue-500/25">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400 inline-block"/>已儲存
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 ml-2">
            {hasSaved && (
              <button
                onClick={handleReset}
                className="px-2 py-1 text-[11px] rounded border border-zinc-600/50 text-zinc-400 hover:text-zinc-200 hover:border-zinc-500 transition-colors"
              >Reset</button>
            )}
            <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300 p-1">
              <X size={16}/>
            </button>
          </div>
        </div>

        {/* Chart area */}
        <div className="p-3">
          <div ref={outerRef} className="relative rounded-lg overflow-hidden" style={{ minHeight: 440, zoom: 1 / bodyZoom }}>
            {!chartBars && (
              <div className="absolute inset-0 flex items-center justify-center bg-zinc-900/80 rounded-lg z-20">
                <div className="flex flex-col items-center gap-2">
                  <div className="w-6 h-6 border-2 border-zinc-600 border-t-blue-400 rounded-full animate-spin"/>
                  <span className="text-[12px] text-zinc-500">Loading 6-month data…</span>
                </div>
              </div>
            )}
            <div ref={containerRef} className="w-full"/>

            {/* Drag handles */}
            {outerRef.current && handlePx && createPortal(
              <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 10 }}>
                {(['upper', 'lower']).flatMap(line =>
                  (handlePx[line] || []).map((p, i) => {
                    if (!p) return null;
                    const key = `${line}-${i}`;
                    const pos = (dragOverride?.key === key) ? dragOverride.pos : p;
                    return (
                      <div
                        key={key}
                        style={{
                          position: 'absolute',
                          left: pos.x - 7, top: pos.y - 7,
                          width: 14, height: 14,
                          borderRadius: '50%',
                          background: line === 'upper' ? '#ef4444' : '#22c55e',
                          border: '2px solid rgba(255,255,255,0.85)',
                          cursor: dragOverride?.key === key ? 'grabbing' : 'grab',
                          pointerEvents: 'auto',
                          boxShadow: '0 2px 8px rgba(0,0,0,0.6)',
                          userSelect: 'none',
                        }}
                        onMouseDown={ev => onHandleMouseDown(line, i, ev)}
                      />
                    );
                  })
                )}
              </div>,
              outerRef.current
            )}
          </div>

          {/* Legend */}
          <div className="mt-2 flex items-center gap-4 text-[11px] text-zinc-500">
            <span className="flex items-center gap-1.5">
              <span className="w-4 h-0.5 bg-red-500 rounded inline-block"/>Resistance
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-4 h-0.5 bg-green-500 rounded inline-block"/>Support
            </span>
            <span className="ml-auto text-zinc-600">拖曳端點可手動調整趨勢線</span>
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
