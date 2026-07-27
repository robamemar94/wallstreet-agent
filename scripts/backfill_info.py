import json, os
import yfinance as yf

system_files = {"portfolio.json", "closed_portfolio.json", "ledger.json", 
                "manual_transactions.json", "historical_dividends.json", 
                "historical_splits.json", "performance_cache.json", "alerts.json"}

for f in os.listdir('data'):
    if f.endswith('.json') and f not in system_files:
        ticker = f.replace('.json', '')
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            db = json.load(open(f'data/{f}'))
            changed = False
            
            if 'longName' in info or 'shortName' in info:
                name = info.get('longName') or info.get('shortName')
                if db.get('company_name') != name:
                    db['company_name'] = name
                    changed = True
            if 'country' in info:
                country = info.get('country')
                if db.get('country') != country:
                    db['country'] = country
                    changed = True
            if 'sector' in info:
                sector = info.get('sector')
                if db.get('sector') != sector:
                    db['sector'] = sector
                    changed = True
            
            if changed:
                with open(f'data/{f}', 'w') as file:
                    json.dump(db, file, indent=4)
                print(f"Updated {ticker} with sector {db.get('sector')}")
        except Exception as e:
            print(f"Error updating {ticker}: {e}")
