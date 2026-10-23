import { nasdaqShare, exchangeOf, loadNasdaqMin, saveNasdaqMin } from "./nasdaq";

const lookup = {
  AAPL: [4000, "Consumer Electronics", "NASDAQ"], MSFT: [3500, "Software", "NASDAQ"], NVDA: [4500, "Semiconductors", "NASDAQ"],
  JPM: [800, "Banks", "NYSE"], "BRK.B": [1000, "Insurance", "NYSE"], TINY: [0.6, "Biotech", "NASDAQ"],
};
const h = (ticker, dv = 500e6) => ({ ticker, dollar_volume: dv });

test("counts the Nasdaq-listed share of the holdings", () => {
  const s = nasdaqShare([h("AAPL"), h("MSFT"), h("NVDA"), h("JPM")], lookup, 100);
  expect(s).toMatchObject({ pct: 75, n: 4, nas: 3 });
  expect(s.tickers).toEqual(["AAPL", "MSFT", "NVDA"]);
});

test("holdings under the liquidity minimum are not counted", () => {
  const s = nasdaqShare([h("AAPL"), h("MSFT"), h("JPM"), h("NVDA"), h("TINY", 20e6)], lookup, 100);
  expect(s).toMatchObject({ n: 4, nas: 3, pct: 75 });
  expect(nasdaqShare([h("AAPL"), h("MSFT"), h("JPM"), h("NVDA"), h("TINY", 20e6)], lookup, 0).n).toBe(5);   // minimum off → everything counts
});

test("unknown tickers are skipped, and fewer than 3 known holdings gives null", () => {
  expect(nasdaqShare([h("AAPL"), h("MSFT"), h("ZZZZ")], lookup, 100)).toBeNull();
  expect(nasdaqShare([], lookup, 100)).toBeNull();
  expect(nasdaqShare([h("AAPL"), h("MSFT"), h("JPM"), h("ZZZZ")], lookup, 100)).toMatchObject({ n: 3, pct: 67 });
});

test("class shares match across BRK-B and BRK.B spellings", () => {
  expect(exchangeOf(lookup, "BRK-B")).toBe("NYSE");
  expect(exchangeOf(lookup, "BRK.B")).toBe("NYSE");
  expect(exchangeOf(lookup, "NOPE")).toBeNull();
  expect(exchangeOf(null, "AAPL")).toBeNull();
});

test("the minimum Nasdaq % defaults to off and is remembered", () => {
  localStorage.clear();
  expect(loadNasdaqMin()).toBe(0);
  saveNasdaqMin(70);
  expect(loadNasdaqMin()).toBe(70);
  localStorage.setItem("etf_nasdaq_min_pct_v1", "999");
  expect(loadNasdaqMin()).toBe(0);
});
