@echo off
title IBKR WebSocket Bridge — Power Theme
cd /d C:\Users\arabi\Power-Theme

echo Waiting 20 seconds for TWS to finish loading...
timeout /t 20 /nobreak

:retry
echo Starting IBKR WebSocket server...
python ibkr_ws_server.py
echo.
echo Server stopped or crashed. Restarting in 10 seconds...
timeout /t 10 /nobreak
goto retry
