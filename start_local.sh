#!/usr/bin/env bash
# start_local.sh — Run all data scripts then launch the React dev server
set -e

echo "=== [1/7] scraper.py ==="
python scraper.py

echo "=== [2/7] earnings_calendar.py ==="
python earnings_calendar.py

echo "=== [3/7] econ_calendar.py ==="
python econ_calendar.py

echo "=== [4/7] market_internals.py ==="
python market_internals.py

echo "=== [5/7] ibkr_themes.py ==="
python ibkr_themes.py

echo "=== [6/7] gapper_service.py ==="
python gapper_service.py

echo "=== [7/7] breadth_monitor.py ==="
python breadth_monitor.py || echo "breadth_monitor.py failed (non-fatal)"

echo ""
echo "=== All data scripts complete. Starting React dev server... ==="
npm start
