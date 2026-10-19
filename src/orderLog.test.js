import { parseOrderLog, isEntryRecord, stopStringFor, matchOrderLog } from "./orderLog";

const rec = (o = {}) => ({
  v: 1, ts: "2026-10-19T10:15:02-04:00", account: "U1", ticker: "AAPL", action: "BUY", order_type: "Market + 3 Stops", qty: 99,
  entry_price: 200, fill_price: 200, stop_price: 190, avg_stop: 193.3333,
  legs: [{ kind: "stop", price: 196.67, qty: 33, action: "SELL" }, { kind: "stop", price: 193.33, qty: 33, action: "SELL" }, { kind: "stop", price: 190, qty: 33, action: "SELL" }],
  ...o,
});
const trade = (o = {}) => ({ id: "t1", ticker: "AAPL", side: "long", date: "2026-10-19", entry_price: "200.05", entry_fills: [{ price: 200.05, qty: 99 }], stop_price: "", ...o });

test("parses JSON lines, skipping blank and corrupt lines", () => {
  const text = `${JSON.stringify(rec())}\n\n{"broken\n${JSON.stringify(rec({ ticker: "NVDA" }))}\n`;
  expect(parseOrderLog(text).map(r => r.ticker)).toEqual(["AAPL", "NVDA"]);
  expect(parseOrderLog("")).toEqual([]);
});

test("equal thirds are stored as the journal's multi-stop list; uneven legs as the share-weighted average", () => {
  expect(stopStringFor(rec())).toBe("196.67, 193.33, 190.00");
  const uneven = rec({ legs: [{ kind: "stop", price: 196, qty: 60 }, { kind: "stop", price: 190, qty: 20 }] });
  expect(stopStringFor(uneven)).toBe("194.50");
  expect(stopStringFor(rec({ legs: [{ kind: "stop", price: 190, qty: 99 }] }))).toBe("190.00");
});

test("a standalone Stop Order or an order with no stop legs is not an entry", () => {
  expect(isEntryRecord(rec())).toBe(true);
  expect(isEntryRecord(rec({ order_type: "Stop Order" }))).toBe(false);
  expect(isEntryRecord(rec({ legs: [] }))).toBe(false);
});

test("matches by ticker, side, date and price; leaves other trades alone", () => {
  const trades = [trade(), trade({ id: "t2", ticker: "NVDA" }), trade({ id: "t3", date: "2026-09-01" })];
  const { updates, matched, unmatched } = matchOrderLog(trades, [rec()]);
  expect([...updates]).toEqual([["t1", "196.67, 193.33, 190.00"]]);
  expect(matched).toHaveLength(1);
  expect(unmatched).toHaveLength(0);
});

test("never overwrites a stop that is already on the trade", () => {
  const { updates, skippedHasStop } = matchOrderLog([trade({ stop_price: "191.00" })], [rec()]);
  expect(updates.size).toBe(0);
  expect(skippedHasStop).toBe(1);
});

test("the first order for a trade wins; an add-on order does not replace the initial stop", () => {
  const first = rec();
  const addOn = rec({ ts: "2026-10-19T11:30:00-04:00", fill_price: 203, entry_price: 203, legs: [{ kind: "stop", price: 195, qty: 50 }] });
  const { updates } = matchOrderLog([trade()], [addOn, first]);
  expect(updates.get("t1")).toBe("196.67, 193.33, 190.00");
});

test("a shorter trade date window for market orders, longer for resting limit orders", () => {
  const farTrade = trade({ date: "2026-10-23" });
  expect(matchOrderLog([farTrade], [rec()]).unmatched[0].reason).toBe("no trade yet");
  const lim = rec({ order_type: "Limit + 3 Stops", fill_price: null });
  expect(matchOrderLog([farTrade], [lim]).updates.size).toBe(1);
});

test("price too far from the entry fills, or a stop on the wrong side, is reported not applied", () => {
  expect(matchOrderLog([trade({ entry_fills: [{ price: 230 }], entry_price: "230" })], [rec()]).unmatched[0].reason).toBe("price doesn't match");
  const bad = rec({ legs: [{ kind: "stop", price: 210, qty: 99 }] });
  expect(matchOrderLog([trade()], [bad]).unmatched[0].reason).toMatch(/wrong side/);
});

test("short orders match short trades with a stop above the entry", () => {
  const s = rec({ action: "SELL", legs: [{ kind: "stop", price: 210, qty: 99, action: "BUY" }] });
  const { updates } = matchOrderLog([trade({ side: "short" }), trade({ id: "tl", side: "long" })], [s]);
  expect([...updates]).toEqual([["t1", "210.00"]]);
});
