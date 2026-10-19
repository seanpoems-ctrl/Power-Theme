import React from "react";
import { exposureGuide, CONFIRM_SESSIONS } from "./breadthCycle";
import { REGIMES } from "./BreadthRegime";
import { loadStageAnalysis } from "./stageDefs";
import { _emaSeries, _smaSeries } from "./chartTheme";

// "Market status" panel (Breadth → Breadth Cycle): one place that pairs the CONFIRMED breadth-cycle regime (3 sessions in a row) with QQQ's own
// trend (21 / 50-day EMA, 200-day average) and T2108, and turns them into an exposure guide for a swing trader. The pairing and the
// 21 / 50 / 200 choice come from the backtests in CLAUDE.md ("Market regime vs QQQ"): confirmed Risk-off AND QQQ below its 50-day EMA was the best
// pairing; the 9 / 21-day EMAs were too noisy as market-level exits, so the 21-day only tightens things up. Context, not a trading system.

const TONE = {
  ok:   { fg: "#5fd4b0", bg: "rgba(95,212,176,0.12)", bd: "rgba(95,212,176,0.45)" },
  info: { fg: "#6db0ff", bg: "rgba(109,176,255,0.12)", bd: "rgba(109,176,255,0.45)" },
  warn: { fg: "#e5b567", bg: "rgba(229,181,103,0.12)", bd: "rgba(229,181,103,0.45)" },
  bad:  { fg: "#ff6b8b", bg: "rgba(255,107,139,0.12)", bd: "rgba(255,107,139,0.45)" },
};

const fullDate = iso => new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
const sgn = (v, d = 1) => `${v > 0 ? "+" : ""}${v.toFixed(d)}`;

export default function BreadthStatus({ enriched, hist, selDate }) {
  const [stage, setStage] = React.useState(null);
  React.useEffect(() => { let dead = false; loadStageAnalysis().then(d => { if (!dead) setStage(d); }); return () => { dead = true; }; }, []);

  // QQQ averages on the full QQQ history (so the 200-day is defined), looked up by date
  const qqqAvg = React.useMemo(() => {
    if (!hist?.qqq?.length) return null;
    const c = hist.qqq;
    return { dates: new Map(hist.qqq_dates.map((d, i) => [d, i])), c, e21: _emaSeries(c, 21), e50: _emaSeries(c, 50), s200: _smaSeries(c, 200) };
  }, [hist]);

  const idx = React.useMemo(() => (enriched || []).findIndex(r => r.date === selDate), [enriched, selDate]);
  const row = idx >= 0 ? enriched[idx] : null;
  if (!row || !row.confirmed) return null;

  const qi = qqqAvg?.dates.get(row.date);
  const qqq = qi != null ? { close: qqqAvg.c[qi], e21: qqqAvg.e21[qi], e50: qqqAvg.e50[qi], s200: qqqAvg.s200[qi] } : null;
  const guide = exposureGuide({ confirmed: row.confirmed, t2108: row.t2108, qqq });
  const tone = TONE[guide.tone];
  const reg = REGIMES[row.confirmed];
  const rawReg = REGIMES[row.phase];
  const isLatest = enriched[enriched.length - 1].date === row.date;

  // Stage 2 share of stocks (latest week only — the stage file has no history before its first week)
  let s2 = null;
  if (isLatest && stage?.groups?.All) {
    const g = stage.groups.All, n = g.s2.length - 1;
    const share = i => { const t = g.s1[i] + g.s2[i] + g.s3[i] + g.s4[i]; return t ? (g.s2[i] / t) * 100 : null; };
    s2 = { now: share(n), d4: share(n) != null && share(n - 4) != null ? share(n) - share(n - 4) : null };
  }

  const dist = (price, avg) => (price != null && avg != null && avg > 0 ? (price / avg - 1) * 100 : null);
  const avgs = [["21-day EMA", qqq && dist(qqq.close, qqq.e21)], ["50-day EMA", qqq && dist(qqq.close, qqq.e50)], ["200-day avg", qqq && dist(qqq.close, qqq.s200)]];
  const t = row.t2108;
  const tState = t == null ? null : t < 20 ? ["washed out", "#6db0ff"] : t > 80 ? ["overbought", "#e5b567"] : ["neutral", "#a1a1aa"];

  const tile = "rounded-lg border border-zinc-800 bg-zinc-900 p-3";
  const lab = "text-[10px] text-zinc-500 tracking-wide";

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5" style={{ borderTopColor: tone.fg, borderTopWidth: 2 }}>
      <div className="flex items-start gap-4 flex-wrap">
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-semibold tracking-wider text-zinc-500">MARKET STATUS · {fullDate(row.date)}</div>
          <div className="flex items-center gap-3 flex-wrap mt-1">
            <span className="text-2xl font-bold px-3 py-0.5 rounded-lg border" style={{ color: tone.fg, background: tone.bg, borderColor: tone.bd }}>{guide.label}</span>
            <span className="text-[13px] text-zinc-300 max-w-[640px]">{guide.line}</span>
          </div>
          <div className="flex flex-wrap gap-1.5 mt-2">
            {guide.why.map(w => <span key={w} className="text-[11px] px-2 py-0.5 rounded-full border border-zinc-700 text-zinc-400">{w}</span>)}
          </div>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5 mt-4">
        <div className={tile} title={`A new regime is only shown after ${CONFIRM_SESSIONS} sessions in a row. The day count starts at the first of those sessions.`}>
          <div className={lab}>REGIME (CONFIRMED)</div>
          <div className="text-lg font-bold mt-0.5" style={{ color: reg.color }}>{reg.label}</div>
          <div className="text-[12px] text-zinc-300">day {row.daysInRegime} <span className="text-zinc-600">· {row.confirmed}</span></div>
          <div className="text-[11px] mt-1" style={{ color: row.phase === row.confirmed ? "#71717a" : rawReg.color }}>
            {row.phase === row.confirmed
              ? "Raw daily read agrees"
              : `Raw today: ${rawReg.label}${row.pendingPhase === row.phase ? ` (${row.pendingCount}/${CONFIRM_SESSIONS} to confirm)` : ""}`}
          </div>
        </div>

        <div className={tile}>
          <div className={lab}>BREADTH STRENGTH</div>
          <div className="text-lg font-mono font-bold text-zinc-100 mt-0.5">{row.strength.toFixed(0)} <span className="text-[12px] text-zinc-500 font-normal">/ 100</span></div>
          <div className={`text-[12px] font-mono ${row.change >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{sgn(row.change)} pts in 5 sessions</div>
          <div className="text-[11px] text-zinc-600 mt-1">risk-on at 55+ and not falling</div>
        </div>

        <div className={tile}>
          <div className={lab}>QQQ VS ITS AVERAGES</div>
          {qqq ? (
            <div className="mt-1 space-y-0.5">
              {avgs.map(([name, d]) => (
                <div key={name} className="flex items-baseline justify-between gap-2 text-[12px]">
                  <span className="text-zinc-400">{name}</span>
                  {d == null ? <span className="text-zinc-600">—</span>
                    : <span className={`font-mono font-semibold ${d >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{d >= 0 ? "▲" : "▼"} {sgn(d)}%</span>}
                </div>
              ))}
              <div className="text-[11px] text-zinc-600 pt-0.5">QQQ {qqq.close.toFixed(2)}</div>
            </div>
          ) : <div className="text-[12px] text-zinc-600 mt-1">QQQ data not loaded yet</div>}
        </div>

        <div className={tile}>
          <div className={lab}>T2108</div>
          <div className="text-lg font-mono font-bold text-zinc-100 mt-0.5">{t == null ? "—" : `${t.toFixed(1)}%`}</div>
          {tState && <div className="text-[12px]" style={{ color: tState[1] }}>{tState[0]}</div>}
          <div className="text-[11px] text-zinc-600 mt-1">under 20 = bounce zone</div>
        </div>

        <div className={tile} title="Share of ~2,800 US stocks (market cap ≥ $500M) in Weinstein Stage 2 — from the Stage Analysis card on the Market Breadth tab">
          <div className={lab}>STAGE 2 STOCKS</div>
          {s2?.now != null ? (
            <>
              <div className="text-lg font-mono font-bold text-zinc-100 mt-0.5">{s2.now.toFixed(0)}%</div>
              {s2.d4 != null && <div className={`text-[12px] font-mono ${s2.d4 >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{sgn(s2.d4)} pts in 4 weeks</div>}
              <div className="text-[11px] text-zinc-600 mt-1">weekly trend structure</div>
            </>
          ) : <div className="text-[12px] text-zinc-600 mt-1">{isLatest ? "not loaded yet" : "latest session only"}</div>}
        </div>
      </div>

      <details className="mt-3 group">
        <summary className="text-[11px] text-zinc-500 cursor-pointer hover:text-zinc-300 select-none">How this is decided</summary>
        <div className="mt-2 text-[12px] text-zinc-400 leading-relaxed space-y-1">
          <p><span className="text-zinc-300 font-semibold">Full size</span> — confirmed Risk-on, QQQ above its 21 and 50-day EMAs. <span className="text-zinc-300 font-semibold">Build</span> — confirmed Recovery with the same trend: starter positions, add as it turns Risk-on.</p>
          <p><span className="text-zinc-300 font-semibold">Reduce</span> — Caution, or Risk-on / Recovery while QQQ is under its 21 or 50-day EMA, or Risk-off while QQQ still holds its 50-day. Half size, tighter stops, fewer entries.</p>
          <p><span className="text-zinc-300 font-semibold">Defensive</span> — confirmed Risk-off with QQQ under its 50-day EMA (or the 200-day average). <span className="text-zinc-300 font-semibold">Bounce watch</span> — confirmed Risk-off with T2108 under 20: short-term trades only.</p>
          <p className="text-zinc-500">Why these: over 2009–2026 the regime alone did not beat a plain 200-day average on QQQ, but "Risk-off AND below the 50-day EMA" beat the regime alone in every 1 / 2 / 5 / 8 / 10-year window, and the 9 / 21-day EMAs only added whipsaw at market level — keep them for your stock entries and trailing stops. Confirmation costs two sessions of lag, so the raw reading is shown beside the regime as an early warning. Past results are context, not a forecast.</p>
        </div>
      </details>
    </div>
  );
}
