import fs from "fs";
import path from "path";
import { buildDataset, scenarioOdds, scenarioReport, HORIZONS } from "./breadthScenario";

// Synthetic history: n trading days, a seeded random-walk S&P and breadth counts loosely tied to the daily move.
function synth(n = 1200, seed = 7, spxAfter = null) {
  let s = seed;
  const rnd = () => { s = (s * 1664525 + 1013904223) % 4294967296; return s / 4294967296; };
  const dates = [], spxDates = [], spx = [];
  let px = 1000;
  const cols = { up_4_pct: [], down_4_pct: [], up_25_q: [], down_25_q: [], up_13_34d: [], down_13_34d: [] };
  const start = new Date(Date.UTC(2010, 0, 1));
  for (let i = 0; i < n + 300; i++) {
    const d = new Date(start.getTime() + i * 86400000).toISOString().slice(0, 10);
    const r = (rnd() - 0.48) * 0.02;
    px *= 1 + r;
    spxDates.push(d); spx.push(px);
    if (i >= 300) {
      dates.push(d);
      const up = Math.round(150 + 4000 * Math.max(r, 0) + rnd() * 50), dn = Math.round(150 + 4000 * Math.max(-r, 0) + rnd() * 50);
      cols.up_4_pct.push(up); cols.down_4_pct.push(dn);
      cols.up_25_q.push(900 + Math.round(rnd() * 600)); cols.down_25_q.push(900 + Math.round(rnd() * 600));
      cols.up_13_34d.push(900 + Math.round(rnd() * 600)); cols.down_13_34d.push(900 + Math.round(rnd() * 600));
    }
  }
  if (spxAfter != null) for (let k = spxAfter; k < spx.length; k++) spx[k] *= 3;    // tamper with the future
  return { dates, cols, spx_dates: spxDates, spx };
}

const H1M = HORIZONS[0];

test("probabilities sum to 1, bands are ordered, counts are sane", () => {
  const ds = buildDataset(synth());
  const r = scenarioOdds(ds, ds.dates.length - 1, H1M);
  expect(r).not.toBeNull();
  expect(r.probs.up + r.probs.range + r.probs.down).toBeCloseTo(1, 8);
  expect(r.base.up + r.base.range + r.base.down).toBeCloseTo(1, 8);
  expect(r.q25).toBeLessThanOrEqual(r.median);
  expect(r.median).toBeLessThanOrEqual(r.q75);
  expect(r.core).toBeGreaterThan(0);
  expect(r.core).toBeLessThan(r.eligible);
  expect(r.effective).toBeGreaterThan(1);
  expect(r.effective).toBeLessThanOrEqual(r.eligible);
});

test("only days whose outcome was complete by the selected date are eligible (no look-ahead)", () => {
  const ds = buildDataset(synth());
  const sel = ds.dates.length - 1;
  const r = scenarioOdds(ds, sel, H1M);
  // Every eligible day i satisfies spxJ[i] + 21 <= spxJ[sel]; the first usable day is index 9 (needs a 10-day ratio).
  const maxEligible = ds.spxJ[sel] - 21 - ds.spxJ[9] + 1;
  expect(r.eligible).toBe(maxEligible);
});

test("changing S&P prices AFTER the selected date cannot change the odds", () => {
  const base = synth();
  const ds1 = buildDataset(base);
  const sel = 800;
  const a = scenarioOdds(ds1, sel, H1M);
  const ds2 = buildDataset(synth(1200, 7, ds1.spxJ[sel] + 1));
  const b = scenarioOdds(ds2, sel, H1M);
  expect(b).toEqual(a);
});

test("not enough history -> null", () => {
  const ds = buildDataset(synth(400));
  expect(scenarioOdds(ds, 120, HORIZONS[3])).toBeNull();
});

test("scenarioReport returns one entry per horizon", () => {
  const ds = buildDataset(synth());
  const rep = scenarioReport(ds, ds.dates.length - 1);
  expect(rep).toHaveLength(4);
  expect(rep.map((x) => x.key)).toEqual(["1M", "3M", "6M", "1Y"]);
});

const REAL = path.join(__dirname, "..", "public", "breadth_long_history.json");
(fs.existsSync(REAL) ? test : test.skip)("real history: 2009+ sessions, plausible odds on the latest day", () => {
  const h = JSON.parse(fs.readFileSync(REAL, "utf8"));
  expect(h.dates.length).toBeGreaterThan(4000);
  const ds = buildDataset(h);
  const sel = h.dates.length - 1;
  const rep = scenarioReport(ds, sel);
  for (const r of rep) {
    expect(r).not.toBeNull();
    expect(r.probs.up + r.probs.range + r.probs.down).toBeCloseTo(1, 6);
    expect(r.eligible).toBeGreaterThan(2000);
    expect(r.median).toBeGreaterThan(-0.5);
    expect(r.median).toBeLessThan(1);
  }
});
