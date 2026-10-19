import { render, screen, act } from '@testing-library/react';
import App from './App';

// Lightweight Charts ships as an ES module that Jest does not transform, and it needs a real canvas; the shell test below does not draw any chart.
jest.mock("lightweight-charts", () => ({
  createChart: jest.fn(),
  CandlestickSeries: "CandlestickSeries", LineSeries: "LineSeries", HistogramSeries: "HistogramSeries",
  createSeriesMarkers: jest.fn(),
}));

beforeEach(() => {
  // every data file / API answers "not found" — the dashboard has to render its shell regardless
  global.fetch = jest.fn(() => Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}), text: () => Promise.resolve("") }));
});

test("renders the dashboard shell with its main menu", async () => {
  await act(async () => { render(<App />); });      // lets the startup fetches settle inside act()
  expect(screen.getByText("Power Theme")).toBeInTheDocument();
  for (const name of ["Stocks", "Themes", "Sectors", "Breadth", "Tools"]) {
    expect(screen.getByRole("button", { name })).toBeInTheDocument();
  }
});
