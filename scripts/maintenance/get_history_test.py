import yfinance as yf
import pandas as pd

# Test fetching history for multiple tickers and normalizing to a base 100
tickers = ["AAPL", "MSFT"]
data = yf.download("AAPL MSFT URTH", period="1y", interval="1d", progress=False)
print(data['Close'].head())
