import yfinance as yf
msft = yf.Ticker("MSFT")
info = msft.info
print("Keys with 'div' or 'date' or 'pay':")
for k, v in info.items():
    if 'div' in k.lower() or 'date' in k.lower() or 'pay' in k.lower():
        print(f"{k}: {v}")
print("\nCalendar:")
print(msft.calendar)
