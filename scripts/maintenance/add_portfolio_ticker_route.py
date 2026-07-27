import re

with open('routers/views.py', 'r') as f:
    content = f.read()

start_idx = content.find('@router.get("/portfolio", response_class=HTMLResponse)')

new_route = """@router.get("/portfolio/ticker/{ticker}", response_class=HTMLResponse)
async def portfolio_ticker_page(request: Request, ticker: str):
    ticker = ticker.upper()
    portfolio_data = storage.load_portfolio()
    closed_portfolio_data = storage.load_closed_portfolio()
    ledger_data = storage.load_ledger(ticker)
    
    item = next((p for p in portfolio_data if p['ticker'] == ticker), None)
    is_active = True
    if not item:
        item = next((p for p in closed_portfolio_data if p['ticker'] == ticker), None)
        is_active = False
        
    if not item:
        return RedirectResponse(url="/portfolio")
        
    # Get current price
    try:
        stock = yf.Ticker(ticker)
        current_price = stock.fast_info['lastPrice']
    except Exception:
        current_price = item.get('average_price', 0)
        
    item['current_price'] = current_price
    
    if is_active:
        item['current_value'] = item['shares'] * current_price
        item['invested_value'] = item['shares'] * item['average_price']
        if item['invested_value'] > 0:
            item['return_pct'] = ((item['current_value'] / item['invested_value']) - 1) * 100
            item['return_abs'] = item['current_value'] - item['invested_value']
        else:
            item['return_pct'] = 0
            item['return_abs'] = 0
            
    db = storage.load_data(ticker)
    currency_code = db.get("currency", item.get("currency_code", "USD"))
    currency_symbol = get_currency_symbol(currency_code, ticker)
    item['currency_symbol'] = currency_symbol
    
    # Calculate Total Profit (Realized + Dividends)
    realized = item.get('realized_pnl', 0)
    dividends = item.get('dividends_collected', 0)
    total_profit_closed = realized + dividends

    return templates.TemplateResponse("portfolio_ticker.html", {
        "request": request,
        "ticker": ticker,
        "item": item,
        "is_active": is_active,
        "ledger": ledger_data,
        "currency_symbol": currency_symbol,
        "total_profit_closed": total_profit_closed
    })

"""

new_content = content[:start_idx] + new_route + content[start_idx:]

with open('routers/views.py', 'w') as f:
    f.write(new_content)

print("Added /portfolio/ticker/{ticker} route")
