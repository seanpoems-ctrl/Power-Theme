import { useState, useEffect, useMemo } from "react";
import { ChevronDown, ChevronRight, Star, Activity, BarChart3, RefreshCw, Search, SlidersHorizontal, X, Layers } from "lucide-react";

const MOCK_DATA = {
  last_updated: "2026-03-13",
  themes: [
    {
      name: "Semiconductors",
      subthemes: [
        {
          name: "AI Chips & Accelerators",
          stocks: [
            { ticker: "NVDA", company: "NVIDIA Corp", price: 142.50, change_pct: 3.21, volume: 58000000, dollar_volume: 8265000000, adr_pct: 5.1, rs_52w: 92, perf_1d: 3.21, perf_1w: 6.8, perf_1m: 12.5, perf_3m: 18.2, perf_6m: 35.1, sparkline: [118,125,121,130,128,135,138,132,140,142.5], pure_play: true },
            { ticker: "AVGO", company: "Broadcom Inc", price: 186.30, change_pct: 1.85, volume: 22000000, dollar_volume: 4098600000, adr_pct: 4.8, rs_52w: 88, perf_1d: 1.85, perf_1w: 4.2, perf_1m: 9.8, perf_3m: 22.5, perf_6m: 42.3, sparkline: [160,165,158,170,175,172,178,180,183,186.3], pure_play: false },
          ]
        },
        {
          name: "Memory & Storage",
          stocks: [
            { ticker: "TSM", company: "Taiwan Semiconductor", price: 192.80, change_pct: 1.42, volume: 18000000, dollar_volume: 3470400000, adr_pct: 4.3, rs_52w: 80, perf_1d: 1.42, perf_1w: 3.8, perf_1m: 8.5, perf_3m: 15.2, perf_6m: 24.1, sparkline: [155,160,165,168,172,175,180,185,189,192.8], pure_play: true },
            { ticker: "AMAT", company: "Applied Materials", price: 178.50, change_pct: 0.95, volume: 7200000, dollar_volume: 1285200000, adr_pct: 4.6, rs_52w: 55, perf_1d: 0.95, perf_1w: 2.1, perf_1m: 4.2, perf_3m: 5.8, perf_6m: 8.1, sparkline: [165,168,170,172,170,174,175,176,177,178.5], pure_play: false },
          ]
        }
      ]
    },
    {
      name: "Quantum Computing",
      subthemes: [
        {
          name: "Quantum Computing",
          stocks: [
            { ticker: "IONQ", company: "IonQ Inc", price: 38.50, change_pct: 6.80, volume: 18000000, dollar_volume: 693000000, adr_pct: 10.5, rs_52w: 85, perf_1d: 6.80, perf_1w: 15.2, perf_1m: 32.5, perf_3m: 68.1, perf_6m: 220.8, sparkline: [12,15,18,22,25,28,30,33,36,38.5], pure_play: true },
            { ticker: "RGTI", company: "Rigetti Computing", price: 14.20, change_pct: 8.40, volume: 35000000, dollar_volume: 497000000, adr_pct: 12.1, rs_52w: 80, perf_1d: 8.40, perf_1w: 18.5, perf_1m: 38.2, perf_3m: 78.5, perf_6m: 255.0, sparkline: [4,5,6.5,8,9,10,11,12.5,13.2,14.2], pure_play: true },
          ]
        }
      ]
    }
  ]
};

const PERF_KEYS = [
  { key: "perf_1d", label: "1D" },
  { key: "perf_1w", label: "1W" },
  { key: "perf_1m", label: "1M" },
  { key: "perf_3m", label: "3M" },
  { key: "perf_6m", label: "6M" },
];

function sparklineSeries(s) {
  if (Array.isArray(s.sparkline) && s.sparkline.length >= 2) return s.sparkline;
  if (Array.isArray(s.candles_10d) && s.candles_10d.length >= 2)
    return s.candles_10d.map((row) => row.c);
  return [];
}

const Sparkline = ({ data, width = 72, height = 26 }) => {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data), max = Math.max(...data);
  const r = max - min || 1;
  const pts = data.map((v, i) => `${(i/(data.length-1))*width},${height-((v-min)/r)*(height-4)-2}`).join(" ");
  const c = data[data.length-1] >= data[0] ? "#22c55e" : "#ef4444";
  const gid = `g${Math.random().toString(36).slice(2,7)}`;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      <defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={c} stopOpacity="0.2"/><stop offset="100%" stopColor={c} stopOpacity="0"/></linearGradient></defs>
      <path d={`M0,${height} L${pts.split(" ").map(p=>p).join(" L")} L${width},${height} Z`} fill={`url(#${gid})`}/>
      <polyline points={pts} fill="none" stroke={c} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
};

const PerfCell = ({ value }) => {
  if (value == null) return <td className="text-right py-2 px-2 text-xs text-zinc-600">—</td>;
  const v = parseFloat(value);
  let bg, txt;
  if (v >= 20) { bg = "bg-emerald-500/30"; txt = "text-emerald-300"; }
  else if (v >= 10) { bg = "bg-emerald-500/20"; txt = "text-emerald-400"; }
  else if (v >= 5) { bg = "bg-emerald-500/10"; txt = "text-emerald-400"; }
  else if (v >= 0) { bg = "bg-emerald-500/5"; txt = "text-emerald-400/80"; }
  else if (v >= -5) { bg = "bg-red-500/5"; txt = "text-red-400/80"; }
  else if (v >= -10) { bg = "bg-red-500/10"; txt = "text-red-400"; }
  else if (v >= -20) { bg = "bg-red-500/20"; txt = "text-red-400"; }
  else { bg = "bg-red-500/30"; txt = "text-red-300"; }
  return (
    <td className={`text-right py-2 px-2 text-xs font-mono font-medium ${txt} ${bg}`}>
      {v >= 0 ? "+" : ""}{v.toFixed(1)}%
    </td>
  );
};

const RSBadge = ({ value }) => {
  let cl;
  if (value >= 80) cl = "bg-emerald-500/15 text-emerald-400 border-emerald-500/20";
  else if (value >= 60) cl = "bg-blue-500/15 text-blue-400 border-blue-500/20";
  else if (value >= 40) cl = "bg-amber-500/15 text-amber-400 border-amber-500/20";
  else cl = "bg-red-500/15 text-red-400 border-red-500/20";
  return <span className={`inline-flex items-center px-1.5 py-0.5 text-[11px] font-semibold rounded border ${cl}`}>{value}</span>;
};

const fmtVol = n => n >= 1e9 ? `$${(n/1e9).toFixed(1)}B` : n >= 1e6 ? `$${(n/1e6).toFixed(0)}M` : `$${(n/1e3).toFixed(0)}K`;
const fmtNum = n => n >= 1e6 ? `${(n/1e6).toFixed(1)}M` : n >= 1e3 ? `${(n/1e3).toFixed(0)}K` : `${n}`;

const Dist52wCell = ({ value }) => {
  if (value == null) return <td className="text-right py-2 px-2 text-xs text-zinc-600">—</td>;
  const v = parseFloat(value);
  let txt;
  if (v >= -3) txt = "text-emerald-300 font-semibold";
  else if (v >= -8) txt = "text-emerald-400";
  else if (v >= -15) txt = "text-amber-400";
  else txt = "text-zinc-500";
  return <td className={`text-right py-2 px-2 text-xs font-mono ${txt}`}>{v.toFixed(1)}%</td>;
};

const RVolCell = ({ value }) => {
  if (value == null) return <td className="text-right py-2 px-2 text-xs text-zinc-600">—</td>;
  const v = parseFloat(value);
  let txt;
  if (v >= 2) txt = "text-emerald-300 font-semibold";
  else if (v >= 1.5) txt = "text-emerald-400";
  else if (v >= 1) txt = "text-zinc-300";
  else txt = "text-zinc-500";
  return <td className={`text-right py-2 px-2 text-xs font-mono ${txt}`}>{v.toFixed(2)}x</td>;
};

const StockTable = ({ stocks, sortKey, sortDir }) => {
  const sorted = useMemo(() => {
    const a = [...stocks];
    a.sort((x, y) => sortDir === "asc" ? ((x[sortKey]||0) > (y[sortKey]||0) ? 1 : -1) : ((x[sortKey]||0) < (y[sortKey]||0) ? 1 : -1));
    return a;
  }, [stocks, sortKey, sortDir]);

  return (
    <div className="overflow-x-auto rounded-lg border border-zinc-700/40">
      <table className="w-full text-sm min-w-[900px]">
        <thead>
          <tr className="text-[11px] text-zinc-500 uppercase tracking-wider bg-zinc-900/80">
            <th className="text-left py-2 px-4 font-medium w-40">Ticker</th>
            <th className="text-right py-2 px-2 font-medium">Price</th>
            <th className="text-right py-2 px-2 font-medium">52W Hi</th>
            <th className="text-right py-2 px-2 font-medium">Dist</th>
            <th className="text-right py-2 px-2 font-medium">Vol</th>
            <th className="text-right py-2 px-2 font-medium">RVol</th>
            <th className="text-right py-2 px-2 font-medium">$ Vol</th>
            <th className="text-right py-2 px-2 font-medium">ADR</th>
            <th className="text-center py-2 px-2 font-medium">RS</th>
            {PERF_KEYS.map(p => <th key={p.key} className="text-right py-2 px-2 font-medium">{p.label}</th>)}
            <th className="text-center py-2 px-2 font-medium">10D</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((s, i) => (
            <tr key={s.ticker+i} className="border-t border-zinc-800/50 hover:bg-zinc-800/30 transition-colors">
              <td className="py-2 px-4">
                <div className="flex items-center gap-2">
                  {s.pure_play && <Star size={11} className="text-amber-400 fill-amber-400 flex-shrink-0"/>}
                  <div>
                    <span className="font-semibold text-zinc-100 text-xs">{s.ticker}</span>
                    <p className="text-[10px] text-zinc-500 leading-tight truncate max-w-[110px]">{s.company}</p>
                  </div>
                </div>
              </td>
              <td className="text-right py-2 px-2 font-mono text-zinc-200 text-xs">${s.price.toFixed(2)}</td>
              <td className="text-right py-2 px-2 font-mono text-zinc-400 text-xs">{s["52w_high"] ? `$${s["52w_high"].toFixed(2)}` : "—"}</td>
              <Dist52wCell value={s.dist_52w_high}/>
              <td className="text-right py-2 px-2 text-zinc-500 text-xs font-mono">{fmtNum(s.volume)}</td>
              <RVolCell value={s.rvol}/>
              <td className="text-right py-2 px-2 text-zinc-500 text-xs font-mono">{fmtVol(s.dollar_volume)}</td>
              <td className="text-right py-2 px-2 text-zinc-400 text-xs font-mono">{s.adr_pct.toFixed(1)}%</td>
              <td className="text-center py-2 px-2"><RSBadge value={s.rs_52w}/></td>
              {PERF_KEYS.map(p => <PerfCell key={p.key} value={s[p.key]}/>)}
              <td className="text-center py-2 px-2"><div className="flex justify-center"><Sparkline data={sparklineSeries(s)}/></div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

/** Normalize: if theme has old flat `stocks` array, wrap it into subthemes */
function normalizeTheme(t) {
  if (t.subthemes) return t;
  return { ...t, subthemes: [{ name: t.name, stocks: t.stocks || [] }] };
}

const SubThemeSection = ({ subtheme, sortKey, sortDir }) => {
  const [open, setOpen] = useState(true);
  const avg = (k) => subtheme.stocks.length
    ? subtheme.stocks.reduce((s, x) => s + (x[k] || 0), 0) / subtheme.stocks.length
    : 0;

  return (
    <div className="ml-4 mb-2">
      <button onClick={() => setOpen(!open)} className="w-full flex items-center justify-between px-3 py-2 bg-zinc-800/40 hover:bg-zinc-800/60 rounded-md border border-zinc-700/30 transition-colors">
        <div className="flex items-center gap-2">
          {open ? <ChevronDown size={12} className="text-zinc-500"/> : <ChevronRight size={12} className="text-zinc-500"/>}
          <span className="text-xs font-medium text-zinc-300">{subtheme.name}</span>
          <span className="text-[10px] text-zinc-600 bg-zinc-700/30 px-1.5 py-0.5 rounded">{subtheme.stocks.length}</span>
        </div>
        <div className="flex items-center gap-2.5 text-[10px]">
          {PERF_KEYS.map(p => {
            const v = avg(p.key);
            return (
              <span key={p.key} className="hidden lg:flex items-center gap-1">
                <span className="text-zinc-600">{p.label}</span>
                <span className={`font-mono font-medium ${v >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{v >= 0 ? '+' : ''}{v.toFixed(1)}%</span>
              </span>
            );
          })}
        </div>
      </button>
      {open && (
        <div className="mt-1">
          <StockTable stocks={subtheme.stocks} sortKey={sortKey} sortDir={sortDir}/>
        </div>
      )}
    </div>
  );
};

const ThemeSection = ({ theme, sortKey, sortDir }) => {
  const [open, setOpen] = useState(true);
  const norm = normalizeTheme(theme);
  const allStocks = norm.subthemes.flatMap(s => s.stocks);

  const avg = (k) => allStocks.length
    ? allStocks.reduce((s, x) => s + (x[k] || 0), 0) / allStocks.length
    : 0;

  return (
    <div className="mb-4">
      <button onClick={() => setOpen(!open)} className="w-full flex items-center justify-between px-4 py-2.5 bg-zinc-800/60 hover:bg-zinc-800/80 rounded-lg border border-zinc-700/50 transition-colors">
        <div className="flex items-center gap-3">
          {open ? <ChevronDown size={14} className="text-zinc-400"/> : <ChevronRight size={14} className="text-zinc-400"/>}
          <Layers size={13} className="text-blue-400"/>
          <span className="font-semibold text-sm text-zinc-100">{norm.name}</span>
          <span className="text-[11px] text-zinc-500 bg-zinc-700/40 px-1.5 py-0.5 rounded">
            {norm.subthemes.length} sub · {allStocks.length} stocks
          </span>
        </div>
        <div className="flex items-center gap-3 text-[11px]">
          {PERF_KEYS.map(p => {
            const v = avg(p.key);
            return (
              <span key={p.key} className="hidden lg:flex items-center gap-1">
                <span className="text-zinc-600">{p.label}</span>
                <span className={`font-mono font-medium ${v >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{v >= 0 ? '+' : ''}{v.toFixed(1)}%</span>
              </span>
            );
          })}
          <span className="text-zinc-500">RS <span className="text-zinc-300 font-medium">{avg("rs_52w").toFixed(0)}</span></span>
        </div>
      </button>
      {open && (
        <div className="mt-1.5 space-y-1.5">
          {norm.subthemes.map((sub, i) => (
            <SubThemeSection key={sub.name + i} subtheme={sub} sortKey={sortKey} sortDir={sortDir}/>
          ))}
        </div>
      )}
    </div>
  );
};

export default function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filtersOn, setFiltersOn] = useState(true);
  const [filterDolVol, setFilterDolVol] = useState(100);
  const [filterADR, setFilterADR] = useState(4);
  const [filterRS, setFilterRS] = useState(50);
  const [filterDist52w, setFilterDist52w] = useState(20);
  const [sortKey, setSortKey] = useState("rs_52w");
  const [sortDir, setSortDir] = useState("desc");
  const [showFP, setShowFP] = useState(false);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const r = await fetch("/thematic_data.json");
        if (r.ok) setData(await r.json()); else setData(MOCK_DATA);
      } catch { setData(MOCK_DATA); }
      setLoading(false);
    })();
  }, []);

  const filtered = useMemo(() => {
    if (!data) return [];
    return data.themes.map(t => {
      const norm = normalizeTheme(t);
      const filteredSubs = norm.subthemes.map(sub => {
        let st = sub.stocks;
        if (search) {
          const q = search.toLowerCase();
          st = st.filter(s => s.ticker.toLowerCase().includes(q) || (s.company || "").toLowerCase().includes(q));
        }
        if (filtersOn) {
          st = st.filter(s =>
            s.dollar_volume >= filterDolVol * 1e6 &&
            s.adr_pct >= filterADR &&
            s.rs_52w >= filterRS &&
            (s.dist_52w_high == null || s.dist_52w_high >= -filterDist52w)
          );
        }
        return { ...sub, stocks: st };
      }).filter(sub => sub.stocks.length > 0);
      return { ...norm, subthemes: filteredSubs };
    }).filter(t => t.subthemes.length > 0);
  }, [data, search, filtersOn, filterDolVol, filterADR, filterRS]);

  const unique = [...new Set(filtered.flatMap(t => t.subthemes.flatMap(s => s.stocks.map(st => st.ticker))))];
  const totalSubs = filtered.reduce((n, t) => n + t.subthemes.length, 0);

  if (loading) return <div className="min-h-screen bg-zinc-950 flex items-center justify-center"><RefreshCw size={24} className="text-zinc-500 animate-spin"/></div>;

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="border-b border-zinc-800/60 bg-zinc-950/80 backdrop-blur-sm sticky top-0 z-20">
        <div className="max-w-[1400px] mx-auto px-4 py-3">
          <div className="flex items-center justify-between mb-2.5">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center"><Activity size={16} className="text-white"/></div>
              <div>
                <h1 className="text-base font-bold tracking-tight">Thematic Scanner</h1>
                <p className="text-[11px] text-zinc-500">美股強勢主題篩選器</p>
              </div>
            </div>
            <span className="text-[11px] text-zinc-600">Updated {data?.last_updated}</span>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-3 text-[11px]">
              <span className="text-zinc-500">{filtered.length} themes</span>
              <span className="text-zinc-600">·</span>
              <span className="text-zinc-500">{totalSubs} sub-themes</span>
              <span className="text-zinc-600">·</span>
              <span className="text-zinc-500">{unique.length} tickers</span>
            </div>
            <div className="flex-1"/>
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500"/>
              <input type="text" value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search..." className="w-40 pl-7 pr-3 py-1.5 text-xs bg-zinc-800/60 border border-zinc-700/50 rounded-md text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-zinc-600"/>
              {search && <button onClick={()=>setSearch("")} className="absolute right-2 top-1/2 -translate-y-1/2"><X size={11} className="text-zinc-500"/></button>}
            </div>
            <button onClick={()=>setShowFP(!showFP)} className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-md border transition-colors ${filtersOn?'bg-blue-500/15 border-blue-500/30 text-blue-400':'bg-zinc-800/60 border-zinc-700/50 text-zinc-400'}`}>
              <SlidersHorizontal size={12}/> Filters
            </button>
            <div className="flex items-center gap-1.5">
              <select value={sortKey} onChange={e=>setSortKey(e.target.value)} className="text-[11px] bg-zinc-800/60 border border-zinc-700/50 rounded px-2 py-1.5 text-zinc-300 focus:outline-none">
                <option value="rs_52w">Sort: RS</option>
                <option value="perf_1d">Sort: 1D</option>
                <option value="perf_1w">Sort: 1W</option>
                <option value="perf_1m">Sort: 1M</option>
                <option value="perf_3m">Sort: 3M</option>
                <option value="perf_6m">Sort: 6M</option>
                <option value="dollar_volume">Sort: $ Vol</option>
                <option value="adr_pct">Sort: ADR</option>
              </select>
              <button onClick={()=>setSortDir(d=>d==="desc"?"asc":"desc")} className="text-[11px] px-2 py-1.5 bg-zinc-800/60 border border-zinc-700/50 rounded text-zinc-400 hover:text-zinc-200">{sortDir==="desc"?"↓":"↑"}</button>
            </div>
          </div>
          {showFP && (
            <div className="mt-2.5 p-3 bg-zinc-800/40 rounded-lg border border-zinc-700/40 flex flex-wrap items-end gap-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={filtersOn} onChange={()=>setFiltersOn(!filtersOn)} className="rounded"/>
                <span className="text-xs text-zinc-300">Enable</span>
              </label>
              <div>
                <label className="text-[10px] text-zinc-500 block mb-1">Min $ Vol</label>
                <select value={filterDolVol} onChange={e=>setFilterDolVol(Number(e.target.value))} className="text-xs bg-zinc-900 border border-zinc-700/50 rounded px-2 py-1 text-zinc-300">
                  {[50,100,250,500].map(v=><option key={v} value={v}>${v}M</option>)}
                </select>
              </div>
              <div>
                <label className="text-[10px] text-zinc-500 block mb-1">Min ADR%</label>
                <select value={filterADR} onChange={e=>setFilterADR(Number(e.target.value))} className="text-xs bg-zinc-900 border border-zinc-700/50 rounded px-2 py-1 text-zinc-300">
                  {[2,3,4,5,7].map(v=><option key={v} value={v}>{v}%</option>)}
                </select>
              </div>
              <div>
                <label className="text-[10px] text-zinc-500 block mb-1">Min RS</label>
                <select value={filterRS} onChange={e=>setFilterRS(Number(e.target.value))} className="text-xs bg-zinc-900 border border-zinc-700/50 rounded px-2 py-1 text-zinc-300">
                  {[30,50,70,80,90].map(v=><option key={v} value={v}>{v}+</option>)}
                </select>
              </div>
              <div>
                <label className="text-[10px] text-zinc-500 block mb-1">Max Dist 52W Hi</label>
                <select value={filterDist52w} onChange={e=>setFilterDist52w(Number(e.target.value))} className="text-xs bg-zinc-900 border border-zinc-700/50 rounded px-2 py-1 text-zinc-300">
                  {[5,10,15,20,30].map(v=><option key={v} value={v}>within {v}%</option>)}
                </select>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="max-w-[1400px] mx-auto px-4 py-4">
        <div className="flex items-center gap-4 mb-3 text-[10px] text-zinc-600">
          <span className="flex items-center gap-1"><Star size={9} className="text-amber-400 fill-amber-400"/> Pure Play</span>
          <span>Performance columns use heatmap coloring</span>
          <span className="flex items-center gap-1"><Layers size={9} className="text-blue-400"/> Theme → Sub-theme hierarchy</span>
        </div>
        {filtered.length === 0 ? (
          <div className="text-center py-16 text-zinc-500">
            <BarChart3 size={28} className="mx-auto mb-3 opacity-40"/>
            <p className="text-sm">No results</p>
            <button onClick={()=>{setFiltersOn(false);setSearch("");}} className="mt-2 text-xs text-blue-400 hover:underline">Reset</button>
          </div>
        ) : filtered.map((t,i) => <ThemeSection key={t.name+i} theme={t} sortKey={sortKey} sortDir={sortDir}/>)}
      </div>
    </div>
  );
}
