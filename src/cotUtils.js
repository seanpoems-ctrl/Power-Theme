// Pure helpers for the COT tab (no React) — kept separate so they can be unit-tested.
//
// A market's `rows` come from cot_builder.py, oldest -> newest:
//   [date, open_interest, ls_long, ls_short, c_long, c_short, ss_long, ss_short]

export const WEEKS_1Y = 52;
export const WEEKS_3Y = 156;
export const MIN_INDEX_WEEKS = 26;       // below this a COT index is too noisy to show
export const CROWDED_HI = 90;
export const CROWDED_LO = 10;

export const GROUPS = [
  { key: "ls", label: "Large Specs",  color: "#6b9bd8", longIdx: 2, shortIdx: 3 },
  { key: "c",  label: "Commercials",  color: "#d9707c", longIdx: 4, shortIdx: 5 },
  { key: "ss", label: "Small Specs",  color: "#e0b94a", longIdx: 6, shortIdx: 7 },
];

/** Net (long - short) series for each trader group, plus dates and open interest. */
export function buildSeries(rows) {
  return {
    dates: rows.map(r => r[0]),
    oi: rows.map(r => r[1]),
    ls: rows.map(r => r[2] - r[3]),
    c: rows.map(r => r[4] - r[5]),
    ss: rows.map(r => r[6] - r[7]),
  };
}

/** [min, max] of values over the trailing `weeks` ending at index i (inclusive). */
export function rangeAt(values, i, weeks) {
  let lo = Infinity, hi = -Infinity;
  for (let k = Math.max(0, i - weeks + 1); k <= i; k++) {
    if (values[k] < lo) lo = values[k];
    if (values[k] > hi) hi = values[k];
  }
  return [lo, hi];
}

/**
 * COT index: where the current net sits inside its trailing range, 0 = most net-short
 * the window has seen, 100 = most net-long. null if the history is too short or flat.
 */
export function cotIndex(values, i, weeks) {
  const n = Math.min(weeks, i + 1);
  if (n < MIN_INDEX_WEEKS) return null;
  const [lo, hi] = rangeAt(values, i, weeks);
  if (hi === lo) return null;
  return ((values[i] - lo) / (hi - lo)) * 100;
}

/** Positioning verdict for the Large Specs 3Y index. */
export function crowdedFlag(idx) {
  if (idx == null) return null;
  if (idx >= CROWDED_HI) return "long";
  if (idx <= CROWDED_LO) return "short";
  return null;
}

/** Everything the scanner table and stat cards need about a market's latest week. */
export function summarize(market) {
  const s = buildSeries(market.rows);
  const i = s.dates.length - 1;
  const out = { symbol: market.symbol, name: market.name, group: market.group, code: market.code,
    date: s.dates[i], oi: s.oi[i], weeks: s.dates.length, shortHistory: s.dates.length < WEEKS_3Y };
  for (const g of GROUPS) {
    const v = s[g.key];
    out[g.key] = {
      net: v[i],
      chg: i > 0 ? v[i] - v[i - 1] : null,
      pctOi: s.oi[i] ? (v[i] / s.oi[i]) * 100 : null,
      idx3: cotIndex(v, i, WEEKS_3Y),
      idx1: cotIndex(v, i, WEEKS_1Y),
    };
  }
  out.flag = crowdedFlag(out.ls.idx3);
  return out;
}

/** 12,345 / (12,345) — negatives in parentheses, as on the reference chart. */
export function fmtNet(n) {
  if (n == null || Number.isNaN(n)) return "—";
  const s = Math.abs(Math.round(n)).toLocaleString("en-US");
  return n < 0 ? `(${s})` : s;
}

/** +1,234 / -1,234 for week-over-week changes. */
export function fmtChg(n) {
  if (n == null) return "—";
  if (n === 0) return "0";
  return `${n > 0 ? "+" : "−"}${Math.abs(Math.round(n)).toLocaleString("en-US")}`;
}

/** "2026-09-22" -> "9/22/2026" (no Date object: avoids timezone off-by-one). */
export function fmtDate(iso) {
  const [y, m, d] = iso.split("-");
  return `${+m}/${+d}/${y}`;
}

/** Add days to an ISO date, returning an ISO date (UTC math, timezone-safe). */
export function addDays(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d + days));
  return t.toISOString().slice(0, 10);
}

/** A "nice" symmetric axis maximum >= x, so ticks at ±max and ±max/2 are round numbers. */
export function niceMax(x) {
  if (!(x > 0)) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(x)));
  for (const m of [1, 1.2, 1.4, 1.6, 2, 2.4, 3, 3.5, 4, 5, 6, 7, 8, 10]) {
    if (m * exp >= x) return m * exp;
  }
  return 10 * exp;
}

/** Compact axis label: 70,000 stays 70,000 (like the reference), negatives in parens. */
export const fmtTick = (n) => (n === 0 ? "-" : fmtNet(n));

/** COT-index colouring (literal class strings so Tailwind keeps them). */
export function idxTone(v) {
  if (v == null) return "text-zinc-600";
  if (v >= CROWDED_HI) return "bg-amber-500/20 text-amber-300 font-semibold";
  if (v >= 75) return "text-amber-300/80";
  if (v <= CROWDED_LO) return "bg-teal-500/20 text-teal-300 font-semibold";
  if (v <= 25) return "text-teal-300/80";
  return "text-zinc-400";
}

// ── Market Situation brief ──────────────────────────────────────────────────
// The headline markets for stocks / crypto / metals / energy / bonds the brief reports on.
export const BRIEF_SYMBOLS = ["ES", "NQ", "RTY", "YM", "VX", "BTC", "ETH", "GC", "SI", "HG", "CL", "NG", "ZT", "ZN", "UB"];

const daysBetween = (a, b) => {
  const ms = (iso) => { const [y, m, d] = iso.split("-").map(Number); return Date.UTC(y, m - 1, d); };
  return Math.round((ms(b) - ms(a)) / 86400000);
};

/**
 * Condense cot_data.json into what the Market Situation brief needs:
 * `prompt` goes to Gemini; `key` / `longs` / `shorts` drive the on-screen strip.
 */
export function buildCotBrief(data, todayIso = new Date().toISOString().slice(0, 10)) {
  const sums = data.markets.map(summarize);
  const r = (x) => (x == null ? null : Math.round(x));
  const key = BRIEF_SYMBOLS.map(sym => sums.find(s => s.symbol === sym)).filter(Boolean);
  const longs = sums.filter(s => s.flag === "long").sort((a, b) => b.ls.idx3 - a.ls.idx3);
  const shorts = sums.filter(s => s.flag === "short").sort((a, b) => a.ls.idx3 - b.ls.idx3);
  const tag = (s) => `${s.name} (${s.symbol}) idx ${r(s.ls.idx3)}`;
  return {
    reportDate: data.report_date, key, longs, shorts,
    prompt: {
      report_date: data.report_date,
      data_age_days: daysBetween(data.report_date, todayIso),
      method: "CFTC Legacy futures-only. Large Specs = hedge funds/CTAs, Commercials = hedgers, Small Specs = retail. " +
              "idx = position of the current net within its 3-year (or 1-year) low-to-high range, 0 = most net short, 100 = most net long. " +
              `Crowded long = Large Specs 3Y idx >= ${CROWDED_HI}; crowded short <= ${CROWDED_LO}.`,
      key_markets: key.map(s => ({
        market: `${s.name} (${s.symbol})`,
        large_specs_net: s.ls.net,
        large_specs_pct_of_oi: s.ls.pctOi == null ? null : +s.ls.pctOi.toFixed(1),
        large_specs_wk_change: s.ls.chg,
        large_specs_idx_3y: r(s.ls.idx3),
        large_specs_idx_1y: r(s.ls.idx1),
        commercials_net: s.c.net,
        commercials_idx_3y: r(s.c.idx3),
        small_specs_net: s.ss.net,
      })),
      crowded_long_all_markets: longs.map(tag),
      crowded_short_all_markets: shorts.map(tag),
    },
  };
}

/** Weekly data: busting the cache once a day (not every call) lets the tab and the brief share one download. */
export const cotDataUrl = () => `${process.env.PUBLIC_URL}/cot_data.json?v=${new Date().toISOString().slice(0, 10)}`;
