import { futuresInfo, futuresMultiplier } from "./futuresMultipliers";

test("micro futures symbols from the journal resolve to their multipliers", () => {
  expect(futuresInfo("MNQZ9")).toEqual({ root: "MNQ", mult: 2, name: "Micro E-mini Nasdaq-100" });
  expect(futuresMultiplier("M6BH0")).toBe(6250);      // micro GBP/USD
  expect(futuresMultiplier("MCDZ9")).toBe(10000);     // micro CAD/USD
  expect(futuresMultiplier("MGCG0")).toBe(10);        // micro gold
  expect(futuresMultiplier("MESH26")).toBe(5);        // two-digit year
  expect(futuresMultiplier("mclk6")).toBe(100);       // case-insensitive
});

test("stocks and bare roots are left alone (many roots collide with stock tickers)", () => {
  expect(futuresMultiplier("AAPL")).toBe(1);
  expect(futuresMultiplier("ES")).toBe(1);            // Eversource
  expect(futuresMultiplier("CL")).toBe(1);            // Colgate
  expect(futuresMultiplier("OKTA")).toBe(1);
  expect(futuresMultiplier("")).toBe(1);
  expect(futuresMultiplier(undefined)).toBe(1);
  expect(futuresInfo("SOXS")).toBeNull();
});

test("a one-contract micro GBP trade: 1.31 -> 1.33 is $125, not $0.02", () => {
  expect((1.33 - 1.31) * futuresMultiplier("M6BH0")).toBeCloseTo(125, 6);
});
