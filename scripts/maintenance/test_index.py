import json, os
import yfinance as yf
import pandas as pd
import storage

ticker_names = storage.get_all_analyzed_tickers()
print(f"Tickers found: {ticker_names}")

daily_changes = {}
try:
    if ticker_names:
        tickers_str = " ".join(ticker_names)
        data = yf.download(tickers_str, period="2d", progress=False)
        print("Data columns:", data.columns)
        if not data.empty and 'Close' in data:
            closes = data['Close']
            if isinstance(closes, pd.Series):
                pct_change = closes.pct_change().iloc[-1]
                daily_changes[ticker_names[0]] = float(pct_change * 100)
            else:
                pct_changes = closes.pct_change().iloc[-1]
                for t in ticker_names:
                    val = pct_changes.get(t)
                    if pd.notna(val):
                        daily_changes[t] = float(val * 100)
except Exception as e:
    print(f"Error fetching daily changes: {e}")

print(f"Daily changes: {daily_changes}")

tickers = []
for t in ticker_names:
    db = storage.load_data(t)
    sector = db.get("sector", "-")
    dc = daily_changes.get(t, 0.0)
    tickers.append({"name": t, "sector": sector, "daily_change": dc})

print("Result tickers:", tickers[:3])
