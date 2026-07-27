import pandas as pd
import json

isin_to_ticker = {
    'US90353T1007': 'UBER',
    'US6701002056': 'NVO',
    'SE0012673267': 'EVO.ST',
    'US67066G1040': 'NVDA',
    'US91324P1021': 'UNH',
    'US7731211089': 'RKLB',
    'US7731221062': 'RKLB',
    'US58733R1023': 'MELI',
    'AU0000185993': 'IREN',
    'US6837121036': 'OPEN',
    'US4330001060': 'HIMS',
    'US17253J1060': 'CIFR',
    'ES0184262212': 'VIS.MC',
    'US00217D1000': 'ASTS',
    'US8200144058': 'SBET',
    'US01609W1027': 'BABA',
    'US5002551043': 'KSS',
    'US02079K3059': 'GOOGL',
    'ES0130670112': 'ELE.MC',
    'US88557W1018': 'QFIN',
    'ES0177542018': 'IAG.MC',
    'FR0000121014': 'MC.PA',
    'ES0684262928': 'VIS.MC',
    'ES0105122024': 'MVC.MC',
    'US6541061031': 'NKE',
    'ES0183746314': 'VID.MC',
    'ES0684262910': 'VIS.MC',
    'US1912161007': 'KO',
    'US30303M1027': 'META',
    'IE00B4ND3602': 'IGLN.L',
    'ES0148396007': 'ITX.MC'
}

df = pd.read_csv('Transactions.csv')

df['Número'] = df['Número'].fillna(0)
df['Precio'] = df['Precio'].str.replace(',', '.').astype(float)
df['Total EUR'] = df['Total EUR'].str.replace('.', '').str.replace(',', '.').astype(float)
df['Tipo de cambio'] = df['Tipo de cambio'].str.replace(',', '.').astype(float).fillna(1.0)
df['Fecha'] = pd.to_datetime(df['Fecha'], format='%d-%m-%Y')
df = df.sort_values('Fecha')

portfolio = {}

for index, row in df.iterrows():
    isin = row['ISIN']
    if pd.isna(isin) or isin not in isin_to_ticker:
        name = row['Producto']
        if 'ROCKET LAB' in str(name):
            ticker = 'RKLB'
        else:
            continue
    else:
        ticker = isin_to_ticker[isin]
    
    if 'RTS' in str(row['Producto']):
        continue

    shares_delta = float(row['Número'])
    price = float(row['Precio'])
    
    if ticker not in portfolio:
        portfolio[ticker] = {
            'ticker': ticker,
            'shares': 0.0,
            'average_price': 0.0,
            'total_cost': 0.0,
            'realized_pnl': 0.0,
            'total_shares_bought': 0.0,
            'total_shares_sold': 0.0,
            'total_sell_revenue': 0.0,
            'currency_code': row['Valor local'] if pd.notna(row['Valor local']) else 'USD'
        }
    
    p = portfolio[ticker]
    
    if shares_delta > 0: # Compra
        p['shares'] += shares_delta
        p['total_cost'] += (shares_delta * price)
        p['average_price'] = p['total_cost'] / p['shares']
        p['total_shares_bought'] += shares_delta
    elif shares_delta < 0: # Venta
        shares_sold = abs(shares_delta)
        p['total_shares_sold'] += shares_sold
        p['total_sell_revenue'] += (shares_sold * price)
        
        if shares_sold <= p['shares'] + 0.0001:
            cost_of_goods_sold = shares_sold * p['average_price']
            sale_revenue = shares_sold * price
            p['realized_pnl'] += (sale_revenue - cost_of_goods_sold)
            
            p['shares'] -= shares_sold
            p['total_cost'] -= cost_of_goods_sold
            if p['shares'] <= 0.0001:
                p['shares'] = 0.0
                p['average_price'] = 0.0
                p['total_cost'] = 0.0

active_portfolio = []
closed_portfolio = []

for p in portfolio.values():
    if p['shares'] > 0:
        active_portfolio.append(p)
    if p['total_shares_sold'] > 0 and p['shares'] < 0.0001:
        # Calcular precio medio de venta
        p['average_sell_price'] = p['total_sell_revenue'] / p['total_shares_sold'] if p['total_shares_sold'] > 0 else 0
        closed_portfolio.append(p)

with open('data/portfolio.json', 'w') as f:
    json.dump(active_portfolio, f, indent=4)

with open('data/closed_portfolio.json', 'w') as f:
    json.dump(closed_portfolio, f, indent=4)

print(f"Parsed. Active: {len(active_portfolio)}, Closed: {len(closed_portfolio)}")
