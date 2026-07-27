import re

with open('routers/views.py', 'r') as f:
    content = f.read()

start_idx = content.find('@router.get("/portfolio", response_class=HTMLResponse)')
end_idx = content.find('@router.get("/ticker/{ticker}", response_class=HTMLResponse)')

new_portfolio_page = """@router.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request):
    import pandas as pd
    portfolio_data = storage.load_portfolio()
    
    tickers_str = " ".join([p['ticker'] for p in portfolio_data])
    current_prices = {}
    
    # Data for historical comparison (1 Year)
    hist_dates = []
    portfolio_hist = []
    benchmark_hist = []
    
    if tickers_str:
        try:
            # We add URTH (iShares MSCI World ETF) as our global benchmark
            query_str = f"{tickers_str} URTH"
            data = yf.download(query_str, period="1y", interval="1d", progress=False)
            
            if not data.empty and 'Close' in data:
                closes = data['Close']
                
                for t in [p['ticker'] for p in portfolio_data]:
                    if t in closes and pd.notna(closes[t].iloc[-1]):
                        current_prices[t] = {
                            'price': float(closes[t].iloc[-1]), 
                            'prev': float(closes[t].iloc[-2]) if len(closes) > 1 else float(closes[t].iloc[-1])
                        }

                # Calculate normalized historical performance
                closes = closes.dropna(how='all')
                hist_dates = closes.index.strftime('%Y-%m-%d').tolist()
                
                # Initialize portfolio value array
                daily_portfolio_values = [0.0] * len(hist_dates)
                
                for item in portfolio_data:
                    t = item['ticker']
                    shares = item['shares']
                    if t in closes:
                        # Forward fill any missing daily prices
                        ticker_series = closes[t].ffill()
                        for i in range(len(hist_dates)):
                            val = ticker_series.iloc[i]
                            if pd.notna(val):
                                daily_portfolio_values[i] += float(val) * shares
                                
                # Normalize portfolio to start at 100
                start_val = daily_portfolio_values[0] if len(daily_portfolio_values) > 0 and daily_portfolio_values[0] > 0 else 1
                portfolio_hist = [(v / start_val) * 100 for v in daily_portfolio_values]
                
                # Normalize benchmark
                if 'URTH' in closes:
                    bench_series = closes['URTH'].ffill()
                    bench_start = float(bench_series.iloc[0]) if len(bench_series) > 0 and float(bench_series.iloc[0]) > 0 else 1
                    benchmark_hist = [(float(v) / bench_start) * 100 for v in bench_series]
                    
        except Exception as e:
            print(f"Error fetching portfolio prices or history: {e}")

    total_invested_eur = 0
    total_current_value_eur = 0
    total_prev_value_eur = 0
    
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
    
    exchange_rates["EUR"] = 1.0

    chart_data = []

    for item in portfolio_data:
        ticker = item['ticker']
        price_data = current_prices.get(ticker, {'price': item['average_price'], 'prev': item['average_price']})
        
        item['current_price'] = price_data['price']
        item['prev_price'] = price_data['prev']
        
        item['current_value'] = item['shares'] * item['current_price']
        item['invested_value'] = item['shares'] * item['average_price']
        
        # Daily change for item
        item['daily_change_abs'] = item['current_price'] - item['prev_price']
        item['daily_change_pct'] = (item['daily_change_abs'] / item['prev_price']) * 100 if item['prev_price'] > 0 else 0
        
        if item['invested_value'] > 0:
            item['return_pct'] = ((item['current_value'] / item['invested_value']) - 1) * 100
            item['return_abs'] = item['current_value'] - item['invested_value']
        else:
            item['return_pct'] = 0
            item['return_abs'] = 0
            
        rate = exchange_rates.get(item['currency_code'], 1.0)
        
        item['invested_value_eur'] = item['invested_value'] * rate
        item['current_value_eur'] = item['current_value'] * rate
        item['prev_value_eur'] = (item['shares'] * item['prev_price']) * rate
            
        total_invested_eur += item['invested_value_eur']
        total_current_value_eur += item['current_value_eur']
        total_prev_value_eur += item['prev_value_eur']
        
        chart_data.append({
            "ticker": ticker,
            "value": item['current_value_eur']
        })
        
    total_return_abs_eur = total_current_value_eur - total_invested_eur
    total_return_pct_eur = ((total_current_value_eur / total_invested_eur) - 1) * 100 if total_invested_eur > 0 else 0
    
    total_daily_abs_eur = total_current_value_eur - total_prev_value_eur
    total_daily_pct_eur = (total_daily_abs_eur / total_prev_value_eur) * 100 if total_prev_value_eur > 0 else 0

    total_realized_pnl_eur = 0
    for item in portfolio_data:
        rate = exchange_rates.get(item.get('currency_code', 'USD'), 1.0)
        total_realized_pnl_eur += item.get('realized_pnl', 0) * rate

    if chart_data:
        chart_data.sort(key=lambda x: x['value'], reverse=True)
        filtered_chart_data = []
        otros_value = 0
        for cd in chart_data:
            if cd['value'] / total_current_value_eur < 0.02:
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
        "total_daily_abs": total_daily_abs_eur,
        "total_daily_pct": total_daily_pct_eur,
        "total_realized_pnl": total_realized_pnl_eur,
        "chart_labels": labels,
        "chart_values": values,
        "hist_dates": hist_dates,
        "portfolio_hist": portfolio_hist,
        "benchmark_hist": benchmark_hist
    })

"""

content = content[:start_idx] + new_portfolio_page + content[end_idx:]

with open('routers/views.py', 'w') as f:
    f.write(content)
print("Fixed views.py portfolio route")
