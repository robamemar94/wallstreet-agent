
import csv
from datetime import datetime

def get_ticker_and_exchange(product_name, exchange_ref):
    # Mapping of product name parts to tickers
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

    # Mapping of reference exchange codes to Google Finance codes
    exchange_map = {
        'NSY': 'NYSE',
        'NDQ': 'NASDAQ',
        'MAD': 'MCE',
        'EPA': 'EPA',
        'LSE': 'LON',
        'OMX': 'STO',
        'DEG': 'ETR', # Frankfurt
        'XNAS': 'NASDAQ',
        'ARCX': 'NASDAQ',
        'BATS': 'BATS',
        'CDED': 'NASDAQ',
        'SOHO': 'NASDAQ',
        'MEMX': 'MEMX',
        'JNST': 'NASDAQ',
        'MESI': 'MCE',
        'XMAD': 'MCE',
        'XPAR': 'EPA',
        'EPRL': 'NYSE',
        'XLON': 'LON',
        'EUCC': 'MCE',
        'XNYS': 'NYSE',
        'XSTO': 'STO'
    }

    ticker = None
    for name_part, t in product_to_ticker.items():
        if name_part.lower() in product_name.lower():
            ticker = t
            break

    if not ticker:
        # Simple fallback for unknown tickers
        parts = product_name.split()
        if len(parts) > 0:
            ticker = parts[0].upper()


    exchange = exchange_map.get(exchange_ref.strip(), exchange_ref)

    # Some tickers in portfolio.json have a suffix, like EVO.ST
    if ticker == 'EVO' and exchange == 'STO':
        return 'EVO.ST', None # In this case, Google Finance does not need the exchange

    if exchange:
         return ticker, exchange
    return ticker, None


def main():
    input_file = 'Transactions.csv'
    output_file = 'google_finance_import.csv'

    with open(input_file, 'r', encoding='utf-8') as infile, \
         open(output_file, 'w', newline='', encoding='utf-8') as outfile:

        reader = csv.DictReader(infile)
        # Google Finance Columns
        fieldnames = [
            'Symbol', 'Name', 'Type', 'Exchange', 'Date purchased', 'Shares',
            'Price', 'Commission', 'Cash currency', 'Notes'
        ]
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()

        for row in reader:
            try:
                # We are interested in purchases, which have a negative "Valor local"
                valor_local_str = row.get('Valor local', '0').replace('.', '').replace(',', '.')
                if not valor_local_str or float(valor_local_str) >= 0:
                    continue

                shares_str = row.get('Número', '0').replace('.','').replace(',', '.')
                shares = abs(float(shares_str))

                price_str = row.get('Precio', '0').replace('.','').replace(',', '.')
                price = float(price_str)

                commission_str = row.get('Costes de transacción y/o externos EUR', '0').replace('.','').replace(',', '.')
                commission = abs(float(commission_str)) if commission_str else 0.0

                date_str = row['Fecha']
                #Convert DD-MM-YYYY to YYYY-MM-DD
                purchase_date = datetime.strptime(date_str, '%d-%m-%Y').strftime('%Y-%m-%d')

                product_name = row['Producto']
                exchange_ref = row['Bolsa de referencia']

                ticker, exchange = get_ticker_and_exchange(product_name, exchange_ref)
                
                if not ticker:
                    print(f"Could not determine ticker for {product_name}")
                    continue

                # Prepare row for Google Finance CSV
                # Symbol needs to be in format EXCHANGE:TICKER
                google_finance_symbol = f"{exchange}:{ticker}" if exchange else ticker

                writer.writerow({
                    'Symbol': google_finance_symbol,
                    'Name': product_name,
                    'Date purchased': purchase_date,
                    'Shares': shares,
                    'Price': price,
                    'Commission': commission,
                    'Cash currency': 'EUR' # Assuming EUR based on commission currency
                })
            except (ValueError, KeyError) as e:
                print(f"Skipping row due to error: {row} - {e}")
                continue

    print(f"Successfully created '{output_file}'")

if __name__ == '__main__':
    main()
