import { alignQqq, ruleSeries, signalAt, indexOnOrBefore } from "./exitSignals";

// 12 sessions of QQQ closes; the 50-day EMA is made to sit at 100 by feeding a long flat history first.
const flat = Array(120).fill(100);
const closes = [...flat, 99, 98, 97, 96, 97, 99, 101, 102, 103, 104];
const dates = closes.map((_, i) => `2026-01-${String(i + 1).padStart(3, "0")}`);
const hist = { qqq: closes, qqq_dates: dates };
// Risk-off confirmed on sessions index 122..126 only
const enriched = dates.map((d, i) => ({ date: d, confirmed: i >= 122 && i <= 126 ? "Contraction" : "Expansion" }));

test("aligns QQQ with the confirmed regime and flags 'below the 50-day EMA'", () => {
  const a = alignQqq(enriched, hist);
  expect(a.dates.length).toBe(closes.length);
  expect(a.riskOff[123]).toBe(true);
  expect(a.riskOff[121]).toBe(false);
  expect(a.below50[121]).toBe(true);        // 99 < ema just under 100
  expect(alignQqq(null, hist)).toBeNull();
  expect(alignQqq(enriched, {})).toBeNull();
});

test("the four rules react as described", () => {
  const a = alignQqq(enriched, hist), s = ruleSeries(a);
  // Risk-off confirmed and under the 50-day EMA from 122 → out (pairing / staged / slow all 0)
  expect([s.pairing[122], s.staged[122], s.slow[122]]).toEqual([0, 0, 0]);
  // QQQ climbs back above its 50-day EMA while the regime is still Risk-off: pairing is back in, staged is half, slow is still out
  const reclaim = a.px.findIndex((p, i) => i > 124 && !a.below50[i] && a.riskOff[i]);
  expect(reclaim).toBeGreaterThanOrEqual(0);
  expect([s.pairing[reclaim], s.staged[reclaim], s.slow[reclaim]]).toEqual([1, 0.5, 0]);
  // 9/21 cross follows momentum only
  expect(s.cross.length).toBe(closes.length);
});

test("signalAt reports the value, the date it changed and how many sessions ago", () => {
  expect(signalAt([1, 1, 0, 0, 0], ["a", "b", "c", "d", "e"], 4)).toMatchObject({ value: 0, since: "c", sessions: 2 });
  expect(signalAt([1, 1, 0, 0, 1], ["a", "b", "c", "d", "e"], 4)).toMatchObject({ value: 1, since: "e", sessions: 0 });
  expect(signalAt([1, 1, 1], ["a", "b", "c"], 2)).toMatchObject({ since: "a", firstSession: true });
});

test("indexOnOrBefore finds the last session not after a date", () => {
  expect(indexOnOrBefore(["2026-01-02", "2026-01-05", "2026-01-06"], "2026-01-05")).toBe(1);
  expect(indexOnOrBefore(["2026-01-02", "2026-01-05"], "2026-01-04")).toBe(0);
  expect(indexOnOrBefore(["2026-01-02"], "2025-12-31")).toBe(-1);
});
