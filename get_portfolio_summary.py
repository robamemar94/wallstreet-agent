
import csv
import json
from datetime import datetime

def get_ticker_and_exchange(product_name, exchange_ref):
    # This function is a copy from the previous script to ensure consistency
    product_to_ticker = {
        'UBER TECHNOLOGIES INC': 'UBER',
        'NOVO NORDISK A/S': 'NVO',
        'EVOLUTION AB (PUBL)': 'EVO',
        'NVIDIA CORP': 'NVDA',
        'UNITEDHEALTH GROUP INC': 'UNH',
        'ROCKET LAB': 'RKLB',
        'MERCADOLIBRE INC': 'MELI',
        'IREN LTD': 'IREN',
        'OPENDOOR TECHNOLOGIES': 'OPEN',
        'HIMS & HERS HEALTH': 'HIMS',
        'CIPHER DIGITAL': 'CIFR',
        'VISCOFAN': 'VIS',
        'AST SPACEMOBILE': 'ASTS',
        'SHARPLINK': 'SBT',
        'ALPHABET INC': 'GOOGL',
        'ENDESA': 'ELE',
        'QFIN HOLDINGS': 'QFIN',
        'INTERNATIONAL CONSOLIDATED AIRLINES': 'IAG',
        'LVMH MOET HENNESSY LOUIS VUITTON': 'MC',
        'COCA-COLA': 'KO',
        'NIKE': 'NKE',
        'METROVACESA': 'MVC',
        'VIDRALA': 'VID',
        'ALIBABA GROUP': 'BABA',
        'ISHARES PHYSICAL GOLD': 'SGLN',
        'INDUSTRIA DE DISENO TEXTIL': 'ITX',
        'META PLATFORMS': 'META'
    }
    
    ticker = None
    for name_part, t in product_to_ticker.items():
        if name_part.lower() in product_name.lower():
            ticker = t
            break

    # Fallback for tickers that are not in the main mapping
    if not ticker:
        if 'google' in product_name.lower() or 'alphabet' in product_name.lower():
            ticker = 'GOOGL'
        elif 'rocket lab' in product_name.lower():
            ticker = 'RKLB'
        elif 'sharplink' in product_name.lower():
            ticker = 'SBET' # Assuming SBET based on portfolio

    # Correcting for specific tickers that might have variations in name
    if 'ROCKET LAB USA INC' in product_name:
        ticker = 'RKLB'
    if 'EVOLUTION AB' in product_name:
        ticker = 'EVO.ST'

    return ticker

def get_portfolio_summary():
    portfolio_file = 'data/portfolio.json'
    transactions_file = 'Transactions.csv'
    
    portfolio_data = {}
    
    # 1. Read portfolio.json to get current holdings and average prices
    try:
        with open(portfolio_file, 'r', encoding='utf-8') as f:
            portfolio = json.load(f)
            for asset in portfolio:
                # Handle cases where ticker might have a suffix like 'EVO.ST'
                ticker = asset['ticker']
                if ticker == 'EVO.ST':
                    # Use a consistent key for matching with transactions
                    internal_ticker = 'EVO.ST'
                else:
                    internal_ticker = ticker.split('.')[0]
                
                portfolio_data[internal_ticker] = {
                    'shares': asset['shares'],
                    'average_price': asset['average_price'],
                    'currency': asset.get('currency_code', 'N/A'),
                    'first_purchase_date': None
                }

    except FileNotFoundError:
        print(f"Error: '{portfolio_file}' not found.")
        return

    # 2. Read Transactions.csv to find the first purchase date
    try:
        with open(transactions_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            # Sort transactions by date to easily find the first one
            sorted_transactions = sorted(list(reader), key=lambda row: datetime.strptime(row['Fecha'], '%d-%m-%Y'))

            for row in sorted_transactions:
                valor_local_str = row.get('Valor local', '0').replace('.', '').replace(',', '.')
                # We only care about purchases (negative value)
                if not valor_local_str or float(valor_local_str) >= 0:
                    continue
                
                product_name = row['Producto']
                exchange_ref = row['Bolsa de referencia']
                ticker = get_ticker_and_exchange(product_name, exchange_ref)

                if ticker and ticker in portfolio_data:
                    # If the first purchase date hasn't been set yet, set it
                    if portfolio_data[ticker]['first_purchase_date'] is None:
                        purchase_date = datetime.strptime(row['Fecha'], '%d-%m-%Y')
                        portfolio_data[ticker]['first_purchase_date'] = purchase_date.strftime('%Y-%m-%d')

    except FileNotFoundError:
        print(f"Error: '{transactions_file}' not found.")
        return
        
    # 3. Print the summary
    print(f"{'Ticker':<10} | {'Acciones':>10} | {'Fecha Primera Compra':<22} | {'Precio Medio':>15} | {'Moneda'}")
    print(f"-"*80)
    for ticker, data in portfolio_data.items():
        shares = data['shares']
        date_str = data['first_purchase_date'] if data['first_purchase_date'] else 'No encontrada'
        avg_price = f"{data['average_price']:.2f}"
        currency = data['currency']
        print(f"{ticker:<10} | {shares:>10} | {date_str:<22} | {avg_price:>15} | {currency}")

if __name__ == '__main__':
    get_portfolio_summary()
