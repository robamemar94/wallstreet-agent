import yfinance as yf
for t in ['MSFT', 'BKNG', 'GOOG']:
    info = yf.Ticker(t).info
    print(t)
    print("  dividendRate:", info.get('dividendRate'))
    print("  lastDividendValue:", info.get('lastDividendValue'))
