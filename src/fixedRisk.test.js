import { fixedRiskDollars, withEstimatedR } from "./fixedRisk";

const pnlOf = t => parseFloat(t.pnl_dollars) * (t.ticker === "MNQZ9" ? 2 : 1);
const closed = (o = {}) => ({ id: "a", ticker: "AAPL", exit_price: "110.00", pnl_dollars: "300.00", r_multiple: "", stop_price: "", ...o });

test("risk dollars = equity × pct, and 0 when off or an input is not a positive number", () => {
  expect(fixedRiskDollars({ on: true, equity: "200,000", pct: "0.15" })).toBeCloseTo(300, 6);
  expect(fixedRiskDollars({ on: true, equity: "$200000", pct: "0.15" })).toBeCloseTo(300, 6);
  expect(fixedRiskDollars({ on: false, equity: "200000", pct: "0.15" })).toBe(0);
  expect(fixedRiskDollars({ on: true, equity: "", pct: "0.15" })).toBe(0);
  expect(fixedRiskDollars({ on: true, equity: "200000", pct: "abc" })).toBe(0);
});

test("a closed trade with no R gets P&L ÷ risk, flagged as an estimate", () => {
  const [t] = withEstimatedR([closed()], 300, pnlOf);
  expect(t.r_multiple).toBe("1.00");
  expect(t.r_est).toBe(true);
  expect(withEstimatedR([closed({ pnl_dollars: "-450.00" })], 300, pnlOf)[0].r_multiple).toBe("-1.50");
});

test("a real stop-based R is never replaced", () => {
  const real = closed({ r_multiple: "2.50", stop_price: "95.00" });
  const [t] = withEstimatedR([real], 300, pnlOf);
  expect(t).toBe(real);
  expect(t.r_est).toBeUndefined();
});

test("open trades and trades without P&L are left alone", () => {
  const open = closed({ exit_price: "", pnl_dollars: "" });
  const noPnl = closed({ id: "b", pnl_dollars: "" });
  expect(withEstimatedR([open, noPnl], 300, pnlOf)).toEqual([open, noPnl]);
});

test("futures use the multiplier-adjusted P&L the journal shows", () => {
  const [t] = withEstimatedR([closed({ ticker: "MNQZ9", pnl_dollars: "150.00" })], 300, pnlOf);
  expect(t.r_multiple).toBe("1.00");       // 150 × 2 = $300 ÷ $300
});

test("with no risk set the same array comes back untouched", () => {
  const list = [closed()];
  expect(withEstimatedR(list, 0, pnlOf)).toBe(list);
});
