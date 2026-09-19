# TradingView datafeed proxy

Serves historical OHLCV bars for the Trade Journal's chart modal, once the
TradingView Advanced Charts library is wired in. Exists because browsers
can't call Yahoo Finance's chart API directly (CORS-blocked — verified: a
`fetch()` from the deployed GitHub Pages origin returns "Failed to fetch").
This Worker fetches Yahoo server-side (same as `scraper.py`'s `yfinance`
calls, unaffected by CORS) and re-serves it with permissive CORS headers.

## Deploy it yourself (I can't do this step — needs your Cloudflare account)

1. **Create a free Cloudflare account** at https://dash.cloudflare.com/sign-up
   if you don't have one already.
2. Install the CLI and log in (run from this `cloudflare-worker/` directory):
   ```bash
   npm install -g wrangler
   wrangler login
   ```
   This opens a browser tab to authorize the CLI against your account — no
   credentials are typed anywhere, just an OAuth approval click.
3. Deploy:
   ```bash
   wrangler deploy
   ```
   Wrangler prints the live URL, something like:
   `https://power-theme-tv-proxy.<your-subdomain>.workers.dev`

4. **Give me that URL** — I'll wire it into the datafeed adapter once the
   Advanced Charts library files are also in hand.

## Test it once deployed

```bash
curl "https://power-theme-tv-proxy.<your-subdomain>.workers.dev/bars?symbol=AAPL&resolution=D&from=1750000000&to=1758000000"
```
Should return `{"bars":[{"time":...,"open":...,"high":...,"low":...,"close":...,"volume":...}, ...]}`.

## Cost

Cloudflare Workers' free tier is 100,000 requests/day — this proxy is only
called when you open a trade's chart in the journal, so you will not get
close to that limit.
