// "What tended to happen next?" — an analog study for the Breadth Cycle page.
//
// For a selected day, find earlier days that LOOKED like it, then report how the S&P 500 did over the following 1 / 3 / 6 / 12 months.
// Data: public/breadth_long_history.json (Stockbee sheet 2009→, S&P 500 from Yahoo). Everything below is our own method, not a published one:
//
//   Features (9, each z-scored over the eligible history):
//     breadth (60%) — daily ±4% up-share, 5-day ratio score, 10-day ratio score, quarter ±25% up-share, 34-day ±13% up-share
//     S&P 500 (40%) — 21-day return, 63-day return, distance from the 200-day average, 20-day realized volatility
//   Eligible = earlier days whose forward outcome was COMPLETE on the selected date (no look-ahead), with full feature history.
//   Similarity: Euclidean distance on the weighted z-scores; "core" similar days = closest 15%. Weight = exp(-0.5 (d/h)^2) with h =
//   the 15th-percentile distance.
//   Overlap: neighbours inside the horizon share one forward period, so each day's weight is divided by the weight of its neighbours
//   (w' = w^2 / sum of w within +/-H sessions): one persistent episode cannot dominate. Effective episodes = Kish n of w'.
//   Outcomes: Strong upside >= +thr, Meaningful downside <= -thr, otherwise Range / modest move (thr = 5% / 10% / 15% / 20% for 1M / 3M / 6M / 1Y).
//   Historical frequencies, not forecasts.

export const HORIZONS = [
  { key: "1M", label: "1 Month",  sessions: 21,  thr: 0.05 },
  { key: "3M", label: "3 Months", sessions: 63,  thr: 0.10 },
  { key: "6M", label: "6 Months", sessions: 126, thr: 0.15 },
  { key: "1Y", label: "1 Year",   sessions: 252, thr: 0.20 },
];
export const FEATURE_WEIGHTS = [0.12, 0.12, 0.12, 0.12, 0.12, 0.10, 0.10, 0.10, 0.10];
const CORE_QUANTILE = 0.15;
const MIN_ELIGIBLE = 250;

const share = (a, b) => (a + b) > 0 ? (a / (a + b)) * 100 : 50;
const ratioScore = (x) => x > 0 ? Math.max(0, Math.min(100, 50 + 50 * Math.log2(x))) : 0;

// h = parsed breadth_long_history.json -> dataset with per-day features (null where history is too short).
export function buildDataset(h) {
  const dates = h.dates, c = h.cols;
  const spxIndex = new Map(h.spx_dates.map((d, j) => [d, j]));
  const spx = h.spx;
  const n = dates.length;
  const cum = (arr) => { const o = [0]; for (let i = 0; i < arr.length; i++) o.push(o[i] + (arr[i] || 0)); return o; };
  const cu = cum(c.up_4_pct), cd = cum(c.down_4_pct);
  const win = (cs, i, k) => cs[i + 1] - cs[i + 1 - k];
  // Prefix sums for the 200-day average and realized volatility on the S&P series.
  const sc = [0]; for (let j = 0; j < spx.length; j++) sc.push(sc[j] + spx[j]);
  const lr = [0]; for (let j = 1; j < spx.length; j++) lr.push(Math.log(spx[j] / spx[j - 1]));
  const spxJ = new Array(n), feat = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const j = spxIndex.get(dates[i]);
    spxJ[i] = j ?? null;
    if (j == null || j < 200 || i < 9) continue;
    const r5 = win(cu, i, 5) / Math.max(win(cd, i, 5), 1e-9), r10 = win(cu, i, 10) / Math.max(win(cd, i, 10), 1e-9);
    let m = 0, m2 = 0;
    for (let k = j - 19; k <= j; k++) { m += lr[k]; m2 += lr[k] * lr[k]; }
    m /= 20; const vol = Math.sqrt(Math.max(m2 / 20 - m * m, 0));
    feat[i] = [
      share(c.up_4_pct[i], c.down_4_pct[i]), ratioScore(r5), ratioScore(r10),
      share(c.up_25_q[i], c.down_25_q[i]), share(c.up_13_34d[i], c.down_13_34d[i]),
      spx[j] / spx[j - 21] - 1, spx[j] / spx[j - 63] - 1,
      spx[j] / ((sc[j + 1] - sc[j - 199]) / 200) - 1, vol,
    ];
  }
  return { dates, feat, spxJ, spx };
}

function wQuantile(vals, ws, q) {          // weighted quantile (vals paired with ws)
  const idx = vals.map((_, i) => i).sort((a, b) => vals[a] - vals[b]);
  const tot = ws.reduce((a, b) => a + b, 0);
  let acc = 0;
  for (const i of idx) { acc += ws[i]; if (acc >= q * tot) return vals[i]; }
  return vals[idx[idx.length - 1]];
}
function quantile(sortedAsc, q) { return sortedAsc[Math.min(sortedAsc.length - 1, Math.floor(q * sortedAsc.length))]; }

// Odds for one horizon on dataset day `selIdx`. Returns null when there is not enough eligible history.
export function scenarioOdds(ds, selIdx, horizon) {
  const { feat, spxJ, spx } = ds;
  const jSel = spxJ[selIdx];
  const fSel = feat[selIdx];
  if (jSel == null || !fSel) return null;
  const H = horizon.sessions;
  const elig = [];
  for (let i = 0; i < selIdx; i++) {
    if (!feat[i] || spxJ[i] == null) continue;
    if (spxJ[i] + H > jSel) continue;                    // outcome not complete by the selected date
    elig.push(i);
  }
  if (elig.length < MIN_ELIGIBLE) return null;
  const F = FEATURE_WEIGHTS.length;
  const mean = new Array(F).fill(0), sd = new Array(F).fill(0);
  for (const i of elig) for (let k = 0; k < F; k++) mean[k] += feat[i][k];
  for (let k = 0; k < F; k++) mean[k] /= elig.length;
  for (const i of elig) for (let k = 0; k < F; k++) sd[k] += (feat[i][k] - mean[k]) ** 2;
  for (let k = 0; k < F; k++) sd[k] = Math.sqrt(sd[k] / elig.length) || 1;
  const dist = elig.map((i) => {
    let s = 0;
    for (let k = 0; k < F; k++) s += FEATURE_WEIGHTS[k] * (((feat[i][k] - fSel[k]) / sd[k]) ** 2);
    return Math.sqrt(s);
  });
  const sorted = [...dist].sort((a, b) => a - b);
  const h = Math.max(quantile(sorted, CORE_QUANTILE), 1e-6);
  const core = dist.filter((d) => d <= h).length;
  const w = dist.map((d) => Math.exp(-0.5 * (d / h) ** 2));
  // Overlap adjustment over the eligible list (sorted by S&P index): w' = w^2 / sum(w within +/- H sessions).
  const order = elig.map((_, p) => p).sort((a, b) => spxJ[elig[a]] - spxJ[elig[b]]);
  const jj = order.map((p) => spxJ[elig[p]]), ww = order.map((p) => w[p]);
  const pre = [0]; for (let p = 0; p < ww.length; p++) pre.push(pre[p] + ww[p]);
  const wAdj = new Array(elig.length);
  let lo = 0, hi = 0;
  for (let p = 0; p < order.length; p++) {
    while (jj[p] - jj[lo] >= H) lo++;
    if (hi < p) hi = p;
    while (hi + 1 < jj.length && jj[hi + 1] - jj[p] < H) hi++;
    const nb = pre[hi + 1] - pre[lo];
    wAdj[order[p]] = nb > 0 ? (ww[p] * ww[p]) / nb : 0;
  }
  const ret = elig.map((i) => spx[spxJ[i] + H] / spx[spxJ[i]] - 1);
  const cat = (r) => (r >= horizon.thr ? "up" : r <= -horizon.thr ? "down" : "range");
  const probs = { up: 0, range: 0, down: 0 }, base = { up: 0, range: 0, down: 0 };
  let tot = 0, tot2 = 0;
  elig.forEach((_, p) => { const c = cat(ret[p]); probs[c] += wAdj[p]; base[c] += 1; tot += wAdj[p]; tot2 += wAdj[p] * wAdj[p]; });
  if (!(tot > 0)) return null;
  for (const k of Object.keys(probs)) { probs[k] /= tot; base[k] /= elig.length; }
  const most = Object.keys(probs).sort((a, b) => probs[b] - probs[a])[0];
  return {
    key: horizon.key, label: horizon.label, sessions: H, thr: horizon.thr,
    probs, base, most, vsBase: (probs[most] - base[most]) * 100,
    median: wQuantile(ret, wAdj, 0.5), q25: wQuantile(ret, wAdj, 0.25), q75: wQuantile(ret, wAdj, 0.75),
    effective: (tot * tot) / tot2, eligible: elig.length, core,
  };
}

export function scenarioReport(ds, selIdx) {
  return HORIZONS.map((hz) => scenarioOdds(ds, selIdx, hz));
}
