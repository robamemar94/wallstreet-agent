import re

with open('routers/views.py', 'r') as f:
    content = f.read()

# Locate portfolio_page function
start_idx = content.find('@router.get("/portfolio", response_class=HTMLResponse)')
end_idx = content.find('@router.get("/ticker/{ticker}", response_class=HTMLResponse)')

old_block = content[start_idx:end_idx]

# We need to extract the previous day's close for both the portfolio elements and the total
# Currently, it uses period="1d" which only gives the last close. We need period="2d"
new_block = old_block.replace('period="1d"', 'period="2d"')
new_block = new_block.replace('current_prices[portfolio_data[0][\'ticker\']] = float(closes.iloc[-1])', 
                              "current_prices[portfolio_data[0]['ticker']] = {'price': float(closes.iloc[-1]), 'prev': float(closes.iloc[-2])}")
new_block = new_block.replace("current_prices[t] = float(closes[t].iloc[-1])", 
                              "current_prices[t] = {'price': float(closes[t].iloc[-1]), 'prev': float(closes[t].iloc[-2]) if len(closes) > 1 else float(closes[t].iloc[-1])}")

# Update loop to use dictionary and calculate daily changes
loop_start = new_block.find('for item in portfolio_data:')
loop_end = new_block.find('total_return_abs_eur =')

old_loop = new_block[loop_start:loop_end]

new_loop = """total_prev_value_eur = 0

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
        
    """

new_block = new_block.replace(old_loop, new_loop)

# Calculate total daily return
ret_calc_start = new_block.find('total_return_abs_eur =')
ret_calc_end = new_block.find('# Group small positions')

old_ret = new_block[ret_calc_start:ret_calc_end]

new_ret = """total_return_abs_eur = total_current_value_eur - total_invested_eur
    total_return_pct_eur = ((total_current_value_eur / total_invested_eur) - 1) * 100 if total_invested_eur > 0 else 0
    
    total_daily_abs_eur = total_current_value_eur - total_prev_value_eur
    total_daily_pct_eur = (total_daily_abs_eur / total_prev_value_eur) * 100 if total_prev_value_eur > 0 else 0

    """

new_block = new_block.replace(old_ret, new_ret)

# Update template variables
temp_vars_start = new_block.find('return templates.TemplateResponse("portfolio.html", {')
temp_vars_end = new_block.find('})', temp_vars_start) + 2

old_temp_vars = new_block[temp_vars_start:temp_vars_end]

new_temp_vars = """return templates.TemplateResponse("portfolio.html", {
        "request": request,
        "portfolio": portfolio_data,
        "total_invested": total_invested_eur,
        "total_current_value": total_current_value_eur,
        "total_return_abs": total_return_abs_eur,
        "total_return_pct": total_return_pct_eur,
        "total_daily_abs": total_daily_abs_eur,
        "total_daily_pct": total_daily_pct_eur,
        "chart_labels": labels,
        "chart_values": values,
        "hist_dates": hist_dates,
        "portfolio_hist": portfolio_hist,
        "benchmark_hist": benchmark_hist
    })"""

new_block = new_block.replace(old_temp_vars, new_temp_vars)

content = content[:start_idx] + new_block + content[end_idx:]

with open('routers/views.py', 'w') as f:
    f.write(content)
print("Updated views.py with daily changes successfully")
