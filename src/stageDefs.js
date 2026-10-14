// Shared definitions + loaders for the Weinstein Stage Analysis feature (card in StageAnalysis.js, table in StageMovers.js, brief in App.js).

// Stage colours follow the reference layout (grey / blue / amber / pink). S1 is deliberately neutral — it is the "base" stage — so it is always
// identified by its label and legend, never by colour alone.
export const STAGES = [
  { k: 1, key: "s1", label: "S1", name: "Basing",    color: "#8b93a1", tip: "Stage 1 — basing: after a decline the 30-week MA flattens and price chops sideways around it; volume dries up." },
  { k: 2, key: "s2", label: "S2", name: "Advancing", color: "#3b82f6", tip: "Stage 2 — advancing: price above BOTH the 30-week and 200-day MAs, with the 30-week rising and the 200-day not falling. The stage to buy; it starts with a high-volume breakout from the base." },
  { k: 3, key: "s3", label: "S3", name: "Topping",   color: "#bf8a0b", tip: "Stage 3 — topping: after an advance the 30-week / 200-day MAs flatten and price churns around them (erratic swings, failed breakouts, slicing below the 10-week MA)." },
  { k: 4, key: "s4", label: "S4", name: "Declining", color: "#ec4899", tip: "Stage 4 — declining: price below BOTH the 30-week and 200-day MAs, both falling, with lower highs and lower lows. Avoid longs." },
];

let _stageP = null;
export const loadStageAnalysis = () => {
  if (!_stageP) {
    _stageP = fetch(`${process.env.PUBLIC_URL}/stage_analysis.json?v=${Date.now()}`).then(r => (r.ok ? r.json() : null)).catch(() => null);
  }
  return _stageP;
};

// public/stage_stocks.json (stage_analysis_builder.py): one row per classified stock.
let _stocksP = null;
export const loadStageStocks = () => {
  if (!_stocksP) {
    _stocksP = fetch(`${process.env.PUBLIC_URL}/stage_stocks.json?v=${Date.now()}`).then(r => (r.ok ? r.json() : null))
      .then(d => (d?.stocks ? { asOf: d.as_of, rows: d.stocks.map(([t, n, sec, st, w, pv, px, d30, vx, dv]) => ({ t, n, sec, st, w, pv, px, d30, vx, dv })) } : null))
      .catch(() => null);
  }
  return _stocksP;
};

/** Counts of recent stage changes for the Market Situation prompt (null if the file is unavailable). */
export function buildMoversBrief(stk, sector = null) {
  if (!stk?.rows) return null;
  const rows = sector ? stk.rows.filter(r => r.sec === sector) : stk.rows;
  const c = fn => rows.filter(fn).length;
  return {
    entered_stage2_last_1w: c(r => r.st === 2 && r.w <= 1),
    entered_stage2_last_4w: c(r => r.st === 2 && r.w <= 4),
    entered_stage2_last_4w_on_2x_volume: c(r => r.st === 2 && r.w <= 4 && (r.vx ?? 0) >= 2),
    stage2_to_stage3_last_4w: c(r => r.st === 3 && r.pv === 2 && r.w <= 4),
    entered_stage4_last_4w: c(r => r.st === 4 && r.w <= 4),
    entered_stage4_last_4w_on_2x_volume: c(r => r.st === 4 && r.w <= 4 && (r.vx ?? 0) >= 2),
  };
}
