import math
import yfinance as yf
import pandas as pd
import logging

logger = logging.getLogger(__name__)

def calculate_custom_metrics(info, close_prices, stock=None):
    """Calcula métricas personalizadas como variaciones de precio, FCF Yield, CAGR, etc."""
    custom_metrics = {}

    # 1. Precio y variación
    current_price = info.get('currentPrice')
    previous_close = info.get('previousClose')
    if not current_price and close_prices:
        current_price = close_prices[-1]
    if not previous_close and len(close_prices) > 1:
        previous_close = close_prices[-2]

    custom_metrics['currentPrice'] = current_price
    if current_price and previous_close and previous_close > 0:
        custom_metrics['priceChange'] = current_price - previous_close
        custom_metrics['priceChangePct'] = (custom_metrics['priceChange'] / previous_close) * 100
        custom_metrics['absPriceChangePct'] = abs(custom_metrics['priceChangePct'])
    else:
        custom_metrics['priceChange'] = None
        custom_metrics['priceChangePct'] = None
        custom_metrics['absPriceChangePct'] = None

    # 2. FCF Yield con ajuste de divisa
    fcf = info.get('freeCashflow', 0)
    market_cap = info.get('marketCap', 1)

    fin_currency = info.get('financialCurrency', 'USD')
    stock_currency = info.get('currency', 'USD')
    exchange_rate = 1.0

    if fin_currency and stock_currency and fin_currency != stock_currency:
        try:
            ex_ticker = f"{fin_currency}{stock_currency}=X"
            ex_info = yf.Ticker(ex_ticker).info
            exchange_rate = ex_info.get('previousClose', 1.0)
        except Exception as e:
            logger.warning(f"Error al obtener tipo de cambio {fin_currency}->{stock_currency}: {e}")

    fcf_adjusted = fcf * exchange_rate
    custom_metrics['fcfYield'] = fcf_adjusted / market_cap if fcf else 0

    # 3. Forward FCF Yield
    # Intentar obtener Forward EPS de forma más precisa
    fwd_eps = None
    if stock:
        try:
            estimates = stock.earnings_estimate
            if '+1y' in estimates.index:
                fwd_eps = estimates.loc['+1y', 'avg']
        except Exception:
            pass
    
    if fwd_eps is None or pd.isna(fwd_eps):
        fwd_eps = info.get('forwardEps', 0)

    trl_eps = info.get('trailingEps', 0)
    
    fwd_fcf_yield = 0
    if fcf_adjusted and market_cap and fwd_eps and trl_eps and trl_eps > 0:
        fwd_fcf_yield = (fcf_adjusted * (fwd_eps / trl_eps)) / market_cap
    elif fcf_adjusted and market_cap:
        fwd_fcf_yield = fcf_adjusted / market_cap

    custom_metrics['fwdFcfYield'] = fwd_fcf_yield
    
    return custom_metrics

def calculate_cagr_metrics(stock):
    """Calcula CAGR de ingresos a 3 y 5 años, y CAGR del precio histórico máximo."""
    cagr_metrics = {'cagr3Y': "N/A", 'cagr5Y': "N/A", 'cagrPriceMax': "N/A", 'cagrPriceMaxYears': "N/A"}
    
    # 1. Ingresos CAGR
    try:
        fin = stock.financials
        if 'Total Revenue' in fin.index:
            revs = fin.loc['Total Revenue'].dropna()

            # 3 Year CAGR
            if len(revs) >= 4:
                latest = revs.iloc[0]
                oldest_3y = revs.iloc[3]
                if oldest_3y > 0:
                    cagr_metrics['cagr3Y'] = (latest / oldest_3y) ** (1 / 3) - 1

            # 5 Year CAGR
            if len(revs) >= 6:
                latest = revs.iloc[0]
                oldest_5y = revs.iloc[5]
                if oldest_5y > 0:
                    cagr_metrics['cagr5Y'] = (latest / oldest_5y) ** (1 / 5) - 1
            elif len(revs) == 5:
                latest = revs.iloc[0]
                oldest_4y = revs.iloc[4]
                if oldest_4y > 0:
                    cagr_metrics['cagr5Y'] = (latest / oldest_4y) ** (1 / 4) - 1
    except Exception as e:
        logger.warning(f"Error calculando Revenue CAGR: {e}")
        
    # 2. Precio CAGR desde Inception
    try:
        hist_max = stock.history(period="max")
        if not hist_max.empty and len(hist_max) > 250: # Al menos un año de datos aprox
            first_date = hist_max.index[0]
            last_date = hist_max.index[-1]
            years = (last_date - first_date).days / 365.25
            
            first_price = hist_max['Close'].iloc[0]
            last_price = hist_max['Close'].iloc[-1]
            
            if years > 0 and first_price > 0:
                cagr_price = (last_price / first_price) ** (1 / years) - 1
                cagr_metrics['cagrPriceMax'] = cagr_price
                cagr_metrics['cagrPriceMaxYears'] = round(years)
    except Exception as e:
        logger.warning(f"Error calculando Price CAGR (Max): {e}")
    
    return cagr_metrics

def get_estimated_growth(stock):
    """Obtiene el crecimiento estimado de ingresos para el próximo año."""
    try:
        rev_est = stock.revenue_estimate
        if rev_est is not None and not rev_est.empty and '+1y' in rev_est.index:
            val = rev_est.loc['+1y', 'growth']
            if val is not None and not math.isnan(float(val)):
                return float(val)
    except Exception:
        pass
    return "N/A"

def get_extended_stats(info, stock=None, db=None):
    """Extrae estadísticas financieras priorizando los datos manuales de la auditoría."""
    stats = {
        'profile': {},
        'margins': {},
        'returns': {},
        'valuation_ttm': {},
        'valuation_ntm': {},
        'health': {},
        'growth': {},
        'dividends': {}
    }

    # Intentar obtener datos manuales TTM o del año más reciente
    manual_fin = db.get("financials_hist_manual") if db else None
    
    def get_latest_manual(metric_key):
        if manual_fin and metric_key in manual_fin and len(manual_fin[metric_key]) > 0:
            return manual_fin[metric_key][-1] # Tomamos el último elemento (más reciente/LTM)
        return None

    def safe_get(d, key, default="N/A"):
        val = d.get(key)
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return default
        return val

    # Profile
    rev_ltm = get_latest_manual('revenue')
    stats['profile'] = {
        'Market Cap': safe_get(info, 'marketCap'),
        'Enterprise Value (EV)': safe_get(info, 'enterpriseValue'),
        'Shares Outstanding': safe_get(info, 'sharesOutstanding'),
        'Revenue (TTM)': rev_ltm if rev_ltm else safe_get(info, 'totalRevenue'),
        'Employees': safe_get(info, 'fullTimeEmployees')
    }

    # Margins
    net_ltm = get_latest_manual('net_income')
    fcf_ltm = get_latest_manual('fcf')
    
    stats['margins'] = {
        'Gross Margin': safe_get(info, 'grossMargins'),
        'EBITDA Margin': safe_get(info, 'ebitdaMargins'),
        'Operating Margin': safe_get(info, 'operatingMargins'),
        'Net Margin': (net_ltm / rev_ltm) if net_ltm and rev_ltm else safe_get(info, 'profitMargins'),
        'FCF Margin': (fcf_ltm / rev_ltm) if fcf_ltm and rev_ltm else "N/A"
    }

    # Returns
    stats['returns'] = {
        'ROA (TTM)': safe_get(info, 'returnOnAssets'),
        'ROE (TTM)': safe_get(info, 'returnOnEquity'),
        'ROIC (TTM)': safe_get(info, 'returnOnInvestment')
    }

    # Valuation (TTM)
    mkt_cap = info.get('marketCap', 0)
    eps_ltm = get_latest_manual('eps')
    curr_price = info.get('currentPrice', 1)
    
    stats['valuation_ttm'] = {
        'P/E (Trailing)': (curr_price / eps_ltm) if eps_ltm and eps_ltm > 0 else safe_get(info, 'trailingPE'),
        'P/B': safe_get(info, 'priceToBook'),
        'EV/Sales': safe_get(info, 'enterpriseToRevenue'),
        'EV/EBITDA': safe_get(info, 'enterpriseToEbitda'),
        'P/FCF': (mkt_cap / fcf_ltm) if mkt_cap and fcf_ltm and fcf_ltm > 0 else "N/A"
    }

    # Valuation (NTM/Forward)
    stats['valuation_ntm'] = {
        'Price Target (Mean)': safe_get(info, 'targetMeanPrice'),
        'P/E (Forward)': safe_get(info, 'forwardPE'),
        'PEG Ratio': safe_get(info, 'pegRatio'),
    }

    # Financial Health
    stats['health'] = {
        'Total Cash': safe_get(info, 'totalCash'),
        'Total Debt': safe_get(info, 'totalDebt'),
        'Net Debt': "N/A",
        'Debt/Equity': safe_get(info, 'debtToEquity'),
        'Current Ratio': safe_get(info, 'currentRatio')
    }
    if info.get('totalDebt') is not None and info.get('totalCash') is not None:
        stats['health']['Net Debt'] = info['totalDebt'] - info['totalCash']

    # Growth (CAGR)
    stats['growth'] = {
        'Rev Growth (YoY)': safe_get(info, 'revenueGrowth'),
        'Earnings Growth (YoY)': safe_get(info, 'earningsGrowth'),
        'Rev Growth (Fwd 1Y)': "N/A"
    }
    if stock:
        try:
            rev_est = stock.revenue_estimate
            if rev_est is not None and not rev_est.empty and '+1y' in rev_est.index:
                stats['growth']['Rev Growth (Fwd 1Y)'] = rev_est.loc['+1y', 'growth']
        except: pass

    # Dividends
    stats['dividends'] = {
        'Yield': safe_get(info, 'dividendYield'),
        'Payout Ratio': safe_get(info, 'payoutRatio'),
        'Dividend Rate (DPS)': safe_get(info, 'dividendRate'),
        '5Y Avg Yield': safe_get(info, 'fiveYearAvgDividendYield')
    }

    return stats

def get_financials_history(ticker, stock, db):
    """Obtiene el historial financiero priorizando los datos manuales de la auditoría."""
    # 1. Prioridad Máxima: Datos introducidos manualmente en Auditoría
    # Devolvemos la estructura completa si existe, para que el front-end 
    # pueda mostrar todas las líneas en el histórico.
    financials_full = db.get("full_audit_structure")
    if financials_full and "years" in financials_full and len(financials_full["years"]) > 0:
        # Fusionamos chart_metrics en el nivel superior para mantener compatibilidad
        # con las gráficas originales que buscan financials_hist['revenue'], etc.
        data = financials_full.copy()
        if "chart_metrics" in data:
            for k, v in data["chart_metrics"].items():
                if k not in data: data[k] = v
        return data

    financials_manual = db.get("financials_hist_manual")
    if financials_manual and "years" in financials_manual and len(financials_manual["years"]) > 0:
        return financials_manual

    # 2. Preferencia 15y (Generados por IA previa)
    financials_hist_15y = db.get("financials_hist_15y")
    if financials_hist_15y and "years" in financials_hist_15y:
        return financials_hist_15y
        
    financials_hist_10y = db.get("financials_hist_10y")
    if financials_hist_10y and "years" in financials_hist_10y:
        return financials_hist_10y
        
    # 2. Fallback a AlphaVantage
    try:
        av = AlphaVantageFetcher()
        financials_hist = av.get_financials(ticker)
        if financials_hist and financials_hist.get('years'):
            return financials_hist
    except Exception as e:
        logger.error(f"Fallo en AlphaVantage fetch: {e}")

    # 3. Último recurso: yfinance directo (solo 4 años aprox)
    financials_hist = {'years': [], 'revenue': [], 'net_income': [], 'eps': [], 'fcf': []}
    try:
        fin = stock.financials
        cf = stock.cashflow
        all_dates = sorted(list(set(fin.columns) | set(cf.columns))) if not fin.empty or not cf.empty else []
        for d in all_dates:
            year_str = d.strftime('%Y')
            financials_hist['years'].append(year_str)
            def get_val(df, row):
                if df is not None and not df.empty and row in df.index and d in df.columns:
                    val = df.loc[row, d]
                    return float(val) if val is not None and not math.isnan(float(val)) else 0
                return 0
            financials_hist['revenue'].append(get_val(fin, 'Total Revenue'))
            financials_hist['net_income'].append(get_val(fin, 'Net Income'))
            financials_hist['eps'].append(get_val(fin, 'Basic EPS'))
            financials_hist['fcf'].append(get_val(cf, 'Free Cash Flow'))
    except Exception as e:
        logger.error(f"Error extrayendo datos de yfinance: {e}")
        
    return financials_hist

def calculate_position_cagr(ticker: str, ledger: list, return_pct: float, is_active: bool = True):
    """Calcula el CAGR (Compound Annual Growth Rate) para una posición a partir de su ledger."""
    from datetime import datetime, date
    if not ledger:
        return None

    buy_dates = []
    sell_dates = []
    for tx in ledger:
        tx_type = tx.get('type') if isinstance(tx, dict) else getattr(tx, 'type', None)
        tx_date_str = tx.get('date') if isinstance(tx, dict) else getattr(tx, 'date', None)
        if not tx_type or not tx_date_str:
            continue

        tx_type_str = tx_type.value if hasattr(tx_type, 'value') else str(tx_type)

        if tx_type_str == 'BUY':
            try:
                dt = datetime.strptime(tx_date_str[:10], '%Y-%m-%d').date()
                buy_dates.append(dt)
            except Exception:
                pass
        elif tx_type_str == 'SELL':
            try:
                dt = datetime.strptime(tx_date_str[:10], '%Y-%m-%d').date()
                sell_dates.append(dt)
            except Exception:
                pass

    if not buy_dates:
        return None

    first_buy_date = min(buy_dates)

    if is_active:
        end_date = date.today()
    else:
        if sell_dates:
            end_date = max(sell_dates)
        else:
            end_date = date.today()

    days = (end_date - first_buy_date).days
    years = days / 365.25

    if years <= 0 or days < 7:
        return None

    try:
        cagr = ((1 + return_pct / 100) ** (1 / years) - 1) * 100
        return round(cagr, 2)
    except Exception:
        return None

