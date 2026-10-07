// Cached Finnhub /quote lookups (with a Finance Query fallback).
//
// Finnhub's free tier allows ~60 calls/minute. Several views fire one quote per gapper ticker every time they
// mount (Gapper Watch, the Gappers table, the position calculator, the ticker modal), so flipping between tabs
// re-requested the same ~20 tickers over and over and tripped HTTP 429. Every lookup now goes through here:
//
//   - results are cached per symbol for `ttlMs` (60s by default);
//   - concurrent requests for the same symbol share one in-flight fetch;
//   - a FAILED lookup (429 / network) is cached only briefly (`failTtlMs`) so it retries soon but doesn't hammer;
//   - callers that deliberately poll faster than the TTL pass a smaller `maxAgeMs` (never larger than the TTL
//     they'd get anyway), so their refresh rate is preserved while mounts and other views still share results.
//
// Fallback (optional `fallback(symbol)` -> quote | null): when Finnhub has no answer for a symbol — HTTP 429, any other
// error, no API key, or Finnhub's all-zero reply for symbols it doesn't know — the lookup is retried against
// Finance Query (finance-query.com, a public Yahoo-backed API; CORS-open, no key). After a 429 Finnhub is skipped
// for `cooldownMs` so we stop spending calls that are going to fail. Fallback lookups made in the same tick are
// sent as ONE batched request. Fallback quotes are reshaped to Finnhub's ({ c, pc, d, dp, h, l, o }), so no caller
// changes; `_source` marks where a quote came from. Finance Query is a third-party hobby service with no SLA, which
// is why it is only ever a fallback.
//
// Resolves to the parsed quote ({ c, pc, l, ... }) or null — it never rejects.

const QUOTE_URL = "https://finnhub.io/api/v1/quote";
const FQ_URL = "https://finance-query.com/v2/quotes";
const FQ_FIELDS = "symbol,regularMarketPrice,regularMarketPreviousClose,regularMarketChange,regularMarketChangePercent,regularMarketDayHigh,regularMarketDayLow,regularMarketOpen";
const FQ_CHUNK = 50;    // symbols per request
export const QUOTE_TTL_MS = 60_000;
export const FAIL_TTL_MS = 15_000;
export const COOLDOWN_MS = 30_000;

// Yahoo spells share classes with a dash (BRK-B), TradingView/our data sometimes with a dot (BRK.B).
const yahooSymbol = (s) => String(s).toUpperCase().replace(/\./g, "-");

function toFinnhubShape(q) {
  if (!q || q.regularMarketPrice == null || !(q.regularMarketPrice > 0)) return null;
  return {
    c: q.regularMarketPrice,
    pc: q.regularMarketPreviousClose ?? null,
    d: q.regularMarketChange ?? null,
    dp: q.regularMarketChangePercent ?? null,
    h: q.regularMarketDayHigh ?? null,
    l: q.regularMarketDayLow ?? null,
    o: q.regularMarketOpen ?? null,
    _source: "finance-query",
  };
}

// Returns fallback(symbol) -> Promise<quote|null>. Lookups issued within `batchMs` of each other share one request.
export function createFinanceQueryFallback({ fetchImpl = (...args) => fetch(...args), batchMs = 30, url = FQ_URL } = {}) {
  let queue = [];
  let timer = null;

  const flush = async () => {
    const batch = queue;
    queue = [];
    timer = null;
    const symbols = [...new Set(batch.map((b) => b.yahoo))];
    const bySymbol = {};
    for (let i = 0; i < symbols.length; i += FQ_CHUNK) {
      const chunk = symbols.slice(i, i + FQ_CHUNK);
      try {
        const r = await fetchImpl(`${url}?symbols=${encodeURIComponent(chunk.join(","))}&fields=${FQ_FIELDS}`);
        if (r.ok) {
          const j = await r.json();
          for (const q of j?.quotes || []) if (q?.symbol) bySymbol[String(q.symbol).toUpperCase()] = q;
        }
      } catch {
        /* leave this chunk unresolved -> null */
      }
    }
    for (const b of batch) b.resolve(toFinnhubShape(bySymbol[b.yahoo]));
  };

  return (symbol) => new Promise((resolve) => {
    queue.push({ yahoo: yahooSymbol(symbol), resolve });
    if (!timer) timer = setTimeout(flush, batchMs);
  });
}

export function createQuoteFetcher({
  apiKey,
  fetchImpl = (...args) => fetch(...args),
  now = Date.now,
  ttlMs = QUOTE_TTL_MS,
  failTtlMs = FAIL_TTL_MS,
  fallback = null,
  cooldownMs = COOLDOWN_MS,
} = {}) {
  const cache = new Map();     // SYMBOL -> { t, data }  (data === null for a failed lookup)
  const inflight = new Map();  // SYMBOL -> Promise
  let finnhubBlockedUntil = 0; // after a 429, skip Finnhub until this time

  return function fetchQuote(symbol, { maxAgeMs = ttlMs } = {}) {
    if (!symbol || (!apiKey && !fallback)) return Promise.resolve(null);
    const key = String(symbol).toUpperCase();

    const hit = cache.get(key);
    if (hit) {
      const limit = hit.data ? Math.min(maxAgeMs, ttlMs) : Math.min(maxAgeMs, failTtlMs);
      if (now() - hit.t < limit) return Promise.resolve(hit.data);
    }

    const pending = inflight.get(key);
    if (pending) return pending;

    const p = (async () => {
      let data = null;
      if (apiKey && now() >= finnhubBlockedUntil) {
        try {
          const r = await fetchImpl(`${QUOTE_URL}?symbol=${encodeURIComponent(String(symbol))}&token=${apiKey}`);
          if (r.ok) data = await r.json();
          else if (r.status === 429) finnhubBlockedUntil = now() + cooldownMs;
        } catch {
          data = null;
        }
      }
      // Finnhub answers { c: 0, pc: 0, ... } for symbols it doesn't carry — treat that as a miss too.
      const empty = !data || (!(data.c > 0) && !(data.pc > 0));
      if (empty && fallback) {
        let alt = null;
        try { alt = await fallback(symbol); } catch { alt = null; }
        if (alt) data = alt;
      }
      cache.set(key, { t: now(), data });
      return data;
    })().finally(() => inflight.delete(key));

    inflight.set(key, p);
    return p;
  };
}

export const fetchFinnhubQuote = createQuoteFetcher({
  apiKey: process.env.REACT_APP_FINNHUB_KEY || "",
  fallback: createFinanceQueryFallback(),
});
