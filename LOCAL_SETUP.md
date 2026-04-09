# Local Setup Guide

## 1. Environment Variables

Create a `.env` file in the project root (gitignored):

```
GEMINI_API_KEY=your_key_here
REACT_APP_GEMINI_KEY=your_key_here
IBKR_HOST=127.0.0.1
IBKR_PORT=4002
```

> **IBKR vars** are only needed for local live testing against TWS/IB Gateway.
> GitHub Actions always runs `ibkr_themes.py` in fallback mode (empty secrets → no TWS connection attempted).

> **REACT_APP_GEMINI_KEY** is the same key as GEMINI_API_KEY — React requires the `REACT_APP_` prefix to expose it in the browser bundle. Note: this key will be visible in the built JS bundle, which is acceptable for a personal dashboard.

---

## 2. Python Dependencies

```bash
pip install -r requirements.txt
```

Or install manually:

```bash
pip install ib_insync requests beautifulsoup4 yfinance lxml exchange_calendars pandas google-genai tradingview_screener python-dotenv
```

---

## 3. Local Test Sequence

Run scripts in this order. Each generates a JSON file in `public/`:

```bash
python scraper.py           # → public/thematic_data.json       (~5-10 min, hits Finviz + yfinance)
python earnings_calendar.py # → public/earnings_calendar.json
python econ_calendar.py     # → public/econ_calendar.json
python market_internals.py  # → public/market_internals.json
python ibkr_themes.py       # → public/ibkr_themes.json         (fallback mode if TWS not running)
python gapper_service.py    # → public/gapper_data.json         (best run before 9:30 AM ET)
npm start                   # serves React app at http://localhost:3000
```

Or run everything at once:

```bash
bash start_local.sh
```

---

## 4. GitHub Secrets

In repo **Settings → Secrets and variables → Actions**, ensure these secrets exist:

| Secret | Status | Value |
|--------|--------|-------|
| `GEMINI_API_KEY` | Already exists | Your Google Gemini API key |
| `FINNHUB_API_KEY` | Already exists | Your Finnhub API key |
| `IBKR_HOST` | Add new | `""` (empty string — CI runs in fallback mode) |
| `IBKR_PORT` | Add new | `4002` |

> When `IBKR_HOST` is empty in CI, `ibkr_themes.py` skips the TWS connection and writes a fallback JSON with `connected: false`.
