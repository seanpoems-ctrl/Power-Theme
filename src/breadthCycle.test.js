import { breadthScore, breadthPhase, computeBreadthCycle, latestBreadthCycle, breadthDivergence } from "./breadthCycle";

const row = (date, over = {}) => ({
  date, up_4_pct: 200, down_4_pct: 200, ratio_5d: 1, ratio_10d: 1, up_25_q: 1000, down_25_q: 1000,
  up_13_34d: 1000, down_13_34d: 1000, t2108: 50, ...over,
});

test("a perfectly balanced day scores 50", () => {
  expect(breadthScore(row("2026-01-01"))).toBeCloseTo(50, 5);
});

test("strength is the mean of six 0-100 scores and is bounded", () => {
  const bull = breadthScore(row("d", { up_4_pct: 900, down_4_pct: 0, ratio_5d: 4, ratio_10d: 4, up_25_q: 3000, down_25_q: 0, up_13_34d: 3000, down_13_34d: 0, t2108: 100 }));
  const bear = breadthScore(row("d", { up_4_pct: 0, down_4_pct: 900, ratio_5d: 0.1, ratio_10d: 0.1, up_25_q: 0, down_25_q: 3000, up_13_34d: 0, down_13_34d: 3000, t2108: 0 }));
  expect(bull).toBeCloseTo(100, 5);
  expect(bear).toBeCloseTo(0, 5);
});

test("ratio 2 scores 100 and ratio 0.5 scores 0", () => {
  const base = { up_4_pct: 1, down_4_pct: 0, up_25_q: 1, down_25_q: 0, up_13_34d: 1, down_13_34d: 0, t2108: 100 };
  const a = breadthScore({ ...base, ratio_5d: 2, ratio_10d: 2 });
  const b = breadthScore({ ...base, ratio_5d: 0.5, ratio_10d: 0.5 });
  expect(a).toBeCloseTo(100, 5);
  expect(b).toBeCloseTo((100 * 4 + 0 * 2) / 6, 5);
});

test("phase rule", () => {
  expect(breadthPhase(60, 3)).toBe("Expansion");
  expect(breadthPhase(60, -3)).toBe("Distribution");
  expect(breadthPhase(50, -2)).toBe("Distribution");
  expect(breadthPhase(40, 6)).toBe("Repair");
  expect(breadthPhase(40, 3)).toBe("Contraction");
  expect(breadthPhase(30, -10)).toBe("Contraction");
});

test("computeBreadthCycle sorts ascending and measures the 5-session change", () => {
  const rows = [];
  for (let i = 0; i < 8; i++) rows.push(row(`2026-01-0${i + 1}`, { t2108: 40 + i * 4 }));
  const shuffled = [rows[3], rows[0], rows[7], rows[5], rows[1], rows[6], rows[2], rows[4]];
  const out = computeBreadthCycle(shuffled);
  expect(out.map((r) => r.date)).toEqual(rows.map((r) => r.date));
  expect(out[0].change).toBe(0);                                        // no row 5 sessions back
  expect(out[5].change).toBeCloseTo(out[5].strength - out[0].strength, 8);
  expect(out[7].change).toBeGreaterThan(0);
});

test("latestBreadthCycle reports the newest session", () => {
  const rows = [row("2026-01-01"), row("2026-01-02", { t2108: 20 })];
  const c = latestBreadthCycle(rows);
  expect(c.date).toBe("2026-01-02");
  expect(c.t2108).toBe(20);
  expect(latestBreadthCycle([])).toBeNull();
});

describe("breadthDivergence", () => {
  const cyc = (phase, strength = 40, t2108 = 24) => ({ phase, strength, t2108 });
  test("uptrend + weak breadth = Narrow (warn)", () => {
    for (const p of ["Contraction", "Repair"]) expect(breadthDivergence("green", cyc(p))).toMatchObject({ kind: "narrow", tone: "warn" });
  });
  test("uptrend + Distribution = Fading", () => expect(breadthDivergence("green", cyc("Distribution", 52))).toMatchObject({ kind: "fading", tone: "warn" }));
  test("uptrend + Expansion = Confirmed", () => expect(breadthDivergence("green", cyc("Expansion", 70))).toMatchObject({ kind: "confirmed", tone: "ok" }));
  test("pullback + weak breadth = No support", () => expect(breadthDivergence("yellow", cyc("Contraction"))).toMatchObject({ kind: "no-support" }));
  test("weak tape + oversold/repairing breadth = Bounce watch", () => {
    expect(breadthDivergence("red", cyc("Repair"))).toMatchObject({ kind: "bounce", tone: "info" });
    expect(breadthDivergence("orange", cyc("Contraction", 30, 15))).toMatchObject({ kind: "bounce" });
  });
  test("weak tape + Contraction (not oversold) = Confirms weakness", () => expect(breadthDivergence("red", cyc("Contraction", 30, 35))).toMatchObject({ kind: "confirms-weakness", tone: "bad" }));
  test("nothing to flag for unknown inputs, and text carries the numbers", () => {
    expect(breadthDivergence(undefined, cyc("Repair"))).toBeNull();
    expect(breadthDivergence("green", null)).toBeNull();
    expect(breadthDivergence("yellow", cyc("Expansion", 70))).toBeNull();
    expect(breadthDivergence("green", cyc("Repair", 38.4, 23.78)).text).toMatch(/strength 38.*T2108 23\.8%/);
  });
});
