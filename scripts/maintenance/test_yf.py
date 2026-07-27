import yfinance as yf
import pandas as pd

ticker_names = ["AAPL", "MSFT", "GOOG"]
tickers_str = " ".join(ticker_names)
daily_changes = {}

data = yf.download(tickers_str, period="2d", progress=False)
if not data.empty and 'Close' in data:
    closes = data['Close']
    if isinstance(closes, pd.Series):
        pct_change = closes.pct_change().iloc[-1]
        daily_changes[ticker_names[0]] = pct_change * 100
    else:
        pct_changes = closes.pct_change().iloc[-1]
        for t in ticker_names:
            val = pct_changes.get(t)
            if pd.notna(val):
                daily_changes[t] = float(val * 100)

print(daily_changes)
