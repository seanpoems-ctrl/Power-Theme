#!/usr/bin/env bash
# start_local.sh — Run all data scripts then launch the React dev server
set -e

echo "=== [1/6] scraper.py ==="
python scraper.py

echo "=== [2/6] earnings_calendar.py ==="
python earnings_calendar.py

echo "=== [3/6] econ_calendar.py ==="
python econ_calendar.py

echo "=== [4/6] market_internals.py ==="
python market_internals.py

echo "=== [5/6] ibkr_themes.py ==="
python ibkr_themes.py

echo "=== [6/6] gapper_service.py ==="
python gapper_service.py

echo ""
echo "=== All data scripts complete. Starting React dev server... ==="
npm start
