import { useState, useEffect, useMemo } from "react";
import { ChevronDown, ChevronRight, Star, Activity, BarChart3, RefreshCw, Search, SlidersHorizontal, X, Layers, Zap, TrendingUp, AlertTriangle, Trophy, Landmark, Minimize2, Clock, ExternalLink } from "lucide-react";

const MOCK_DATA = {
  last_updated: "2026-03-13",
  spy_benchmarks: { perf_1w: 1.2, perf_1m: 3.5, perf_3m: 8.2 },
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

const LB_KEYS = [
  { key: "perf_1d", label: "1D", hint: "Flash" },
  { key: "perf_1w", label: "1W", hint: "" },
  { key: "perf_1m", label: "1M", hint: "" },
  { key: "perf_3m", label: "3M", hint: "" },
  { key: "perf_6m", label: "6M", hint: "Structural" },
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

function getRSTrend(s) {
  const m1 = s.perf_1m, m3 = s.perf_3m;
  if (m1 == null || m3 == null) return null;
  // Compare this month vs average monthly rate over last 3 months
  return m1 > m3 / 3 ? "up" : "down";
}

const RSBadge = ({ value, trend }) => {
  let cl;
  if (value >= 80) cl = "bg-emerald-500/15 text-emerald-400 border-emerald-500/20";
  else if (value >= 60) cl = "bg-blue-500/15 text-blue-400 border-blue-500/20";
  else if (value >= 40) cl = "bg-amber-500/15 text-amber-400 border-amber-500/20";
  else cl = "bg-red-500/15 text-red-400 border-red-500/20";
  return (
    <span className="inline-flex items-center gap-0.5">
      <span className={`inline-flex items-center px-1.5 py-0.5 text-[11px] font-semibold rounded border ${cl}`}>{value}</span>
      {trend === "up"   && <span className="text-[10px] font-bold text-cyan-400" title="RS Improving">▲</span>}
      {trend === "down" && <span className="text-[10px] font-bold text-rose-400" title="RS Declining">▼</span>}
    </span>
  );
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

// ── Elite Badge System ──
const BADGE_CONFIG = {
  triple_crown:     { Icon: Trophy,   color: "text-amber-400",   bg: "bg-amber-500/10 border-amber-500/25",   tip: "Triple Crown: #1 Theme + ADR >5% + Pure Play" },
  volatility_king:  { Icon: Zap,      color: "text-blue-400",    bg: "bg-blue-500/10 border-blue-500/25",     tip: "Volatility King: ADR in top 10% of dataset" },
  liquidity_monster:{ Icon: Landmark, color: "text-emerald-400", bg: "bg-emerald-500/10 border-emerald-500/25", tip: "Liquidity Monster: Daily Dollar Vol >$500M" },
  vcp_tightening:   { Icon: Minimize2,color: "text-violet-400",  bg: "bg-violet-500/10 border-violet-500/25", tip: "VCP Tightening: <2% range last 3 days + Volume dry-up" },
};

const EliteBadge = ({ type }) => {
  const { Icon, color, bg, tip } = BADGE_CONFIG[type];
  return (
    <span title={tip} className={`inline-flex items-center justify-center w-4 h-4 rounded border backdrop-blur-sm cursor-help ${bg}`}>
      <Icon size={9} className={color}/>
    </span>
  );
};

const GRADE_STYLE = {
  "A+": "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
  "A":  "bg-blue-500/20 text-blue-300 border-blue-500/30",
  "B":  "bg-zinc-700/40 text-zinc-400 border-zinc-600/30",
};

const GradeBadge = ({ grade }) => {
  if (!grade) return null;
  return <span className={`inline-flex items-center px-1 py-0.5 text-[10px] font-bold rounded border backdrop-blur-sm ${GRADE_STYLE[grade]}`}>{grade}</span>;
};

function isVDU(s) {
  const bars = s.bars_30d;
  if (bars && bars.length >= 10) {
    const vol10avg = bars.slice(-10, -1).reduce((sum, b) => sum + b.v, 0) / 9;
    const todayVol = bars[bars.length - 1]?.v || 0;
    return vol10avg > 0 && todayVol < vol10avg * 0.5;
  }
  return (s.rvol || 1) < 0.5;
}

function isTight(s) {
  const bars = s.bars_30d;
  if (bars && bars.length >= 3) {
    const last3 = bars.slice(-3);
    const range = (Math.max(...last3.map(b => b.h)) - Math.min(...last3.map(b => b.l))) / Math.min(...last3.map(b => b.l));
    return range < 0.015;
  }
  const sp = s.sparkline;
  if (!sp || sp.length < 3) return false;
  const last3 = sp.slice(-3);
  return (Math.max(...last3) - Math.min(...last3)) / Math.min(...last3) < 0.015;
}

function isInsideDay(s) {
  const bars = s.bars_30d;
  if (!bars || bars.length < 2) return false;
  const today = bars[bars.length - 1];
  const prev  = bars[bars.length - 2];
  return today.h <= prev.h && today.l >= prev.l;
}

function isVCPStage1(s) {
  if (s.dist_52w_high == null || s.dist_52w_high < -5) return false;
  const bars = s.bars_30d;
  if (!bars || bars.length < 10) return false;
  const avgRange = arr => arr.reduce((sum, b) => sum + (b.h - b.l) / b.l, 0) / arr.length;
  return avgRange(bars.slice(-5)) < avgRange(bars.slice(-10, -5)) * 0.8 && isVDU(s);
}

function isVCPTightening(s) {
  return isTight(s) && isVDU(s);
}

function getEliteGrade(s) {
  const sp = s.sparkline;
  const aboveSMA10 = sp && sp.length >= 5
    ? s.price > sp.reduce((a, b) => a + b, 0) / sp.length
    : null;
  const above20  = s.sma20_pct  != null ? s.sma20_pct  > 0 : null;
  const above50  = s.sma50_pct  != null ? s.sma50_pct  > 0 : null;
  const above200 = s.sma200_pct != null ? s.sma200_pct > 0 : null;
  if (s.rs_52w >= 90 && above200 && above50 && above20 && aboveSMA10) return "A+";
  if (s.rs_52w >= 80 && above200 && above50) return "A";
  if (above200) return "B";
  return null;
}

function getEliteBadges(s, { isTopTheme, isTopADR }) {
  const badges = [];
  if (isTopTheme && s.adr_pct > 5 && s.pure_play) badges.push("triple_crown");
  if (isTopADR) badges.push("volatility_king");
  if ((s.avg_dollar_volume || s.dollar_volume || 0) >= 500e6) badges.push("liquidity_monster");
  if (isVCPTightening(s)) badges.push("vcp_tightening");
  return badges;
}

// ── Helpers ──
function pearson(a, b) {
  const n = Math.min(a.length, b.length);
  if (n < 3) return 0;
  const as = a.slice(-n), bs = b.slice(-n);
  const ma = as.reduce((s, v) => s + v, 0) / n;
  const mb = bs.reduce((s, v) => s + v, 0) / n;
  let num = 0, da2 = 0, db2 = 0;
  for (let i = 0; i < n; i++) {
    const da = as[i] - ma, db = bs[i] - mb;
    num += da * db; da2 += da * da; db2 += db * db;
  }
  return da2 && db2 ? num / Math.sqrt(da2 * db2) : 0;
}

function getThemeDailyReturns(theme) {
  const norm = normalizeTheme ? normalizeTheme(theme) : theme;
  const sparklines = (norm.subthemes || []).flatMap(s => s.stocks).map(s => s.sparkline || []).filter(sp => sp.length >= 3);
  if (!sparklines.length) return [];
  const len = Math.min(...sparklines.map(sp => sp.length));
  const returns = [];
  for (let i = 1; i < len; i++) {
    const day = sparklines.map(sp => (sp[i] - sp[i-1]) / sp[i-1]);
    returns.push(day.reduce((s, v) => s + v, 0) / day.length);
  }
  return returns;
}


// ── RS vs SPY Badge ──
const RS_SPY_KEYS = [
  { key: "perf_1w", label: "1W" },
  { key: "perf_1m", label: "1M" },
  { key: "perf_3m", label: "3M" },
  { key: "perf_6m", label: "6M" },
];

const RSvsSPYBadge = ({ stockPerf, spyPerf }) => {
  if (stockPerf == null || spyPerf == null)
    return <span className="text-[10px] text-zinc-700">—</span>;
  const diff = stockPerf - spyPerf;
  if (diff > 5) return <span className="px-1 py-0.5 text-[9px] font-bold rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">Leader</span>;
  if (diff < -5) return <span className="px-1 py-0.5 text-[9px] font-bold rounded bg-orange-500/15 text-orange-400 border border-orange-500/20">Lagging</span>;
  return <span className="px-1 py-0.5 text-[9px] font-bold rounded bg-zinc-700/40 text-zinc-500 border border-zinc-600/30">In-Line</span>;
};

// ── Market Condition ──
const MarketCondition = ({ mc }) => {
  if (!mc) return null;
  const { signal, spy, qqq, iwm } = mc;
  const cfg = {
    green:  { dot: "bg-emerald-400", ring: "border-emerald-500/40 bg-emerald-500/8",  label: "🟢 Market Uptrend",    sub: "SPY & QQQ 站上 SMA50 & SMA200，200SMA 向上，正常執行突破單" },
    yellow: { dot: "bg-amber-400",   ring: "border-amber-500/40 bg-amber-500/8",   label: "🟡 Market Correction", sub: "SPY 或 QQQ 跌破 SMA50，暫停常規突破，只做 RS 最強的少數股票" },
    red:    { dot: "bg-red-400",     ring: "border-red-500/40 bg-red-500/8",       label: "🔴 Market Downtrend",  sub: "停止所有新倉突破單" },
  }[signal] || {};

  const fmtChg = (v) => {
    if (v == null) return "—";
    const s = `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
    return v > 0 ? <span className="text-emerald-400">{s}</span> : v < 0 ? <span className="text-red-400">{s}</span> : <span>{s}</span>;
  };
  const statusColor = (st) =>
    st === "Strong"   ? "text-emerald-400"
    : st === "Weak"   ? "text-red-500"
    : st === "Lagging"  ? "text-red-400"
    : st === "Mediocre" ? "text-yellow-400"
    : "text-zinc-400";
  const IndexTag = ({ label, d }) => (
    <span className="flex items-center gap-1">
      <span className="text-zinc-400">{label}</span>
      {d?.price != null && <span className="text-zinc-300">${d.price.toFixed(2)}</span>}
      {fmtChg(d?.change_pct)}
      {d?.index_status && <span className={`${statusColor(d.index_status)}`}>{d.index_status}</span>}
    </span>
  );
  return (
    <div className={`mb-4 px-4 py-2.5 rounded-lg border flex flex-wrap items-center gap-3 ${cfg.ring}`}>
      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${cfg.dot}`}/>
      <span className="text-xs font-semibold text-zinc-200">{cfg.label}</span>
      <span className="text-[11px] text-zinc-500 hidden sm:block">{cfg.sub}</span>
      <span className="ml-auto flex items-center gap-3 text-[10px] font-mono whitespace-nowrap">
        <IndexTag label="QQQ" d={qqq} />
        <span className="text-zinc-600">·</span>
        <IndexTag label="SPY" d={spy} />
        <span className="text-zinc-600">·</span>
        <IndexTag label="IWM" d={iwm} />
      </span>
    </div>
  );
};

// ── Earnings helpers ──
function earningsDaysAway(s) {
  if (!s || s === "-") return null;
  const m = s.match(/([A-Za-z]+ \d+)/);
  if (!m) return null;
  const year = new Date().getFullYear();
  const d = new Date(`${m[1]} ${year}`);
  if (isNaN(d.getTime())) return null;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = Math.round((d - today) / 86400000);
  return diff < 0 ? null : diff;
}

const EarningsCell = ({ value }) => {
  const days = earningsDaysAway(value);
  const label = value ? value.replace(/\s+(AMC|BMO|--)/i, "") : null;
  if (!label || days == null) return <td className="text-right py-2 px-2 text-xs text-zinc-700">—</td>;
  if (days <= 7)
    return <td className="text-right py-2 px-2"><span className="text-[10px] font-bold text-red-400 bg-red-500/15 border border-red-500/30 px-1 py-0.5 rounded">⚠ {label}</span></td>;
  if (days <= 14)
    return <td className="text-right py-2 px-2"><span className="text-[10px] font-medium text-amber-400">{label}</span></td>;
  return <td className="text-right py-2 px-2 text-[10px] text-zinc-600 font-mono">{label}</td>;
};

// ── Counter-Trend Warning ──
const CounterTrendWarning = ({ themes }) => {
  const warnings = useMemo(() => {
    const themeAvg = (t, key) => {
      const norm = normalizeThemeRaw(t);
      const vals = norm.subthemes.flatMap(s => s.stocks).map(s => s[key]).filter(v => v != null);
      return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    };
    const ranked1d = [...themes]
      .map(t => ({ name: normalizeThemeRaw(t).name, avg1d: themeAvg(t, "perf_1d"), avg6m: themeAvg(t, "perf_6m") }))
      .filter(t => t.avg1d != null && t.avg6m != null)
      .sort((a, b) => b.avg1d - a.avg1d);
    return ranked1d.filter((t, i) => i === 0 && t.avg6m <= -15);
  }, [themes]);

  if (!warnings.length) return null;
  return (
    <div className="mb-4 px-4 py-3 bg-rose-500/10 border border-rose-500/30 rounded-lg flex items-start gap-2">
      <AlertTriangle size={13} className="text-rose-400 flex-shrink-0 mt-0.5"/>
      <div>
        <span className="text-xs font-semibold text-rose-400">⚠ Counter-Trend Alert</span>
        {warnings.map(t => (
          <p key={t.name} className="text-[11px] text-rose-400/70 mt-0.5">
            <span className="font-medium text-rose-400">{t.name}</span> is{" "}
            <span className="font-medium">#1 Flash Momentum today (+{t.avg1d?.toFixed(1)}%)</span> but in a{" "}
            <span className="font-medium">Structural Downtrend ({t.avg6m?.toFixed(1)}% over 6M)</span> — likely a dead-cat bounce, not a real breakout.
          </p>
        ))}
      </div>
    </div>
  );
};

// ── Correlation Guard ──
const CorrelationGuard = ({ themes }) => {
  const warning = useMemo(() => {
    const top5 = themes.slice(0, 5);
    const tr = top5.map(t => ({ name: (t.subthemes ? t : { ...t, subthemes: [{ name: t.name, stocks: t.stocks || [] }] }).name, returns: getThemeDailyReturns(t) })).filter(t => t.returns.length >= 3);
    for (let i = 0; i < tr.length; i++)
      for (let j = i + 1; j < tr.length; j++)
        for (let k = j + 1; k < tr.length; k++) {
          const len = Math.min(tr[i].returns.length, tr[j].returns.length, tr[k].returns.length);
          if (pearson(tr[i].returns.slice(-len), tr[j].returns.slice(-len)) > 0.80 &&
              pearson(tr[i].returns.slice(-len), tr[k].returns.slice(-len)) > 0.80 &&
              pearson(tr[j].returns.slice(-len), tr[k].returns.slice(-len)) > 0.80)
            return [tr[i].name, tr[j].name, tr[k].name];
        }
    return null;
  }, [themes]);

  if (!warning) return null;
  return (
    <div className="mb-4 px-4 py-3 bg-orange-500/10 border border-orange-500/30 rounded-lg flex items-start gap-2">
      <AlertTriangle size={13} className="text-orange-400 flex-shrink-0 mt-0.5"/>
      <div>
        <span className="text-xs font-semibold text-orange-400">Concentration Warning</span>
        <p className="text-[11px] text-orange-400/70 mt-0.5">High correlation detected. Risk concentrated in: <span className="font-medium text-orange-400">{warning.join(', ')}</span></p>
      </div>
    </div>
  );
};

function normalizeThemeRaw(t) {
  if (t.subthemes) return t;
  return { ...t, subthemes: [{ name: t.name, stocks: t.stocks || [] }] };
}

const Leaderboard = ({ themes, perfKey, onPerfKeyChange }) => {
  const ranked = useMemo(() => {
    return [...themes]
      .map(t => {
        const norm = normalizeThemeRaw(t);
        const allStocks = norm.subthemes.flatMap(s => s.stocks);
        const vals = allStocks.map(s => s[perfKey]).filter(v => v != null);
        const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
        return { name: norm.name, avg, subs: norm.subthemes.length, stocks: allStocks.length };
      })
      .sort((a, b) => b.avg - a.avg)
      .slice(0, 5);
  }, [themes, perfKey]);

  return (
    <div className="mb-5 p-4 bg-zinc-900/60 rounded-xl border border-zinc-800/60">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <BarChart3 size={13} className="text-blue-400"/>
          <span className="text-xs font-semibold text-zinc-300">Theme Leaderboard</span>
          <span className="text-[10px] text-zinc-600">Top 5</span>
        </div>
        <div className="flex items-center gap-1">
          {LB_KEYS.map(k => (
            <button key={k.key} onClick={() => onPerfKeyChange(k.key)}
              className={`px-2.5 py-1 text-[11px] font-medium rounded transition-colors ${perfKey === k.key ? 'bg-blue-500/25 text-blue-300 border border-blue-500/40' : 'bg-zinc-800/60 text-zinc-500 border border-zinc-700/30 hover:text-zinc-300'}`}>
              {k.label}{k.hint ? <span className="ml-1 text-[9px] opacity-60">{k.hint}</span> : null}
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-5 gap-2">
        {ranked.map((t, i) => (
          <div key={t.name} className={`p-3 rounded-lg border ${i === 0 ? 'bg-blue-500/10 border-blue-500/30' : 'bg-zinc-800/40 border-zinc-700/30'}`}>
            <div className="flex items-center gap-1.5 mb-1">
              <span className={`text-[10px] font-bold font-mono ${i === 0 ? 'text-blue-400' : 'text-zinc-600'}`}>#{i+1}</span>
              <span className="text-[10px] font-semibold text-zinc-200 leading-tight line-clamp-2">{t.name}</span>
            </div>
            <div className={`text-sm font-bold font-mono ${t.avg >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {t.avg >= 0 ? '+' : ''}{t.avg.toFixed(1)}%
            </div>
            <div className="text-[10px] text-zinc-600 mt-1">{t.subs} sub · {t.stocks} stocks</div>
          </div>
        ))}
      </div>
    </div>
  );
};

const StockTable = ({ stocks, sortKey, sortDir, spyPerf, rsSPYKey, isTopTheme, topADRTickers }) => {
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
            <th className="text-right py-2 px-2 font-medium">Avg $V</th>
            <th className="text-right py-2 px-2 font-medium">ADR</th>
            <th className="text-center py-2 px-2 font-medium">RS</th>
            <th className="text-center py-2 px-2 font-medium">vs SPY</th>
            {PERF_KEYS.map(p => <th key={p.key} className="text-right py-2 px-2 font-medium">{p.label}</th>)}
            <th className="text-right py-2 px-2 font-medium">Earnings</th>
            <th className="text-center py-2 px-2 font-medium">6M</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((s, i) => (
            <tr key={s.ticker+i} className="border-t border-zinc-800/50 hover:bg-zinc-800/30 transition-colors">
              <td className="py-2 px-4">
                <div className="flex items-center gap-2">
                  {s.pure_play
                    ? <Star size={11} className="text-amber-400 fill-amber-400 flex-shrink-0"/>
                    : <TrendingUp size={11} className="text-zinc-600 flex-shrink-0"/>}
                  <div>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="font-semibold text-zinc-100 text-xs">{s.ticker}</span>
                      <GradeBadge grade={getEliteGrade(s)}/>
                      {isVCPStage1(s) && <span title="Narrowing consolidation + VDU + near 52W high" className="text-[9px] font-bold text-violet-400 bg-violet-500/15 border border-violet-500/30 px-1 py-0.5 rounded">🎯 VCP S1</span>}
                      {!isVCPStage1(s) && isVDU(s) && <span title="Volume below 50% of 10-day avg — selling pressure exhausted" className="text-[9px] font-bold text-blue-400 bg-blue-500/15 border border-blue-500/30 px-1 py-0.5 rounded">VDU</span>}
                      {isTight(s) && <span title="Last 3 days range < 1.5% — extremely tight" className="text-[9px] font-bold text-fuchsia-400 bg-fuchsia-500/15 border border-fuchsia-500/30 px-1 py-0.5 rounded">Tight</span>}
                      {isInsideDay(s) && <span title="Today's range inside yesterday's range" className="text-[9px] font-bold text-slate-400 bg-slate-500/15 border border-slate-500/30 px-1 py-0.5 rounded">ID</span>}
                      <span className="hidden sm:flex items-center gap-0.5">
                        {getEliteBadges(s, { isTopTheme, isTopADR: topADRTickers?.has(s.ticker) }).map(b => <EliteBadge key={b} type={b}/>)}
                      </span>
                    </div>
                    <p className="text-[10px] text-zinc-500 leading-tight truncate max-w-[120px]">{s.company}</p>
                  </div>
                </div>
              </td>
              <td className="text-right py-2 px-2 font-mono text-zinc-200 text-xs">${s.price.toFixed(2)}</td>
              <td className="text-right py-2 px-2 font-mono text-zinc-400 text-xs">{s["52w_high"] ? `$${s["52w_high"].toFixed(2)}` : "—"}</td>
              <Dist52wCell value={s.dist_52w_high}/>
              <td className="text-right py-2 px-2 text-zinc-500 text-xs font-mono">{fmtNum(s.volume)}</td>
              <RVolCell value={s.rvol}/>
              <td className="text-right py-2 px-2 text-zinc-500 text-xs font-mono">{fmtVol(s.avg_dollar_volume || s.dollar_volume)}</td>
              <td className="text-right py-2 px-2 text-zinc-400 text-xs font-mono">{s.adr_pct.toFixed(1)}%</td>
              <td className="text-center py-2 px-2"><RSBadge value={s.rs_52w} trend={getRSTrend(s)}/></td>
              <td className="text-center py-2 px-2"><RSvsSPYBadge stockPerf={s[rsSPYKey]} spyPerf={spyPerf}/></td>
              {PERF_KEYS.map(p => <PerfCell key={p.key} value={s[p.key]}/>)}
              <EarningsCell value={s.earnings}/>
              <td className="text-center py-2 px-2"><div className="flex justify-center"><Sparkline data={sparklineSeries(s)}/></div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

function normalizeTheme(t) {
  if (t.subthemes) return t;
  return { ...t, subthemes: [{ name: t.name, stocks: t.stocks || [] }] };
}

const SubThemeSection = ({ subtheme, sortKey, sortDir, parentAvg, lbPerfKey, spyPerf, rsSPYKey, isTopTheme, topADRTickers }) => {
  const [open, setOpen] = useState(true);
  const avg = (k) => subtheme.stocks.length
    ? subtheme.stocks.reduce((s, x) => s + (x[k] || 0), 0) / subtheme.stocks.length
    : 0;
  const subAvg = avg(lbPerfKey);
  const hasDivergence = parentAvg != null && subAvg > parentAvg + 3;

  return (
    <div className="ml-4 mb-2">
      <button onClick={() => setOpen(!open)} className="w-full flex items-center justify-between px-3 py-2 bg-zinc-800/40 hover:bg-zinc-800/60 rounded-md border border-zinc-700/30 transition-colors">
        <div className="flex items-center gap-2">
          {open ? <ChevronDown size={12} className="text-zinc-500"/> : <ChevronRight size={12} className="text-zinc-500"/>}
          <span className="text-xs font-medium text-zinc-300">{subtheme.name}</span>
          <span className="text-[10px] text-zinc-600 bg-zinc-700/30 px-1.5 py-0.5 rounded">{subtheme.stocks.length}</span>
          {hasDivergence && (
            <span className="flex items-center gap-0.5 px-1.5 py-0.5 bg-yellow-500/15 border border-yellow-500/30 rounded text-[10px] text-yellow-400 font-medium">
              <Zap size={9} className="fill-yellow-400"/> +{(subAvg - parentAvg).toFixed(1)}%
            </span>
          )}
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
          <StockTable stocks={subtheme.stocks} sortKey={sortKey} sortDir={sortDir} spyPerf={spyPerf} rsSPYKey={rsSPYKey} isTopTheme={isTopTheme} topADRTickers={topADRTickers}/>
        </div>
      )}
    </div>
  );
};

const ThemeSection = ({ theme, sortKey, sortDir, lbPerfKey, spyPerf, rsSPYKey, isTopTheme, topADRTickers }) => {
  const [open, setOpen] = useState(true);
  const norm = normalizeTheme(theme);
  const allStocks = norm.subthemes.flatMap(s => s.stocks);

  const avg = (k) => allStocks.length
    ? allStocks.reduce((s, x) => s + (x[k] || 0), 0) / allStocks.length
    : 0;
  const parentAvg = avg(lbPerfKey);

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
            <SubThemeSection key={sub.name + i} subtheme={sub} sortKey={sortKey} sortDir={sortDir} parentAvg={parentAvg} lbPerfKey={lbPerfKey} spyPerf={spyPerf} rsSPYKey={rsSPYKey} isTopTheme={isTopTheme} topADRTickers={topADRTickers}/>
          ))}
        </div>
      )}
    </div>
  );
};

// ── Gapper Scanner UI ──
const fmtCap = n => n >= 1e12 ? `$${(n/1e12).toFixed(1)}T` : n >= 1e9 ? `$${(n/1e9).toFixed(1)}B` : `$${(n/1e6).toFixed(0)}M`;

const CATEGORY_STYLE = {
  "Earnings":                 "bg-emerald-500/15 text-emerald-400 border-emerald-500/25",
  "New Contract/Partnership": "bg-blue-500/15 text-blue-400 border-blue-500/25",
  "Thematic Narratives":      "bg-violet-500/15 text-violet-400 border-violet-500/25",
  "Government Policy":        "bg-amber-500/15 text-amber-400 border-amber-500/25",
  "Institutional Buying":     "bg-cyan-500/15 text-cyan-400 border-cyan-500/25",
  "Insider Buying":           "bg-fuchsia-500/15 text-fuchsia-400 border-fuchsia-500/25",
  "Upgrade":                  "bg-zinc-700/40 text-zinc-400 border-zinc-600/30",
  "FDA":                      "bg-rose-500/15 text-rose-400 border-rose-500/25",
  "Others":                   "bg-zinc-700/40 text-zinc-500 border-zinc-600/30",
};

const ConvictionBar = ({ value }) => {
  const color = value >= 70 ? "bg-emerald-500" : value >= 50 ? "bg-amber-500" : "bg-rose-500";
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${value}%` }}/>
      </div>
      <span className="text-[10px] font-mono text-zinc-400">{value}%</span>
    </div>
  );
};

const GapperScanner = () => {
  const [gapperData, setGapperData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const r = await fetch("/gapper_data.json");
        if (r.ok) setGapperData(await r.json());
      } catch {}
      setLoading(false);
    })();
  }, []);

  if (loading) return <div className="flex items-center justify-center py-20"><RefreshCw size={20} className="text-zinc-500 animate-spin"/></div>;

  if (!gapperData || !gapperData.gappers?.length) return (
    <div className="text-center py-20 text-zinc-500">
      <Clock size={28} className="mx-auto mb-3 opacity-40"/>
      <p className="text-sm font-medium">No pre-market data available</p>
      <p className="text-xs mt-1 text-zinc-600">Scanner runs weekdays 08:05 AM ET</p>
    </div>
  );

  return (
    <div className="max-w-[1400px] mx-auto px-4 py-4">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-bold text-zinc-100 tracking-wide uppercase">Institutional Gappers</h2>
          <p className="text-[11px] text-zinc-600 mt-0.5">Scanned: {gapperData.scan_time}</p>
        </div>
        <span className="text-[10px] text-zinc-600 bg-zinc-800/60 border border-zinc-700/40 px-2 py-1 rounded">{gapperData.gappers.length} gappers found</span>
      </div>

      <div className="overflow-x-auto rounded-lg border border-zinc-700/40">
        <table className="w-full text-sm min-w-[1000px]">
          <thead>
            <tr className="text-[11px] text-zinc-500 uppercase tracking-wider bg-zinc-900/80">
              <th className="text-left py-2 px-4 font-medium">Ticker</th>
              <th className="text-right py-2 px-2 font-medium">Price / Gap</th>
              <th className="text-right py-2 px-2 font-medium">PM Vol</th>
              <th className="text-right py-2 px-2 font-medium">RVOL</th>
              <th className="text-right py-2 px-2 font-medium">Mkt Cap</th>
              <th className="text-center py-2 px-2 font-medium">Category</th>
              <th className="text-left py-2 px-4 font-medium">Gemini Reasoning</th>
              <th className="text-left py-2 px-4 font-medium">Trade Hypothesis</th>
              <th className="text-center py-2 px-2 font-medium">Conviction</th>
            </tr>
          </thead>
          <tbody>
            {gapperData.gappers.map((g, i) => (
              <tr key={g.ticker + i} className="border-t border-zinc-800/50 hover:bg-zinc-800/30 transition-colors">
                <td className="py-2.5 px-4">
                  <a href={`https://www.tradingview.com/chart/?symbol=${g.ticker}`} target="_blank" rel="noreferrer"
                    className="flex items-center gap-1.5 font-bold text-zinc-100 text-xs hover:text-blue-400 transition-colors">
                    {g.ticker}
                    <ExternalLink size={9} className="text-zinc-600"/>
                  </a>
                </td>
                <td className="text-right py-2.5 px-2">
                  <div className="text-xs font-mono text-zinc-200">${g.price.toFixed(2)}</div>
                  <div className="text-[10px] font-bold text-emerald-400">+{g.gap_pct.toFixed(1)}%</div>
                </td>
                <td className="text-right py-2.5 px-2 text-xs font-mono text-zinc-400">{fmtNum(g.pm_volume)}</td>
                <td className="text-right py-2.5 px-2">
                  <span className={`text-xs font-bold font-mono ${g.rvol >= 5 ? 'text-emerald-300' : g.rvol >= 3 ? 'text-emerald-400' : g.rvol >= 2 ? 'text-amber-400' : 'text-zinc-500'}`}>
                    {g.rvol.toFixed(1)}x
                  </span>
                </td>
                <td className="text-right py-2.5 px-2 text-xs font-mono text-zinc-400">{fmtCap(g.mkt_cap)}</td>
                <td className="text-center py-2.5 px-2">
                  <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border ${CATEGORY_STYLE[g.category] || CATEGORY_STYLE["Others"]}`}>
                    {g.category}
                  </span>
                </td>
                <td className="py-2.5 px-4 text-[11px] text-zinc-400 max-w-[200px]">{g.reasoning}</td>
                <td className="py-2.5 px-4 text-[11px] text-zinc-300 max-w-[180px]">{g.hypothesis}</td>
                <td className="text-center py-2.5 px-2"><ConvictionBar value={g.conviction}/></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const Legend = () => {
  const [open, setOpen] = useState(false);
  return (
    <div className="mb-3">
      <button onClick={() => setOpen(o => !o)} className="px-2.5 py-1 text-[11px] text-zinc-500 bg-zinc-800/60 border border-zinc-700/50 rounded-md hover:text-zinc-300 hover:border-zinc-600 transition-colors">
        Symbol Legend
      </button>
      {open && (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[10px] text-zinc-600 px-1">
          <span className="flex items-center gap-1"><Star size={9} className="text-amber-400 fill-amber-400"/> Pure Play</span>
          <span className="flex items-center gap-1"><TrendingUp size={9} className="text-zinc-600"/> Legacy Leader</span>
          <span className="flex items-center gap-1"><Zap size={9} className="text-yellow-400 fill-yellow-400"/> Sub-theme outperforming parent &gt;3%</span>
          <span className="flex items-center gap-1"><Layers size={9} className="text-blue-400"/> Theme → Sub-theme hierarchy</span>
          <span className="text-zinc-700">|</span>
          <span className="flex items-center gap-1"><Trophy size={9} className="text-amber-400"/> Triple Crown: #1主題 + ADR&gt;5% + Pure Play</span>
          <span className="flex items-center gap-1"><Zap size={9} className="text-blue-400"/> Volatility King: ADR 前10%</span>
          <span className="flex items-center gap-1"><Landmark size={9} className="text-emerald-400"/> Liquidity Monster: 日均成交額 &gt;$500M</span>
          <span className="flex items-center gap-1"><Minimize2 size={9} className="text-violet-400"/> VCP Tightening: 近3日波動&lt;2% + 量縮</span>
          <span className="text-zinc-700">|</span>
          <span className="flex items-center gap-1"><span className="text-emerald-300 font-bold">A+</span> 站上全部均線 + RS≥90</span>
          <span className="flex items-center gap-1"><span className="text-blue-300 font-bold">A</span> 站上50/200MA + RS≥80</span>
          <span className="flex items-center gap-1"><span className="text-zinc-400 font-bold">B</span> 站上200MA</span>
          <span className="text-zinc-700">|</span>
          <span className="flex items-center gap-1"><span className="text-violet-400 font-bold">🎯 VCP S1</span> 靠近52W高 + 波動收窄 + 量縮</span>
          <span className="flex items-center gap-1"><span className="text-blue-400 font-bold">VDU</span> 今日成交量 &lt; 10日均量50%</span>
          <span className="flex items-center gap-1"><span className="text-fuchsia-400 font-bold">Tight</span> 近3日波動 &lt; 1.5%</span>
          <span className="flex items-center gap-1"><span className="text-slate-400 font-bold">ID</span> Inside Day — 今日高低在昨日範圍內</span>
          <span className="text-zinc-700">|</span>
          <span className="flex items-center gap-1"><AlertTriangle size={9} className="text-rose-400"/> Counter-Trend: 今日1D第一但6M跌超-15%，可能是死貓反彈</span>
        </div>
      )}
    </div>
  );
};

export default function App() {
  const [tab, setTab] = useState("scanner");
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
  const [lbPerfKey, setLbPerfKey] = useState("perf_1m");
  const [rsSPYKey, setRsSPYKey] = useState("perf_1m");

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const r = await fetch("/thematic_data.json");
        if (r.ok) setData(await r.json());
      } catch {}
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
    }).filter(t => t.subthemes.length > 0)
    .sort((a, b) => {
      const avgRS = t => {
        const stocks = t.subthemes.flatMap(s => s.stocks);
        const vals = stocks.map(s => s.rs_52w).filter(v => v != null);
        return vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : 0;
      };
      return avgRS(b) - avgRS(a);
    });
  }, [data, search, filtersOn, filterDolVol, filterADR, filterRS, filterDist52w]);

  const unique = [...new Set(filtered.flatMap(t => t.subthemes.flatMap(s => s.stocks.map(st => st.ticker))))];
  const totalSubs = filtered.reduce((n, t) => n + t.subthemes.length, 0);

  const topADRTickers = useMemo(() => {
    if (!data) return new Set();
    const all = data.themes.flatMap(t => normalizeTheme(t).subthemes.flatMap(s => s.stocks));
    const sorted = [...all].sort((a, b) => (b.adr_pct || 0) - (a.adr_pct || 0));
    return new Set(sorted.slice(0, Math.ceil(sorted.length * 0.1)).map(s => s.ticker));
  }, [data]);

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
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1 bg-zinc-800/60 border border-zinc-700/50 rounded-lg p-1">
                <button onClick={() => setTab("scanner")} className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${tab === "scanner" ? "bg-blue-500/25 text-blue-300 border border-blue-500/40" : "text-zinc-500 hover:text-zinc-300"}`}>
                  Thematic Scanner
                </button>
                <button onClick={() => setTab("gapper")} className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${tab === "gapper" ? "bg-emerald-500/25 text-emerald-300 border border-emerald-500/40" : "text-zinc-500 hover:text-zinc-300"}`}>
                  Pre-Market Gappers
                </button>
              </div>
              <span className="text-[11px] text-zinc-600">Updated {data?.last_updated}</span>
            </div>
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
            <div className="flex items-center gap-1 border border-zinc-700/50 rounded-md overflow-hidden">
              <span className="px-2 text-[10px] text-zinc-600 bg-zinc-800/60">vs SPY</span>
              {RS_SPY_KEYS.map(k => (
                <button key={k.key} onClick={() => setRsSPYKey(k.key)} className={`px-2 py-1.5 text-[11px] transition-colors ${rsSPYKey === k.key ? 'bg-blue-500/25 text-blue-300' : 'bg-zinc-800/60 text-zinc-500 hover:text-zinc-300'}`}>{k.label}</button>
              ))}
            </div>
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

      {tab === "gapper" ? <GapperScanner/> : (
        <div className="max-w-[1400px] mx-auto px-4 py-4">
          {data && <MarketCondition mc={data.market_condition}/>}
          {data && <Leaderboard themes={data.themes} perfKey={lbPerfKey} onPerfKeyChange={setLbPerfKey}/>}
          {data && <CorrelationGuard themes={data.themes}/>}
          {data && <CounterTrendWarning themes={data.themes}/>}
          <Legend/>
          {filtered.length === 0 ? (
            <div className="text-center py-16 text-zinc-500">
              <BarChart3 size={28} className="mx-auto mb-3 opacity-40"/>
              <p className="text-sm">No results</p>
              <button onClick={()=>{setFiltersOn(false);setSearch("");}} className="mt-2 text-xs text-blue-400 hover:underline">Reset</button>
            </div>
          ) : filtered.map((t,i) => <ThemeSection key={t.name+i} theme={t} sortKey={sortKey} sortDir={sortDir} lbPerfKey={lbPerfKey} spyPerf={data?.spy_benchmarks?.[rsSPYKey]} rsSPYKey={rsSPYKey} isTopTheme={i===0} topADRTickers={topADRTickers}/>)}
        </div>
      )}
    </div>
  );
}
