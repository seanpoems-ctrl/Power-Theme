// Trade Journal ← IB Order Panel order log.
//
// IBKR's trade report only lists executions, so a stop that never fired (the trade closed in profit by hand or at a target) is invisible to the journal and
// the trade has no R. The IB Order Panel appends every order it sends — with the stop legs it placed — to order_log.jsonl (one JSON object per line:
// ts in New York time, ticker, action BUY/SELL, order_type, qty, entry_price, fill_price, stop_price, avg_stop, legs[{kind, price, qty, action}]).
// This module reads that file and matches each order to the journal trade it opened, so the trade's Stop (and so R) can be filled in.
//
// R is measured against the INITIAL stop, so only the first order that opened a trade is used; stops moved or cancelled afterwards in TWS don't change it.

const STORE_KEY = "power_theme_order_log_v1";
const MAX_STORED = 3000;
const MAX_PRICE_GAP = 0.03;        // an order matches a trade only if its price is within 3 % of one of the trade's entry fills

const day = iso => (iso || "").slice(0, 10);
const addDays = (iso, n) => { const d = new Date(`${iso}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const dayGap = (a, b) => Math.round((new Date(`${a}T12:00:00Z`) - new Date(`${b}T12:00:00Z`)) / 86400000);

/** Parse the file's text: one JSON object per line (also accepts a JSON array). Bad lines are skipped. */
export function parseOrderLog(text) {
  const out = [];
  const t = String(text || "").trim();
  if (!t) return out;
  const push = o => { if (o && typeof o === "object" && o.ticker && o.ts) out.push(o); };
  if (t.startsWith("[")) { try { JSON.parse(t).forEach(push); return out; } catch { /* fall through to lines */ } }
  for (const line of t.split(/\r?\n/)) {
    const s = line.trim();
    if (!s) continue;
    try { push(JSON.parse(s)); } catch { /* partial / corrupt line */ }
  }
  return out;
}

export const recordKey = r => `${r.ts}|${r.ticker}|${r.action}|${r.qty}`;

const stopLegs = r => (Array.isArray(r.legs) ? r.legs : []).filter(l => l && l.kind === "stop" && Number.isFinite(+l.price) && +l.qty > 0);

/** Does this order open a position with a stop attached? (A lone "Stop Order" is an exit for an existing position, not an entry.) */
export function isEntryRecord(r) {
  return r.order_type !== "Stop Order" && stopLegs(r).length > 0 && (r.action === "BUY" || r.action === "SELL");
}

/**
 * The stop to store on the trade. Several legs → the journal's multi-stop format ("196.67, 193.33, 190.00"; it averages them), but only when the legs are
 * (near-)equal in size, because the journal's average is unweighted; otherwise the share-weighted average as one price.
 */
export function stopStringFor(r) {
  const legs = stopLegs(r);
  if (!legs.length) return "";
  if (legs.length === 1) return (+legs[0].price).toFixed(2);
  const qtys = legs.map(l => +l.qty);
  if (Math.max(...qtys) - Math.min(...qtys) <= 1) return legs.map(l => (+l.price).toFixed(2)).join(", ");
  const shares = qtys.reduce((a, b) => a + b, 0);
  return (legs.reduce((s, l) => s + l.price * l.qty, 0) / shares).toFixed(2);
}

const avgOf = s => { const n = String(s).split(",").map(x => parseFloat(x)).filter(Number.isFinite); return n.length ? n.reduce((a, b) => a + b, 0) / n.length : NaN; };

/**
 * Match order-log records to journal trades that have no stop yet.
 * Returns { updates: Map(tradeId → stop string), matched: [{rec, trade}], unmatched: [{rec, reason}], skippedHasStop }.
 * Never touches a trade that already has a stop (typed by hand, or from LOD backfill).
 */
export function matchOrderLog(trades, records) {
  const updates = new Map();
  const matched = [], unmatched = [];
  let skippedHasStop = 0;
  const claimed = new Set();
  const entries = records.filter(isEntryRecord).sort((a, b) => a.ts.localeCompare(b.ts));

  for (const rec of entries) {
    const side = rec.action === "BUY" ? "long" : "short";
    const recDay = day(rec.ts);
    const isLimit = /limit/i.test(rec.order_type || "");
    const ref = Number.isFinite(+rec.fill_price) && +rec.fill_price > 0 ? +rec.fill_price : +rec.entry_price;
    const ticker = String(rec.ticker).toUpperCase();
    const stop = stopStringFor(rec);
    const stopN = avgOf(stop);

    let best = null, sawTrade = false, hadStopAlready = false;
    for (const t of trades) {
      if (String(t.ticker).toUpperCase() !== ticker || (t.side === "short" ? "short" : "long") !== side || !t.date) continue;
      const gap = dayGap(t.date, recDay);                         // trade entry day − order day
      if (gap < -1 || gap > (isLimit ? 10 : 1)) continue;         // New York vs statement-date slack; a resting limit can fill days later
      sawTrade = true;
      if (claimed.has(t.id)) continue;
      if (t.stop_price) { hadStopAlready = true; continue; }
      const prices = (Array.isArray(t.entry_fills) && t.entry_fills.length ? t.entry_fills.map(f => +f.price) : [parseFloat(t.entry_price)]).filter(Number.isFinite);
      if (!prices.length || !(ref > 0)) continue;
      const priceGap = Math.min(...prices.map(p => Math.abs(p - ref) / ref));
      if (priceGap > MAX_PRICE_GAP) continue;
      const score = priceGap + Math.abs(gap) * 0.001;
      if (!best || score < best.score) best = { t, score };
    }
    if (!best) {
      if (hadStopAlready) skippedHasStop++;
      else unmatched.push({ rec, reason: sawTrade ? "price doesn't match" : "no trade yet" });
      continue;
    }
    const entryN = parseFloat(best.t.entry_price);
    if (Number.isFinite(entryN) && Number.isFinite(stopN) && (side === "long" ? stopN >= entryN : stopN <= entryN)) {
      unmatched.push({ rec, reason: "stop is on the wrong side of the entry" });
      continue;
    }
    claimed.add(best.t.id);
    updates.set(best.t.id, stop);
    matched.push({ rec, trade: best.t });
  }
  return { updates, matched, unmatched, skippedHasStop };
}

// ---- records kept in the browser, so an order log imported BEFORE the matching IBKR statement is applied when that statement arrives ----

export function loadOrderLog() {
  try { const a = JSON.parse(localStorage.getItem(STORE_KEY) || "[]"); return Array.isArray(a) ? a : []; } catch { return []; }
}

/** Merge new records into the stored set (deduped), keep the newest MAX_STORED. Returns the merged list. */
export function saveOrderLog(records) {
  const map = new Map(loadOrderLog().map(r => [recordKey(r), r]));
  for (const r of records) map.set(recordKey(r), r);
  const all = [...map.values()].sort((a, b) => a.ts.localeCompare(b.ts)).slice(-MAX_STORED);
  try { localStorage.setItem(STORE_KEY, JSON.stringify(all)); } catch { /* quota / private mode */ }
  return all;
}

export { addDays as _addDays };
