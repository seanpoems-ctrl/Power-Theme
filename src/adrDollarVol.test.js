import { adrDollarVol, fmtAdrDollarVol } from "./adrDollarVol";

test("multiplies ADR% by dollar volume", () => {
  expect(adrDollarVol(10.4, 3.22e9)).toBeCloseTo(33.488e9, 0);
});
test("missing or non-positive inputs give null", () => {
  expect(adrDollarVol(null, 1e9)).toBeNull();
  expect(adrDollarVol(5, undefined)).toBeNull();
  expect(adrDollarVol(0, 1e9)).toBeNull();
  expect(adrDollarVol("x", 1e9)).toBeNull();
});
test("formats like the screener", () => {
  expect(fmtAdrDollarVol(9.45e9)).toBe("9.45B");
  expect(fmtAdrDollarVol(162.6e6)).toBe("162.6M");
  expect(fmtAdrDollarVol(789_600)).toBe("790K");
  expect(fmtAdrDollarVol(null)).toBe("—");
});
