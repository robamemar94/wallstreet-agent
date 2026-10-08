import yfinance as yf
import concurrent.futures
import time

tickers = ['GOOGL', 'AAPL', 'MSFT', 'LULU']

def get_cal(t):
    try:
        stock = yf.Ticker(t)
        cal = stock.calendar
        # Get latest dividend to guess next payout
        try:
            divs = stock.dividends
            last_div = float(divs.iloc[-1]) if not divs.empty else 0.0
        except Exception:
            last_div = 0.0
        return t, cal, last_div
    except Exception as e:
        return t, {}, 0.0

start = time.time()
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    results = dict(executor.map(get_cal, tickers))
print("Time taken:", time.time() - start)
print(results)
