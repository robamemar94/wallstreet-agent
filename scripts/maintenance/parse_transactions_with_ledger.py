import pandas as pd
import json
import yfinance as yf
import os
import sys

# Agregar el directorio raíz al path para poder importar módulos de la app
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.infrastructure.db.database import SessionLocal
from app.infrastructure.db.models import DBTransaction

isin_to_ticker = {
    'US90353T1007': 'UBER', 'US6701002056': 'NVO', 'SE0012673267': 'EVO.ST',
    'US67066G1040': 'NVDA', 'US91324P1021': 'UNH', 'US7731211089': 'RKLB',
    'US7731221062': 'RKLB', 'US58733R1023': 'MELI', 'AU0000185993': 'IREN',
    'US6837121036': 'OPEN', 'US4330001060': 'HIMS', 'US17253J1060': 'CIFR',
    'ES0184262212': 'VIS.MC', 'US00217D1000': 'ASTS', 'US8200144058': 'SBET',
    'US01609W1027': 'BABA', 'US5002551043': 'KSS', 'US02079K3059': 'GOOGL',
    'ES0130670112': 'ELE.MC', 'US88557W1018': 'QFIN', 'ES0177542018': 'IAG.MC',
    'FR0000121014': 'MC.PA', 'ES0684262928': 'VIS.MC', 'ES0105122024': 'MVC.MC',
    'US6541061031': 'NKE', 'ES0183746314': 'VID.MC', 'ES0684262910': 'VIS.MC',
    'US1912161007': 'KO', 'US30303M1027': 'META', 'IE00B4ND3602': 'IGLN.L',
    'ES0148396007': 'ITX.MC'
}

# 1. Cargar Cachés Históricos
DIV_CACHE_FILE = 'data/historical_dividends.json'
SPLIT_CACHE_FILE = 'data/historical_splits.json'
MANUAL_TX_FILE = 'data/manual_transactions.json'

def load_cache(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
    return {}

master_dividend_history = load_cache(DIV_CACHE_FILE)
master_split_history = load_cache(SPLIT_CACHE_FILE)

# 2. Procesar CSV de Transacciones
raw_transactions = []
all_tickers = set()

if os.path.exists('Transactions.csv'):
    df = pd.read_csv('Transactions.csv')
    df['Número'] = df['Número'].fillna(0)
    df['Precio'] = df['Precio'].astype(str).str.replace(',', '.').astype(float)
    df['Fecha'] = pd.to_datetime(df['Fecha'], format='%d-%m-%Y')

    for index, row in df.iterrows():
        isin = row['ISIN']
        ticker = isin_to_ticker.get(isin)
        if not ticker:
            name = row['Producto']
            if pd.notna(name) and 'ROCKET LAB' in str(name): ticker = 'RKLB'
            else: continue
        
        if pd.notna(row['Producto']) and 'RTS' in str(row['Producto']): continue

        shares_delta = float(row['Número'])
        if shares_delta == 0: continue
        
        price = float(row['Precio'])
        date_str = row['Fecha'].strftime('%Y-%m-%d')
        all_tickers.add(ticker)
        
        raw_transactions.append({
            'date': date_str,
            'type': 'BUY' if shares_delta > 0 else 'SELL',
            'ticker': ticker,
            'shares': abs(shares_delta),
            'price': price,
            'currency': row['Unnamed: 8'] if pd.notna(row['Unnamed: 8']) else 'USD'
        })

# 2b. Procesar Transacciones Manuales (Fuente única de verdad: DB si es posible, si no JSON)
manual_entries = []
db_loaded = False
try:
    db = SessionLocal()
    db_txs = db.query(DBTransaction).all()
    if db_txs:
        print(f"Cargando {len(db_txs)} transacciones manuales desde la Base de Datos...")
        for tx in db_txs:
            tx_type = tx.type.value if hasattr(tx.type, 'value') else str(tx.type)
            manual_entries.append({
                'date': tx.date,
                'type': tx_type,
                'ticker': tx.ticker,
                'shares': tx.shares,
                'price': tx.price,
                'currency': tx.currency,
                'total': tx.total
            })
        db_loaded = True
    db.close()
except Exception as e:
    print(f"Error al cargar desde DB: {e}")

if not db_loaded:
    if os.path.exists(MANUAL_TX_FILE):
        with open(MANUAL_TX_FILE, 'r') as f:
            manual_data = json.load(f)
            print(f"Cargando {len(manual_data)} transacciones manuales desde JSON...")
            for tx in manual_data:
                manual_entries.append({
                    'date': tx['date'],
                    'type': tx['type'],
                    'ticker': tx['ticker'],
                    'shares': tx['shares'],
                    'price': tx['price'],
                    'currency': tx.get('currency', 'USD'),
                    'total': tx.get('total')
                })

for m in manual_entries:
    all_tickers.add(m['ticker'])
    raw_transactions.append(m)

# 3. Actualizar Cachés (Splits y Dividendos)
print("Actualizando historial de splits y dividendos...")
for ticker in all_tickers:
    try:
        stock = yf.Ticker(ticker)
        # Splits
        splits = stock.splits
        if not splits.empty:
            if ticker not in master_split_history: master_split_history[ticker] = {}
            splits.index = splits.index.tz_convert(None)
            for s_date, s_ratio in splits.items():
                master_split_history[ticker][s_date.strftime('%Y-%m-%d')] = float(s_ratio)
        
        # Dividendos
        divs = stock.dividends
        if not divs.empty:
            if ticker not in master_dividend_history: master_dividend_history[ticker] = {}
            divs.index = divs.index.tz_convert(None)
            for d_date, d_amount in divs.items():
                master_dividend_history[ticker][d_date.strftime('%Y-%m-%d')] = float(d_amount)
    except Exception as e:
        print(f"Error actualizando {ticker}: {e}")

with open(DIV_CACHE_FILE, 'w') as f: json.dump(master_dividend_history, f, indent=4)
with open(SPLIT_CACHE_FILE, 'w') as f: json.dump(master_split_history, f, indent=4)

# 4. Construir Timeline y Calcular Portfolio
all_events = raw_transactions.copy()

# Encontrar la fecha de la primera transacción para cada ticker
first_tx_dates = {}
for tx in raw_transactions:
    ticker = tx['ticker']
    dt = pd.to_datetime(tx['date'])
    if ticker not in first_tx_dates or dt < first_tx_dates[ticker]:
        first_tx_dates[ticker] = dt

for ticker, splits in master_split_history.items():
    if ticker not in all_tickers: continue
    first_date = first_tx_dates.get(ticker)
    for d_str, ratio in splits.items():
        if first_date and pd.to_datetime(d_str) >= first_date:
            all_events.append({
                'date': d_str,
                'type': 'SPLIT',
                'ticker': ticker,
                'shares': ratio,
                'price': 0.0
            })

# Ordenar por fecha. SPLIT va primero en caso de empate.
all_events.sort(key=lambda x: (x['date'], 0 if x['type'] == 'SPLIT' else 1))

portfolio = {}
ledger = {}

for ev in all_events:
    ticker = ev['ticker']
    if ticker not in portfolio:
        portfolio[ticker] = {
            'ticker': ticker, 'shares': 0.0, 'average_price': 0.0, 'total_cost': 0.0,
            'realized_pnl': 0.0, 'total_shares_bought': 0.0, 'total_shares_sold': 0.0,
            'total_sell_revenue': 0.0, 'currency_code': ev.get('currency', 'USD'),
            'dividends_collected': 0.0
        }
        ledger[ticker] = []
    
    p = portfolio[ticker]
    
    if ev['type'] in ['BUY', 'SELL']:
        shares_delta = ev['shares'] if ev['type'] == 'BUY' else -ev['shares']
        price = ev['price']
        
        ledger[ticker].append({'date': ev['date'], 'type': ev['type'], 'shares': abs(shares_delta), 'price': price, 'total': abs(shares_delta) * price})
        
        if ev['type'] == 'BUY':
            p['shares'] += shares_delta
            p['total_cost'] += (ev['shares'] * price)
            p['average_price'] = p['total_cost'] / p['shares'] if p['shares'] > 0 else 0
            p['total_shares_bought'] += ev['shares']
        else:
            shares_sold = abs(shares_delta)
            p['total_shares_sold'] += shares_sold
            p['total_sell_revenue'] += (shares_sold * price)
            
            if p['shares'] > 0.0001:
                p['realized_pnl'] += (shares_sold * price - shares_sold * p['average_price'])
                p['shares'] -= shares_sold
                p['total_cost'] -= (shares_sold * p['average_price'])
            else:
                p['realized_pnl'] += shares_sold * price
                p['shares'] -= shares_sold

            if p['shares'] <= 0.0001: 
                p['shares'] = 0.0
                p['average_price'] = 0.0
                p['total_cost'] = 0.0
            
    elif ev['type'] == 'SPLIT':
        ratio = ev['shares']
        if ratio > 0 and ratio != 1.0:
            ledger[ticker].append({'date': ev['date'], 'type': 'SPLIT', 'shares': ratio, 'price': 0.0, 'total': 0.0})
            p['shares'] *= ratio
            p['average_price'] /= ratio
            p['total_shares_bought'] *= ratio
            p['total_shares_sold'] *= ratio

# 5. Aplicar Dividendos al Ledger
for ticker, txs in ledger.items():
    daily_balance = {}
    curr = 0
    txs_sorted = sorted(txs, key=lambda x: (x['date'], 0 if x['type'] == 'SPLIT' else 1))
    
    for tx in txs_sorted:
        dt = pd.to_datetime(tx['date'])
        if tx['type'] == 'SPLIT':
            curr *= tx['shares']
        else:
            curr += tx['shares'] if tx['type'] == 'BUY' else -tx['shares']
        daily_balance[dt] = curr
    
    if not daily_balance: continue
    balance_series = pd.Series(daily_balance).sort_index()

    ticker_divs = master_dividend_history.get(ticker, {})
    total_divs_collected = 0
    
    for d_date_str, d_amount in ticker_divs.items():
        if pd.isna(d_amount) or d_amount <= 0: continue
        d_date = pd.to_datetime(d_date_str)
        held_before = balance_series[balance_series.index <= d_date]
        if not held_before.empty:
            shares_at_ex_date = held_before.iloc[-1]
            if shares_at_ex_date > 0.0001:
                payout = shares_at_ex_date * d_amount
                total_divs_collected += payout
                ledger[ticker].append({'date': d_date_str, 'type': 'DIVIDEND', 'shares': shares_at_ex_date, 'price': d_amount, 'total': payout})

    portfolio[ticker]['dividends_collected'] = total_divs_collected
    ledger[ticker] = sorted(ledger[ticker], key=lambda x: x['date'])

# 6. Guardar Resultados Finales
active_portfolio = [p for p in portfolio.values() if p['shares'] > 0.0001]
closed_portfolio = [p for p in portfolio.values() if p['total_shares_sold'] > 0 and p['shares'] <= 0.0001]
for p in closed_portfolio: 
    p['average_sell_price'] = p['total_sell_revenue'] / p['total_shares_sold'] if p['total_shares_sold'] > 0 else 0

with open('data/portfolio.json', 'w') as f: json.dump(active_portfolio, f, indent=4)
with open('data/closed_portfolio.json', 'w') as f: json.dump(closed_portfolio, f, indent=4)
with open('data/ledger.json', 'w') as f: json.dump(ledger, f, indent=4)

print(f"Finalizado. Cachés actualizados en data/")
