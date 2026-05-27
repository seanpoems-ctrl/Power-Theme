import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { createChart, ColorType, CandlestickSeries, LineSeries } from 'lightweight-charts';
import { X, TrendingUp, ChevronDown, BarChart2 } from 'lucide-react';

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
  if (upper.at(last) <= lower.at(last)) return null;
  if (upper.slope - lower.slope >= 0) return null;

  const apexX = (lower.intercept - upper.intercept) / (upper.slope - lower.slope);
  const barsToApex = apexX - last;
  if (barsToApex < 1 || barsToApex > 45) return null;

  const startIdx = Math.min(rh[0].x, rl[0].x);
  if (last - startIdx < 5) return null;

  const cur = bars[last].c;
  if (cur > upper.at(last) * 1.04 || cur < lower.at(last) * 0.96) return null;

  return { upper, lower, startIdx, barsToApex: Math.round(barsToApex) };
}

// ─── Chart Modal ───────────────────────────────────────────────────────────────

const BASE_TIME = 1700000000;
const mkTime = idx => BASE_TIME + idx * 86400;

function TriangleChartModal({ stock, onClose }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const upperSerRef = useRef(null);
  const lowerSerRef = useRef(null);
  const upperPtsRef = useRef(null);
  const lowerPtsRef = useRef(null);
  const dragging = useRef(null);

  const [upperPts, setUpperPts] = useState(null);
  const [lowerPts, setLowerPts] = useState(null);
  const [handlePx, setHandlePx] = useState(null);

  const bars = useMemo(() => stock.bars_30d || [], [stock]);
  const triangle = useMemo(() => detectTriangle(bars), [bars]);

  const computeHandles = useCallback(() => {
    if (!chartRef.current || !upperSerRef.current || !lowerSerRef.current) return;
    const uPts = upperPtsRef.current;
    const lPts = lowerPtsRef.current;
    if (!uPts || !lPts) return;
    const ts = chartRef.current.timeScale();
    const px = (barIdx, price, ser) => {
      const x = ts.timeToCoordinate(mkTime(barIdx));
      const y = ser.priceToCoordinate(price);
      return (x != null && y != null) ? { x, y } : null;
    };
    const up0 = px(uPts[0].barIdx, uPts[0].price, upperSerRef.current);
    const up1 = px(uPts[1].barIdx, uPts[1].price, upperSerRef.current);
    const lo0 = px(lPts[0].barIdx, lPts[0].price, lowerSerRef.current);
    const lo1 = px(lPts[1].barIdx, lPts[1].price, lowerSerRef.current);
    if (up0 && up1 && lo0 && lo1) setHandlePx({ upper: [up0, up1], lower: [lo0, lo1] });
  }, []);

  // ── Build chart once on mount ────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || !bars.length) return;

    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: '#18181b' }, textColor: '#a1a1aa' },
      grid: { vertLines: { color: '#27272a' }, horzLines: { color: '#27272a' } },
      width: containerRef.current.clientWidth,
      height: 360,
      rightPriceScale: { borderColor: '#3f3f46' },
      timeScale: { borderColor: '#3f3f46', timeVisible: false },
    });
    chartRef.current = chart;

    const cSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#e2e8f0', downColor: '#64748b',
      borderUpColor: '#e2e8f0', borderDownColor: '#64748b',
      wickUpColor: '#94a3b8', wickDownColor: '#64748b',
    });
    cSeries.setData(bars.map((b, i) => ({
      time: mkTime(i),
      open: i > 0 ? bars[i - 1].c : b.c,
      high: b.h, low: b.l, close: b.c,
    })));

    // Initial trendline endpoints
    const last = bars.length - 1;
    let initU, initL;
    if (triangle) {
      const { upper, lower, startIdx } = triangle;
      initU = [{ barIdx: startIdx, price: upper.at(startIdx) }, { barIdx: last, price: upper.at(last) }];
      initL = [{ barIdx: startIdx, price: lower.at(startIdx) }, { barIdx: last, price: lower.at(last) }];
    } else {
      const start = Math.max(0, bars.length - 20);
      const slice = bars.slice(start);
      const topS = Math.max(...slice.slice(0, 5).map(b => b.h));
      const botS = Math.min(...slice.slice(0, 5).map(b => b.l));
      const prRange = Math.max(...bars.map(b => b.h)) - Math.min(...bars.map(b => b.l));
      initU = [{ barIdx: start, price: topS }, { barIdx: last, price: bars[last].c + prRange * 0.02 }];
      initL = [{ barIdx: start, price: botS }, { barIdx: last, price: bars[last].c - prRange * 0.02 }];
    }

    setUpperPts(initU); upperPtsRef.current = initU;
    setLowerPts(initL); lowerPtsRef.current = initL;

    const uSer = chart.addSeries(LineSeries, {
      color: '#ef4444', lineWidth: 2,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    });
    uSer.setData([
      { time: mkTime(initU[0].barIdx), value: initU[0].price },
      { time: mkTime(initU[1].barIdx), value: initU[1].price },
    ]);
    upperSerRef.current = uSer;

    const lSer = chart.addSeries(LineSeries, {
      color: '#22c55e', lineWidth: 2,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    });
    lSer.setData([
      { time: mkTime(initL[0].barIdx), value: initL[0].price },
      { time: mkTime(initL[1].barIdx), value: initL[1].price },
    ]);
    lowerSerRef.current = lSer;

    chart.timeScale().fitContent();
    chart.timeScale().subscribeVisibleTimeRangeChange(computeHandles);

    const t = setTimeout(computeHandles, 120);

    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
        setTimeout(computeHandles, 50);
      }
    });
    ro.observe(containerRef.current);

    return () => {
      clearTimeout(t);
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      upperSerRef.current = null;
      lowerSerRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Sync series + handles when endpoints change ──────────────────────────
  useEffect(() => {
    if (!upperSerRef.current || !lowerSerRef.current || !upperPts || !lowerPts) return;
    upperPtsRef.current = upperPts;
    lowerPtsRef.current = lowerPts;
    upperSerRef.current.setData([
      { time: mkTime(upperPts[0].barIdx), value: upperPts[0].price },
      { time: mkTime(upperPts[1].barIdx), value: upperPts[1].price },
    ]);
    lowerSerRef.current.setData([
      { time: mkTime(lowerPts[0].barIdx), value: lowerPts[0].price },
      { time: mkTime(lowerPts[1].barIdx), value: lowerPts[1].price },
    ]);
    computeHandles();
  }, [upperPts, lowerPts, computeHandles]);

  // ── Drag interaction ─────────────────────────────────────────────────────
  const onHandleMouseDown = useCallback((line, ptIdx, e) => {
    e.preventDefault();
    e.stopPropagation();
    dragging.current = { line, ptIdx };

    const onMove = (ev) => {
      if (!dragging.current || !containerRef.current || !chartRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const y = ev.clientY - rect.top;
      const x = ev.clientX - rect.left;
      const ser = dragging.current.line === 'upper' ? upperSerRef.current : lowerSerRef.current;
      if (!ser) return;

      const price = ser.coordinateToPrice(y);
      if (price == null) return;

      const rawTime = chartRef.current.timeScale().coordinateToTime(x);
      let barIdx = null;
      if (rawTime != null) {
        barIdx = Math.round((rawTime - BASE_TIME) / 86400);
        barIdx = Math.max(0, Math.min(bars.length - 1, barIdx));
      }

      const setter = dragging.current.line === 'upper' ? setUpperPts : setLowerPts;
      const serRef = dragging.current.line === 'upper' ? upperSerRef : lowerSerRef;
      setter(prev => {
        if (!prev) return prev;
        const next = [...prev];
        next[dragging.current.ptIdx] = {
          barIdx: barIdx ?? next[dragging.current.ptIdx].barIdx,
          price,
        };
        // Update series directly for smooth dragging (bypass React render cycle)
        if (serRef.current) {
          const updated = [...next];
          serRef.current.setData([
            { time: mkTime(updated[0].barIdx), value: updated[0].price },
            { time: mkTime(updated[1].barIdx), value: updated[1].price },
          ]);
        }
        return next;
      });
    };

    const onUp = () => {
      dragging.current = null;
      computeHandles();
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [bars.length, computeHandles]);

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
            <span className="text-zinc-400 text-sm truncate max-w-[220px]">{stock.company}</span>
            {triangle ? (
              <span className="px-2 py-0.5 rounded text-[11px] bg-amber-500/20 text-amber-400 border border-amber-500/30">
                Triangle · apex ~{triangle.barsToApex}d
              </span>
            ) : (
              <span className="px-2 py-0.5 rounded text-[11px] bg-zinc-700/60 text-zinc-400 border border-zinc-600/30">
                Manual
              </span>
            )}
          </div>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300 p-1 ml-2">
            <X size={16}/>
          </button>
        </div>

        {/* Chart area */}
        <div className="p-3">
          <div className="relative rounded-lg overflow-hidden" style={{ minHeight: 360 }}>
            <div ref={containerRef} className="w-full"/>

            {/* Drag handles overlay */}
            {handlePx && (
              <div className="absolute inset-0" style={{ pointerEvents: 'none', zIndex: 10 }}>
                {(['upper', 'lower']).flatMap(line =>
                  (handlePx[line] || []).map((p, i) => p ? (
                    <div
                      key={`${line}-${i}`}
                      style={{
                        position: 'absolute',
                        left: p.x - 7,
                        top: p.y - 7,
                        width: 14,
                        height: 14,
                        borderRadius: '50%',
                        background: line === 'upper' ? '#ef4444' : '#22c55e',
                        border: '2px solid rgba(255,255,255,0.85)',
                        cursor: 'grab',
                        pointerEvents: 'auto',
                        boxShadow: '0 2px 8px rgba(0,0,0,0.6)',
                        userSelect: 'none',
                      }}
                      onMouseDown={e => onHandleMouseDown(line, i, e)}
                    />
                  ) : null)
                )}
              </div>
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
  const [collapsed, setCollapsed] = useState(false);

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
        <button
          className="w-full flex items-center justify-between px-3 py-2.5 hover:bg-zinc-800/40 transition-colors"
          onClick={() => setCollapsed(c => !c)}
        >
          <div className="flex items-center gap-2">
            <BarChart2 size={13} className="text-amber-400"/>
            <span className="text-[13px] font-semibold text-zinc-200">Flagging</span>
            <span className="px-1.5 py-0.5 rounded text-[11px] bg-amber-500/20 text-amber-400 font-mono">
              {flagging.length}
            </span>
          </div>
          <ChevronDown size={13} className={`text-zinc-500 transition-transform ${collapsed ? '-rotate-90' : ''}`}/>
        </button>

        {!collapsed && (
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
        )}
      </div>

      {selectedStock && (
        <TriangleChartModal stock={selectedStock} onClose={() => setSelectedStock(null)}/>
      )}
    </>
  );
}
