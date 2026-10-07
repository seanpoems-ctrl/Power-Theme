import { createQuoteFetcher, QUOTE_TTL_MS, FAIL_TTL_MS } from "./finnhubQuote";

// A fake Finnhub: records every request and answers from a queue (or a default quote).
function setup({ responses = [] } = {}) {
  let t = 1_000_000;
  const calls = [];
  const queue = [...responses];
  const fetchImpl = async (url) => {
    calls.push(url);
    const next = queue.length ? queue.shift() : { ok: true, body: { c: 100, pc: 99 } };
    if (next instanceof Error) throw next;
    return { ok: next.ok, json: async () => next.body };
  };
  const fetchQuote = createQuoteFetcher({ apiKey: "k", fetchImpl, now: () => t });
  return { fetchQuote, calls, advance: (ms) => { t += ms; } };
}

test("a second lookup within 60s is served from cache (no new request)", async () => {
  const { fetchQuote, calls, advance } = setup();
  const a = await fetchQuote("NVDA");
  advance(59_000);
  const b = await fetchQuote("NVDA");
  expect(calls).toHaveLength(1);
  expect(b).toEqual(a);
  expect(a).toEqual({ c: 100, pc: 99 });
});

test("the cache expires after 60s", async () => {
  const { fetchQuote, calls, advance } = setup();
  await fetchQuote("NVDA");
  advance(QUOTE_TTL_MS + 1);
  await fetchQuote("NVDA");
  expect(calls).toHaveLength(2);
});

test("symbols are cached independently and case-insensitively", async () => {
  const { fetchQuote, calls } = setup();
  await fetchQuote("NVDA");
  await fetchQuote("nvda");
  await fetchQuote("AMD");
  expect(calls).toHaveLength(2);
  expect(calls[0]).toContain("symbol=NVDA");
  expect(calls[1]).toContain("symbol=AMD");
});

test("concurrent lookups for one symbol share a single in-flight request", async () => {
  const { fetchQuote, calls } = setup();
  const [a, b, c] = await Promise.all([fetchQuote("PBR"), fetchQuote("PBR"), fetchQuote("pbr")]);
  expect(calls).toHaveLength(1);
  expect(a).toEqual(b);
  expect(b).toEqual(c);
});

test("a pollers' smaller maxAgeMs preserves its own refresh rate", async () => {
  const { fetchQuote, calls, advance } = setup();
  await fetchQuote("VALE", { maxAgeMs: 25_000 });
  advance(10_000);
  await fetchQuote("VALE", { maxAgeMs: 25_000 });   // fresh enough -> cached
  expect(calls).toHaveLength(1);
  advance(20_000);                                   // 30s old: a 30s poller must refresh
  await fetchQuote("VALE", { maxAgeMs: 25_000 });
  expect(calls).toHaveLength(2);
});

test("a caller can never extend life beyond the TTL", async () => {
  const { fetchQuote, calls, advance } = setup();
  await fetchQuote("NU");
  advance(QUOTE_TTL_MS + 1);
  await fetchQuote("NU", { maxAgeMs: 10 * 60_000 });
  expect(calls).toHaveLength(2);
});

test("a failed lookup (429) is retried after 15s, not held for 60s, and does not poison the cache", async () => {
  const { fetchQuote, calls, advance } = setup({ responses: [{ ok: false, body: null }] });
  expect(await fetchQuote("STNE")).toBeNull();        // 429
  advance(5_000);
  expect(await fetchQuote("STNE")).toBeNull();        // still inside the 15s failure window -> no new request
  expect(calls).toHaveLength(1);
  advance(FAIL_TTL_MS);                               // now >15s
  expect(await fetchQuote("STNE")).toEqual({ c: 100, pc: 99 });   // retried, succeeded
  expect(calls).toHaveLength(2);
  advance(30_000);
  await fetchQuote("STNE");                           // success is cached for the full TTL
  expect(calls).toHaveLength(2);
});

test("a network error resolves to null (never rejects) and is retried later", async () => {
  const { fetchQuote, calls, advance } = setup({ responses: [new Error("offline")] });
  await expect(fetchQuote("ABEV")).resolves.toBeNull();
  advance(FAIL_TTL_MS + 1);
  await expect(fetchQuote("ABEV")).resolves.toEqual({ c: 100, pc: 99 });
  expect(calls).toHaveLength(2);
});

test("no API key or no symbol: resolves null without any request", async () => {
  const calls = [];
  const noKey = createQuoteFetcher({ apiKey: "", fetchImpl: async (u) => { calls.push(u); return { ok: true, json: async () => ({}) }; } });
  expect(await noKey("NVDA")).toBeNull();
  const { fetchQuote, calls: c2 } = setup();
  expect(await fetchQuote("")).toBeNull();
  expect(calls).toHaveLength(0);
  expect(c2).toHaveLength(0);
});

test("a flurry of remounts (the original 429 trigger) makes one request per ticker", async () => {
  const { fetchQuote, calls, advance } = setup();
  const tickers = ["STNE", "NU", "CRML", "ABEV", "PBR", "PAX", "XRPN", "ALVO", "GGB", "VALE"];
  for (let mount = 0; mount < 6; mount++) {            // six view switches inside ~50s
    await Promise.all(tickers.map((s) => fetchQuote(s)));
    advance(8_000);
  }
  expect(calls).toHaveLength(tickers.length);          // before: 6 x 10 = 60 requests
});

// ── Finance Query fallback ───────────────────────────────────────────────────────────────────────────────────────
import { createFinanceQueryFallback, COOLDOWN_MS } from "./finnhubQuote";

function fallbackSetup({ finnhub = [], fq } = {}) {
  let t = 5_000_000;
  const finnhubCalls = [];
  const fqCalls = [];
  const queue = [...finnhub];
  const fetchImpl = async (url) => {
    finnhubCalls.push(url);
    const next = queue.length ? queue.shift() : { ok: true, status: 200, body: { c: 100, pc: 99 } };
    if (next instanceof Error) throw next;
    return { ok: next.ok, status: next.status ?? (next.ok ? 200 : 500), json: async () => next.body };
  };
  const fqFetch = async (url) => {
    fqCalls.push(url);
    if (fq instanceof Error) throw fq;
    const symbols = decodeURIComponent(new URL(url).searchParams.get("symbols")).split(",");
    const quotes = symbols.map((s) => ({ symbol: s, regularMarketPrice: 50, regularMarketPreviousClose: 48, regularMarketChange: 2, regularMarketChangePercent: 4.17 }));
    return { ok: fq ? fq.ok !== false : true, json: async () => ({ quotes }) };
  };
  const fallback = createFinanceQueryFallback({ fetchImpl: fqFetch, batchMs: 0 });
  const fetchQuote = createQuoteFetcher({ apiKey: "k", fetchImpl, now: () => t, fallback });
  return { fetchQuote, finnhubCalls, fqCalls, advance: (ms) => { t += ms; } };
}

test("fallback: a Finnhub 429 is answered by Finance Query, reshaped like a Finnhub quote", async () => {
  const { fetchQuote, fqCalls } = fallbackSetup({ finnhub: [{ ok: false, status: 429, body: null }] });
  const q = await fetchQuote("NVDA");
  expect(q).toMatchObject({ c: 50, pc: 48, d: 2, dp: 4.17, _source: "finance-query" });
  expect(fqCalls).toHaveLength(1);
});

test("fallback: after a 429 Finnhub is skipped for the cooldown, then used again", async () => {
  const { fetchQuote, finnhubCalls, fqCalls, advance } = fallbackSetup({ finnhub: [{ ok: false, status: 429, body: null }] });
  await fetchQuote("AAA");                       // 429 -> fallback
  await fetchQuote("BBB");                       // inside the cooldown -> straight to fallback, no Finnhub call
  expect(finnhubCalls).toHaveLength(1);
  expect(fqCalls.length).toBeGreaterThanOrEqual(2);
  advance(COOLDOWN_MS + 1);
  const q = await fetchQuote("CCC");             // cooldown over -> Finnhub again
  expect(finnhubCalls).toHaveLength(2);
  expect(q).toEqual({ c: 100, pc: 99 });
});

test("fallback: lookups in the same tick are sent as ONE batched request (dots become dashes)", async () => {
  const { fetchQuote, fqCalls } = fallbackSetup({ finnhub: Array(3).fill({ ok: false, status: 429, body: null }) });
  const out = await Promise.all([fetchQuote("NVDA"), fetchQuote("BRK.B"), fetchQuote("AMD")]);
  expect(out.every((q) => q && q.c === 50)).toBe(true);
  expect(fqCalls).toHaveLength(1);
  expect(decodeURIComponent(fqCalls[0])).toContain("symbols=NVDA,BRK-B,AMD");
});

test("fallback: not used when Finnhub answers", async () => {
  const { fetchQuote, fqCalls } = fallbackSetup();
  expect(await fetchQuote("MU")).toEqual({ c: 100, pc: 99 });
  expect(fqCalls).toHaveLength(0);
});

test("fallback: Finnhub's all-zero reply (unknown symbol) is treated as a miss", async () => {
  const { fetchQuote, fqCalls } = fallbackSetup({ finnhub: [{ ok: true, body: { c: 0, d: null, dp: null, h: 0, l: 0, o: 0, pc: 0, t: 0 } }] });
  const q = await fetchQuote("PETR4");
  expect(q).toMatchObject({ c: 50, _source: "finance-query" });
  expect(fqCalls).toHaveLength(1);
});

test("fallback: works with no Finnhub key at all", async () => {
  const fqCalls = [];
  const fallback = createFinanceQueryFallback({
    fetchImpl: async (u) => { fqCalls.push(u); return { ok: true, json: async () => ({ quotes: [{ symbol: "SPY", regularMarketPrice: 700, regularMarketPreviousClose: 695 }] }) }; },
    batchMs: 0,
  });
  const fetchQuote = createQuoteFetcher({ apiKey: "", fetchImpl: async () => { throw new Error("must not call Finnhub"); }, fallback });
  expect(await fetchQuote("SPY")).toMatchObject({ c: 700, pc: 695 });
  expect(fqCalls).toHaveLength(1);
});

test("fallback: if Finance Query also fails the lookup resolves null (never rejects) and retries after 15s", async () => {
  const { fetchQuote, fqCalls, advance } = fallbackSetup({ finnhub: [{ ok: false, status: 429, body: null }], fq: new Error("down") });
  await expect(fetchQuote("XYZ")).resolves.toBeNull();
  advance(5_000);
  await expect(fetchQuote("XYZ")).resolves.toBeNull();     // still inside the failure window
  expect(fqCalls).toHaveLength(1);
});

test("fallback: a symbol Finance Query doesn't return resolves null", async () => {
  const fallback = createFinanceQueryFallback({ fetchImpl: async () => ({ ok: true, json: async () => ({ quotes: [] }) }), batchMs: 0 });
  expect(await fallback("NOPE")).toBeNull();
});
