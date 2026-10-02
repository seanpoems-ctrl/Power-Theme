import React from "react";
import { buildCotBrief, idxTone, fmtDate, cotDataUrl } from "./cotUtils";

// COT pieces of the Market Situation brief: the loader that feeds Gemini, and the
// compact strip shown under the narrative.

/** Fetch public/cot_data.json and condense it; resolves null if unavailable (brief just omits COT). */
export async function loadCotBrief() {
  try {
    const res = await fetch(cotDataUrl());
    if (!res.ok) return null;
    const data = await res.json();
    if (!Array.isArray(data?.markets) || !data.markets.length) return null;
    return buildCotBrief(data);
  } catch {
    return null;
  }
}

const WEEKDAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const dayName = (iso) => { const [y, m, d] = iso.split("-").map(Number); return WEEKDAY[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]; };

const chipCls = {
  long: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  short: "border-teal-500/40 bg-teal-500/10 text-teal-300",
};

/** "Futures positioning (COT)" strip: headline markets' Large Specs index + what's crowded. */
export function CotBriefStrip({ brief, onOpen }) {
  if (!brief) return null;
  const Chip = ({ s, flag }) => (
    <span className={`px-1.5 py-0.5 rounded border text-[11px] font-mono ${chipCls[flag]}`} title={`${s.name} — Large Specs 3Y COT index ${Math.round(s.ls.idx3)}`}>
      {s.symbol} <span className="opacity-70">{Math.round(s.ls.idx3)}</span>
    </span>
  );
  return (
    <div className="mt-3 pt-3 border-t border-zinc-800/80">
      <div className="flex items-center gap-2 flex-wrap mb-2">
        <span className="text-[11px] font-bold text-zinc-400 uppercase tracking-wider">Futures Positioning · COT</span>
        <span className="text-[10px] text-zinc-600 font-mono">Large Specs 3Y index · as of {dayName(brief.reportDate)} {fmtDate(brief.reportDate)}</span>
        {onOpen && (
          <button onClick={onOpen} className="ml-auto text-[11px] text-blue-400/90 hover:text-blue-300">open COT →</button>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          {brief.key.map(s => (
            <span key={s.symbol} className="flex items-center gap-1 text-[11px] font-mono text-zinc-500" title={`${s.name}: Large Specs 3Y COT index`}>
              {s.symbol}
              <span className={`min-w-[28px] text-center px-1 py-0.5 rounded ${idxTone(s.ls.idx3)}`}>{s.ls.idx3 == null ? "—" : Math.round(s.ls.idx3)}</span>
            </span>
          ))}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] font-semibold tracking-wider text-amber-300">CROWDED LONG</span>
          {brief.longs.length ? brief.longs.map(s => <Chip key={s.symbol} s={s} flag="long" />) : <span className="text-[11px] text-zinc-600">none</span>}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] font-semibold tracking-wider text-teal-300">CROWDED SHORT</span>
          {brief.shorts.length ? brief.shorts.map(s => <Chip key={s.symbol} s={s} flag="short" />) : <span className="text-[11px] text-zinc-600">none</span>}
        </div>
      </div>
    </div>
  );
}
