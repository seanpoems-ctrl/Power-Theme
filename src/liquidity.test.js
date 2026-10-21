import { isLiquid, loadMinDollarVolM, saveMinDollarVolM, DEFAULT_MIN_DVOL_M } from "./liquidity";

test("a holding passes when its avg dollar volume is at or above the minimum ($M)", () => {
  expect(isLiquid(100e6, 100)).toBe(true);
  expect(isLiquid(99.9e6, 100)).toBe(false);
  expect(isLiquid(2.5e9, 100)).toBe(true);
});

test("minimum 0 (or blank) shows everything, including holdings with no volume figure", () => {
  expect(isLiquid(5e6, 0)).toBe(true);
  expect(isLiquid(null, 0)).toBe(true);
  expect(isLiquid(undefined, NaN)).toBe(true);
});

test("with a minimum set, a missing volume cannot be verified and is hidden", () => {
  expect(isLiquid(null, 100)).toBe(false);
  expect(isLiquid(undefined, 50)).toBe(false);
});

test("the minimum defaults to $100M and is remembered", () => {
  localStorage.clear();
  expect(loadMinDollarVolM()).toBe(DEFAULT_MIN_DVOL_M);
  saveMinDollarVolM(250);
  expect(loadMinDollarVolM()).toBe(250);
  saveMinDollarVolM(0);
  expect(loadMinDollarVolM()).toBe(0);
  localStorage.setItem("etf_holdings_min_dvol_m_v1", "garbage");
  expect(loadMinDollarVolM()).toBe(DEFAULT_MIN_DVOL_M);
});
