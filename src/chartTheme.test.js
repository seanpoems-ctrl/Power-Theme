import { _isInsideBar, candleData, _rvolSeries, rvolHistogram, MINI_CHART_THEME, tdvData, _fmtDollarVol } from "./chartTheme";

const bar = (o, h, l, c, v = 100, time = 0) => ({ open: o, high: h, low: l, close: c, volume: v, time });

test("inside bar: range within the previous bar's range, strictly narrower on at least one side", () => {
  const d = [bar(10, 12, 8, 11), bar(10, 11, 9, 10), bar(10, 11, 9, 10), bar(10, 13, 9, 12)];
  expect(_isInsideBar(d, 0)).toBe(false);   // first bar has no predecessor
  expect(_isInsideBar(d, 1)).toBe(true);
  expect(_isInsideBar(d, 2)).toBe(false);   // identical to the previous bar
  expect(_isInsideBar(d, 3)).toBe(false);   // higher high
});

test("candleData paints only inside bars orange", () => {
  const d = [bar(10, 12, 8, 11), bar(10, 11, 9, 10), bar(10, 13, 7, 12)];
  const out = candleData(d);
  expect(out[0].color).toBeUndefined();
  expect(out[1].color).toBe(MINI_CHART_THEME.inside);
  expect(out[2].color).toBeUndefined();
});

test("rvol: needs 20 prior bars, then volume / average of the previous 50", () => {
  const d = Array.from({ length: 60 }, (_, i) => bar(1, 2, 1, 2, i === 59 ? 300 : 100, i));
  const r = _rvolSeries(d);
  expect(r[19]).toBeNull();
  expect(r[20]).toBeCloseTo(1, 8);
  expect(r[59]).toBeCloseTo(3, 8);
  const h = rvolHistogram(d);
  expect(h).toHaveLength(40);
  expect(h[h.length - 1].color).toBe(MINI_CHART_THEME.rvolUp);   // close > open
});

test("tdv: dollar volume = volume x close, green when close >= open, red below", () => {
  const out = tdvData([bar(10, 12, 9, 11, 1000, 1), bar(11, 12, 9, 10, 500, 2)]);
  expect(out[0].value).toBe(11000);
  expect(out[0].color).toBe(MINI_CHART_THEME.tdvUp);
  expect(out[1].value).toBe(5000);
  expect(out[1].color).toBe(MINI_CHART_THEME.tdvDown);
});

test("dollar volume formatter", () => {
  expect(_fmtDollarVol(23_160_000)).toBe("23.16M");
  expect(_fmtDollarVol(29_680_000)).toBe("29.68M");
  expect(_fmtDollarVol(1_250_000_000)).toBe("1.25B");
  expect(_fmtDollarVol(45_000)).toBe("45K");
  expect(_fmtDollarVol(null)).toBe("—");
});
