import yfinance as yf
import pandas as pd

def calculate_forward_pe(symbol):
    stock = yf.Ticker(symbol)
    
    # 1. Obtener el precio actual
    try:
        current_price = stock.fast_info['lastPrice']
    except Exception:
        current_price = stock.info.get('currentPrice') or stock.info.get('regularMarketPrice')

    print(f"--- {symbol} ---")
    print(f"Precio Actual: {current_price}")

    forward_pe = None
    method_used = None

    # 2. Intentar obtener EPS estimado mediante el método que encontraste
    # Probamos primero el acceso directo que mencionaste, pero asegurándonos de que se cargue
    try:
        # En algunas versiones hay que llamar al método para que se pueble _analysis
        # o usar directamente las propiedades nuevas
        try:
            df_estimates = stock.earnings_estimate
        except:
            df_estimates = stock.get_earnings_estimate()

        if df_estimates is not None and not df_estimates.empty:
            # El usuario sugirió .loc['+1y','avg']
            # Vamos a ver qué índices tiene el dataframe
            print(f"Indices encontrados en estimates: {df_estimates.index.tolist()}")
            
            if '+1y' in df_estimates.index:
                forward_eps = df_estimates.loc['+1y', 'avg']
                if pd.notna(forward_eps) and forward_eps != 0:
                    forward_pe = current_price / forward_eps
                    method_used = "earnings_estimate (+1y)"
            elif 'Next Year' in df_estimates.index: # A veces los nombres cambian
                forward_eps = df_estimates.loc['Next Year', 'avg']
                forward_pe = current_price / forward_eps
                method_used = "earnings_estimate (Next Year)"
    except Exception as e:
        print(f"Error al acceder a estimates para {symbol}: {e}")

    # 3. Fallback al método info pero calculado manualmente (más fiable que info['forwardPE'])
    if forward_pe is None:
        print("Cambiando a método info (fallback)...")
        forward_eps_info = stock.info.get('forwardEps')
        if forward_eps_info and forward_eps_info != 0:
            forward_pe = current_price / forward_eps_info
            method_used = "info.forwardEps (manual calculation)"
        else:
            forward_pe = stock.info.get('forwardPE')
            method_used = "info.forwardPE (direct)"

    return forward_pe, method_used

# Prueba
for ticker in ["AAPL", "MSFT"]:
    pe, method = calculate_forward_pe(ticker)
    print(f"Forward PE: {pe}")
    print(f"Método: {method}\n")
