import re

with open('routers/views.py', 'r') as f:
    content = f.read()

# Replace portfolio_page definition
start_idx = content.find('@router.get("/portfolio", response_class=HTMLResponse)')
end_idx = content.find('@router.get("/ticker/{ticker}", response_class=HTMLResponse)')

new_portfolio_page = """@router.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request):
    portfolio_data = storage.load_portfolio()
    
    tickers_str = " ".join([p['ticker'] for p in portfolio_data])
    current_prices = {}
    
    if tickers_str:
        try:
            data = yf.download(tickers_str, period="1d", progress=False)
            if not data.empty and 'Close' in data:
                closes = data['Close']
                if isinstance(closes, pd.Series):
                    current_prices[portfolio_data[0]['ticker']] = float(closes.iloc[-1])
                else:
                    for t in [p['ticker'] for p in portfolio_data]:
                        if t in closes and pd.notna(closes[t].iloc[-1]):
                            current_prices[t] = float(closes[t].iloc[-1])
        except Exception as e:
            print(f"Error fetching portfolio prices: {e}")

    total_invested_eur = 0
    total_current_value_eur = 0
    
    # We will need exchange rates to EUR
    currencies = set()
    for item in portfolio_data:
        ticker = item['ticker']
        db = storage.load_data(ticker)
        currency_code = db.get("currency", "USD")
        item['currency_code'] = currency_code
        item['currency_symbol'] = get_currency_symbol(currency_code, ticker)
        currencies.add(currency_code)
    
    exchange_rates = {}
    if currencies:
        # e.g., USDEUR=X
        ex_tickers = [f"{c}EUR=X" for c in currencies if c != "EUR"]
        if ex_tickers:
            try:
                ex_data = yf.download(" ".join(ex_tickers), period="1d", progress=False)
                if not ex_data.empty and 'Close' in ex_data:
                    ex_closes = ex_data['Close']
                    if isinstance(ex_closes, pd.Series):
                        exchange_rates[ex_tickers[0].replace("EUR=X", "")] = float(ex_closes.iloc[-1])
                    else:
                        for c in currencies:
                            if c != "EUR":
                                t_ex = f"{c}EUR=X"
                                if t_ex in ex_closes and pd.notna(ex_closes[t_ex].iloc[-1]):
                                    exchange_rates[c] = float(ex_closes[t_ex].iloc[-1])
            except Exception as e:
                print(f"Error fetching exchange rates: {e}")
    
    exchange_rates["EUR"] = 1.0 # Base

    chart_data = []

    for item in portfolio_data:
        ticker = item['ticker']
        item['current_price'] = current_prices.get(ticker, item['average_price'])
        item['current_value'] = item['shares'] * item['current_price']
        item['invested_value'] = item['shares'] * item['average_price']
        
        if item['invested_value'] > 0:
            item['return_pct'] = ((item['current_value'] / item['invested_value']) - 1) * 100
            item['return_abs'] = item['current_value'] - item['invested_value']
        else:
            item['return_pct'] = 0
            item['return_abs'] = 0
            
        rate = exchange_rates.get(item['currency_code'], 1.0)
        
        item['invested_value_eur'] = item['invested_value'] * rate
        item['current_value_eur'] = item['current_value'] * rate
            
        total_invested_eur += item['invested_value_eur']
        total_current_value_eur += item['current_value_eur']
        
        chart_data.append({
            "ticker": ticker,
            "value": item['current_value_eur']
        })
        
    total_return_abs_eur = total_current_value_eur - total_invested_eur
    total_return_pct_eur = ((total_current_value_eur / total_invested_eur) - 1) * 100 if total_invested_eur > 0 else 0

    # Group small positions into 'Otros'
    if chart_data:
        chart_data.sort(key=lambda x: x['value'], reverse=True)
        filtered_chart_data = []
        otros_value = 0
        for cd in chart_data:
            if cd['value'] / total_current_value_eur < 0.02: # Less than 2%
                otros_value += cd['value']
            else:
                filtered_chart_data.append(cd)
        if otros_value > 0:
            filtered_chart_data.append({"ticker": "Otros", "value": otros_value})
        
        labels = [d['ticker'] for d in filtered_chart_data]
        values = [d['value'] for d in filtered_chart_data]
    else:
        labels = []
        values = []

    return templates.TemplateResponse("portfolio.html", {
        "request": request,
        "portfolio": portfolio_data,
        "total_invested": total_invested_eur,
        "total_current_value": total_current_value_eur,
        "total_return_abs": total_return_abs_eur,
        "total_return_pct": total_return_pct_eur,
        "chart_labels": labels,
        "chart_values": values
    })

"""

content = content[:start_idx] + new_portfolio_page + content[end_idx:]

with open('routers/views.py', 'w') as f:
    f.write(content)
print("Updated views.py successfully")
