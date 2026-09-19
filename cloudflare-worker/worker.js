/**
 * TradingView Advanced Charts datafeed proxy.
 *
 * Browsers can't call Yahoo Finance's chart API directly — no CORS headers
 * (verified live: a fetch() from the deployed GitHub Pages origin fails with
 * "Failed to fetch"). Server-to-server calls (scraper.py's yfinance, and this
 * Worker) aren't affected — CORS is a browser-enforced restriction only.
 *
 * This Worker re-serves Yahoo's chart data with permissive CORS headers so
 * the Trade Journal's TradeChartModal can implement TradingView's
 * IBasicDataFeed.getBars() against it once the Advanced Charts library is
 * wired in.
 *
 * Endpoint:
 *   GET /bars?symbol=AAPL&resolution=D&from=<unix_seconds>&to=<unix_seconds>
 *   -> { bars: [{ time, open, high, low, close, volume }, ...] }
 *
 * resolution follows TradingView's convention: "1","5","15","30","60","D",
 * "W","M" (minutes, or D/W/M).
 */

const TV_RESOLUTION_TO_YAHOO_INTERVAL = {
  "1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1h", "120": "1h",
  "D": "1d", "1D": "1d", "W": "1wk", "1W": "1wk", "M": "1mo", "1M": "1mo",
};

// Yahoo intraday intervals only keep a limited lookback (1m: ~7d, up to 60m:
// ~2yr) — callers requesting an older intraday range than Yahoo will serve
// get an empty result, which is expected; TradingView shows "no data" for
// that range rather than erroring.

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") return withCors(new Response(null, { status: 204 }));

    const url = new URL(request.url);
    if (url.pathname !== "/bars") return withCors(json({ error: "not found" }, 404));

    const symbol = url.searchParams.get("symbol");
    const resolution = url.searchParams.get("resolution") || "D";
    const from = url.searchParams.get("from");
    const to = url.searchParams.get("to");
    if (!symbol || !from || !to) return withCors(json({ error: "symbol, from, to are required" }, 400));

    const interval = TV_RESOLUTION_TO_YAHOO_INTERVAL[resolution] || "1d";

    // Cache historical bar responses at the edge — trade-review lookups are
    // for fixed past windows, so a 5-minute TTL just absorbs repeat clicks on
    // the same trade without ever serving stale *live* data (this app never
    // asks for "now" through this endpoint, only bounded past ranges).
    const cacheKey = new Request(url.toString(), request);
    const cache = caches.default;
    const cached = await cache.match(cacheKey);
    if (cached) return withCors(cached);

    const yahooUrl =
      `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}` +
      `?interval=${interval}&period1=${from}&period2=${to}&includePrePost=false`;

    let yahooRes;
    try {
      yahooRes = await fetch(yahooUrl, { headers: { "User-Agent": "Mozilla/5.0 (compatible; PowerThemeProxy/1.0)" } });
    } catch (e) {
      return withCors(json({ error: `upstream fetch failed: ${e.message}` }, 502));
    }
    if (!yahooRes.ok) return withCors(json({ error: `Yahoo returned ${yahooRes.status}` }, 502));

    let data;
    try {
      data = await yahooRes.json();
    } catch {
      return withCors(json({ error: "Yahoo returned non-JSON" }, 502));
    }

    const result = data?.chart?.result?.[0];
    const err = data?.chart?.error;
    if (err) return withCors(json({ error: err.description || "Yahoo chart error" }, 404));
    if (!result) return withCors(json({ error: "no data for symbol" }, 404));

    const timestamps = result.timestamp || [];
    const quote = result.indicators?.quote?.[0] || {};
    const bars = timestamps
      .map((t, i) => ({
        time: t * 1000, // TradingView bar.time is ms since epoch
        open: quote.open?.[i],
        high: quote.high?.[i],
        low: quote.low?.[i],
        close: quote.close?.[i],
        volume: quote.volume?.[i] ?? 0,
      }))
      .filter(b => b.open != null && b.high != null && b.low != null && b.close != null);

    const response = json({ bars, symbol, resolution });
    response.headers.set("Cache-Control", "public, max-age=300");
    ctx.waitUntil(cache.put(cacheKey, response.clone()));
    return withCors(response);
  },
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function withCors(res) {
  const headers = new Headers(res.headers);
  headers.set("Access-Control-Allow-Origin", "*"); // public read-only price data, no auth/secrets involved
  headers.set("Access-Control-Allow-Methods", "GET, OPTIONS");
  headers.set("Access-Control-Allow-Headers", "Content-Type");
  return new Response(res.body, { status: res.status, headers });
}
