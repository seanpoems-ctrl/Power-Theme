// Cached Finnhub /quote lookups.
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
// Resolves to the parsed quote ({ c, pc, l, ... }) or null — it never rejects.

const QUOTE_URL = "https://finnhub.io/api/v1/quote";
export const QUOTE_TTL_MS = 60_000;
export const FAIL_TTL_MS = 15_000;

export function createQuoteFetcher({
  apiKey,
  fetchImpl = (...args) => fetch(...args),
  now = Date.now,
  ttlMs = QUOTE_TTL_MS,
  failTtlMs = FAIL_TTL_MS,
} = {}) {
  const cache = new Map();     // SYMBOL -> { t, data }  (data === null for a failed lookup)
  const inflight = new Map();  // SYMBOL -> Promise

  return function fetchQuote(symbol, { maxAgeMs = ttlMs } = {}) {
    if (!apiKey || !symbol) return Promise.resolve(null);
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
      try {
        const r = await fetchImpl(`${QUOTE_URL}?symbol=${encodeURIComponent(String(symbol))}&token=${apiKey}`);
        data = r.ok ? await r.json() : null;
      } catch {
        data = null;
      }
      cache.set(key, { t: now(), data });
      return data;
    })().finally(() => inflight.delete(key));

    inflight.set(key, p);
    return p;
  };
}

export const fetchFinnhubQuote = createQuoteFetcher({ apiKey: process.env.REACT_APP_FINNHUB_KEY || "" });
