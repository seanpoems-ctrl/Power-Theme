// Shared look + helpers for every candlestick chart on the dashboard (Mini Charts, enlarged chart, stock panel, Trade Journal chart,
// trendline chart). Modelled on the user's TradingView layout: grey canvas, white up-candles / grey down-candles with black outlines,
// inside bars in orange, white / grey relative-volume bars.

export const MINI_CHART_THEME = {
  bg: "#b3b3b3", text: "#1f1f1f", axis: "#6b6b6b", crosshair: "#444444",
  up: "#ffffff", down: "#6e6e6e", outline: "#000000",
  inside: "#ff9800",
  rvolUp: "#ffffff", rvolDown: "#707070",
};

// Candlestick series options in the shared look.
export const candleOptions = () => ({
  upColor: MINI_CHART_THEME.up, downColor: MINI_CHART_THEME.down, borderVisible: true,
  borderUpColor: MINI_CHART_THEME.outline, borderDownColor: MINI_CHART_THEME.outline,
  wickUpColor: MINI_CHART_THEME.outline, wickDownColor: MINI_CHART_THEME.outline,
});

// Inside bar: the bar's whole range sits within the previous bar's range (and it isn't an identical bar).
export const _isInsideBar = (data, i) => i > 0 && data[i].high <= data[i - 1].high && data[i].low >= data[i - 1].low
  && (data[i].high < data[i - 1].high || data[i].low > data[i - 1].low);

// Candle data with inside bars recoloured orange. Bars need { time, open, high, low, close }.
export const candleData = data => data.map((b, i) => _isInsideBar(data, i)
  ? { ...b, color: MINI_CHART_THEME.inside, borderColor: MINI_CHART_THEME.outline, wickColor: MINI_CHART_THEME.outline } : b);

export const _smaSeries = (values, period) => values.map((_, i) => {
  if (i < period - 1) return null;
  let sum = 0;
  for (let k = i - period + 1; k <= i; k++) sum += values[k];
  return sum / period;
});

// Relative volume: this bar's volume ÷ the average of the previous 50 bars (needs ≥ 20 of them). null where unknown.
export const _rvolSeries = data => data.map((b, i) => {
  const from = Math.max(0, i - 50), n = i - from;
  if (n < 20) return null;
  let sum = 0;
  for (let k = from; k < i; k++) sum += data[k].volume || 0;
  return sum > 0 ? (b.volume || 0) / (sum / n) : null;
});

// Histogram points for the RVOL series (white = up bar, grey = down bar).
export const rvolHistogram = data => {
  const r = _rvolSeries(data);
  return data.reduce((acc, b, i) => {
    if (r[i] != null) acc.push({ time: b.time, value: r[i], color: b.close >= b.open ? MINI_CHART_THEME.rvolUp : MINI_CHART_THEME.rvolDown });
    return acc;
  }, []);
};

// Series options for the RVOL histogram (value shown as e.g. 1.84x).
export const rvolSeriesOptions = (extra = {}) => ({
  priceFormat: { type: "custom", minMove: 0.01, formatter: v => `${v.toFixed(2)}x` },
  priceLineVisible: false, lastValueVisible: false, ...extra,
});
