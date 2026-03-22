import React, { useState, useEffect, useMemo, useCallback } from "react";
import { ChevronDown, ChevronRight, Star, Activity, BarChart3, RefreshCw, Search, SlidersHorizontal, X, Layers, Zap, TrendingUp, AlertTriangle, Trophy, Landmark, Minimize2, Clock, ExternalLink, FlaskConical } from "lucide-react";
import { useReactTable, getCoreRowModel, flexRender } from "@tanstack/react-table";

// eslint-disable-next-line no-unused-vars
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

/* Instant tooltip — no browser delay */
const TIP_COLORS = {
  amber:   'border-amber-500/60 text-amber-200',
  blue:    'border-blue-500/60 text-blue-200',
  emerald: 'border-emerald-500/60 text-emerald-200',
  violet:  'border-violet-500/60 text-violet-200',
  fuchsia: 'border-fuchsia-500/60 text-fuchsia-200',
  slate:   'border-slate-500/60 text-slate-200',
  zinc:    'border-zinc-700 text-zinc-200',
};
const Tip = ({ text, color = 'zinc', children }) => (
  <span className="group relative inline-flex">
    {children}
    <span className={`pointer-events-none absolute bottom-full left-0 mb-1.5 px-2 py-1 text-[10px] leading-snug bg-zinc-900 border rounded-md whitespace-nowrap z-[9999] shadow-lg opacity-0 group-hover:opacity-100 transition-none ${TIP_COLORS[color] ?? TIP_COLORS.zinc}`}>
      {text}
    </span>
  </span>
);

// ── Elite Badge System ──
const BADGE_CONFIG = {
  triple_crown:     { Icon: Trophy,   color: "text-amber-400",   bg: "bg-amber-500/10 border-amber-500/25",   tip: "Triple Crown: #1 Theme + ADR >5% + Pure Play",          tipColor: "amber"   },
  volatility_king:  { Icon: Zap,      color: "text-blue-400",    bg: "bg-blue-500/10 border-blue-500/25",     tip: "Volatility King: ADR in top 10% of dataset",            tipColor: "blue"    },
  liquidity_monster:{ Icon: Landmark, color: "text-emerald-400", bg: "bg-emerald-500/10 border-emerald-500/25", tip: "Liquidity Monster: Daily Dollar Vol >$500M",          tipColor: "emerald" },
  vcp_tightening:   { Icon: Minimize2,color: "text-violet-400",  bg: "bg-violet-500/10 border-violet-500/25", tip: "VCP Tightening: <2% range last 3 days + Volume dry-up", tipColor: "violet"  },
};

const EliteBadge = ({ type }) => {
  const { Icon, color, bg, tip, tipColor } = BADGE_CONFIG[type];
  return (
    <Tip text={tip} color={tipColor}>
      <span className={`inline-flex items-center justify-center w-4 h-4 rounded border backdrop-blur-sm cursor-help ${bg}`}>
        <Icon size={9} className={color}/>
      </span>
    </Tip>
  );
};

const GRADE_STYLE = {
  "A+": "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
  "A":  "bg-blue-500/20 text-blue-300 border-blue-500/30",
  "B":  "bg-zinc-700/40 text-zinc-400 border-zinc-600/30",
};

const GRADE_TIP = {
  "A+": "A+ — Above all SMAs (20/50/200) + RS ≥ 90",
  "A":  "A  — Above SMA50 & SMA200 + RS ≥ 80",
  "B":  "B  — Above SMA200",
};
const GRADE_TIP_COLOR = { "A+": "emerald", "A": "blue", "B": "zinc" };
const GradeBadge = ({ grade }) => {
  if (!grade) return null;
  return <Tip text={GRADE_TIP[grade]} color={GRADE_TIP_COLOR[grade]}><span className={`inline-flex items-center px-1 py-0.5 text-[10px] font-bold rounded border backdrop-blur-sm cursor-help ${GRADE_STYLE[grade]}`}>{grade}</span></Tip>;
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
// eslint-disable-next-line no-unused-vars
const MarketCondition = ({ mc }) => {
  if (!mc) return null;
  const { signal } = mc;
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
  // eslint-disable-next-line no-unused-vars
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

const PerfCellLB = ({ val }) => {
  if (val == null) return <td className="px-2 py-1.5 text-center text-[10px] text-zinc-600">—</td>;
  const color = val > 0 ? 'text-emerald-400' : val < 0 ? 'text-red-400' : 'text-zinc-400';
  const bg = val > 5 ? 'bg-emerald-500/10' : val < -5 ? 'bg-red-500/10' : '';
  return <td className={`px-2 py-1.5 text-right text-[11px] font-mono font-medium ${color} ${bg}`}>{val > 0 ? '+' : ''}{val.toFixed(1)}%</td>;
};

const LB_PERF_COLS = new Set(['perf_1d','perf_1w','perf_1m','perf_3m','perf_6m']);

const Leaderboard = ({ themeRankings, industryRankings, finvizThemeRankings, themeSparklines = {} }) => {
  const [sortPriority, setSortPriority] = useState([{ key: 'rs_score', direction: 'desc' }]);
  const [multiMode, setMultiMode] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const [view, setView] = useState("themes"); // "themes" (Finviz map) or "industry"
  const [themeHover, setThemeHover] = useState(null); // { ticker, rect }

  const activeData = view === "themes" ? finvizThemeRankings : themeRankings;

  const handleLBSort = (key) => {
    if (multiMode) {
      const primary = sortPriority[0];
      if (primary && LB_PERF_COLS.has(primary.key) && LB_PERF_COLS.has(key)) return;
      setSortPriority(prev => {
        const existing = prev.findIndex((p, i) => i > 0 && p.key === key);
        if (existing > 0) return prev.map((p, i) => i === existing ? { ...p, direction: p.direction === 'desc' ? 'asc' : 'desc' } : p);
        return [prev[0], { key, direction: 'desc' }];
      });
    } else {
      setSortPriority(prev => {
        const cur = prev.find(p => p.key === key);
        if (cur && prev[0].key === key) return [{ key, direction: cur.direction === 'desc' ? 'asc' : 'desc' }, ...prev.slice(1)];
        return [{ key, direction: 'desc' }];
      });
    }
  };

  const primaryKey = sortPriority[0]?.key;
  const secondaryKey = sortPriority[1]?.key;

  const ranked = useMemo(() => {
    if (!activeData || !activeData.length) return [];
    return [...activeData].sort((a, b) => {
      for (let i = 0; i < sortPriority.length; i++) {
        const { key, direction } = sortPriority[i];
        let va = a[key] ?? 0;
        let vb = b[key] ?? 0;
        // Bucketing: round to 1 decimal for perf cols when primary sort
        if (i === 0 && LB_PERF_COLS.has(key)) {
          va = Math.round(va * 10) / 10;
          vb = Math.round(vb * 10) / 10;
        }
        if (va === vb) continue;
        const cmp = va > vb ? 1 : -1;
        return direction === 'desc' ? -cmp : cmp;
      }
      return 0;
    });
  }, [activeData, sortPriority]);


  const industryMap = useMemo(() => {
    if (!industryRankings) return {};
    const m = {};
    for (const ind of industryRankings) {
      const p = ind.parent_theme;
      if (!m[p]) m[p] = [];
      m[p].push(ind);
    }
    for (const k of Object.keys(m)) {
      m[k].sort((a, b) => {
        const sa = (a.perf_1w||0)*0.2 + (a.perf_1m||0)*0.3 + (a.perf_3m||0)*0.3 + (a.perf_6m||0)*0.2;
        const sb = (b.perf_1w||0)*0.2 + (b.perf_1m||0)*0.3 + (b.perf_3m||0)*0.3 + (b.perf_6m||0)*0.2;
        return sb - sa;
      });
    }
    return m;
  }, [industryRankings]);

  const LBSortHeader = ({ k, label, w }) => {
    const priIdx = sortPriority.findIndex(p => p.key === k);
    const isActive = priIdx >= 0;
    const dir = isActive ? sortPriority[priIdx].direction : null;
    const isPrimary = priIdx === 0;
    const isSecondary = priIdx === 1;
    const isBlocked = multiMode && LB_PERF_COLS.has(k) && LB_PERF_COLS.has(primaryKey) && !isPrimary;
    return (
      <th onClick={() => handleLBSort(k)}
        className={`px-2 py-2 text-right cursor-pointer select-none ${w || 'w-14'} ${isActive ? (isPrimary ? 'text-blue-400' : 'text-violet-400') : isBlocked ? 'text-zinc-700' : 'text-zinc-500 hover:text-zinc-300'}`}>
        <span className="inline-flex items-center justify-end gap-0.5 text-[10px] font-semibold uppercase tracking-wider">
          {label}
          {isPrimary   && <span className="text-[8px] text-blue-400/70">①{dir === 'desc' ? '▼' : '▲'}</span>}
          {isSecondary && <span className="text-[8px] text-violet-400/70">②{dir === 'desc' ? '▼' : '▲'}</span>}
        </span>
      </th>
    );
  };

  return (
    <><div className="p-4 bg-zinc-900/60 rounded-xl border border-zinc-800/60">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <BarChart3 size={13} className="text-blue-400"/>
          <span className="text-xs font-semibold text-zinc-300">Theme Leaderboard</span>
          <span className="text-[10px] text-zinc-600">{ranked.length} themes</span>
          <button
            onClick={() => setMultiMode(m => !m)}
            className={`text-[9px] px-2 py-0.5 rounded border transition-colors ${multiMode ? 'bg-violet-500/20 text-violet-300 border-violet-500/40' : 'text-zinc-500 border-zinc-700/50 hover:text-zinc-300'}`}>
            {multiMode ? '② 次排序模式' : '+ 次排序'}
          </button>
          {secondaryKey && (
            <button onClick={() => { setSortPriority([{ key: 'rs_score', direction: 'desc' }]); setMultiMode(false); }}
              className="text-[9px] text-zinc-600 hover:text-zinc-400 px-1.5 py-0.5 border border-zinc-700/50 rounded transition-colors">
              ✕ Reset
            </button>
          )}
        </div>
        <div className="flex bg-zinc-800/60 rounded-lg p-0.5 border border-zinc-700/40">
          {[{k:"themes",l:"Themes Map"},{k:"industry",l:"Industry"}].map(v => (
            <button key={v.k} onClick={() => { setView(v.k); setExpanded(null); }}
              className={`px-2.5 py-1 text-[10px] font-medium rounded-md transition-all ${view === v.k ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30' : 'text-zinc-500 hover:text-zinc-300 border border-transparent'}`}>
              {v.l}
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-y-auto overflow-x-auto" style={{ maxHeight: '420px' }}>
        <table className="w-full text-left">
          <thead style={{ position: 'sticky', top: 0, zIndex: 1, background: '#18181b' }}>
            <tr className="border-b border-zinc-800/60">
              <th className="px-2 py-2 w-6 text-[10px] text-zinc-600 select-none">#</th>
              <th className="px-2 py-2 text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">Theme</th>
              {LB_KEYS.map(k => <LBSortHeader key={k.key} k={k.key} label={k.label} />)}
              <LBSortHeader k="rs_score" label="RS" w="w-16" />
            </tr>
          </thead>
          <tbody>
            {ranked.map((t, i) => {
              const isIndustryView = view === "industry";
              const isExpanded = isIndustryView && expanded === t.name;
              const industries = isIndustryView ? (industryMap[t.name] || []) : [];
              return (<React.Fragment key={`lb-${t.name}`}>
                <tr
                  onClick={() => isIndustryView && setExpanded(isExpanded ? null : t.name)}
                  className={`border-b border-zinc-800/30 transition-colors ${isIndustryView ? 'cursor-pointer' : ''} ${i === 0 ? 'bg-blue-500/5' : 'hover:bg-zinc-800/40'}`}>
                  <td className={`px-2 py-2 text-[11px] font-bold font-mono ${i === 0 ? 'text-blue-400' : 'text-zinc-600'}`}>{i + 1}</td>
                  <td className="px-2 py-2">
                    <div className="flex items-center gap-1.5">
                      {isIndustryView && (isExpanded ? <ChevronDown size={11} className="text-zinc-500 flex-shrink-0"/> : <ChevronRight size={11} className="text-zinc-600 flex-shrink-0"/>)}
                      <span
                        className="text-[11px] font-semibold text-zinc-200 cursor-default"
                        onMouseEnter={e => { const etf = THEME_ETF_MAP[t.name]; if (etf) setThemeHover({ ticker: etf, rect: e.currentTarget.getBoundingClientRect() }); }}
                        onMouseLeave={() => setThemeHover(null)}
                      >{t.name}</span>
                      {t.stage2_momentum && <span className="px-1.5 py-0.5 text-[8px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 rounded-full leading-none">STAGE 2</span>}
                      {isIndustryView && t.n_industries && <span className="text-[9px] text-zinc-600">{t.n_industries} ind</span>}
                    </div>
                  </td>
                  {LB_KEYS.map(k => <PerfCellLB key={k.key} val={t[k.key]}/>)}
                  <td className={`px-2 py-1.5 text-right text-[11px] font-mono font-bold ${t.rs_score > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {t.rs_score > 0 ? '+' : ''}{t.rs_score.toFixed(1)}
                  </td>
                </tr>
                {isExpanded && industries.map(ind => (
                  <tr key={ind.name} className="bg-zinc-800/20 border-b border-zinc-800/20">
                    <td className="px-2 py-1.5"></td>
                    <td className="px-2 py-1.5 pl-8">
                      <span className="text-[10px] text-zinc-400">{ind.name}</span>
                    </td>
                    {LB_KEYS.map(k => <PerfCellLB key={k.key} val={ind[k.key]}/>)}
                    <td className="px-2 py-1.5"></td>
                  </tr>
                ))}
              </React.Fragment>);
            })}
          </tbody>
        </table>
      </div>
    </div>{themeHover && <TVPopup ticker={themeHover.ticker} anchorRect={themeHover.rect}/>}</>
  );
};

const TVPopup = ({ ticker, anchorRect }) => {
  if (!ticker || !anchorRect) return null;
  const MAX_W = 600, MAX_H = 200;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const edgeX = 8, edgeBot = 130;
  // Clear the sticky navbar
  const navEl = document.getElementById("app-navbar");
  const edgeTop = navEl ? (navEl.getBoundingClientRect().bottom + 8) : 110;
  // Pick side based on anchor center
  const anchorCenterX = anchorRect.left + anchorRect.width / 2;
  const useLeft = anchorCenterX > vw / 2;
  let W, H, left;
  if (useLeft) {
    // panelLeft captured at hover time (most reliable — avoids render-time layout issues)
    const panelLeft = anchorRect.panelLeft != null
      ? anchorRect.panelLeft
      : (document.getElementById("search-result-panel")?.getBoundingClientRect().left ?? anchorRect.left);
    const maxRight = panelLeft - 20;         // chart right edge stays 20px left of panel
    W = Math.max(220, Math.min(MAX_W, maxRight - edgeX));
    H = Math.round(W * MAX_H / MAX_W);
    left = Math.max(edgeX, maxRight - W);
  } else {
    W = MAX_W; H = MAX_H;
    left = Math.min(anchorRect.right + 4, vw - W - edgeX);
    left = Math.max(edgeX, left);
  }
  let top = anchorRect.top;
  top = Math.max(edgeTop, Math.min(top, vh - H - edgeBot));
  const src = `https://finviz.com/chart.ashx?t=${encodeURIComponent(ticker)}&ty=c&ta=1&p=d&s=l`;
  return (
    <div style={{ position:"fixed", left, top, width:W, height:H, zIndex:9999, borderRadius:8, overflow:"hidden", border:"1px solid #27272a", boxShadow:"0 24px 64px rgba(0,0,0,0.85)", pointerEvents:"none", background:"#fff" }}>
      <img src={src} alt={ticker} style={{ width:"100%", height:"100%", objectFit:"fill", display:"block" }}/>
    </div>
  );
};

// ── MagicSort constants ──
const SORT_PERF_COLS = new Set(['perf_1d','perf_1w','perf_1m','perf_3m','perf_6m']);
const SORT_TECH_COLS = new Set(['rs_52w','rvol','avg_dollar_volume','adr_pct','dist_52w_high','volume','price','52w_high','ticker']);

function getAlphaGrade(s, spyPerf1d = 0) {
  const rs = s.rs_52w || 0;
  if (rs > 97 && (s.perf_1m||0) > 0 && (s.perf_1d||0) > 0) return 'A+';
  if (rs > 95 && (s.perf_1m||0) > 0) return 'A';
  if (rs > 90 || (s.perf_1d||0) > spyPerf1d) return 'B';
  return null;
}

// Returns true if stock "fits" a given sort key (used for dynamic badge)
function fitsSortKey(stock, key) {
  if (SORT_PERF_COLS.has(key)) return (stock[key] || 0) > 0;
  if (key === 'rs_52w') return (stock.rs_52w || 0) > 95;
  return true;
}

const AlphaLeaderBadge = ({ stock, sortPriority = [], spyPerf1d = 0 }) => {
  const grade = getAlphaGrade(stock, spyPerf1d);
  if (!grade || grade === 'B') return null;
  if ((stock.perf_1d||0) <= 0) return null;
  // Dynamic: must fit the current top-2 sort priorities
  const fits = sortPriority.slice(0, 2).every(p => fitsSortKey(stock, p.key));
  if (!fits) return null;
  const style = grade === 'A+'
    ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
    : 'bg-blue-500/20 text-blue-300 border-blue-500/40';
  return <span className={`inline-flex items-center gap-0.5 px-1 py-0.5 text-[9px] font-bold rounded border ${style}`}>🔥 {grade}</span>;
};

const StockTable = ({ stocks, spyPerf, rsSPYKey, isTopTheme, topADRTickers, themeName, subthemeName }) => {
  const [hovered, setHovered] = useState(null);
  const [sortPriority, setSortPriority] = useState([{ key: 'rs_52w', direction: 'desc' }]);
  const [multiMode, setMultiMode] = useState(false);

  const handleSort = (key) => {
    if (multiMode) {
      const primary = sortPriority[0];
      if (primary && SORT_PERF_COLS.has(primary.key) && SORT_PERF_COLS.has(key)) return;
      setSortPriority(prev => {
        const existing = prev.findIndex((p, i) => i > 0 && p.key === key);
        if (existing > 0) {
          return prev.map((p, i) => i === existing ? { ...p, direction: p.direction === 'desc' ? 'asc' : 'desc' } : p);
        }
        return [prev[0], { key, direction: 'desc' }];
      });
    } else {
      setSortPriority(prev => {
        const cur = prev.find(p => p.key === key);
        if (cur && prev[0].key === key) {
          return [{ key, direction: cur.direction === 'desc' ? 'asc' : 'desc' }, ...prev.slice(1)];
        }
        return [{ key, direction: 'desc' }];
      });
    }
  };

  const sorted = useMemo(() => {
    return [...stocks].sort((a, b) => {
      for (let i = 0; i < sortPriority.length; i++) {
        const { key, direction } = sortPriority[i];
        let va = key === 'ticker' ? (a.ticker||'') : (a[key]||0);
        let vb = key === 'ticker' ? (b.ticker||'') : (b[key]||0);
        // Bucketing: round to 1 decimal for performance cols when primary sort
        if (i === 0 && SORT_PERF_COLS.has(key)) {
          va = Math.round(va * 10) / 10;
          vb = Math.round(vb * 10) / 10;
        }
        if (va === vb) continue;
        const cmp = key === 'ticker' ? va.localeCompare(vb) : (va > vb ? 1 : -1);
        return direction === 'desc' ? -cmp : cmp;
      }
      return 0;
    });
  }, [stocks, sortPriority]);

  const primaryKey = sortPriority[0]?.key;
  const secondaryKey = sortPriority[1]?.key;

  const SH = ({ k, label, align = "right" }) => {
    const priIdx = sortPriority.findIndex(p => p.key === k);
    const isActive = priIdx >= 0;
    const dir = isActive ? sortPriority[priIdx].direction : null;
    const isPrimary = priIdx === 0;
    const isSecondary = priIdx === 1;
    const isBlocked = multiMode && SORT_PERF_COLS.has(k) && SORT_PERF_COLS.has(primaryKey) && !isPrimary;
    return (
      <th onClick={() => handleSort(k)}
        className={`py-2 px-2 font-medium cursor-pointer select-none hover:text-zinc-300 transition-colors text-${align} ${isActive ? (isPrimary ? 'text-blue-400' : 'text-violet-400') : isBlocked ? 'text-zinc-700' : 'text-zinc-500'}`}>
        <span className="inline-flex items-center gap-0.5">
          {label}
          {isPrimary && <span className="text-[8px] text-blue-400/70 ml-0.5">①{dir === 'desc' ? '▼' : '▲'}</span>}
          {isSecondary && <span className="text-[8px] text-violet-400/70 ml-0.5">②{dir === 'desc' ? '▼' : '▲'}</span>}
        </span>
      </th>
    );
  };

  return (
    <>
    <div className="flex items-center gap-2 mb-1">
      <button
        onClick={() => setMultiMode(m => !m)}
        className={`text-[9px] px-2 py-0.5 rounded border transition-colors ${multiMode ? 'bg-violet-500/20 text-violet-300 border-violet-500/40' : 'text-zinc-500 border-zinc-700/50 hover:text-zinc-300'}`}>
        {multiMode ? '② 次排序模式' : '+ 次排序'}
      </button>
      {secondaryKey && (
        <>
          <span className="text-[10px] text-zinc-500">
            <span className="text-blue-400">①{primaryKey}</span>
            {' → '}<span className="text-violet-400">②{secondaryKey}</span>
          </span>
          <button onClick={() => { setSortPriority([{ key: 'rs_52w', direction: 'desc' }]); setMultiMode(false); }} className="text-[9px] text-zinc-600 hover:text-zinc-400 px-1.5 py-0.5 border border-zinc-700/50 rounded transition-colors">✕ Reset</button>
        </>
      )}
    </div>
    <div className="overflow-x-auto rounded-lg border border-zinc-700/40">
      <table className="w-full text-sm min-w-[900px]">
        <thead>
          <tr className="text-[11px] uppercase tracking-wider bg-zinc-900/80">
            <th className="text-left py-2 px-4 font-medium w-40 text-zinc-500">Ticker</th>
            <SH k="price" label="Price"/>
            {PERF_KEYS.map(p => <SH key={p.key} k={p.key} label={p.label}/>)}
            <th className="text-right py-2 px-2 font-medium text-zinc-500">Earnings</th>
            <th className="text-center py-2 px-2 font-medium text-zinc-500">6M</th>
            <SH k="52w_high" label="52W Hi"/>
            <SH k="dist_52w_high" label="Dist"/>
            <SH k="volume" label="Vol"/>
            <SH k="rvol" label="RVol"/>
            <SH k="avg_dollar_volume" label="Avg $V"/>
            <SH k="adr_pct" label="ADR"/>
            <SH k="rs_52w" label="RS" align="center"/>
            <th className="text-center py-2 px-2 font-medium text-zinc-500">vs SPY</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((s, i) => (
            <tr key={s.ticker+i} className="border-t border-zinc-800/50 hover:bg-zinc-800/30 transition-colors">
              <td className="py-2 px-4">
                <div className="flex items-center gap-2">
                  {s.pure_play
                    ? <Tip text="Pure Play — appears in only one sub-theme" color="amber"><Star size={11} className="text-amber-400 fill-amber-400 flex-shrink-0 cursor-help"/></Tip>
                    : <Tip text="Legacy Leader — appears across multiple sub-themes" color="zinc"><TrendingUp size={11} className="text-zinc-600 flex-shrink-0 cursor-help"/></Tip>}
                  <div>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span
                        className="font-semibold text-zinc-100 text-xs cursor-default hover:text-blue-400 transition-colors"
                        onMouseEnter={e => setHovered({ ticker: s.ticker, rect: e.currentTarget.getBoundingClientRect() })}
                        onMouseLeave={() => setHovered(null)}
                      >{s.ticker}</span>
                      <GradeBadge grade={getEliteGrade(s)}/>
                      <AlphaLeaderBadge stock={s} sortPriority={sortPriority} spyPerf1d={spyPerf || 0}/>
                      {isVCPStage1(s) && <Tip text="Narrowing consolidation + VDU + near 52W high" color="violet"><span className="text-[9px] font-bold text-violet-400 bg-violet-500/15 border border-violet-500/30 px-1 py-0.5 rounded cursor-help">🎯 VCP S1</span></Tip>}
                      {!isVCPStage1(s) && isVDU(s) && <Tip text="Volume below 50% of 10-day avg — selling pressure exhausted" color="blue"><span className="text-[9px] font-bold text-blue-400 bg-blue-500/15 border border-blue-500/30 px-1 py-0.5 rounded cursor-help">VDU</span></Tip>}
                      {isTight(s) && <Tip text="Last 3 days range < 1.5% — extremely tight" color="fuchsia"><span className="text-[9px] font-bold text-fuchsia-400 bg-fuchsia-500/15 border border-fuchsia-500/30 px-1 py-0.5 rounded cursor-help">Tight</span></Tip>}
                      {isInsideDay(s) && <Tip text="Today's range inside yesterday's range" color="slate"><span className="text-[9px] font-bold text-slate-400 bg-slate-500/15 border border-slate-500/30 px-1 py-0.5 rounded cursor-help">ID</span></Tip>}
                      <span className="hidden sm:flex items-center gap-0.5">
                        {getEliteBadges(s, { isTopTheme, isTopADR: topADRTickers?.has(s.ticker) }).map(b => <EliteBadge key={b} type={b}/>)}
                      </span>
                    </div>
                    <p className="text-[10px] text-zinc-500 leading-tight truncate max-w-[160px]">{s.company}</p>
                  </div>
                </div>
              </td>
              <td className="text-right py-2 px-2 font-mono text-zinc-200 text-xs">${s.price.toFixed(2)}</td>
              {PERF_KEYS.map(p => <PerfCell key={p.key} value={s[p.key]}/>)}
              <EarningsCell value={s.earnings}/>
              <td className="text-center py-2 px-2"><div className="flex justify-center"><Sparkline data={sparklineSeries(s)}/></div></td>
              <td className="text-right py-2 px-2 font-mono text-zinc-400 text-xs">{s["52w_high"] ? `$${s["52w_high"].toFixed(2)}` : "—"}</td>
              <Dist52wCell value={s.dist_52w_high}/>
              <td className="text-right py-2 px-2 text-zinc-500 text-xs font-mono">{fmtNum(s.volume)}</td>
              <RVolCell value={s.rvol}/>
              <td className="text-right py-2 px-2 text-zinc-500 text-xs font-mono">{fmtVol(s.avg_dollar_volume || s.dollar_volume)}</td>
              <td className="text-right py-2 px-2 text-zinc-400 text-xs font-mono">{s.adr_pct.toFixed(1)}%</td>
              <td className="text-center py-2 px-2"><RSBadge value={s.rs_52w} trend={getRSTrend(s)}/></td>
              <td className="text-center py-2 px-2"><RSvsSPYBadge stockPerf={s[rsSPYKey]} spyPerf={spyPerf}/></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    {hovered && <TVPopup ticker={hovered.ticker} anchorRect={hovered.rect}/>}
    </>
  );
};

function normalizeTheme(t) {
  if (t.subthemes) return t;
  return { ...t, subthemes: [{ name: t.name, stocks: t.stocks || [] }] };
}

const SubThemeSection = ({ subtheme, parentAvg, lbPerfKey, spyPerf, rsSPYKey, isTopTheme, topADRTickers, themeName }) => {
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
          <StockTable stocks={subtheme.stocks} spyPerf={spyPerf} rsSPYKey={rsSPYKey} isTopTheme={isTopTheme} topADRTickers={topADRTickers} themeName={themeName} subthemeName={subtheme.name}/>
        </div>
      )}
    </div>
  );
};

const ThemeSection = ({ theme, lbPerfKey, spyPerf, rsSPYKey, isTopTheme, topADRTickers, themeRankings, finvizThemeRankings }) => {
  const [open, setOpen] = useState(true);
  const norm = normalizeTheme(theme);
  const allStocks = norm.subthemes.flatMap(s => s.stocks);

  const avg = (k) => allStocks.length
    ? allStocks.reduce((s, x) => s + (x[k] || 0), 0) / allStocks.length
    : 0;

  // Look up ranking data for this theme (use finviz first, fallback to theme_rankings)
  const rankingEntry = useMemo(() => {
    const name = norm.name.toLowerCase();
    const search = (arr) => arr?.find(r => r.name?.toLowerCase() === name);
    return search(finvizThemeRankings) || search(themeRankings) || null;
  }, [norm.name, finvizThemeRankings, themeRankings]);

  const perfVal = (key) => rankingEntry ? (rankingEntry[key] ?? null) : avg(key);
  const rsVal = rankingEntry ? (rankingEntry.rs_score ?? avg("rs_52w")) : avg("rs_52w");
  const parentAvg = rankingEntry ? (rankingEntry[lbPerfKey] ?? avg(lbPerfKey)) : avg(lbPerfKey);

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
            const v = perfVal(p.key);
            if (v == null) return null;
            return (
              <span key={p.key} className="hidden lg:flex items-center gap-1">
                <span className="text-zinc-600">{p.label}</span>
                <span className={`font-mono font-medium ${v >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{v >= 0 ? '+' : ''}{v.toFixed(1)}%</span>
              </span>
            );
          })}
          <span className="text-zinc-500">RS <span className="text-zinc-300 font-medium">{rsVal.toFixed(0)}</span></span>
        </div>
      </button>
      {open && (
        <div className="mt-1.5 space-y-1.5">
          {norm.subthemes.map((sub, i) => (
            <SubThemeSection key={sub.name + i} subtheme={sub} parentAvg={parentAvg} lbPerfKey={lbPerfKey} spyPerf={spyPerf} rsSPYKey={rsSPYKey} isTopTheme={isTopTheme} topADRTickers={topADRTickers} themeName={norm.name}/>
          ))}
        </div>
      )}
    </div>
  );
};

// ── Gapper Filter Input ──
const FInput = ({ label, value, onChange, hint }) => {
  const [local, setLocal] = useState(String(value));
  // Sync when parent resets (e.g. Reset button)
  useEffect(() => { setLocal(String(value)); }, [value]);
  return (
    <div className="flex flex-col gap-1 min-w-0">
      <label className="text-[10px] text-zinc-500 whitespace-nowrap">{label}</label>
      <input
        type="number"
        value={local}
        onChange={e => {
          setLocal(e.target.value);
          const n = parseFloat(e.target.value);
          if (!isNaN(n)) onChange(n);
        }}
        className="w-full px-2 py-1.5 text-xs font-mono bg-zinc-900 border border-zinc-700/60 rounded text-zinc-200 focus:outline-none focus:border-blue-500/50"
      />
      <span className="text-[10px] text-zinc-600 h-3">{hint || ""}</span>
    </div>
  );
};

// ── Gapper Scanner UI ──
const fmtCap = n => n >= 1e12 ? `$${(n/1e12).toFixed(1)}T` : n >= 1e9 ? `$${(n/1e9).toFixed(1)}B` : `$${(n/1e6).toFixed(0)}M`;

const POS_WORDS = new Set(["beat","beats","beating","surpassed","exceeded","surpassing","exceeding","raised","raise","above","approved","approval","strong","won","outperformed","record"]);
const NEG_WORDS = new Set(["miss","missed","missing","below","decline","declined","declining","cut","weak","rejected","rejection","loss","fell","failed","disappointing","missed"]);

const renderStyledText = (text) => {
  if (!text) return null;
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      const inner = part.slice(2, -2);
      const lower = inner.toLowerCase().trim();
      const isPos = POS_WORDS.has(lower) || [...POS_WORDS].some(w => lower.includes(w));
      const isNeg = NEG_WORDS.has(lower) || [...NEG_WORDS].some(w => lower.includes(w));
      const cls = isPos ? "text-emerald-400 font-semibold" : isNeg ? "text-red-400 font-semibold" : "text-zinc-200 font-semibold";
      return <strong key={i} className={cls}>{inner}</strong>;
    }
    return <span key={i}>{part}</span>;
  });
};

const AnalysisCell = ({ text }) => {
  const [expanded, setExpanded] = useState(false);
  if (!text) return <span className="text-zinc-600 text-[11px]">—</span>;
  const sections = text.split(/\n\n(?=•)/).map(s => s.trim()).filter(Boolean);
  const visible = expanded ? sections : sections.slice(0, 1);
  return (
    <div className="text-[11px] leading-relaxed min-w-[320px] max-w-[420px]">
      {visible.map((sec, i) => {
        const nl = sec.indexOf("\n");
        const header = nl > -1 ? sec.slice(0, nl) : sec;
        const body   = nl > -1 ? sec.slice(nl + 1) : "";
        return (
          <div key={i} className={i > 0 ? "mt-2 pt-2 border-t border-zinc-800/60" : ""}>
            <div className="text-zinc-200 font-medium mb-0.5">{renderStyledText(header)}</div>
            {body && <div className="text-zinc-400">{renderStyledText(body)}</div>}
          </div>
        );
      })}
      {sections.length > 1 && (
        <button
          onClick={e => { e.stopPropagation(); setExpanded(x => !x); }}
          className="mt-2 flex items-center justify-center w-5 h-5 rounded-full border border-zinc-600/60 text-zinc-500 hover:border-zinc-400 hover:text-zinc-300 transition-colors text-[10px] font-bold"
        >
          {expanded ? "−" : "+"}
        </button>
      )}
    </div>
  );
};

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

// eslint-disable-next-line no-unused-vars
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

const gradeStyle = (g) => {
  if (g === "A+") return "text-emerald-300 border-emerald-500/40 bg-emerald-500/10";
  if (g === "A")  return "text-blue-300 border-blue-500/40 bg-blue-500/10";
  if (g === "B")  return "text-zinc-400 border-zinc-600/40 bg-zinc-700/20";
  return "text-red-400 border-red-500/40 bg-red-500/10";
};

const DailyChg = ({ val }) => {
  if (val == null) return <span className="text-zinc-600 text-xs">—</span>;
  const pos = val >= 0;
  return <span className={`text-xs font-mono font-bold ${pos ? "text-emerald-400" : "text-red-400"}`}>{pos ? "+" : ""}{val.toFixed(2)}%</span>;
};

/* ─────────────────────────────────────────────────────────── VIX GAUGE ── */
const VIX_ZONES = [
  { name: "Extreme Complacency", range: "VIX < 12",  color: "#00e676", vMin: 0,  vMax: 12, aStart: 180, aEnd: 126, label: ["EXTREME","COMPLACENCY"], impact: 'Markets are dangerously calm. High performance and steady grind higher — watch for sharp "rug pull" corrections as complacency peaks.' },
  { name: "Healthy / Normal",    range: "VIX 12–20", color: "#ffee58", vMin: 12, vMax: 20, aStart: 126, aEnd: 90,  label: ["HEALTHY","NORMAL"],       impact: "Markets thrive in stable macro environments. SPX sees consistent, sustainable growth with minimal headline risk." },
  { name: "Elevated Concern",    range: "VIX 20–30", color: "#ff9100", vMin: 20, vMax: 30, aStart: 90,  aEnd: 45,  label: ["ELEVATED","CONCERN"],      impact: "Choppy, headline-driven trading. SPX often struggles to hold gains. Reduce position size and tighten stops." },
  { name: "Extreme Panic",       range: "VIX 30+",   color: "#ff1744", vMin: 30, vMax: 40, aStart: 45,  aEnd: 0,   label: ["EXTREME","PANIC"],         impact: "Maximum fear. While painful initially, extreme VIX spikes are historically the best entry points for massive SPX rallies." },
];

const VixGauge = ({ initialVix }) => {
  const [vix, setVix] = useState(initialVix ?? 18);
  useEffect(() => { if (initialVix != null) setVix(initialVix); }, [initialVix]);
  const [hov, setHov] = useState(null);

  /* Layout */
  const CX = 200, CY = 178, RO = 145, RI = 60, GAP = 1.8, VMAX = 40;
  const f      = n => +n.toFixed(2);
  const toRad  = d => d * Math.PI / 180;
  const pt     = (r, deg) => [CX + r * Math.cos(toRad(deg)), CY - r * Math.sin(toRad(deg))];
  const v2a    = v => 180 - Math.min(Math.max(v, 0), VMAX) / VMAX * 180;
  const zoneOf = v => VIX_ZONES.find(z => v >= z.vMin && v < z.vMax) ?? VIX_ZONES[VIX_ZONES.length - 1];
  const activeIdx = (() => { const i = VIX_ZONES.findIndex(z => vix >= z.vMin && vix < z.vMax); return i === -1 ? VIX_ZONES.length - 1 : i; })();
  const dispIdx = hov !== null ? hov : activeIdx;
  const active  = VIX_ZONES[dispIdx];
  const dz      = initialVix != null ? zoneOf(initialVix) : null;

  function arcPath(aS, aE, r1, r2, g = 0) {
    const s = aS - g, e = aE + g;
    const large = (s - e) > 180 ? 1 : 0;
    const [ox1, oy1] = pt(r2, s), [ox2, oy2] = pt(r2, e);
    const [ix1, iy1] = pt(r1, s), [ix2, iy2] = pt(r1, e);
    return `M${f(ox1)} ${f(oy1)} A${r2} ${r2} 0 ${large} 0 ${f(ox2)} ${f(oy2)}`
         + ` L${f(ix2)} ${f(iy2)} A${r1} ${r1} 0 ${large} 1 ${f(ix1)} ${f(iy1)}Z`;
  }

  /* Needle */
  const na = v2a(vix);
  const [ntx, nty] = pt(RI + 8, na);
  const [nb1x, nb1y] = pt(4, na + 90);
  const [nb2x, nb2y] = pt(4, na - 90);
  const needlePath = `M${f(ntx)} ${f(nty)} L${f(nb1x)} ${f(nb1y)} L${f(nb2x)} ${f(nb2y)}Z`;
  const da = initialVix != null ? v2a(initialVix) : null;

  return (
    <div className="mb-4 px-4 pt-3 pb-3 bg-zinc-900/60 border border-zinc-800/50 rounded-xl">

      {/* Header */}
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">VIX Fear Gauge</span>
          <span className="text-[9px] text-zinc-700 border border-zinc-800 rounded px-1.5 py-0.5">CBOE · SPX</span>
        </div>
      </div>

      {/* Gauge SVG + Zone info side by side */}
      <div className="flex items-start gap-3">
        <svg viewBox="0 0 400 215" className="flex-shrink-0 h-auto block" style={{ overflow: 'visible', width: '210px' }}>
          <defs>
            <filter id="vg-needle-shadow" x="-50%" y="-20%" width="200%" height="140%">
              <feGaussianBlur stdDeviation="2" result="b"/>
              <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
            </filter>
          </defs>

          {/* Zone arcs */}
          {VIX_ZONES.map((z, i) => {
            const isActive = i === activeIdx;
            const isHov    = hov === i;
            const mid = (z.aStart + z.aEnd) / 2;
            const lr  = (RI + RO) / 2 + 2;
            const [lx, ly] = pt(lr, mid);
            const rot = -(90 - mid);
            return (
              <g key={i} style={{ cursor: 'pointer' }}
                onMouseEnter={() => setHov(i)} onMouseLeave={() => setHov(null)}>
                <path d={arcPath(z.aStart, z.aEnd, RI, RO, GAP)}
                  fill={isActive ? z.color : isHov ? '#2d2d2d' : '#1e1e1e'}
                  style={{ transition: 'fill 0.25s' }}/>
                <g transform={`rotate(${f(rot)} ${f(lx)} ${f(ly)})`} style={{ pointerEvents: 'none' }}>
                  {z.label.map((line, li) => (
                    <text key={li}
                      x={f(lx)} y={f(ly + (z.label.length > 1 ? (li === 0 ? -6 : 6) : 0))}
                      textAnchor="middle" dominantBaseline="middle"
                      fill={isActive ? '#111111' : isHov ? '#4a4a4a' : '#383838'}
                      fontSize="8.5" fontWeight="900" letterSpacing="0.08em"
                      fontFamily="system-ui,-apple-system,sans-serif"
                      style={{ transition: 'fill 0.25s' }}>
                      {line}
                    </text>
                  ))}
                </g>
              </g>
            );
          })}

          {/* Tick marks + numbers */}
          {[0, 12, 20, 30, 40].map(v => {
            const a  = v2a(v);
            const [d1x, d1y] = pt(RI - 6, a);
            const [lx,  ly]  = pt(RI - 20, a);
            return (
              <g key={v}>
                <circle cx={f(d1x)} cy={f(d1y)} r="2.2" fill="#2e2e2e"/>
                <text x={f(lx)} y={f(ly)} textAnchor="middle" dominantBaseline="middle"
                  fill="#303030" fontSize="9.5" fontFamily="monospace">{v}</text>
              </g>
            );
          })}

          {/* Minor dots */}
          {[5, 8, 15, 17, 25, 35].map(v => {
            const a = v2a(v);
            const [dx, dy] = pt(RI - 6, a);
            return <circle key={v} cx={f(dx)} cy={f(dy)} r="1.3" fill="#252525"/>;
          })}

          {/* Daily marker */}
          {da != null && (() => {
            const [mx1, my1] = pt(RI, da);
            const [mx2, my2] = pt(RO, da);
            return <line x1={f(mx1)} y1={f(my1)} x2={f(mx2)} y2={f(my2)}
              stroke={dz.color} strokeWidth="2" strokeDasharray="4,3"
              strokeLinecap="round" opacity="0.6"/>;
          })()}

          {/* Needle */}
          <path d={needlePath} fill="#d8d8d8"
            filter="url(#vg-needle-shadow)" style={{ transition: 'all 0.3s' }}/>
          <circle cx={CX} cy={CY} r="10" fill="#171717" stroke="#3a3a3a" strokeWidth="2"/>
          <circle cx={CX} cy={CY} r="4"  fill="#404040"/>

          {/* VIX number */}
          <text x={CX} y={CY + 36} textAnchor="middle"
            fontSize="36" fontWeight="800" fontFamily="system-ui,-apple-system,sans-serif"
            fill={active.color} style={{ transition: 'fill 0.3s' }}>
            {vix.toFixed(1)}
          </text>
          <text x={CX} y={CY + 54} textAnchor="middle"
            fill="#272727" fontSize="8.5" letterSpacing="0.25em"
            fontFamily="system-ui,sans-serif">VIX</text>
        </svg>

        {/* Zone info */}
        <div className="flex-1 mt-4 px-3 py-2.5 rounded-lg border transition-colors duration-300"
          style={{ background: '#1a1a1a', borderColor: active.color + '28' }}>
          <div className="flex items-baseline gap-2 mb-0.5">
            <span className="text-[11px] font-bold uppercase tracking-wider transition-colors duration-300"
              style={{ color: active.color }}>{active.name}</span>
            <span className="text-[9px] font-mono" style={{ color: '#363636' }}>{active.range}</span>
          </div>
          <div className="text-[11px] text-zinc-500 leading-relaxed">{active.impact}</div>
        </div>
      </div>

      {/* Slider */}
      <div className="flex items-center gap-2 mt-2 w-[210px]">
        <span className="text-[9px] text-zinc-700 font-mono">0</span>
        <input type="range" min="0" max="45" step="0.5" value={vix}
          onChange={e => setVix(parseFloat(e.target.value))}
          className="flex-1 accent-zinc-500 h-1 cursor-pointer"/>
        <span className="text-[9px] text-zinc-700 font-mono">45</span>
      </div>
      {initialVix != null && vix !== initialVix && (
        <button onClick={() => setVix(initialVix)}
          className="mt-1.5 w-[210px] text-center py-0.5 text-[9px] font-medium rounded border border-zinc-700/60 bg-zinc-800/60 text-zinc-500 hover:text-zinc-300 hover:border-zinc-600 transition-colors">
          Restore {initialVix.toFixed(1)}
        </button>
      )}

    </div>
  );
};

const MacroNews = ({ news = [] }) => {
  if (!news.length) return null;
  return (
    <div className="mt-3 px-4 pt-3 pb-3 bg-zinc-900/60 border border-zinc-800/50 rounded-xl flex flex-col flex-1 min-h-0">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Market-Moving News</span>
        <span className="text-[9px] text-zinc-700">last 24h</span>
      </div>
      <div className="overflow-y-auto space-y-2" style={{ maxHeight: '200px' }}>
        {news.map((n, i) => (
          <a key={i} href={n.url} target="_blank" rel="noopener noreferrer"
            className="block group">
            <div className="text-[11px] font-semibold text-zinc-300 group-hover:text-blue-400 leading-snug transition-colors line-clamp-1">
              {n.title}
            </div>
            {n.summary && (
              <div className="text-[10px] text-zinc-600 mt-0.5 leading-snug line-clamp-1">
                {n.summary}
              </div>
            )}
            <div className="text-[9px] text-zinc-700 mt-0.5 font-mono">{n.date} · {n.source}</div>
          </a>
        ))}
      </div>
    </div>
  );
};


const GapperScanner = () => {
  const [gapperData, setGapperData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [hovered, setHovered] = useState(null);
  const [tickerDb, setTickerDb] = useState({});

  // Filter state — human-friendly units: PMVol/AvgVol in K, MktCap in $B, DolVol in $M
  const [fMinGap,    setFMinGap]    = useState(0);
  const [fMinPMVol,  setFMinPMVol]  = useState(0);    // K
  const [fMinPrice,  setFMinPrice]  = useState(0);
  const [fMinAvgVol, setFMinAvgVol] = useState(0);    // K
  const [fMinMktCap, setFMinMktCap] = useState(0);      // $B
  const [fMinDolVol, setFMinDolVol] = useState(0);     // $M

  useEffect(() => {
    fetch(`${process.env.PUBLIC_URL}/stock_db.json`)
      .catch(() => fetch(`${process.env.PUBLIC_URL}/all_tickers.json`))
      .then(r => r.json())
      .then(arr => {
        const map = {};
        arr.forEach(t => { map[t.ticker] = t; });
        setTickerDb(map);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const r = await fetch(process.env.PUBLIC_URL + "/gapper_data.json?v=" + Date.now());
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

  const filtered = gapperData.gappers.filter(g =>
    g.gap_pct   >= fMinGap &&
    g.pm_volume >= fMinPMVol * 1000 &&
    g.price     >= fMinPrice &&
    (g.avg_vol_10d || 0)     >= fMinAvgVol * 1000 &&
    (g.mkt_cap   || 0)       >= fMinMktCap * 1e9 &&
    (g.avg_dollar_vol || g.price * (g.avg_vol_10d || 0)) >= fMinDolVol * 1e6
  );

  const resetFilters = () => {
    setFMinGap(5); setFMinPMVol(200); setFMinPrice(5);
    setFMinAvgVol(500); setFMinMktCap(2); setFMinDolVol(50);
  };

  return (
    <>
    <div className="max-w-[1800px] mx-auto px-4 py-4">
      {/* Filter Bar */}
      <div className="mb-4 p-3 bg-zinc-800/40 border border-zinc-700/40 rounded-lg">
        <div className="grid grid-cols-6 gap-3 mb-2">
          <FInput label="Min Gap (%)"          value={fMinGap}    onChange={setFMinGap}    hint="e.g. 5 = 5%"/>
          <FInput label="Min PM Vol (K)"       value={fMinPMVol}  onChange={setFMinPMVol}  hint={`= ${fmtNum(fMinPMVol * 1000)}`}/>
          <FInput label="Min Price ($)"        value={fMinPrice}  onChange={setFMinPrice}/>
          <FInput label="Min Avg Vol 10d (K)"  value={fMinAvgVol} onChange={setFMinAvgVol} hint={`= ${fmtNum(fMinAvgVol * 1000)}`}/>
          <FInput label="Min Mkt Cap ($B)"     value={fMinMktCap} onChange={setFMinMktCap} hint={`= ${fmtCap(fMinMktCap * 1e9)}`}/>
          <FInput label="Min Avg $ Vol ($M)"   value={fMinDolVol} onChange={setFMinDolVol} hint={`= ${fmtVol(fMinDolVol * 1e6)}`}/>
        </div>
        <div className="flex items-center justify-between pt-1 border-t border-zinc-700/30">
          <button onClick={resetFilters} className="text-[11px] px-2.5 py-1 bg-zinc-700/50 border border-zinc-600/40 rounded text-zinc-400 hover:text-zinc-200 hover:border-zinc-500 transition-colors">
            Reset
          </button>
          <span className="text-[11px] text-zinc-500">
            Scanned: <span className="text-zinc-400">{gapperData.scan_time}</span>
            <span className="ml-3 text-zinc-600">{filtered.length} / {gapperData.gappers.length} shown</span>
          </span>
        </div>
      </div>

      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-bold text-zinc-100 tracking-wide uppercase">Institutional Gappers</h2>
        {filtered.length === 0 && (
          <p className="text-xs text-zinc-500">No gappers match current filters — try loosening the criteria</p>
        )}
      </div>

      <div className="overflow-x-auto rounded-lg border border-zinc-700/40">
        <table className="w-full table-fixed min-w-[1300px]">
          <colgroup>
            <col style={{width:"5%"}}/>
            <col style={{width:"5%"}}/>
            <col style={{width:"5%"}}/>
            <col style={{width:"4%"}}/>
            <col style={{width:"4%"}}/>
            <col style={{width:"4%"}}/>
            <col style={{width:"5%"}}/>
            <col style={{width:"6%"}}/>
            <col style={{width:"8%"}}/>
            <col style={{width:"8%"}}/>
            <col style={{width:"4%"}}/>
            <col style={{width:"18%"}}/>
            <col style={{width:"24%"}}/>
          </colgroup>
          <thead>
            <tr className="text-[10px] text-zinc-500 uppercase tracking-wider bg-zinc-900/80 border-b border-zinc-700/40 align-middle">
              <th className="text-center py-2 px-1.5 font-medium align-middle">Ticker</th>
              <th className="text-center py-2 px-1.5 font-medium align-middle leading-tight">Premkt<br/>Price<br/>Chg %</th>
              <th className="text-center py-2 px-1.5 font-medium align-middle leading-tight">Premkt<br/>Vol</th>
              <th className="text-center py-2 px-1.5 font-medium align-middle">RVol</th>
              <th className="text-center py-2 px-1.5 font-medium align-middle">Daily %</th>
              <th className="text-center py-2 px-1.5 font-medium align-middle leading-tight">Short<br/>Int</th>
              <th className="text-center py-2 px-1.5 font-medium align-middle">Float</th>
              <th className="text-center py-2 px-1.5 font-medium align-middle">Sector</th>
              <th className="text-center py-2 px-1.5 font-medium align-middle">Industry</th>
              <th className="text-center py-2 px-1.5 font-medium align-middle">Category</th>
              <th className="text-center py-2 px-1 font-medium align-middle">Grade</th>
              <th className="text-center py-2 px-1.5 font-medium align-middle">Reasoning</th>
              <th className="text-center py-2 px-2 font-medium align-middle">Analysis Details</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((g, i) => (
              <tr key={g.ticker + i} className="border-t border-zinc-800/40 hover:bg-zinc-800/20 transition-colors align-middle">
                {/* Ticker */}
                <td className="py-2 px-1.5 text-center">
                  <span
                    className="font-bold text-zinc-100 text-xs hover:text-blue-400 transition-colors cursor-default"
                    onMouseEnter={e => setHovered({ ticker: g.ticker, rect: e.currentTarget.getBoundingClientRect() })}
                    onMouseLeave={() => setHovered(null)}
                  >
                    {g.ticker}
                  </span>
                  <a href={`https://www.tradingview.com/chart/?symbol=${g.ticker}`} target="_blank" rel="noreferrer" className="ml-1">
                    <ExternalLink size={8} className="inline text-zinc-600 hover:text-blue-400"/>
                  </a>
                  <div className="text-[10px] font-mono text-zinc-500">${g.price.toFixed(2)}</div>
                </td>
                {/* Premkt % */}
                <td className="py-2 px-1.5 text-center">
                  <div className="text-[11px] font-mono text-zinc-300">${g.price.toFixed(2)}</div>
                  <span className="text-xs font-bold font-mono text-emerald-400">+{g.gap_pct.toFixed(1)}%</span>
                </td>
                {/* Premkt Vol */}
                <td className="py-2 px-1.5 text-center text-[11px] font-mono text-zinc-400">{fmtNum(g.pm_volume)}</td>
                {/* RVol */}
                <td className="py-2 px-1.5 text-center">
                  <span className={`text-[11px] font-bold font-mono ${g.rvol >= 5 ? "text-emerald-300" : g.rvol >= 3 ? "text-emerald-400" : g.rvol >= 2 ? "text-amber-400" : "text-zinc-500"}`}>
                    {g.rvol.toFixed(2)}x
                  </span>
                </td>
                {/* Daily % */}
                <td className="py-2 px-1.5 text-center"><DailyChg val={g.daily_pct}/></td>
                {/* Short Int */}
                <td className="py-2 px-1.5 text-center text-[11px] font-mono text-zinc-400">{g.short_float || "—"}</td>
                {/* Float */}
                <td className="py-2 px-1.5 text-center text-[11px] font-mono text-zinc-400">{g.float_shares || "—"}</td>
                {/* Sector / Industry */}
                {(() => {
                  const db = tickerDb[g.ticker] || {};
                  const sector = db.sector || "";
                  const industry = db.industry || g.industry || "";
                  return (
                    <>
                      <td className="py-2 px-1.5 text-center text-[10px] text-zinc-200 align-middle">{sector || <span className="text-zinc-600">—</span>}</td>
                      <td className="py-2 px-1.5 text-center text-[10px] text-zinc-200 align-middle">{industry || <span className="text-zinc-600">—</span>}</td>
                    </>
                  );
                })()}
                {/* Category */}
                <td className="py-2 px-1.5 text-center">
                  <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full border ${CATEGORY_STYLE[g.category] || CATEGORY_STYLE["Others"]}`}>
                    {g.category}
                  </span>
                </td>
                {/* Grade */}
                <td className="py-2 px-1 text-center">
                  {g.grade
                    ? <span className={`text-[10px] font-bold px-1 py-0.5 rounded border ${gradeStyle(g.grade)}`}>{g.grade}</span>
                    : <span className="text-zinc-600">—</span>}
                </td>
                {/* Reasoning */}
                <td className="py-2 px-1.5 text-[11px] text-zinc-400 leading-relaxed align-middle whitespace-normal break-words">{g.reasoning}</td>
                {/* Analysis Details */}
                <td className="py-2 px-2 align-middle"><AnalysisCell text={g.analysis_details}/></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
    {hovered && <TVPopup ticker={hovered.ticker} anchorRect={hovered.rect}/>}
    </>
  );
};

// ── Multi-theme overrides: legacy frontend fallback (now mostly handled by scraper's ticker_extra_subthemes) ──
// Only add entries here for themes the scraper doesn't cover yet.
const TICKER_EXTRA_THEMES = {};

// Industries that map to custom themes not in INDUSTRY_TO_THEME
const THEME_INDUSTRY_MAP = {
  "Autonomous Vehicles": ["Auto Manufacturers", "Auto Parts", "Auto & Truck Dealerships"],
};

// Representative ETF or ticker per Finviz theme name — used to fetch real 6M sparklines
const THEME_ETF_MAP = {
  // ── Finviz Themes Map names ──
  "Artificial Intelligence":       "AIQ",
  "Semiconductors":                "SOXX",
  "Cloud Computing":               "WCLD",
  "Cybersecurity":                 "CIBR",
  "Electric Vehicles":             "LIT",
  "Autonomous Systems":            "DRIV",
  "Defense & Aerospace":           "ITA",
  "Healthcare & Biotech":          "XBI",
  "FinTech":                       "FINX",
  "Crypto & Blockchain":           "MSTR",
  "Space Tech":                    "UFO",
  "Quantum Computing":             "QTUM",
  "Robotics":                      "BOTZ",
  "Energy Renewable":              "ICLN",
  "Energy Traditional":            "XLE",
  "Software":                      "IGV",
  "E-commerce":                    "IBUY",
  "Social Media":                  "SOCL",
  "Real Estate & REITs":           "VNQ",
  "Internet of Things":            "SNSR",
  "Industrial Automation":         "IRBO",
  "Big Data":                      "META",
  "Consumer Goods":                "XLY",
  "Commodities Metals":            "GDX",
  "Commodities Energy":            "USO",
  "Commodities Agriculture":       "DBA",
  "Transportation & Logistics":    "XTN",
  "Telecommunications":            "XLC",
  "Virtual & Augmented Reality":   "METV",
  "Agriculture & FoodTech":        "MOO",
  "Environmental Sustainability":  "ESGU",
  "Hardware":                      "AMAT",
  "Smart Home":                    "AAPL",
  "Wearables":                     "AAPL",
  "Digital Entertainment":         "HERO",
  "Aging Population & Longevity":  "IHF",
  "Healthy Food & Nutrition":      "MOO",
  "Education Technology":          "INST",
  "Biometrics":                    "MSFT",
  "Nanotechnology":                "NVDA",
  // ── Industry view theme names ──
  "Telecom":                       "FCOM",
  "Clean Energy & Utilities":      "ICLN",
  "Industrials":                   "XLI",
  "Consumer Staples":              "XLP",
  "Consumer Discretionary":        "XLY",
  "Financials":                    "XLF",
  "Real Estate":                   "VNQ",
  "Software & Cloud":              "IGV",
  "Internet & E-Commerce":         "IBUY",
  "Media & Entertainment":         "SOCL",
  "Materials & Mining":            "XLB",
  "Agriculture & Food":            "MOO",
  "E-Commerce":                    "IBUY",
  "Fintech":                       "FINX",
};

// ── Merged Search + Ticker Lookup ──
const SearchBar = ({ data, search, setSearch }) => {
  const [open, setOpen] = useState(false);
  const [allTickers, setAllTickers] = useState([]);
  const [livePrice, setLivePrice] = useState(null); // { price, change_pct } for non-scanner stocks
  const [tickerHover, setTickerHover] = useState(null); // { ticker, rect } for TVPopup
  const [selectedTheme, setSelectedTheme] = useState(null); // theme name to expand stock list
  const [selectedSubTheme, setSelectedSubTheme] = useState(null); // subtheme name to expand stock list

  useEffect(() => {
    fetch(`${process.env.PUBLIC_URL}/stock_db.json`)
      .then(r => r.json())
      .then(setAllTickers)
      .catch(() => {
        // fallback to all_tickers.json
        fetch(`${process.env.PUBLIC_URL}/all_tickers.json`)
          .then(r => r.json())
          .then(setAllTickers)
          .catch(() => {});
      });
  }, []);

  // Scanner index — stocks in current scan with theme/subtheme info
  const index = useMemo(() => {
    if (!data) return {};
    const map = {};
    data.themes.forEach(t => {
      const norm = normalizeTheme(t);
      norm.subthemes.forEach(sub => {
        sub.stocks.forEach(s => {
          if (!map[s.ticker]) map[s.ticker] = { ticker: s.ticker, company: s.company, sector: s.sector || "", industry: s.industry || "", price: s.price ?? null, change_pct: s.change_pct ?? s.perf_1d ?? null, appearances: [] };
          map[s.ticker].appearances.push({ theme: norm.name, subtheme: sub.name });
        });
      });
    });
    return map;
  }, [data]);

  const q = search.trim();
  const upper = q.toUpperCase();
  const lower = q.toLowerCase();

  // Exact match for detail panel
  const result = upper.length >= 1 ? (index[upper] || null) : null;

  // Suggestions: search all US tickers + scanner stocks
  const suggestions = useMemo(() => {
    if (q.length < 1) return [];
    const seen = new Set();
    // Buckets: ticker-prefix > company-starts-with > company-contains
    const bucketTicker = [], bucketStarts = [], bucketContains = [];
    // Scanner stocks first (have theme info)
    for (const s of Object.values(index)) {
      seen.add(s.ticker);
      const item = { ...s, inScanner: true };
      if (s.ticker.startsWith(upper)) bucketTicker.push(item);
      else if (s.company?.toLowerCase().startsWith(lower)) bucketStarts.push(item);
      else if (s.company?.toLowerCase().includes(lower)) bucketContains.push(item);
    }
    // Then all tickers — iterate fully so ranked sort works
    for (const s of allTickers) {
      if (seen.has(s.ticker)) continue;
      const item = { ticker: s.ticker, company: s.company, appearances: [], inScanner: false };
      if (s.ticker.startsWith(upper)) bucketTicker.push(item);
      else if (s.company?.toLowerCase().startsWith(lower)) bucketStarts.push(item);
      else if (s.company?.toLowerCase().includes(lower)) bucketContains.push(item);
    }
    return [...bucketTicker, ...bucketStarts, ...bucketContains].slice(0, 8);
  }, [index, allTickers, upper, lower, q]);

  // Map theme name → all stocks, and subtheme name → stocks (from scanner data)
  const [themeStocksMap, subThemeStocksMap] = useMemo(() => {
    if (!data) return [{}, {}];
    const tMap = {}, sMap = {};
    data.themes.forEach(t => {
      const norm = normalizeTheme(t);
      const stocks = [];
      norm.subthemes.forEach(sub => {
        sub.stocks.forEach(s => stocks.push(s));
        sMap[sub.name] = sub.stocks;
      });
      tMap[norm.name] = stocks;
    });
    return [tMap, sMap];
  }, [data]);

  // Also check stock_db for exact match if not in scanner
  const allTickerMatch = upper.length >= 1 ? allTickers.find(t => t.ticker === upper) : null;
  const fullResult = (() => {
    const base = result || (allTickerMatch ? {
      ticker: allTickerMatch.ticker,
      company: allTickerMatch.company,
      sector: allTickerMatch.sector || "",
      industry: allTickerMatch.industry || "",
      appearances: allTickerMatch.theme ? [{ theme: allTickerMatch.theme, subtheme: allTickerMatch.subtheme || "" }] : [],
      inScanner: false,
    } : null);
    if (!base) return null;
    const existingKeys = new Set(base.appearances.map(a => `${a.theme}||${a.subtheme}`));
    // Extra appearances from scraper-generated ticker_extra_subthemes (backend source of truth)
    const scraperExtras = ((data?.ticker_extra_subthemes || {})[base.ticker] || [])
      .filter(a => !existingKeys.has(`${a.theme}||${a.subtheme}`));
    scraperExtras.forEach(a => existingKeys.add(`${a.theme}||${a.subtheme}`));
    // Legacy frontend overrides (for themes not yet in scraper data)
    const extraThemes = TICKER_EXTRA_THEMES[base.ticker] || [];
    const extraAppearances = extraThemes
      .map(t => typeof t === "string" ? { theme: t, subtheme: "" } : t)
      .filter(a => !existingKeys.has(`${a.theme}||${a.subtheme}`));
    const merged = [...base.appearances, ...scraperExtras, ...extraAppearances];
    return { ...base, appearances: merged };
  })();

  // Fetch live price from local price_api.py for any exact match
  useEffect(() => {
    setLivePrice(null);
    if (!fullResult) return;
    const ticker = fullResult.ticker;
    fetch(`http://localhost:5001/price/${ticker}`)
      .then(r => r.json())
      .then(d => {
        if (d.price != null) setLivePrice({ price: d.price, change_pct: d.change_pct });
      })
      .catch(() => {});
  }, [fullResult?.ticker]); // eslint-disable-line react-hooks/exhaustive-deps

  const displayPrice = livePrice;

  const showSuggestions = open && q.length >= 1 && !fullResult && suggestions.length > 0;
  const noMatch = open && q.length >= 2 && !fullResult && suggestions.length === 0;

  return (
    <div className="relative">
      <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500"/>
      <input
        type="text"
        value={search}
        onChange={e => { setSearch(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => { setOpen(false); setSelectedTheme(null); setSelectedSubTheme(null); }, 150)}
        placeholder="Search ticker or company…"
        className="w-52 pl-7 pr-7 py-1.5 text-xs bg-zinc-800/60 border border-zinc-700/50 rounded-md text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-blue-500/50"
      />
      {search && <button onMouseDown={e => { e.preventDefault(); setSearch(""); setOpen(false); }} className="absolute right-2 top-1/2 -translate-y-1/2"><X size={11} className="text-zinc-500"/></button>}

      {/* Suggestions dropdown */}
      {showSuggestions && (
        <div className="absolute top-full right-0 mt-1.5 w-72 bg-zinc-900 border border-zinc-700/60 rounded-lg shadow-2xl z-50 py-1">
          {suggestions.map(s => (
            <button key={s.ticker} onMouseDown={e => { e.preventDefault(); setSearch(s.ticker); setOpen(true); }}
              className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-zinc-800 text-left">
              <span className="text-xs font-bold text-zinc-200 w-14 flex-shrink-0">{s.ticker}</span>
              <span className="text-[11px] text-zinc-500 truncate flex-1">{s.company}</span>
              {s.inScanner && <span className="text-[9px] text-blue-400 flex-shrink-0">in scanner</span>}
            </button>
          ))}
        </div>
      )}

      {/* Detail panel for exact match */}
      {open && fullResult && (
        <div id="search-result-panel" className="absolute top-full right-0 mt-1.5 w-72 bg-zinc-900 border border-zinc-700/60 rounded-lg shadow-2xl z-50 p-3 space-y-2">
          <div className="flex items-baseline gap-2">
            <span
              className="text-sm font-bold text-zinc-100 cursor-default hover:text-blue-400 transition-colors"
              onMouseEnter={e => { const panel = e.currentTarget.closest('[class*="shadow-2xl"]') || e.currentTarget; setTickerHover({ ticker: fullResult.ticker, rect: panel.getBoundingClientRect() }); }}
              onMouseLeave={() => setTickerHover(null)}
            >{fullResult.ticker}</span>
            {fullResult.company && <span className="text-[11px] text-zinc-500 truncate">{fullResult.company}</span>}
          </div>
          <div className="space-y-1.5">
            {displayPrice?.price != null && (
              <div className="flex gap-2 text-xs">
                <span className="text-zinc-500 w-16 flex-shrink-0">Price</span>
                <span className="text-zinc-200">${displayPrice.price.toFixed(2)}</span>
                {displayPrice.change_pct != null && (
                  <span className={`font-medium ${displayPrice.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {displayPrice.change_pct >= 0 ? '+' : ''}{displayPrice.change_pct.toFixed(2)}%
                  </span>
                )}
              </div>
            )}
            {[{ label: "Sector", value: fullResult.sector }, { label: "Industry", value: fullResult.industry }].map(({ label, value }) => value ? (
              <div key={label} className="flex gap-2 text-xs">
                <span className="text-zinc-500 w-16 flex-shrink-0">{label}</span>
                <span className="text-zinc-200">{value}</span>
              </div>
            ) : null)}
            {fullResult.appearances.length > 0 && (
              <div className="mt-2 pt-2 border-t border-zinc-800 space-y-1.5">
                {/* Single "Theme" row — display only, not expandable */}
                <div className="flex gap-2 text-xs items-start">
                  <span className="text-zinc-500 w-16 flex-shrink-0">Theme</span>
                  <div className="flex flex-wrap gap-x-2 gap-y-1">
                    {fullResult.appearances.map((a, i) => (
                      <span key={i} className="text-blue-300 font-medium">{a.theme}</span>
                    ))}
                  </div>
                </div>
                {/* Sub-theme for scanner stocks */}
                {fullResult.appearances.some(a => a.subtheme) && (
                  <div className="flex gap-2 text-xs items-start">
                    <span className="text-zinc-500 w-16 flex-shrink-0">Sub-Theme</span>
                    <div className="flex flex-wrap gap-x-2 gap-y-1">
                      {fullResult.appearances.filter(a => a.subtheme).map((a, i) => {
                        const isOpen = selectedSubTheme === a.subtheme;
                        return (
                          <button key={i}
                            className="text-violet-300 font-medium hover:text-violet-200 flex items-center gap-0.5 text-left"
                            onMouseDown={e => e.preventDefault()}
                            onClick={() => setSelectedSubTheme(isOpen ? null : a.subtheme)}
                          >
                            {a.subtheme}
                            <span className="text-zinc-500 text-[9px] ml-0.5">{isOpen ? '▲' : '▼'}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
                {/* Expanded stock list for selected subtheme */}
                {selectedSubTheme && (() => {
                  const scannerStocks = subThemeStocksMap[selectedSubTheme] || [];
                  // Also find tickers that have this subtheme as an extra (from ticker_extra_subthemes)
                  const extraTickers = data?.ticker_extra_subthemes
                    ? Object.entries(data.ticker_extra_subthemes)
                        .filter(([, extras]) => extras.some(e => e.subtheme === selectedSubTheme))
                        .map(([t]) => t)
                    : [];
                  const extraStocks = extraTickers.map(t => allTickers.find(s => s.ticker === t)).filter(Boolean);
                  const primaryStocks = allTickers.filter(s => s.subtheme === selectedSubTheme);
                  const combined = [...new Map([...primaryStocks, ...extraStocks].map(s => [s.ticker, s])).values()];
                  const stocks = scannerStocks.length > 0 ? scannerStocks : combined.slice(0, 50);
                  if (stocks.length === 0) return null;
                  return (
                    <div className="mt-1 border border-zinc-700/50 rounded-md overflow-hidden">
                      <div className="overflow-y-auto" style={{ maxHeight: '200px' }}>
                        {[...stocks].sort((a, b) => (b.rs_52w || 0) - (a.rs_52w || 0) || a.ticker.localeCompare(b.ticker)).map(s => (
                          <button key={s.ticker}
                            className="w-full flex items-center gap-2 px-2 py-1 hover:bg-zinc-800 text-left border-b border-zinc-800 last:border-0"
                            onMouseDown={e => { e.preventDefault(); setSearch(s.ticker); setSelectedSubTheme(null); }}
                          >
                            <span
                              className="text-[11px] font-bold text-zinc-200 w-12 flex-shrink-0 hover:text-blue-400 cursor-default"
                              onMouseEnter={e => { e.stopPropagation(); const panelEl = document.getElementById('search-result-panel'); const panelLeft = panelEl ? panelEl.getBoundingClientRect().left : null; const sr = e.currentTarget.getBoundingClientRect(); setTickerHover({ ticker: s.ticker, rect: { ...sr, panelLeft } }); }}
                              onMouseLeave={() => setTickerHover(null)}
                            >{s.ticker}</span>
                            <span className="text-[10px] text-zinc-500 truncate flex-1">{s.company}</span>
                            {s.change_pct != null && (
                              <span className={`text-[10px] font-medium flex-shrink-0 ${s.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                {s.change_pct >= 0 ? '+' : ''}{s.change_pct.toFixed(1)}%
                              </span>
                            )}
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
        </div>
      )}

      {open && noMatch && (
        <div className="absolute top-full right-0 mt-1.5 w-72 bg-zinc-900 border border-zinc-700/60 rounded-lg shadow-2xl z-50 p-3">
          <p className="text-xs text-zinc-500">"{q}" not found in scanner data</p>
        </div>
      )}
      {tickerHover && <TVPopup ticker={tickerHover.ticker} anchorRect={tickerHover.rect}/>}
    </div>
  );
};


// ── Momentum Catalyst Analyst Cockpit ────────────────────────────────────────

const MomentumCockpit = () => {
  const [gapperData, setGapperData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedTicker, setSelectedTicker] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [analyzing, setAnalyzing] = useState(false);

  useEffect(() => {
    fetch(process.env.PUBLIC_URL + "/gapper_data.json?v=" + Date.now())
      .then(r => r.ok ? r.json() : null)
      .then(d => { setGapperData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const fetchAnalysis = useCallback(async (ticker, row) => {
    setSelectedTicker(ticker);
    setAnalysis(null);
    setAnalyzing(true);
    try {
      const params = new URLSearchParams({
        gap: row.gap_pct || 0, pm_vol: row.pm_volume || 0,
        rvol: row.rvol || 0, price: row.price || 0, mkt_cap: row.mkt_cap || 0,
      });
      const r = await fetch(`http://localhost:5002/analyze/${ticker}?${params}`);
      if (r.ok) setAnalysis(await r.json());
      else setAnalysis({ error: `API error ${r.status}` });
    } catch {
      setAnalysis({ error: "Analyst API offline — run: python3 momentum_cockpit.py" });
    }
    setAnalyzing(false);
  }, []);

  const columns = useMemo(() => [
    {
      id: "ticker", header: "Ticker",
      cell: ({ row }) => {
        const g = row.original;
        return (
          <div>
            <div className="font-bold font-mono text-xs text-zinc-100 hover:text-blue-400 cursor-pointer" onClick={() => fetchAnalysis(g.ticker, g)}>{g.ticker}</div>
            <div className="text-[9px] text-zinc-500 truncate max-w-[90px]">{g.company || ""}</div>
          </div>
        );
      }
    },
    {
      id: "price", header: "Price / Chg",
      cell: ({ row }) => {
        const g = row.original;
        return (
          <div className="text-right">
            <div className="font-mono text-xs text-zinc-200">{g.price != null ? `$${g.price.toFixed(2)}` : "—"}</div>
            <div className={`text-[10px] font-mono ${(g.daily_pct || 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>
              {g.daily_pct != null ? `${g.daily_pct >= 0 ? "+" : ""}${g.daily_pct.toFixed(2)}%` : "—"}
            </div>
          </div>
        );
      }
    },
    {
      id: "gap", header: "Gap %",
      cell: ({ row }) => <span className="font-mono text-xs text-emerald-400">+{(row.original.gap_pct || 0).toFixed(1)}%</span>
    },
    {
      id: "adr", header: "ADR", accessorKey: "adr_pct",
      cell: ({ getValue }) => {
        const v = getValue();
        return <span className={`font-mono text-xs ${v >= 5 ? "text-emerald-400" : v >= 4 ? "text-amber-400" : "text-zinc-500"}`}>{v != null ? `${v.toFixed(1)}%` : "—"}</span>;
      }
    },
    {
      id: "rvol", header: "RVOL", accessorKey: "rvol",
      cell: ({ getValue }) => {
        const v = getValue();
        return <span className={`font-mono text-xs font-bold ${v >= 5 ? "text-emerald-300" : v >= 3 ? "text-emerald-400" : v >= 2 ? "text-amber-400" : "text-zinc-500"}`}>{v != null ? `${v.toFixed(2)}x` : "—"}</span>;
      }
    },
    {
      id: "industry", header: "Industry",
      cell: ({ row }) => <span className="text-[10px] text-zinc-300">{row.original.industry || "—"}</span>
    },
    {
      id: "theme", header: "Theme",
      cell: ({ row }) => <span className="text-[10px] text-blue-300">{row.original.finviz_theme || row.original.theme || "—"}</span>
    },
    {
      id: "grade", header: "Grade", accessorKey: "grade",
      cell: ({ getValue }) => {
        const g = getValue();
        return g ? <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${gradeStyle(g)}`}>{g}</span> : <span className="text-zinc-600">—</span>;
      }
    },
    {
      id: "reasoning", header: "Reasoning", accessorKey: "reasoning",
      cell: ({ getValue }) => <span className="text-[10px] text-zinc-400 leading-tight line-clamp-2">{getValue() || "—"}</span>
    },
    {
      id: "analysis", header: "Analysis Detail",
      cell: ({ row }) => {
        const d = row.original.analysis_detail;
        if (!d) return <span className="text-zinc-600 text-[10px]">—</span>;
        return (
          <div className="text-[10px] leading-snug space-y-1">
            {d.catalyst && <div className="text-zinc-300 line-clamp-2">{d.catalyst}</div>}
            {d.impact && <div className="text-zinc-500 italic line-clamp-1">{d.impact}</div>}
          </div>
        );
      }
    },
  ], [fetchAnalysis]);

  const table = useReactTable({
    data: gapperData?.gappers || [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  if (loading) return <div className="flex items-center justify-center py-20 text-zinc-500"><RefreshCw size={20} className="animate-spin"/></div>;
  if (!gapperData?.gappers?.length) return <div className="text-center py-16 text-zinc-500 text-sm">No gapper data — run gapper_service.py first</div>;

  return (
    <div className="relative flex min-h-0">
      {/* Main Table */}
      <div className={`flex-1 overflow-x-auto transition-all duration-300 ${selectedTicker ? "mr-[440px]" : ""}`}>
        <div className="flex items-center justify-between px-4 py-2 border-b border-zinc-800/60">
          <span className="text-xs text-zinc-500">
            {gapperData.gappers.length} gappers · {gapperData.scan_time ? `Scanned ${gapperData.scan_time}` : ""}
          </span>
          <span className="text-[10px] text-zinc-600">Click any row to fetch live Gemini analysis →</span>
        </div>
        <table className="w-full min-w-[1100px] text-left border-collapse">
          <thead>
            {table.getHeaderGroups().map(hg => (
              <tr key={hg.id} className="text-[9px] text-zinc-500 uppercase tracking-wider bg-zinc-900/80 border-b border-zinc-700/40">
                {hg.headers.map(h => (
                  <th key={h.id} className="py-2 px-2 font-medium whitespace-nowrap">
                    {flexRender(h.column.columnDef.header, h.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map(row => {
              const g = row.original;
              const isFail = g.technical_status === "Fail";
              const isSelected = selectedTicker === g.ticker;
              return (
                <tr key={row.id}
                  className={`border-b border-zinc-800/40 transition-colors cursor-pointer
                    ${isSelected ? "bg-blue-500/10" : "hover:bg-zinc-800/20"}
                    ${isFail ? "opacity-40" : ""}
                  `}
                  onClick={() => fetchAnalysis(g.ticker, g)}
                >
                  {row.getVisibleCells().map(cell => (
                    <td key={cell.id} className={`py-1.5 px-2 align-top ${cell.column.id === "analysis" ? "min-w-[280px]" : ""}`}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Slide-out Analysis Panel */}
      <div className={`fixed right-0 top-0 h-full w-[440px] bg-zinc-950 border-l border-zinc-800 shadow-2xl z-30 flex flex-col transition-transform duration-300 ${selectedTicker ? "translate-x-0" : "translate-x-full"}`}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800 flex-shrink-0">
          <div className="flex items-center gap-2">
            <span className="font-bold font-mono text-zinc-100">{selectedTicker}</span>
            {analysis?.grade && <span className={`text-xs font-bold px-1.5 py-0.5 rounded border ${gradeStyle(analysis.grade)}`}>{analysis.grade}</span>}
            {analysis?.technical_status && (
              <span className={`text-[9px] px-1.5 py-0.5 rounded ${analysis.technical_status === "Pass" ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30" : "bg-red-500/20 text-red-400 border border-red-500/30"}`}>
                {analysis.technical_status}
              </span>
            )}
          </div>
          <button onClick={() => { setSelectedTicker(null); setAnalysis(null); }} className="text-zinc-500 hover:text-zinc-200 transition-colors text-lg leading-none">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {analyzing && (
            <div className="flex items-center gap-2 text-zinc-500 text-sm py-12 justify-center">
              <RefreshCw size={16} className="animate-spin"/> Fetching live analysis…
            </div>
          )}
          {analysis?.error && <div className="text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded p-3">{analysis.error}</div>}
          {analysis && !analysis.error && (
            <>
              {/* Stats Grid */}
              <div className="grid grid-cols-3 gap-1.5">
                {[
                  { label: "Price",    value: analysis.price    != null ? `$${analysis.price.toFixed(2)}` : "—" },
                  { label: "Gap",      value: analysis.gap_pct  != null ? `+${analysis.gap_pct.toFixed(1)}%` : "—" },
                  { label: "PM Vol",   value: analysis.pm_volume != null ? `${(analysis.pm_volume/1000).toFixed(0)}K` : "—" },
                  { label: "ADR(20)",  value: analysis.adr_pct  != null ? `${analysis.adr_pct.toFixed(1)}%` : "—" },
                  { label: "RVOL",     value: analysis.rvol     != null ? `${analysis.rvol.toFixed(2)}x` : "—" },
                  { label: "Mkt Cap",  value: analysis.mkt_cap  != null ? `$${(analysis.mkt_cap/1e9).toFixed(2)}B` : "—" },
                  { label: "Float",    value: analysis.float_shares || "—" },
                  { label: "Short",    value: analysis.short_float || "—" },
                  { label: "Avg Vol",  value: analysis.avg_vol_10d != null ? `${(analysis.avg_vol_10d/1000).toFixed(0)}K` : "—" },
                ].map(({ label, value }) => (
                  <div key={label} className="bg-zinc-900 rounded p-2">
                    <div className="text-[9px] text-zinc-500 uppercase tracking-wide">{label}</div>
                    <div className="font-mono text-xs text-zinc-200 mt-0.5">{value}</div>
                  </div>
                ))}
              </div>

              {/* Category + Conviction */}
              <div className="flex gap-2">
                <div className="flex-1 bg-zinc-900 rounded p-2">
                  <div className="text-[9px] text-zinc-500 uppercase mb-0.5">Category</div>
                  <div className="text-xs text-zinc-200">{analysis.category || "—"}</div>
                </div>
                <div className="bg-zinc-900 rounded p-2 text-center w-20">
                  <div className="text-[9px] text-zinc-500 uppercase mb-0.5">Conviction</div>
                  <div className={`text-base font-bold ${analysis.conviction >= 70 ? "text-emerald-400" : analysis.conviction >= 50 ? "text-amber-400" : "text-zinc-500"}`}>{analysis.conviction ?? "—"}</div>
                </div>
              </div>

              {/* Reasoning */}
              {analysis.reasoning && (
                <div>
                  <div className="text-[9px] text-zinc-500 uppercase tracking-wide mb-1">Mechanical Trigger</div>
                  <div className="text-xs text-zinc-300 bg-zinc-900 rounded p-2.5 leading-relaxed">{analysis.reasoning}</div>
                </div>
              )}

              {/* Catalyst + Impact */}
              {analysis.analysis_detail && (
                <div>
                  <div className="text-[9px] text-zinc-500 uppercase tracking-wide mb-1">Catalyst Breakdown</div>
                  <div className="bg-zinc-900 rounded p-3 space-y-2">
                    {analysis.analysis_detail.catalyst && (
                      <div className="text-[11px] text-zinc-300 leading-relaxed"
                        dangerouslySetInnerHTML={{ __html: analysis.analysis_detail.catalyst.replace(/\*\*(.*?)\*\*/g, '<strong class="text-zinc-100">$1</strong>') }}/>
                    )}
                    {analysis.analysis_detail.impact && (
                      <div className="text-[11px] text-zinc-500 italic border-t border-zinc-800 pt-2 leading-relaxed">{analysis.analysis_detail.impact}</div>
                    )}
                  </div>
                </div>
              )}

              {/* Full Analysis markdown */}
              {analysis.analysis_details && (
                <div>
                  <div className="text-[9px] text-zinc-500 uppercase tracking-wide mb-1">Full Analysis</div>
                  <div className="text-[11px] text-zinc-400 bg-zinc-900 rounded p-3 leading-relaxed whitespace-pre-line"
                    dangerouslySetInnerHTML={{ __html: analysis.analysis_details
                      .replace(/\*\*(.*?)\*\*/g, '<strong class="text-zinc-200">$1</strong>') }}/>
                </div>
              )}

              {/* Trade Hypothesis */}
              {analysis.hypothesis && (
                <div className="bg-blue-500/10 border border-blue-500/30 rounded p-3">
                  <div className="text-[9px] text-blue-400 uppercase tracking-wide mb-1">Trade Hypothesis</div>
                  <div className="text-[11px] text-blue-300 leading-relaxed">{analysis.hypothesis}</div>
                </div>
              )}

              {/* Headlines */}
              {analysis.headlines?.length > 0 && (
                <div>
                  <div className="text-[9px] text-zinc-500 uppercase tracking-wide mb-1">Recent Headlines</div>
                  <div className="space-y-1">
                    {analysis.headlines.map((h, i) => (
                      <div key={i} className="text-[10px] text-zinc-400 bg-zinc-900/60 rounded px-2 py-1.5 leading-snug">· {h}</div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default function App() {
  const [tab, setTab] = useState("scanner");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filtersOn, setFiltersOn] = useState(false);
  const [filterDolVol, setFilterDolVol] = useState(100);
  const [filterADR, setFilterADR] = useState(4);
  const [filterRS, setFilterRS] = useState(50);
  const [filterDist52w, setFilterDist52w] = useState(20);
  const [showFP, setShowFP] = useState(false);
  // eslint-disable-next-line no-unused-vars
  const [lbPerfKey, setLbPerfKey] = useState("perf_1m");
  const [rsSPYKey, setRsSPYKey] = useState("perf_1m");
  const [fetchedAt, setFetchedAt] = useState(null);
  const [countdown, setCountdown] = useState(null);

  const REFRESH_SEC = 5 * 60; // scraper runs every 5 min

  // Countdown ticker — aligned to clock's 5-min boundaries (:00, :05, :10, ...)
  useEffect(() => {
    const tick = () => {
      const msIntoInterval = Date.now() % (REFRESH_SEC * 1000);
      const remaining = Math.ceil((REFRESH_SEC * 1000 - msIntoInterval) / 1000) || REFRESH_SEC;
      setCountdown(remaining);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const r = await fetch(process.env.PUBLIC_URL + "/thematic_data.json?v=" + Date.now());
        if (r.ok) {
          const json = await r.json();
          setFetchedAt(Date.now());
          // Normalize: fill perf_1d from change_pct when scraper left it null
          for (const theme of (json.themes || []))
            for (const sub of (theme.subthemes || []))
              for (const stock of (sub.stocks || []))
                if (stock.perf_1d == null) stock.perf_1d = stock.change_pct ?? null;
          setData(json);
        }
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
      const getRankingRS = (theme) => {
        const name = theme.name.toLowerCase();
        const entry =
          data.finviz_theme_rankings?.find(r => r.name?.toLowerCase() === name) ||
          data.theme_rankings?.find(r => r.name?.toLowerCase() === name);
        if (entry?.rs_score != null) return entry.rs_score;
        const stocks = theme.subthemes.flatMap(s => s.stocks);
        const vals = stocks.map(s => s.rs_52w).filter(v => v != null);
        return vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : 0;
      };
      return getRankingRS(b) - getRankingRS(a);
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
      <div id="app-navbar" className="border-b border-zinc-800/60 bg-zinc-950/80 backdrop-blur-sm sticky top-0 z-20">
        <div className="max-w-[1400px] mx-auto px-4 py-3">
          <div className="flex items-center justify-between mb-2.5">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center"><Activity size={16} className="text-white"/></div>
              <div>
                <h1 className="text-base font-bold tracking-tight">Thematic Scanner</h1>
                <p className="text-[11px] text-zinc-500">美股強勢主題篩選器</p>
              </div>
              {/* Index tickers — left side of header */}
              {data?.market_condition && (() => {
                const { spy, qqq, iwm } = data.market_condition;
                const fmtChg = v => v == null ? null : v > 0
                  ? <span className="text-emerald-400">+{v.toFixed(2)}%</span>
                  : <span className="text-red-400">{v.toFixed(2)}%</span>;
                const statusColor = st => st === "Strong" ? "text-emerald-400" : st === "Weak" || st === "Lagging" ? "text-red-400" : st === "Mediocre" ? "text-yellow-400" : "text-zinc-500";
                const Tag = ({ label, d }) => d ? (
                  <span className="flex items-center gap-1 text-[11px] font-mono">
                    <span className="text-zinc-500">{label}</span>
                    {d.price != null && <span className="text-zinc-300">${d.price.toFixed(2)}</span>}
                    {fmtChg(d.change_pct)}
                    {d.index_status && <span className={statusColor(d.index_status)}>{d.index_status}</span>}
                  </span>
                ) : null;
                return (
                  <div className="hidden lg:flex items-center gap-3 ml-4 pl-4 border-l border-zinc-700/50">
                    <Tag label="QQQ" d={qqq}/>
                    <span className="text-zinc-700">·</span>
                    <Tag label="SPY" d={spy}/>
                    <span className="text-zinc-700">·</span>
                    <Tag label="IWM" d={iwm}/>
                  </div>
                );
              })()}
            </div>
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1 bg-zinc-800/60 border border-zinc-700/50 rounded-lg p-1">
                <button onClick={() => setTab("scanner")} className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${tab === "scanner" ? "bg-blue-500/25 text-blue-300 border border-blue-500/40" : "text-zinc-500 hover:text-zinc-300"}`}>
                  Thematic Scanner
                </button>
                <button onClick={() => setTab("gapper")} className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${tab === "gapper" ? "bg-emerald-500/25 text-emerald-300 border border-emerald-500/40" : "text-zinc-500 hover:text-zinc-300"}`}>
                  Pre-Market Gappers
                </button>
                <button onClick={() => setTab("analyst")} className={`px-3 py-1 text-xs font-medium rounded-md transition-colors flex items-center gap-1 ${tab === "analyst" ? "bg-violet-500/25 text-violet-300 border border-violet-500/40" : "text-zinc-500 hover:text-zinc-300"}`}>
                  <FlaskConical size={11}/> Analyst Cockpit
                </button>
              </div>
              {data && fetchedAt && (
                <div className="text-right leading-tight">
                  <div className="text-[11px] font-medium text-emerald-400">
                    Updated {new Date(fetchedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                  </div>
                  {countdown != null && (
                    <div className="text-[10px] text-zinc-500">
                      Next refresh in <span className="text-zinc-400 font-mono">{countdown >= 60 ? `${Math.floor(countdown/60)}m ${countdown%60}s` : `${countdown}s`}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
          {tab === "scanner" && (
            <>
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-3 text-[11px]">
                <span className="text-zinc-500">{filtered.length} themes</span>
                <span className="text-zinc-600">·</span>
                <span className="text-zinc-500">{totalSubs} sub-themes</span>
                <span className="text-zinc-600">·</span>
                <span className="text-zinc-500">{unique.length} tickers</span>
              </div>
              <div className="flex-1"/>
              <SearchBar data={data} search={search} setSearch={setSearch}/>
              <button onClick={()=>setShowFP(!showFP)} className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-md border transition-colors ${filtersOn?'bg-blue-500/15 border-blue-500/30 text-blue-400':'bg-zinc-800/60 border-zinc-700/50 text-zinc-400'}`}>
                <SlidersHorizontal size={12}/> Filters
              </button>
              <div className="flex items-center gap-1 border border-zinc-700/50 rounded-md overflow-hidden">
                <span className="px-2 text-[10px] text-zinc-600 bg-zinc-800/60">vs SPY</span>
                {RS_SPY_KEYS.map(k => (
                  <button key={k.key} onClick={() => setRsSPYKey(k.key)} className={`px-2 py-1.5 text-[11px] transition-colors ${rsSPYKey === k.key ? 'bg-blue-500/25 text-blue-300' : 'bg-zinc-800/60 text-zinc-500 hover:text-zinc-300'}`}>{k.label}</button>
                ))}
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
            </>
          )}
        </div>
      </div>

      {tab === "analyst" ? <MomentumCockpit/> : tab === "gapper" ? <GapperScanner finvizThemeRankings={data?.finviz_theme_rankings || []} themeRankings={data?.theme_rankings || []}/> : (
        <div className="max-w-[1400px] mx-auto px-4 py-4">
          <div className="flex gap-4 items-stretch mb-2">
            <div className="w-[520px] flex-shrink-0 flex flex-col">
              <VixGauge initialVix={data?.vix}/>
              <MacroNews news={data?.macro_news || []}/>
            </div>
            <div className="flex-1 min-w-0">
              {data && <Leaderboard themeRankings={data.theme_rankings} industryRankings={data.industry_rankings} finvizThemeRankings={data.finviz_theme_rankings} />}
            </div>
          </div>
          <div className="mb-4">
            {data && <CorrelationGuard themes={data.themes}/>}
            {data && <CounterTrendWarning themes={data.themes}/>}
          </div>
          {filtered.length === 0 ? (
            <div className="text-center py-16 text-zinc-500">
              <BarChart3 size={28} className="mx-auto mb-3 opacity-40"/>
              <p className="text-sm">No results</p>
              <button onClick={()=>{setFiltersOn(false);setSearch("");}} className="mt-2 text-xs text-blue-400 hover:underline">Reset</button>
            </div>
          ) : filtered.map((t,i) => <ThemeSection key={t.name+i} theme={t} lbPerfKey={lbPerfKey} spyPerf={data?.spy_benchmarks?.[rsSPYKey]} rsSPYKey={rsSPYKey} isTopTheme={i===0} topADRTickers={topADRTickers} themeRankings={data?.theme_rankings} finvizThemeRankings={data?.finviz_theme_rankings}/>)}
        </div>
      )}
    </div>
  );
}
