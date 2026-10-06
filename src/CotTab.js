import React, { useState, useEffect, useLayoutEffect, useMemo, useRef, useCallback } from "react";
import { RefreshCw, Search, Info, ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import {
  groupsFor, WEEKS_1Y, WEEKS_3Y, CROWDED_HI, CROWDED_LO,
  buildSeries, rangeAt, summarize, fmtNet, fmtChg, fmtDate, addDays, niceMax, fmtTick, idxTone, idxToneNeutral, BASIS_NOTE, cotDataUrl,
} from "./cotUtils";

// ─────────────────────────────────────────────────────────────────────────────
// COT tab — CFTC Commitment of Traders positioning (Legacy futures-only; TFF for Treasuries).
// Data: public/cot_data.json, built weekly-ish by cot_builder.py. The CFTC
// publishes Fridays 15:30 ET with Tuesday's positions.
// ─────────────────────────────────────────────────────────────────────────────

const GROUP_ORDER = ["Indices", "Crypto", "Metals", "Energy", "Bonds"];
const WEEK_CHOICES = [26, 51, 104, 156];
const WEEKDAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const LS_KEY = "cot_market";

const weekdayOf = (iso) => {
  const [y, m, d] = iso.split("-").map(Number);
  return WEEKDAY[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
};

const readPref = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const writePref = (k, v) => { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } };

function IdxCell({ v, short, neutral }) {
  return (
    <span
      className={`inline-block min-w-[34px] text-center px-1.5 py-0.5 rounded font-mono text-[12px] ${neutral ? idxToneNeutral(v) : idxTone(v)}`}
      title={short && v != null ? "Less than 3 years of history for this contract — index uses what exists" : undefined}
    >
      {v == null ? "—" : Math.round(v)}{short && v != null ? "*" : ""}
    </span>
  );
}

function FlagBadge({ flag, label, tff }) {
  if (!flag && !label) return <span className="text-zinc-700">—</span>;
  const who = tff ? "Asset managers" : "Large specs";
  return (
    <span className="flex flex-wrap items-center gap-1">
      {flag === "long" && <span title={`${who} 3Y COT index ≥ ${CROWDED_HI}`} className="text-[10px] font-semibold tracking-wide px-1.5 py-0.5 rounded border border-amber-500/40 bg-amber-500/10 text-amber-300 whitespace-nowrap">CROWDED LONG</span>}
      {flag === "short" && <span title={`${who} 3Y COT index ≤ ${CROWDED_LO}`} className="text-[10px] font-semibold tracking-wide px-1.5 py-0.5 rounded border border-teal-500/40 bg-teal-500/10 text-teal-300 whitespace-nowrap">CROWDED SHORT</span>}
      {label && <span title={BASIS_NOTE} className="text-[10px] font-semibold tracking-wide px-1.5 py-0.5 rounded border border-sky-500/40 bg-sky-500/10 text-sky-300 whitespace-nowrap">{label}</span>}
    </span>
  );
}

// ── Market picker (searchable, grouped — like the reference dropdown) ────────
function MarketPicker({ summaries, selected, onSelect }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const boxRef = useRef(null);
  const inputRef = useRef(null);

  const sel = summaries.find(s => s.symbol === selected);

  const flat = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = summaries.filter(s => !needle || s.name.toLowerCase().includes(needle) || s.symbol.toLowerCase().includes(needle) || s.group.toLowerCase().includes(needle));
    return GROUP_ORDER.flatMap(g => list.filter(s => s.group === g));
  }, [summaries, q]);

  useEffect(() => { setCursor(0); }, [q, open]);
  useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const choose = (s) => { onSelect(s.symbol); setOpen(false); setQ(""); };
  const onKey = (e) => {
    if (e.key === "Escape") setOpen(false);
    else if (e.key === "ArrowDown") { e.preventDefault(); setCursor(c => Math.min(flat.length - 1, c + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setCursor(c => Math.max(0, c - 1)); }
    else if (e.key === "Enter" && flat[cursor]) choose(flat[cursor]);
  };

  // keep the keyboard cursor in view
  useEffect(() => {
    if (!open) return;
    boxRef.current?.querySelector('[data-cursor="1"]')?.scrollIntoView({ block: "nearest" });
  }, [cursor, open]);

  let lastGroup = null;
  return (
    <div className="relative" ref={boxRef}>
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 text-left group"
        aria-haspopup="listbox" aria-expanded={open}
      >
        <span className="text-[22px] sm:text-2xl font-semibold text-zinc-100 group-hover:text-white leading-tight">
          {sel ? `${sel.name} — ${sel.symbol}` : "Select market"}
        </span>
        <ChevronDown size={18} className={`text-zinc-500 group-hover:text-zinc-300 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="absolute left-0 top-full mt-2 w-[340px] max-w-[92vw] bg-zinc-900 border border-zinc-700/70 rounded-lg shadow-2xl z-50 overflow-hidden" onKeyDown={onKey}>
          <div className="flex items-center gap-2 px-3 py-2.5 border-b border-zinc-800">
            <Search size={14} className="text-zinc-500" />
            <input
              ref={inputRef} value={q} onChange={e => setQ(e.target.value)}
              placeholder="Search markets…"
              className="flex-1 bg-transparent outline-none text-[13px] text-zinc-200 placeholder-zinc-600"
            />
          </div>
          <div className="max-h-[380px] overflow-y-auto py-1" role="listbox">
            {flat.length === 0 && <div className="px-3 py-4 text-[13px] text-zinc-600">No market matches “{q}”.</div>}
            {flat.map((s, i) => {
              const header = s.group !== lastGroup ? s.group : null;
              lastGroup = s.group;
              const isSel = s.symbol === selected;
              return (
                <React.Fragment key={s.symbol}>
                  {header && <div className="px-3 pt-2.5 pb-1 text-[10px] font-semibold tracking-[0.14em] uppercase text-zinc-500">{header}</div>}
                  <button
                    role="option" aria-selected={isSel}
                    data-cursor={i === cursor ? "1" : "0"}
                    onMouseEnter={() => setCursor(i)}
                    onClick={() => choose(s)}
                    className={`w-full flex items-center justify-between px-3 py-1.5 text-[13px] text-left ${i === cursor ? "bg-amber-500/10" : ""} ${isSel ? "text-amber-400" : "text-zinc-300"}`}
                  >
                    <span>{s.name} <span className="text-zinc-500">({s.symbol})</span></span>
                    {s.flag && <span className={`w-1.5 h-1.5 rounded-full ${s.flag === "long" ? "bg-amber-400" : "bg-teal-400"}`} title={s.flag === "long" ? "Crowded long" : "Crowded short"} />}
                  </button>
                </React.Fragment>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Net-positioning bar chart (SVG, grouped bars + range band + hover) ───────
function CotChart({ market, weeks, show3y, show1y }) {
  const wrapRef = useRef(null);
  const [w, setW] = useState(960);
  const [hover, setHover] = useState(null); // index into the visible window

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const measure = () => setW(Math.max(320, Math.floor(el.clientWidth)));
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const defs = groupsFor(market);
  const s = useMemo(() => buildSeries(market.rows), [market]);
  const n = s.dates.length;
  const nVis = Math.min(weeks, n);
  const start = n - nVis;

  // Trailing 3Y / 1Y range of the primary group's net (Large Specs / Asset Managers), evaluated at each visible week.
  const bands = useMemo(() => {
    const out = [];
    for (let i = start; i < n; i++) out.push({ r3: rangeAt(s.ls, i, WEEKS_3Y), r1: rangeAt(s.ls, i, WEEKS_1Y) });
    return out;
  }, [s, start, n]);

  const H = 400;

  const M = useMemo(() => {
    let maxAbs = 0;
    for (let k = 0; k < nVis; k++) {
      const i = start + k;
      maxAbs = Math.max(maxAbs, Math.abs(s.ls[i]), Math.abs(s.c[i]), Math.abs(s.ss[i]));
      if (show3y) maxAbs = Math.max(maxAbs, Math.abs(bands[k].r3[0]), Math.abs(bands[k].r3[1]));
      if (show1y) maxAbs = Math.max(maxAbs, Math.abs(bands[k].r1[0]), Math.abs(bands[k].r1[1]));
    }
    return niceMax(maxAbs * 1.04);
  }, [s, bands, start, nVis, show3y, show1y]);

  // Left margin grows with the widest y label so big numbers (Treasuries: "(3,000,000)") aren't clipped.
  const m = { l: Math.max(66, fmtTick(-M).length * 6.8 + 18), r: 16, t: 10, b: 66 };
  const plotW = w - m.l - m.r, plotH = H - m.t - m.b;
  const band = plotW / nVis;

  const y = (v) => m.t + plotH / 2 - (v / M) * (plotH / 2);
  const cx = (k) => m.l + band * (k + 0.5);
  const barW = Math.max(1.2, Math.min(9, band * 0.2));
  const barGap = barW > 3 ? 1 : 0.5;

  const bandPoly = (key) => {
    const top = bands.map((b, k) => `${cx(k)},${y(b[key][1])}`);
    const bot = bands.map((b, k) => `${cx(k)},${y(b[key][0])}`).reverse();
    return [...top, ...bot].join(" ");
  };

  const ticks = [-M, -M / 2, 0, M / 2, M];
  const step = Math.max(1, Math.round(nVis / 9));
  const xLabels = [];
  for (let k = nVis - 1; k >= 0; k -= step) xLabels.push(k);

  const onMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const k = Math.floor(((e.clientX - rect.left) / rect.width) * nVis);
    setHover(Math.max(0, Math.min(nVis - 1, k)));
  };

  const hi = hover != null ? start + hover : null;
  const activeKey = show3y ? "r3" : show1y ? "r1" : null;
  const shown = hi ?? n - 1;

  const legend = defs.map(g => ({ ...g, value: s[g.key][shown] }));
  const tipLeft = hover != null && cx(hover) < w * 0.62;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 px-1 pb-2">
        {legend.map(g => (
          <span key={g.key} className="flex items-center gap-2 text-[13px]">
            <span className="inline-block w-3 h-[3px] rounded" style={{ background: g.color }} />
            <span className="text-zinc-300">{g.label}</span>
            <span className="font-mono text-[12px] text-zinc-500">{g.value < 0 ? `(${fmtNet(g.value).slice(1, -1)})` : fmtNet(g.value)}</span>
          </span>
        ))}
        {hi != null && <span className="text-[11px] text-zinc-600 font-mono ml-auto">{fmtDate(s.dates[hi])}</span>}
      </div>

      <div ref={wrapRef} className="relative select-none">
        <svg width={w} height={H} role="img" aria-label={`${market.name} net positioning by trader group`} className="block">
          {/* gridlines + y labels */}
          {ticks.map(t => (
            <g key={t}>
              <line x1={m.l} x2={w - m.r} y1={y(t)} y2={y(t)} stroke={t === 0 ? "#52525b" : "#27272a"} strokeWidth={1} strokeDasharray={t === 0 ? "2 3" : undefined} />
              <text x={m.l - 8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="#71717a" fontFamily="ui-monospace, monospace">{fmtTick(t)}</text>
            </g>
          ))}

          {/* range bands of the primary group's net (3Y lighter underneath, 1Y over it) */}
          {show3y && <polygon points={bandPoly("r3")} fill="rgba(107,155,216,0.07)" stroke="rgba(107,155,216,0.18)" strokeWidth={1} />}
          {show1y && <polygon points={bandPoly("r1")} fill="rgba(107,155,216,0.12)" stroke="rgba(107,155,216,0.25)" strokeWidth={1} />}

          {/* bars */}
          {Array.from({ length: nVis }, (_, k) => {
            const i = start + k;
            return defs.map((g, j) => {
              const v = s[g.key][i];
              if (!v) return null;
              const x = cx(k) + (j - 1) * (barW + barGap) - barW / 2;
              const y0 = y(0), y1 = y(v);
              return <rect key={`${k}-${g.key}`} x={x} y={Math.min(y0, y1)} width={barW} height={Math.max(0.5, Math.abs(y1 - y0))} fill={g.color} opacity={hover == null || hover === k ? 0.95 : 0.55} rx={barW > 4 ? 0.5 : 0} />;
            });
          })}

          {/* hover crosshair + range edge guides */}
          {hover != null && (
            <>
              {activeKey && [0, 1].map(e => (
                <line key={e} x1={m.l} x2={w - m.r} y1={y(bands[hover][activeKey][e])} y2={y(bands[hover][activeKey][e])} stroke="#71717a" strokeWidth={1} strokeDasharray="3 4" />
              ))}
              <line x1={cx(hover)} x2={cx(hover)} y1={m.t} y2={H - m.b} stroke="#e4e4e7" strokeWidth={1} />
            </>
          )}

          {/* x labels */}
          {xLabels.map(k => (
            <text key={k} transform={`translate(${cx(k) + 4},${H - m.b + 12}) rotate(-45)`} textAnchor="end" fontSize="11" fill="#71717a" fontFamily="ui-monospace, monospace">{fmtDate(s.dates[start + k])}</text>
          ))}

          {/* pointer capture (mouse + touch) */}
          <rect x={m.l} y={m.t} width={plotW} height={plotH} fill="transparent" style={{ touchAction: "pan-y", cursor: "crosshair" }}
            onPointerMove={onMove} onPointerDown={onMove} onPointerLeave={() => setHover(null)} />
        </svg>

        {hover != null && (
          <div
            className="absolute z-10 pointer-events-none bg-zinc-900/95 border border-zinc-700/70 rounded-md shadow-xl px-3 py-2 text-[12px] font-mono min-w-[190px]"
            style={{ top: 28, ...(tipLeft ? { left: cx(hover) + 14 } : { right: w - cx(hover) + 14 }) }}
          >
            <div className="text-zinc-500 mb-1.5">{fmtDate(s.dates[hi])}</div>
            {[["3Y", bands[hover].r3], ["1Y", bands[hover].r1]].map(([lab, r]) => (
              <div key={lab} className="flex justify-between gap-4 text-zinc-300">
                <span className="text-zinc-600" title={`Range of ${defs[0].label} net`}>{lab} range</span>
                <span>{fmtNet(r[0])} → {fmtNet(r[1])}</span>
              </div>
            ))}
            <div className="mt-1.5 pt-1.5 border-t border-zinc-800 space-y-0.5">
              {defs.map(g => (
                <div key={g.key} className="flex justify-between gap-4" style={{ color: g.color }}>
                  <span className="text-zinc-600">{g.label}</span>
                  <span className="font-semibold">{fmtNet(s[g.key][hi])}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── COT-index gauge ──────────────────────────────────────────────────────────
function IndexBar({ label, value, short, neutral }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-6 text-[10px] font-mono text-zinc-500">{label}</span>
      <div className="relative flex-1 h-1.5 rounded-full bg-zinc-800">
        <div className={`absolute inset-y-0 left-0 rounded-l-full ${neutral ? "bg-sky-500/25" : "bg-teal-500/25"}`} style={{ width: `${CROWDED_LO}%` }} />
        <div className={`absolute inset-y-0 right-0 rounded-r-full ${neutral ? "bg-sky-500/25" : "bg-amber-500/25"}`} style={{ width: `${100 - CROWDED_HI}%` }} />
        {value != null && <div className="absolute top-1/2 w-2.5 h-2.5 rounded-full bg-zinc-100 border border-zinc-900 -translate-x-1/2 -translate-y-1/2" style={{ left: `${value}%` }} />}
      </div>
      <IdxCell v={value} short={short} neutral={neutral} />
    </div>
  );
}

function verdict(key, idx, sum) {
  if (idx == null) return "Not enough history for a COT index yet.";
  if (sum?.tff) {
    if (key === "ls") {
      if (idx >= CROWDED_HI) return "Crowded long — asset managers (real money) hold near their most duration in 3 years. Little buying power left; exposed to a yield spike.";
      if (idx <= CROWDED_LO) return "Crowded short — asset managers are near their most underweight duration in 3 years. Short-covering fuel if yields fall.";
      return "Mid-range — real-money duration positioning isn't stretched.";
    }
    if (key === "c") return `${BASIS_NOTE}${sum.basisLabel ? ` Currently: ${sum.basisLabel.toLowerCase()}.` : ""}`;
    return "Dealers take the other side of asset-manager and leveraged-fund flows — a balancing position, not a directional signal.";
  }
  if (key === "ls") {
    if (idx >= CROWDED_HI) return "Crowded long — specs are near their most bullish in 3 years. Little buying power left; the position is the risk if the market turns.";
    if (idx <= CROWDED_LO) return "Crowded short — specs are near their most bearish in 3 years. Short-covering fuel if price firms.";
    return "Mid-range — no positioning extreme in the trend-following group.";
  }
  if (key === "c") {
    if (idx >= CROWDED_HI) return "Hedgers at a 3-year net-long extreme — classically a contrarian-bullish setup.";
    if (idx <= CROWDED_LO) return "Hedgers at a 3-year net-short extreme — classically a contrarian-bearish setup (often into price highs).";
    return "Mid-range hedger positioning.";
  }
  if (idx >= CROWDED_HI) return "Retail at a 3-year net-long extreme — a late-trend, contrarian-bearish tell.";
  if (idx <= CROWDED_LO) return "Retail at a 3-year net-short extreme — a washed-out, contrarian-bullish tell.";
  return "Mid-range retail positioning.";
}

function StatCards({ sum }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
      {groupsFor(sum).map(g => {
        const x = sum[g.key];
        return (
          <div key={g.key} className="bg-zinc-900/50 border border-zinc-800 rounded-lg p-3.5">
            <div className="flex items-center gap-2 text-[12px] text-zinc-400">
              <span className="inline-block w-3 h-[3px] rounded" style={{ background: g.color }} />
              {g.label}
            </div>
            <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
              <span className="text-2xl font-semibold font-mono" style={{ color: g.color }}>{fmtNet(x.net)}</span>
              <span className="text-[12px] font-mono text-zinc-500 whitespace-nowrap">
                {x.pctOi != null && <>{x.pctOi.toFixed(1)}% of OI · </>}
                <span className={x.chg > 0 ? "text-emerald-400/80" : x.chg < 0 ? "text-rose-400/80" : ""}>{fmtChg(x.chg)} wk</span>
              </span>
            </div>
            <div className="mt-3 space-y-1.5">
              <IndexBar label="3Y" value={x.idx3} short={sum.shortHistory} neutral={sum.tff && g.key === "c"} />
              <IndexBar label="1Y" value={x.idx1} neutral={sum.tff && g.key === "c"} />
            </div>
            <p className="mt-3 text-[12px] leading-snug text-zinc-500">{verdict(g.key, x.idx3, sum)}</p>
          </div>
        );
      })}
    </div>
  );
}

// ── Cross-market scanner table ───────────────────────────────────────────────
const SORTS = {
  extreme: s => (s.ls.idx3 == null ? -1 : Math.abs(s.ls.idx3 - 50)),
  symbol: s => s.symbol,
  group: s => GROUP_ORDER.indexOf(s.group),
  "ls.net": s => s.ls.net, "ls.pctOi": s => s.ls.pctOi, "ls.chg": s => s.ls.chg,
  "ls.idx3": s => s.ls.idx3, "ls.idx1": s => s.ls.idx1,
  "c.net": s => s.c.net, "c.idx3": s => s.c.idx3,
  "ss.net": s => s.ss.net, "ss.idx3": s => s.ss.idx3,
};

function Scanner({ summaries, selected, onPick, kicker, title, chips, footnote }) {
  const defs = groupsFor(summaries[0]);
  const [groupFilter, setGroupFilter] = useState("All");
  const [crowdedOnly, setCrowdedOnly] = useState(false);
  const [sort, setSort] = useState({ key: "extreme", dir: -1 });

  const rows = useMemo(() => {
    const get = SORTS[sort.key];
    return summaries
      .filter(s => (groupFilter === "All" || s.group === groupFilter) && (!crowdedOnly || s.flag || s.basisLabel))
      .sort((a, b) => {
        const va = get(a), vb = get(b);
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        const c = typeof va === "string" ? va.localeCompare(vb) : va - vb;
        return c * sort.dir;
      });
  }, [summaries, groupFilter, crowdedOnly, sort]);

  const th = (key, label, extra = "") => (
    <th
      onClick={() => setSort(p => ({ key, dir: p.key === key ? -p.dir : (key === "symbol" || key === "group" ? 1 : -1) }))}
      className={`px-2 py-1.5 font-medium cursor-pointer hover:text-zinc-200 whitespace-nowrap ${extra}`}
    >
      {label}{sort.key === key ? (sort.dir === -1 ? " ↓" : " ↑") : ""}
    </th>
  );
  const num = "px-2 py-1.5 text-right font-mono text-[12px]";

  return (
    <div className="bg-zinc-900/40 border border-zinc-800 rounded-lg">
      <div className="flex flex-wrap items-center gap-2 px-4 pt-3.5 pb-2">
        <div>
          <div className="text-[10px] tracking-[0.18em] font-mono text-zinc-500 uppercase">{kicker}</div>
          <div className="text-[15px] font-semibold text-zinc-200">{title}</div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 ml-auto">
          {(chips ? ["All", ...chips] : []).map(g => (
            <button key={g} onClick={() => setGroupFilter(g)}
              className={`px-2.5 py-1 text-[12px] rounded-md border transition-colors ${groupFilter === g ? "bg-blue-500/15 border-blue-500/30 text-blue-300" : "bg-zinc-800/50 border-zinc-700/50 text-zinc-400 hover:text-zinc-200"}`}>
              {g}
            </button>
          ))}
          <label className="flex items-center gap-1.5 pl-2 text-[12px] text-zinc-400 cursor-pointer">
            <input type="checkbox" checked={crowdedOnly} onChange={e => setCrowdedOnly(e.target.checked)} className="rounded" />
            Extremes only
          </label>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[13px] min-w-[980px]">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider text-zinc-600 border-b border-zinc-800/60">
              <th colSpan={2} />
              <th colSpan={5} className="px-2 pt-1 text-center" style={{ color: defs[0].color }}>{defs[0].label}</th>
              <th colSpan={2} className="px-2 pt-1 text-center border-l border-zinc-800/60" style={{ color: defs[1].color }}>{defs[1].label}</th>
              <th colSpan={2} className="px-2 pt-1 text-center border-l border-zinc-800/60" style={{ color: defs[2].color }}>{defs[2].label}</th>
              <th />
            </tr>
            <tr className="text-[11px] text-zinc-500 border-b border-zinc-800">
              {th("symbol", "Market", "text-left")}
              {th("group", "Group", "text-left")}
              {th("ls.net", "Net", "text-right")}
              {th("ls.pctOi", "% OI", "text-right")}
              {th("ls.chg", "Δ wk", "text-right")}
              {th("ls.idx3", "3Y idx", "text-center")}
              {th("ls.idx1", "1Y idx", "text-center")}
              {th("c.net", "Net", "text-right border-l border-zinc-800/60")}
              {th("c.idx3", "3Y idx", "text-center")}
              {th("ss.net", "Net", "text-right border-l border-zinc-800/60")}
              {th("ss.idx3", "3Y idx", "text-center")}
              {th("extreme", "Signal", "text-left")}
            </tr>
          </thead>
          <tbody>
            {rows.map(s => (
              <tr key={s.symbol} onClick={() => onPick(s.symbol)}
                className={`cursor-pointer border-b border-zinc-800/40 hover:bg-zinc-800/40 ${s.symbol === selected ? "bg-blue-500/10" : ""}`}>
                <td className="px-2 py-1.5 whitespace-nowrap"><span className="font-semibold text-zinc-200">{s.symbol}</span> <span className="text-zinc-500">{s.name}</span></td>
                <td className="px-2 py-1.5 text-[12px] text-zinc-500">{s.group}</td>
                <td className={`${num} text-zinc-300`}>{fmtNet(s.ls.net)}</td>
                <td className={`${num} text-zinc-500`}>{s.ls.pctOi == null ? "—" : `${s.ls.pctOi.toFixed(1)}%`}</td>
                <td className={`${num} ${s.ls.chg > 0 ? "text-emerald-400/80" : s.ls.chg < 0 ? "text-rose-400/80" : "text-zinc-500"}`}>{fmtChg(s.ls.chg)}</td>
                <td className="px-2 py-1.5 text-center"><IdxCell v={s.ls.idx3} short={s.shortHistory} /></td>
                <td className="px-2 py-1.5 text-center"><IdxCell v={s.ls.idx1} /></td>
                <td className={`${num} text-zinc-300 border-l border-zinc-800/60`}>{fmtNet(s.c.net)}</td>
                <td className="px-2 py-1.5 text-center"><IdxCell v={s.c.idx3} short={s.shortHistory} neutral={s.tff} /></td>
                <td className={`${num} text-zinc-300 border-l border-zinc-800/60`}>{fmtNet(s.ss.net)}</td>
                <td className="px-2 py-1.5 text-center"><IdxCell v={s.ss.idx3} short={s.shortHistory} /></td>
                <td className="px-2 py-1.5"><FlagBadge flag={s.flag} label={s.basisLabel} tff={s.tff} /></td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={12} className="px-4 py-6 text-center text-[13px] text-zinc-600">Nothing is at a positioning extreme in this group right now.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="px-4 py-2.5 text-[11px] text-zinc-600 leading-relaxed">{footnote}</p>
    </div>
  );
}

// ── Crowded-now strip ────────────────────────────────────────────────────────
function CrowdedStrip({ summaries, onPick, data }) {
  const longs = summaries.filter(s => s.flag === "long").sort((a, b) => b.ls.idx3 - a.ls.idx3);
  const shorts = summaries.filter(s => s.flag === "short").sort((a, b) => a.ls.idx3 - b.ls.idx3);
  const chip = (s, cls) => (
    <button key={s.symbol} onClick={() => onPick(s.symbol)} className={`px-2 py-0.5 rounded border text-[12px] font-mono ${cls}`} title={`${s.name} — ${s.tff ? "Asset Managers" : "Large Specs"} 3Y index ${Math.round(s.ls.idx3)}`}>
      {s.symbol}{s.tff ? " AM" : ""} <span className="opacity-70">{Math.round(s.ls.idx3)}</span>
    </button>
  );
  const none = <span className="text-[12px] text-zinc-600">none</span>;
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 bg-zinc-900/40 border border-zinc-800 rounded-lg px-4 py-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] font-semibold tracking-wider text-amber-300 mr-1">CROWDED LONG</span>
        {longs.length ? longs.map(s => chip(s, "border-amber-500/40 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20")) : none}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] font-semibold tracking-wider text-teal-300 mr-1">CROWDED SHORT</span>
        {shorts.length ? shorts.map(s => chip(s, "border-teal-500/40 bg-teal-500/10 text-teal-300 hover:bg-teal-500/20")) : none}
      </div>
      {summaries.some(s => s.basisLabel) && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-semibold tracking-wider text-sky-300 mr-1" title={BASIS_NOTE}>TREASURY BASIS</span>
          {summaries.filter(s => s.basisLabel).map(s => (
            <button key={s.symbol} onClick={() => onPick(s.symbol)} title={`${s.name} — ${BASIS_NOTE}`}
              className="px-2 py-0.5 rounded border border-sky-500/40 bg-sky-500/10 text-sky-300 hover:bg-sky-500/20 text-[12px] font-mono">
              {s.symbol} <span className="opacity-70">{s.basisLabel.toLowerCase()}</span>
            </button>
          ))}
        </div>
      )}
      <div className="ml-auto text-[11px] font-mono text-zinc-500 text-right">
        Positions as of {weekdayOf(data.report_date)} {fmtDate(data.report_date)} · CFTC releases Fri 3:30 PM ET · next ≈ {fmtDate(addDays(data.report_date, 10))}
      </div>
    </div>
  );
}

// ── Tab ──────────────────────────────────────────────────────────────────────
export default function CotTab() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(() => readPref(LS_KEY, "NQ"));
  const [weeks, setWeeks] = useState(() => +readPref("cot_weeks", "51") || 51);
  const [show3y, setShow3y] = useState(() => readPref("cot_3y", "0") === "1");
  const [show1y, setShow1y] = useState(() => readPref("cot_1y", "1") === "1");
  const [showInfo, setShowInfo] = useState(false);
  const chartRef = useRef(null);

  useEffect(() => {
    let alive = true;
    fetch(cotDataUrl())
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(d => { if (alive) setData(d); })
      .catch(e => { if (alive) setError(e.message || "failed"); });
    return () => { alive = false; };
  }, []);

  const summaries = useMemo(() => (data ? data.markets.map(summarize) : []), [data]);
  const market = useMemo(() => {
    if (!data) return null;
    return data.markets.find(m => m.symbol === selected) || data.markets.find(m => m.symbol === "NQ") || data.markets[0];
  }, [data, selected]);
  const sum = useMemo(() => summaries.find(s => s.symbol === market?.symbol), [summaries, market]);

  const pick = useCallback((sym) => {
    setSelected(sym); writePref(LS_KEY, sym);
    chartRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  // Previous / next market in the picker's order (grouped), wrapping at the ends. No scrolling: the chart is already in view.
  const ordered = useMemo(() => GROUP_ORDER.flatMap(g => summaries.filter(s => s.group === g)), [summaries]);
  const at = ordered.findIndex(s => s.symbol === market?.symbol);
  const neighbour = (dir) => (ordered.length > 1 && at >= 0 ? ordered[(at + dir + ordered.length) % ordered.length] : null);
  const prevMarket = neighbour(-1), nextMarket = neighbour(1);
  const step = useCallback((dir) => {
    if (ordered.length < 2 || at < 0) return;
    const sym = ordered[(at + dir + ordered.length) % ordered.length].symbol;
    setSelected(sym); writePref(LS_KEY, sym);
  }, [ordered, at]);

  // ← / → step through markets, unless the user is typing or using a modifier shortcut.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      const t = e.target;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      step(e.key === "ArrowLeft" ? -1 : 1);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [step]);

  if (error) {
    return (
      <div className="max-w-[1560px] mx-auto px-4 py-16 text-center">
        <p className="text-zinc-300">Couldn’t load COT data ({error}).</p>
        <p className="text-[13px] text-zinc-600 mt-1">Run <code className="text-zinc-400">python cot_builder.py</code> to generate <code className="text-zinc-400">public/cot_data.json</code>.</p>
      </div>
    );
  }
  if (!data || !market || !sum) {
    return <div className="flex items-center justify-center py-24 text-zinc-500 gap-2"><RefreshCw size={18} className="animate-spin" /> Loading COT data…</div>;
  }

  const weeksShown = Math.min(weeks, market.rows.length);

  return (
    <div className="max-w-[1560px] mx-auto px-4 pt-3 pb-8 flex flex-col gap-3">
      <CrowdedStrip summaries={summaries} onPick={pick} data={data} />

      <div ref={chartRef} className="bg-zinc-900/40 border border-zinc-800 rounded-lg p-4 scroll-mt-24">
        <div className="text-[10px] tracking-[0.18em] font-mono text-zinc-500 uppercase">CFTC · Commitment of Traders</div>
        <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-2 pb-3 border-b border-zinc-800/80">
          <div className="flex flex-wrap items-center gap-x-2">
            <MarketPicker summaries={summaries} selected={market.symbol} onSelect={pick} />
            <span className="text-[22px] sm:text-2xl text-zinc-500">· Net Positioning</span>
            <div className="flex items-center gap-1 ml-1">
              <button
                onClick={() => step(-1)} disabled={!prevMarket}
                title={prevMarket ? `Previous market: ${prevMarket.name} (${prevMarket.symbol})  ←` : "Previous market"} aria-label="Previous market"
                className="p-1.5 rounded-md border border-zinc-700/60 bg-zinc-800/50 text-zinc-400 hover:text-zinc-100 hover:bg-zinc-700/60 disabled:opacity-30 transition-colors"
              ><ChevronLeft size={16} /></button>
              <button
                onClick={() => step(1)} disabled={!nextMarket}
                title={nextMarket ? `Next market: ${nextMarket.name} (${nextMarket.symbol})  →` : "Next market"} aria-label="Next market"
                className="p-1.5 rounded-md border border-zinc-700/60 bg-zinc-800/50 text-zinc-400 hover:text-zinc-100 hover:bg-zinc-700/60 disabled:opacity-30 transition-colors"
              ><ChevronRight size={16} /></button>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-4 text-[12px] text-zinc-400 font-mono">
            <select
              value={weeks}
              onChange={e => { setWeeks(+e.target.value); writePref("cot_weeks", e.target.value); }}
              className="bg-transparent text-zinc-400 outline-none cursor-pointer hover:text-zinc-200"
              aria-label="Weeks shown"
            >
              {WEEK_CHOICES.map(wk => <option key={wk} value={wk} className="bg-zinc-900">{wk} weeks</option>)}
            </select>
            <label className="flex items-center gap-1.5 cursor-pointer hover:text-zinc-200">
              <input type="checkbox" checked={show3y} onChange={e => { setShow3y(e.target.checked); writePref("cot_3y", e.target.checked ? "1" : "0"); }} />
              3yr range
            </label>
            <label className="flex items-center gap-1.5 cursor-pointer hover:text-zinc-200">
              <input type="checkbox" checked={show1y} onChange={e => { setShow1y(e.target.checked); writePref("cot_1y", e.target.checked ? "1" : "0"); }} />
              1yr range
            </label>
            <button onClick={() => setShowInfo(v => !v)} className={`hover:text-zinc-200 ${showInfo ? "text-blue-400" : ""}`} aria-label="About this chart" aria-expanded={showInfo}>
              <Info size={16} />
            </button>
          </div>
        </div>

        {showInfo && (
          <div className="mt-3 text-[12px] leading-relaxed text-zinc-400 bg-zinc-950/60 border border-zinc-800 rounded-md p-3 space-y-1.5">
            {sum.tff ? (
              <>
                <p><b style={{ color: groupsFor(sum)[0].color }}>Asset Managers</b> — real-money institutions (pensions, insurers, mutual funds). Their net is the directional duration view; this is the group whose crowding matters.</p>
                <p><b style={{ color: groupsFor(sum)[1].color }}>Leveraged Funds</b> — hedge funds. Their Treasury shorts are mostly the short leg of hedged cash-futures basis trades, not a bet on higher yields: a very large short is a big trade that can unwind.</p>
                <p><b style={{ color: groupsFor(sum)[2].color }}>Dealers</b> — banks and primary dealers, the counterparty to the other two.</p>
                <p>Treasury futures: net long = positioned for lower yields. Bars are net positions (long − short, contracts). The shaded band is the lowest → highest Asset Managers net over the trailing 3 years / 1 year at each date. Source: CFTC Traders in Financial Futures (TFF), futures-only — positions as of Tuesday, published Friday.</p>
              </>
            ) : (
              <>
                <p><b style={{ color: groupsFor(sum)[0].color }}>Large Specs</b> — non-commercial reportable traders (hedge funds, CTAs). Trend-followers; the group whose crowding matters most.</p>
                <p><b style={{ color: groupsFor(sum)[1].color }}>Commercials</b> — hedgers (producers, dealers). Usually on the other side of the specs.</p>
                <p><b style={{ color: groupsFor(sum)[2].color }}>Small Specs</b> — non-reportable positions (retail).</p>
                <p>Bars are net positions (long − short, contracts). The shaded band is the lowest → highest Large Specs net over the trailing 3 years / 1 year at each date; the tooltip shows both ranges. Source: CFTC Legacy Futures-Only report — positions as of Tuesday, published Friday.</p>
              </>
            )}
          </div>
        )}

        <div className="mt-3 bg-zinc-950/50 border border-zinc-800/80 rounded-md p-3">
          <CotChart market={market} weeks={weeksShown} show3y={show3y} show1y={show1y} />
        </div>
      </div>

      <StatCards sum={sum} />
      <Scanner
        summaries={summaries.filter(s => !s.tff)} selected={market.symbol} onPick={pick}
        kicker="CFTC · Crowding scanner" title="Where positioning is stretched" chips={GROUP_ORDER.filter(g => g !== "Bonds")}
        footnote={`COT index = where the current net sits between its lowest (0) and highest (100) over the window. Crowded = Large Specs 3Y index ≥ ${CROWDED_HI} or ≤ ${CROWDED_LO}. Positioning is context, not a timing signal — crowded markets can stay crowded while a trend runs. * = under 3 years of history.`}
      />
      {summaries.some(s => s.tff) && (
        <Scanner
          summaries={summaries.filter(s => s.tff)} selected={market.symbol} onPick={pick}
          kicker="CFTC · TFF · Treasuries" title="Treasury futures — real-money duration vs. the basis trade" chips={null}
          footnote={`Treasuries use the Traders in Financial Futures report, which separates real-money Asset Managers (crowded = 3Y index ≥ ${CROWDED_HI} or ≤ ${CROWDED_LO}) from Leveraged Funds, whose shorts are mostly hedged basis trades. The blue label tracks the size of that basis trade, not a directional view.`}
        />
      )}
    </div>
  );
}
