"""
price_api.py — Tiny local HTTP server for stock price lookups.
Run: python3 price_api.py
Serves on http://localhost:5001/price/<TICKER>
         http://localhost:5001/sparkline/<TICKER>
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import yfinance as yf


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress access logs

    def do_GET(self):
        path = self.path.split("?")[0]

        if path.startswith("/sparkline/"):
            ticker = path.split("/sparkline/")[1].upper()
            try:
                hist = yf.Ticker(ticker).history(period="6mo", interval="1d")
                closes = [round(float(v), 4) for v in hist["Close"].tolist()]
                body = json.dumps({"ticker": ticker, "closes": closes})
                self.send_response(200)
            except Exception as e:
                body = json.dumps({"error": str(e)})
                self.send_response(500)

        elif path.startswith("/etf_holdings/"):
            ticker = path.split("/etf_holdings/")[1].upper()
            try:
                t = yf.Ticker(ticker)
                holdings_df = t.get_funds_data().top_holdings
                if holdings_df is not None and not holdings_df.empty:
                    rows = []
                    for sym, row in holdings_df.iterrows():
                        name = str(row.get("Name", "")).strip()
                        pct = float(row.get("Holding Percent", 0)) * 100
                        rows.append({"ticker": str(sym).strip(), "name": name, "weight": round(pct, 2)})
                    rows.sort(key=lambda x: x["weight"], reverse=True)
                    body = json.dumps({"etf": ticker, "holdings": rows})
                else:
                    body = json.dumps({"etf": ticker, "holdings": []})
                self.send_response(200)
            except Exception as e:
                body = json.dumps({"etf": ticker, "holdings": [], "error": str(e)})
                self.send_response(200)

        elif path.startswith("/price/"):
            ticker = path.split("/price/")[1].upper()
            try:
                t = yf.Ticker(ticker)
                info = t.fast_info
                price = round(info.last_price, 2) if info.last_price else None
                prev = info.previous_close
                change_pct = round((price - prev) / prev * 100, 2) if price and prev else None
                body = json.dumps({"ticker": ticker, "price": price, "change_pct": change_pct})
                self.send_response(200)
            except Exception as e:
                body = json.dumps({"error": str(e)})
                self.send_response(500)

        else:
            self.send_response(404)
            self.end_headers()
            return

        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode())


if __name__ == "__main__":
    server = HTTPServer(("localhost", 5001), Handler)
    print("Price API running on http://localhost:5001")
    server.serve_forever()
