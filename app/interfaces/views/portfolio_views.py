import yfinance as yf
import pandas as pd
import time
import datetime
import concurrent.futures
import os

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.application.services.portfolio_service import PortfolioService
from app.application.services.performance_service import PerformanceService
from app.infrastructure.dependencies import (
    get_asset_repository,
    get_portfolio_service,
    get_performance_service,
    get_settings_from_db,
)
from app.infrastructure.templates import templates
from app.interfaces.views.market_views import get_currency_symbol, is_reload_request, INDEX_CACHE, ensure_prices_cached
from utils.financial_math import calculate_position_cagr
# from tools.download_logos import download_logo

router = APIRouter(tags=["Portfolio Views"])

# Cache global simple para evitar peticiones constantes a yfinance
GLOBAL_CACHE = {"prices": {}, "rates": {}, "last_update": 0, "upcoming_dividends": []}

# Tasas de cambio de respaldo realistas para evitar picos de error (ej: SEK = 1.0) si yfinance falla o no se ha cargado la caché
FALLBACK_RATES = {
    "EUR": 1.0,
    "USD": 0.92,
    "SEK": 0.09,
    "GBP": 1.17,
    "JPY": 0.006,
    "DKK": 0.134,
    "PLN": 0.23,
    "CHF": 1.03,
    "MXN": 0.05
}

def get_current_rate(currency_code: str) -> float:
    ccy = (currency_code or "USD").upper()
    return GLOBAL_CACHE.get("rates", {}).get(ccy) or FALLBACK_RATES.get(ccy, 1.0)

@router.get("/portfolio/ticker/{ticker}", response_class=HTMLResponse)
def portfolio_ticker_page(
    request: Request, 
    ticker: str,
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    db_session: Session = Depends(get_db)
):
    ticker = ticker.upper()
    portfolio_data = [p.model_dump() for p in portfolio_service.get_active_portfolio()]
    closed_portfolio_data = [p.model_dump() for p in portfolio_service.get_closed_portfolio()]
    ledger_data = [l.model_dump() for l in portfolio_service.get_ledger(ticker)]
    
    item = next((p for p in portfolio_data if p['ticker'] == ticker), None)
    is_active = True
    if not item:
        item = next((p for p in closed_portfolio_data if p['ticker'] == ticker), None)
        is_active = False
        
    if not item:
        return RedirectResponse(url="/portfolio")
        
    try:
        stock = yf.Ticker(ticker)
        current_price = stock.fast_info['lastPrice']
        if pd.isna(current_price):
            current_price = item.get('average_price', 0)
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
            
    asset_data = asset_repo.get_asset_data(ticker)
    item['company_name'] = asset_data.get("company_name") or ticker
    currency_code = asset_data.get("currency", item.get("currency_code", "USD")).upper()
    currency_symbol = get_currency_symbol(currency_code, ticker)
    item['currency_symbol'] = currency_symbol

    # Obtener tipo de cambio actual
    rate = GLOBAL_CACHE.get("rates", {}).get(currency_code)
    if not rate and currency_code != 'EUR':
        try:
            ex_ticker = f"{currency_code}EUR=X"
            rate_data = yf.download(ex_ticker, period="2d", progress=False)['Close']
            if isinstance(rate_data, pd.Series):
                rate = float(rate_data.iloc[-1])
            elif isinstance(rate_data, pd.DataFrame):
                rate = float(rate_data.iloc[-1].iloc[0])
            if "rates" not in GLOBAL_CACHE:
                GLOBAL_CACHE["rates"] = {}
            GLOBAL_CACHE["rates"][currency_code] = rate
        except Exception:
            rate = None

    if rate is None:
        rate = get_current_rate(currency_code)

    # Desglose de P&L latente (Posición activa)
    if is_active and currency_code != 'EUR':
        total_cost_eur = item.get('total_cost_eur', 0.0)
        if total_cost_eur > 0 and item.get('total_cost', 0) > 0:
            fx_avg_purchase = total_cost_eur / item['total_cost']
        else:
            fx_avg_purchase = rate
        current_value_eur = item['current_value'] * rate
        invested_value_eur = total_cost_eur if total_cost_eur > 0 else item['invested_value'] * fx_avg_purchase
        total_pnl_eur = current_value_eur - invested_value_eur
        
        pnl_price_eur = item['shares'] * (current_price - item['average_price']) * fx_avg_purchase
        pnl_fx_eur = item['shares'] * current_price * (rate - fx_avg_purchase)
        
        item['fx_avg_purchase'] = fx_avg_purchase
        item['current_fx_rate'] = rate
        item['current_value_eur'] = current_value_eur
        item['invested_value_eur'] = invested_value_eur
        item['total_pnl_eur'] = total_pnl_eur
        item['pnl_price_eur'] = pnl_price_eur
        item['pnl_fx_eur'] = pnl_fx_eur
        item['total_fees_eur'] = item.get('total_fees', 0.0) * rate
    else:
        item['fx_avg_purchase'] = 1.0
        item['current_fx_rate'] = 1.0
        item['current_value_eur'] = item.get('current_value', 0.0)
        item['invested_value_eur'] = item.get('invested_value', 0.0)
        item['total_pnl_eur'] = item.get('return_abs', 0.0)
        item['pnl_price_eur'] = item.get('return_abs', 0.0)
        item['pnl_fx_eur'] = 0.0
        item['total_fees_eur'] = item.get('total_fees', 0.0) * 1.0

    # Obtener historial realizado filtrado para enriquecer Ledger
    ticker_trades = portfolio_service.get_realized_trades(GLOBAL_CACHE.get("rates", {}))
    ticker_trades = [t for t in ticker_trades if t['ticker'] == ticker]

    from app.infrastructure.db.models import DBTransaction
    manual_txs = db_session.query(DBTransaction).filter(DBTransaction.ticker == ticker).all()

    for ledger_entry in ledger_data:
        match = None
        for trade in ticker_trades:
            trade_type_matches = (
                (trade['type'] == 'VENTA' and ledger_entry['type'] == 'SELL') or
                (trade['type'] == 'COMPRA' and ledger_entry['type'] == 'BUY') or
                (trade['type'] == 'DIVIDENDO' and ledger_entry['type'] == 'DIVIDEND')
            )
            if trade_type_matches and trade['date'][:10] == ledger_entry['date'][:10] and abs(trade['shares'] - ledger_entry['shares']) < 0.001:
                match = trade
                break
        
        # Comprobar si la transacción es manual
        is_manual = False
        for m_tx in manual_txs:
            if m_tx.date[:10] == ledger_entry['date'][:10] and m_tx.type == ledger_entry['type'] \
               and abs(m_tx.shares - ledger_entry['shares']) < 0.001:
                is_manual = True
                break
        ledger_entry['is_manual'] = is_manual

        if match:
            ledger_entry['pnl_eur'] = match.get('pnl_eur')
            ledger_entry['pnl_price_eur'] = match.get('pnl_price_eur')
            ledger_entry['pnl_fx_eur'] = match.get('pnl_fx_eur')
            ledger_entry['fx_rate'] = match.get('fx_rate')
            ledger_entry['fx_avg_purchase'] = match.get('fx_avg_purchase')
            ledger_entry['broker'] = match.get('broker') or 'IBKR'
        else:
            ledger_entry['pnl_eur'] = None
            ledger_entry['pnl_price_eur'] = None
            ledger_entry['pnl_fx_eur'] = None
            ledger_entry['fx_rate'] = ledger_entry.get('fx_rate_at_purchase') or rate or 1.0
            ledger_entry['fx_avg_purchase'] = None
            ledger_entry['broker'] = ledger_entry.get('broker') or 'IBKR'
    
    realized = item.get('realized_pnl', 0)
    dividends = item.get('dividends_collected', 0)
    total_profit_closed = realized + dividends

    # Calcular CAGR para la ventana de operaciones
    if is_active:
        return_pct = item.get('return_pct', 0.0)
    else:
        invested_val = item.get('total_sell_revenue', 0.0) - item.get('realized_pnl', 0.0) - item.get('total_fees', 0.0)
        if invested_val > 0:
            return_pct = (total_profit_closed / invested_val) * 100
        else:
            return_pct = 0.0
    item['cagr'] = calculate_position_cagr(ticker, ledger_data, return_pct, is_active=is_active)

    return templates.TemplateResponse("portfolio_ticker.html", {
        "request": request, "ticker": ticker, "item": item, "is_active": is_active,
        "ledger": ledger_data, "currency_symbol": currency_symbol,
        "total_profit_closed": total_profit_closed,
        "settings": get_settings_from_db(db_session)
    })

@router.get("/portfolio", response_class=HTMLResponse)
def portfolio_page(
    request: Request,
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    performance_service: PerformanceService = Depends(get_performance_service),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    db_session: Session = Depends(get_db)
):
    portfolio_data = [p.model_dump() for p in portfolio_service.get_active_portfolio()]
    closed_portfolio_data = [p.model_dump() for p in portfolio_service.get_closed_portfolio()]
    
    # 1. Rendimiento histórico (ya tiene su propia caché interna)
    perf_data = performance_service.get_performance_data()
    
    # Si es una recarga del navegador (F5), forzar la expiración de la caché de precios compartida
    if is_reload_request(request):
        INDEX_CACHE["prices_update"] = 0

    # 2. Actualizar caché de precios y divisas (compartida con el dashboard vía INDEX_CACHE,
    # para que "Var. Diaria" aquí y "MI CARTERA" en el dashboard nunca diverjan)
    now = time.time()
    tickers = [p['ticker'] for p in portfolio_data] + [p['ticker'] for p in closed_portfolio_data]
    missing_tickers = any(t for t in tickers if t not in GLOBAL_CACHE.get("prices", {}))

    ensure_prices_cached(tickers, asset_repo)
    GLOBAL_CACHE["prices"] = INDEX_CACHE["data"]

    currencies = {asset_repo.get_asset_data(p['ticker']).get("currency", "USD") for p in portfolio_data + closed_portfolio_data}
    valid_currencies = {c for c in currencies if isinstance(c, str) and len(c) == 3}
    GLOBAL_CACHE["rates"]["EUR"] = 1.0
    for c in valid_currencies:
        if c == "EUR":
            continue
        fx_data = INDEX_CACHE["data"].get(f"{c}EUR=X")
        if fx_data:
            GLOBAL_CACHE["rates"][c] = fx_data["price"]

    if now - GLOBAL_CACHE["last_update"] > 900 or missing_tickers:
        try:
            GLOBAL_CACHE["last_update"] = now

            # 2.5 Actualizar dividendos futuros y métricas fundamentales

            def fetch_dividend(item):
                ticker = item['ticker']
                shares = item['shares']
                curr = asset_repo.get_asset_data(ticker).get("currency", "USD")
                
                ret_data = {
                    "ticker": ticker,
                    "upcoming_dividend": None,
                    "metrics": {
                        "beta": None,
                        "roic": None,
                        "dividend_yield": None,
                        "expected_growth": None,
                        "gross_margin": None,
                        "operating_margin": None,
                        "trailing_pe": None,
                        "forward_pe": None,
                        "peg_ratio": None,
                        "fcf_yield": None,
                        "net_debt_ebitda": None,
                        "current_ratio": None,
                        "company_name": None
                    }
                }
                
                try:
                    stock = yf.Ticker(ticker)
                    cal = stock.calendar
                    info = stock.info
                    today = datetime.date.today()
                    
                    # 1. Extraer métricas fundamentales
                    ret_data["metrics"]["beta"] = info.get('beta')
                    ret_data["metrics"]["roic"] = info.get('returnOnInvestment') or info.get('returnOnAssets') or info.get('returnOnEquity')
                    ret_data["metrics"]["dividend_yield"] = info.get('dividendYield')
                    ret_data["metrics"]["gross_margin"] = info.get('grossMargins')
                    ret_data["metrics"]["operating_margin"] = info.get('operatingMargins')
                    
                    # Extraer métricas de valoración y salud financiera
                    ret_data["metrics"]["trailing_pe"] = info.get('trailingPE')
                    ret_data["metrics"]["forward_pe"] = info.get('forwardPE')
                    ret_data["metrics"]["peg_ratio"] = info.get('pegRatio')
                    ret_data["metrics"]["current_ratio"] = info.get('currentRatio')
                    
                    # FCF Yield
                    fcf = info.get('freeCashflow')
                    mcap = info.get('marketCap')
                    if fcf and mcap and mcap > 0:
                        fcf_val = float(fcf)
                        # yfinance a veces reporta freeCashflow en la divisa de los estados
                        # financieros (financialCurrency), distinta de la divisa de cotización
                        # (currency) usada en marketCap. Ej: OMAB reporta en MXN pero cotiza en USD.
                        # Sin esta conversión el ratio sale inflado por el tipo de cambio.
                        fin_ccy = info.get('financialCurrency')
                        quote_ccy = info.get('currency')
                        if fin_ccy and quote_ccy and fin_ccy != quote_ccy:
                            try:
                                fx_rate = yf.Ticker(f"{fin_ccy}{quote_ccy}=X").fast_info['lastPrice']
                                if fx_rate:
                                    fcf_val = fcf_val * float(fx_rate)
                                else:
                                    fcf_val = None
                            except Exception:
                                fcf_val = None
                        ret_data["metrics"]["fcf_yield"] = (fcf_val / float(mcap)) if fcf_val is not None else None
                    else:
                        ret_data["metrics"]["fcf_yield"] = None
                        
                    # Deuda Neta / EBITDA o Deuda / Capital
                    # yfinance tiene ebitda, totalDebt, totalCash. Calculemos Deuda Neta / EBITDA
                    ebitda = info.get('ebitda')
                    total_debt = info.get('totalDebt')
                    total_cash = info.get('totalCash')
                    if total_debt is not None and total_cash is not None and ebitda and ebitda > 0:
                        net_debt = total_debt - total_cash
                        ret_data["metrics"]["net_debt_to_ebitda"] = net_debt / ebitda
                    
                    # Crecimiento de ingresos esperado (Intentar obtener revenue estimate de +1y)
                    expected_growth = info.get('revenueGrowth')
                    try:
                        rev_est = stock.revenue_estimate
                        if rev_est is not None and not rev_est.empty and '+1y' in rev_est.index:
                            growth_val = rev_est.loc['+1y', 'growth']
                            if growth_val is not None and not pd.isna(growth_val):
                                expected_growth = float(growth_val)
                    except Exception:
                        pass
                    ret_data["metrics"]["expected_growth"] = expected_growth
                    
                    company_name = info.get('longName') or info.get('shortName')
                    if company_name:
                        ret_data["metrics"]["company_name"] = company_name
                    
                    # 2. Dividendo futuro
                    ex_ts = info.get('exDividendDate') or info.get('dividendExDate')
                    ex_date = None
                    if ex_ts:
                        ex_date = datetime.date.fromtimestamp(ex_ts)
                        
                    pay_date_obj = None
                    pay_ts = info.get('dividendDate') or info.get('dividendPaymentDate')
                    if pay_ts:
                        pay_date_obj = datetime.date.fromtimestamp(pay_ts)
                    elif isinstance(cal, dict) and cal.get('Dividend Date'):
                        pay_date_obj = cal.get('Dividend Date')
                        if hasattr(pay_date_obj, 'date'):
                            pay_date_obj = pay_date_obj.date()
                    elif isinstance(cal, pd.DataFrame) and 'Dividend Date' in cal.index:
                        pay_date_obj = cal.loc['Dividend Date'].iloc[0]
                        if hasattr(pay_date_obj, 'date'):
                            pay_date_obj = pay_date_obj.date()
                    
                    # We only care about upcoming dividends (either ex-date or pay-date is in the future)
                    if (ex_date and ex_date >= today) or (pay_date_obj and pay_date_obj >= today):
                        val = info.get('lastDividendValue')
                        if not val:
                            div_rate = info.get('dividendRate')
                            val = (div_rate / 4.0) if div_rate else 0.0
                            
                        if val > 0:
                            # Prefer sorting by ex-date if available, otherwise pay-date
                            sort_date = ex_date if ex_date else pay_date_obj
                            ret_data["upcoming_dividend"] = {
                                "ticker": ticker,
                                "date": ex_date or sort_date,
                                "pay_date": pay_date_obj,
                                "amount_per_share": val,
                                "total_amount": val * shares,
                                "currency_code": curr,
                                "sort_date": sort_date
                            }
                except Exception:
                    pass
                return ret_data
            
            upcoming = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                results = list(executor.map(fetch_dividend, portfolio_data))
                
            for res in results:
                if not res:
                    continue
                t = res["ticker"]
                metrics = res["metrics"]
                
                # Guardar métricas en DB en el hilo principal de forma segura
                if metrics["beta"] is not None: asset_repo.save_asset_data(t, "beta", float(metrics["beta"]))
                if metrics["roic"] is not None: asset_repo.save_asset_data(t, "roic", float(metrics["roic"]))
                if metrics["dividend_yield"] is not None: asset_repo.save_asset_data(t, "dividend_yield", float(metrics["dividend_yield"]))
                if metrics["expected_growth"] is not None: asset_repo.save_asset_data(t, "expected_growth", float(metrics["expected_growth"]))
                if metrics["gross_margin"] is not None: asset_repo.save_asset_data(t, "gross_margin", float(metrics["gross_margin"]))
                if metrics["operating_margin"] is not None: asset_repo.save_asset_data(t, "operating_margin", float(metrics["operating_margin"]))
                if metrics["trailing_pe"] is not None: asset_repo.save_asset_data(t, "trailing_pe", float(metrics["trailing_pe"]))
                if metrics["forward_pe"] is not None: asset_repo.save_asset_data(t, "forward_pe", float(metrics["forward_pe"]))
                if metrics["peg_ratio"] is not None: asset_repo.save_asset_data(t, "peg_ratio", float(metrics["peg_ratio"]))
                if metrics["current_ratio"] is not None: asset_repo.save_asset_data(t, "current_ratio", float(metrics["current_ratio"]))
                if metrics["fcf_yield"] is not None: asset_repo.save_asset_data(t, "fcf_yield", float(metrics["fcf_yield"]))
                if metrics.get("net_debt_to_ebitda") is not None: asset_repo.save_asset_data(t, "net_debt_to_ebitda", float(metrics["net_debt_to_ebitda"]))
                if metrics["company_name"] is not None: asset_repo.save_asset_data(t, "company_name", metrics["company_name"])
                
                if res.get("upcoming_dividend"):
                    upcoming.append(res["upcoming_dividend"])
            
            upcoming.sort(key=lambda x: x["sort_date"])
            GLOBAL_CACHE["upcoming_dividends"] = upcoming
            
        except Exception: pass

    # 3. Procesar Activos con datos de Caché
    total_invested_eur, total_current_value_eur, total_prev_value_eur, total_realized_pnl_eur, total_fees_all_eur = 0, 0, 0, 0, 0
    chart_data = []
    
    for item in portfolio_data:
        ticker = item['ticker']
        asset_info = asset_repo.get_asset_data(ticker)
        curr = asset_info.get("currency", "USD")
        item['company_name'] = asset_info.get("company_name") or ticker
        
        # Download logo if not exists
        # download_logo(ticker) - Deshabilitado por petición del usuario
        
        rate = get_current_rate(curr)
        p_data = GLOBAL_CACHE["prices"].get(ticker, {'price': item['average_price'], 'prev': item['average_price']})
        
        p_price = p_data['price']
        p_prev = p_data['prev']
        p_last_date = p_data.get('last_date')
        
        today_date = datetime.date.today()
        is_weekday = today_date.weekday() < 5
        if is_weekday and p_last_date and p_last_date != today_date:
            p_prev = p_price
            
        current_value = item['shares'] * p_price
        current_value_eur = current_value * rate
        invested_value = item['shares'] * item['average_price']
        invested_value_eur = invested_value * rate

        total_fees = item.get('total_fees') or 0.0
        total_fees_eur = total_fees * rate

        item.update({
            'current_price': p_price,
            'current_value': current_value,
            'current_value_eur': current_value_eur,
            'invested_value': invested_value,
            'invested_value_eur': invested_value_eur,
            'currency_symbol': get_currency_symbol(curr, ticker),
            'daily_change_abs': p_price - p_prev,
            'daily_change_total_eur': (item['shares'] * (p_price - p_prev)) * rate,
            'daily_change_pct': ((p_price / p_prev) - 1) * 100 if p_prev > 0 else 0,
            'total_profit_closed': item.get('realized_pnl', 0) + item.get('dividends_collected', 0),
            'total_fees_eur': round(total_fees_eur, 2),
        })
        item['total_profit_closed_eur'] = item['total_profit_closed'] * rate
        item['return_pct'] = (current_value / invested_value - 1) * 100 if invested_value > 0 else 0
        
        ledger_entries = portfolio_service.get_ledger(ticker)
        ledger_list = [l.model_dump() for l in ledger_entries] if ledger_entries else []
        item['cagr'] = calculate_position_cagr(ticker, ledger_list, item['return_pct'], is_active=True)
        
        total_invested_eur += item['invested_value_eur']
        total_current_value_eur += item['current_value_eur']
        total_prev_value_eur += (item['shares'] * p_prev) * rate
        total_realized_pnl_eur += item['total_profit_closed_eur']
        total_fees_all_eur += item.get('total_fees_eur', 0.0)
        chart_data.append({"ticker": ticker, "value": item['current_value_eur']})
        
    for item in closed_portfolio_data:
        ticker = item['ticker']
        asset_info = asset_repo.get_asset_data(ticker)
        curr = asset_info.get("currency", "USD")
        item['company_name'] = asset_info.get("company_name") or ticker
        
        # Download logo if not exists
        # download_logo(ticker) - Deshabilitado por petición del usuario
        
        rate = get_current_rate(curr)
        item['currency_symbol'] = get_currency_symbol(curr, ticker)
        item['total_profit_closed'] = item.get('realized_pnl', 0) + item.get('dividends_collected', 0)
        item['total_profit_closed_eur'] = item['total_profit_closed'] * rate
        
        # Calcular CAGR para posiciones cerradas
        ledger_entries_closed = portfolio_service.get_ledger(ticker)
        ledger_list_closed = [l.model_dump() for l in ledger_entries_closed] if ledger_entries_closed else []
        total_profit = item.get('realized_pnl', 0.0) + item.get('dividends_collected', 0.0)
        invested_val = item.get('total_sell_revenue', 0.0) - item.get('realized_pnl', 0.0) - item.get('total_fees', 0.0)
        if invested_val > 0:
            return_pct = (total_profit / invested_val) * 100
        else:
            return_pct = 0.0
        item['cagr'] = calculate_position_cagr(ticker, ledger_list_closed, return_pct, is_active=False)
        
        # Precalculados extra para visualización en plantilla HTML
        item['invested_value'] = invested_val
        item['return_pct'] = return_pct
        item['average_buy_price'] = (invested_val / item['total_shares_sold']) if item.get('total_shares_sold', 0) > 0 else 0.0
        
        # Obtener precio actual y calcular coste de oportunidad o pérdida evitada ("Efecto Salida")
        p_data = GLOBAL_CACHE["prices"].get(ticker, {'price': 0.0, 'prev': 0.0})
        current_price = p_data.get('price', 0.0)
        item['current_price'] = current_price
        if current_price > 0 and item.get('total_shares_sold', 0) > 0:
            avg_sell_price = item.get('average_sell_price', 0.0) or 0.0
            # Diferencia neta en divisa original: (Precio Actual - Precio Venta Medio) * Acciones Vendidas
            opp_diff_native = (current_price - avg_sell_price) * item['total_shares_sold']
            item['opp_diff_eur'] = opp_diff_native * rate
        else:
            item['opp_diff_eur'] = 0.0
        
        total_realized_pnl_eur += item['total_profit_closed_eur']

    # 3.1 Calcular efectos precio vs divisa globales
    realized_trades_data = portfolio_service.get_realized_trades(GLOBAL_CACHE.get("rates", {}))
    
    global_unrealized_price_pnl = 0.0
    global_unrealized_fx_pnl = 0.0
    
    for item in portfolio_data:
        ticker = item['ticker']
        curr = asset_repo.get_asset_data(ticker).get("currency", "USD").upper()
        rate = get_current_rate(curr)
        p_data = GLOBAL_CACHE["prices"].get(ticker, {'price': item['average_price'], 'prev': item['average_price']})
        
        current_price = p_data['price']
        average_price = item['average_price']
        shares = item['shares']
        
        if curr != 'EUR':
            total_cost_eur = item.get('total_cost_eur', 0.0)
            if total_cost_eur > 0 and item.get('total_cost', 0) > 0:
                fx_avg_purchase = total_cost_eur / item['total_cost']
            else:
                fx_avg_purchase = rate
            pnl_price_eur = shares * (current_price - average_price) * fx_avg_purchase
            pnl_fx_eur = shares * current_price * (rate - fx_avg_purchase)
        else:
            pnl_price_eur = shares * (current_price - average_price)
            pnl_fx_eur = 0.0
            
        global_unrealized_price_pnl += pnl_price_eur
        global_unrealized_fx_pnl += pnl_fx_eur

    global_realized_price_pnl = 0.0
    global_realized_fx_pnl = 0.0
    global_total_dividends = 0.0
    
    for trade in realized_trades_data:
        if trade['type'] == 'VENTA':
            global_realized_price_pnl += trade.get('pnl_price_eur', 0.0)
            global_realized_fx_pnl += trade.get('pnl_fx_eur', 0.0)
        elif trade['type'] == 'DIVIDENDO':
            global_total_dividends += trade.get('pnl_eur', 0.0)

    global_total_price_pnl = global_unrealized_price_pnl + global_realized_price_pnl
    global_total_fx_pnl = global_unrealized_fx_pnl + global_realized_fx_pnl
    global_total_profits = global_total_price_pnl + global_total_fx_pnl + global_total_dividends

    # 4. Preparar datos para gráficos
    labels, values = [], []
    country_labels, country_values = [], []
    sector_labels, sector_values = [], []

    if total_current_value_eur > 0:
        chart_data.sort(key=lambda x: x['value'], reverse=True)
        labels = [d['ticker'] for d in chart_data if d['value']/total_current_value_eur > 0.02]
        values = [d['value'] for d in chart_data if d['value']/total_current_value_eur > 0.02]
        if (otros := sum(d['value'] for d in chart_data if d['value']/total_current_value_eur <= 0.02)) > 0:
            labels.append("Otros"); values.append(otros)

        COUNTRY_TRANSLATIONS = {
            "United States": "Estados Unidos",
            "Spain": "España",
            "Germany": "Alemania",
            "France": "Francia",
            "United Kingdom": "Reino Unido",
            "Netherlands": "Países Bajos",
            "Switzerland": "Suiza",
            "Sweden": "Suecia",
            "Mexico": "México",
            "Denmark": "Dinamarca",
            "Australia": "Australia",
            "China": "China",
            "Japan": "Japón",
            "Canada": "Canadá",
            "Italy": "Italia",
            "Poland": "Polonia",
            "Greece": "Grecia",
            "Uruguay": "Uruguay",
            "Luxembourg": "Luxemburgo",
            "Brazil": "Brasil",
            "Argentina": "Argentina",
            "Ireland": "Irlanda",
            "Belgium": "Bélgica",
            "Norway": "Noruega",
            "Finland": "Finlandia",
            "Portugal": "Portugal",
            "Austria": "Austria",
        }

        SECTOR_TRANSLATIONS = {
            "Technology": "Tecnología",
            "Healthcare": "Salud",
            "Financial Services": "Servicios Financieros",
            "Consumer Cyclical": "Consumo Cíclico",
            "Industrials": "Industria",
            "Communication Services": "Servicios de Comunicación",
            "Consumer Defensive": "Consumo Defensivo",
            "Energy": "Energía",
            "Utilities": "Servicios Públicos",
            "Real Estate": "Inmobiliario",
            "Basic Materials": "Materiales Básicos",
        }

        country_totals = {}
        sector_totals = {}
        for item in portfolio_data:
            ticker = item['ticker']
            val = item.get('current_value_eur', 0.0)
            if val <= 0:
                continue
            asset_info = asset_repo.get_asset_data(ticker)
            country_raw = asset_info.get("country")
            country = COUNTRY_TRANSLATIONS.get(country_raw, country_raw or "Desconocido")
            sector_raw = asset_info.get("sector")
            sector = SECTOR_TRANSLATIONS.get(sector_raw, sector_raw or "Desconocido")
            country_totals[country] = country_totals.get(country, 0.0) + val
            sector_totals[sector] = sector_totals.get(sector, 0.0) + val

        sorted_countries = sorted(country_totals.items(), key=lambda x: x[1], reverse=True)
        country_labels = [k for k, v in sorted_countries]
        country_values = [v for k, v in sorted_countries]

        sorted_sectors = sorted(sector_totals.items(), key=lambda x: x[1], reverse=True)
        sector_labels = [k for k, v in sorted_sectors]
        sector_values = [v for k, v in sorted_sectors]

    # ── Calcular Métricas Ponderadas de la Cartera Activa ────────────────────
    weighted_beta = 0.0
    weight_sum_beta = 0.0
    
    weighted_roic = 0.0
    weight_sum_roic = 0.0
    
    weighted_yield = 0.0
    weight_sum_yield = 0.0
    
    weighted_growth = 0.0
    weight_sum_growth = 0.0
    
    weighted_gross_margin = 0.0
    weight_sum_gross_margin = 0.0
    
    weighted_operating_margin = 0.0
    weight_sum_operating_margin = 0.0

    # Nuevas variables institucionales ponderadas
    weighted_trailing_pe = 0.0
    weight_sum_trailing_pe = 0.0
    
    weighted_forward_pe = 0.0
    weight_sum_forward_pe = 0.0
    
    weighted_peg_ratio = 0.0
    weight_sum_peg_ratio = 0.0
    
    weighted_fcf_yield = 0.0
    weight_sum_fcf_yield = 0.0
    
    weighted_net_debt_ebitda = 0.0
    weight_sum_net_debt_ebitda = 0.0
    
    weighted_current_ratio = 0.0
    weight_sum_current_ratio = 0.0
    
    def to_pct_from_fraction(val):
        if val is None or val == "N/A":
            return None
        try:
            val = float(val)
            # Si el valor absoluto está entre 0.0 y 1.0 (excluyendo 0), asumimos que es una fracción y lo multiplicamos por 100
            if 0.0 < abs(val) <= 1.0:
                return val * 100
            return val
        except (ValueError, TypeError):
            return None

    def to_pct_direct(val):
        if val is None or val == "N/A":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    for item in portfolio_data:
        val_eur = item.get('current_value_eur', 0.0)
        if val_eur <= 0:
            continue
            
        ticker = item['ticker']
        asset_info = asset_repo.get_asset_data(ticker)
        
        # 1. Beta
        b = asset_info.get("beta")
        item["beta"] = float(b) if b is not None and b != "N/A" else None
        if b is not None and b != "N/A":
            try:
                weighted_beta += val_eur * float(b)
                weight_sum_beta += val_eur
            except (ValueError, TypeError): pass
            
        # 2. ROIC
        r = to_pct_from_fraction(asset_info.get("roic"))
        item["roic"] = r
        if r is not None:
            weighted_roic += val_eur * r
            weight_sum_roic += val_eur
            
        # 3. Dividend Yield
        y_raw = asset_info.get("dividend_yield")
        y = to_pct_from_fraction(y_raw)
        if y is not None and y > 20: # Heurística para yields > 20% que son probablemente un error de escala
            try:
                y = to_pct_from_fraction(float(y_raw) / 100.0)
            except (ValueError, TypeError):
                pass
        item["dividend_yield"] = y
        if y is not None:
            weighted_yield += val_eur * y
            weight_sum_yield += val_eur
            
        # 4. Expected Revenue Growth
        g = to_pct_from_fraction(asset_info.get("expected_growth"))
        item["expected_growth"] = g
        if g is not None:
            weighted_growth += val_eur * g
            weight_sum_growth += val_eur
            
        # 5. Gross Margin
        gm = to_pct_from_fraction(asset_info.get("gross_margin"))
        item["gross_margin"] = gm
        if gm is not None:
            weighted_gross_margin += val_eur * gm
            weight_sum_gross_margin += val_eur
            
        # 6. Operating Margin
        om = to_pct_from_fraction(asset_info.get("operating_margin"))
        item["operating_margin"] = om
        if om is not None:
            weighted_operating_margin += val_eur * om
            weight_sum_operating_margin += val_eur

        # 7. Trailing P/E
        tpe = asset_info.get("trailing_pe")
        item["trailing_pe"] = float(tpe) if tpe is not None and tpe != "N/A" else None
        if item["trailing_pe"] is not None:
            weighted_trailing_pe += val_eur * item["trailing_pe"]
            weight_sum_trailing_pe += val_eur
            
        # 8. Forward P/E
        fpe = asset_info.get("forward_pe")
        item["forward_pe"] = float(fpe) if fpe is not None and fpe != "N/A" else None
        if item["forward_pe"] is not None:
            weighted_forward_pe += val_eur * item["forward_pe"]
            weight_sum_forward_pe += val_eur
            
        # 9. PEG Ratio
        peg = asset_info.get("peg_ratio")
        item["peg_ratio"] = float(peg) if peg is not None and peg != "N/A" else None
        if item["peg_ratio"] is not None:
            weighted_peg_ratio += val_eur * item["peg_ratio"]
            weight_sum_peg_ratio += val_eur
            
        # 10. FCF Yield
        fy = to_pct_from_fraction(asset_info.get("fcf_yield"))
        item["fcf_yield"] = fy
        if fy is not None:
            weighted_fcf_yield += val_eur * fy
            weight_sum_fcf_yield += val_eur
            
        # 11. Net Debt / EBITDA
        nde = asset_info.get("net_debt_to_ebitda")
        item["net_debt_ebitda"] = float(nde) if nde is not None and nde != "N/A" else None
        if item["net_debt_ebitda"] is not None:
            weighted_net_debt_ebitda += val_eur * item["net_debt_ebitda"]
            weight_sum_net_debt_ebitda += val_eur
            
        # 12. Current Ratio
        cr = asset_info.get("current_ratio")
        item["current_ratio"] = float(cr) if cr is not None and cr != "N/A" else None
        if item["current_ratio"] is not None:
            weighted_current_ratio += val_eur * item["current_ratio"]
            weight_sum_current_ratio += val_eur

    # Calcular promedios ponderados finales
    port_beta = weighted_beta / weight_sum_beta if weight_sum_beta > 0 else 1.0
    port_roic = weighted_roic / weight_sum_roic if weight_sum_roic > 0 else 0.0
    port_yield = weighted_yield / weight_sum_yield if weight_sum_yield > 0 else 0.0
    port_growth = weighted_growth / weight_sum_growth if weight_sum_growth > 0 else 0.0
    port_gross_margin = weighted_gross_margin / weight_sum_gross_margin if weight_sum_gross_margin > 0 else 0.0
    port_operating_margin = weighted_operating_margin / weight_sum_operating_margin if weight_sum_operating_margin > 0 else 0.0
    
    port_trailing_pe = weighted_trailing_pe / weight_sum_trailing_pe if weight_sum_trailing_pe > 0 else 0.0
    port_forward_pe = weighted_forward_pe / weight_sum_forward_pe if weight_sum_forward_pe > 0 else 0.0
    port_peg = weighted_peg_ratio / weight_sum_peg_ratio if weight_sum_peg_ratio > 0 else 0.0
    port_fcf_yield = weighted_fcf_yield / weight_sum_fcf_yield if weight_sum_fcf_yield > 0 else 0.0
    port_net_debt_ebitda = weighted_net_debt_ebitda / weight_sum_net_debt_ebitda if weight_sum_net_debt_ebitda > 0 else 0.0
    port_current_ratio = weighted_current_ratio / weight_sum_current_ratio if weight_sum_current_ratio > 0 else 0.0

    # Calcular Alpha de Jensen (CAPM)
    portfolio_ann_return = perf_data.get("annualized_return", 0.0)
    benchmark_ann_return = perf_data.get("benchmark_annualized_return", 0.0)
    port_alpha = portfolio_ann_return - (port_beta * benchmark_ann_return)

    return templates.TemplateResponse("portfolio.html", {
        "request": request, "portfolio": portfolio_data, "closed_portfolio": closed_portfolio_data,
        "total_invested": total_invested_eur,
        "total_current_value": total_current_value_eur,
        "total_daily_abs": total_current_value_eur - total_prev_value_eur,
        "total_daily_pct": (total_current_value_eur / total_prev_value_eur - 1) * 100 if total_prev_value_eur > 0 else 0,
        "total_return_abs": total_current_value_eur - total_invested_eur,
        "total_return_pct": (total_current_value_eur / total_invested_eur - 1) * 100 if total_invested_eur > 0 else 0,
        "total_realized_pnl": total_realized_pnl_eur,
        "total_fees": total_fees_all_eur,
        "global_unrealized_price_pnl": global_unrealized_price_pnl,
        "global_unrealized_fx_pnl": global_unrealized_fx_pnl,
        "global_realized_price_pnl": global_realized_price_pnl,
        "global_realized_fx_pnl": global_realized_fx_pnl,
        "global_total_price_pnl": global_total_price_pnl,
        "global_total_fx_pnl": global_total_fx_pnl,
        "global_total_dividends": global_total_dividends,
        "global_total_profits": global_total_profits,
        # Nuevos datos de performance
        "perf_dates":       perf_data["dates"],
        "perf_portfolio":   perf_data["portfolio_uv"],
        "perf_values_eur":  perf_data.get("portfolio_values_eur", []),
        "perf_benchmarks":  perf_data.get("benchmarks", {}),
        "perf_benchmark":   perf_data.get("benchmark_uv", []),   # compatibilidad
        "max_drawdown":     perf_data.get("max_drawdown", 0.0),
        "annual_returns":   perf_data.get("annual_returns", []),
        "total_return_twr": perf_data.get("total_return", 0.0),
        "annualized_return": perf_data.get("annualized_return", 0.0),
        # Métricas fundamentales agregadas del portafolio
        "port_beta":             round(port_beta, 2),
        "port_roic":             round(port_roic, 2),
        "port_yield":            round(port_yield, 2),
        "port_growth":           round(port_growth, 2),
        "port_gross_margin":     round(port_gross_margin, 2),
        "port_operating_margin": round(port_operating_margin, 2),
        "port_alpha":            round(port_alpha, 2),
        # Nuevas métricas institucionales agregadas del portafolio
        "port_trailing_pe":      round(port_trailing_pe, 1),
        "port_forward_pe":       round(port_forward_pe, 1),
        "port_peg":              round(port_peg, 2),
        "port_fcf_yield":        round(port_fcf_yield, 2),
        "port_net_debt_ebitda":  round(port_net_debt_ebitda, 2),
        "port_current_ratio":    round(port_current_ratio, 2),
        "div_data": portfolio_service.get_yearly_dividends(),
        "div_kpis": portfolio_service.get_dividend_kpis(),
        "div_by_ticker": portfolio_service.get_dividend_by_ticker(),
        "fiscal_data": portfolio_service.get_fiscal_summary(GLOBAL_CACHE.get("rates", {})),
        "realized_trades": portfolio_service.get_realized_trades(GLOBAL_CACHE.get("rates", {})),
        "upcoming_dividends": GLOBAL_CACHE.get("upcoming_dividends", []),
        "latest_dividends": portfolio_service.get_latest_collected_dividends(limit=20),
        "chart_labels": labels, "chart_values": values,
        "country_labels": country_labels, "country_values": country_values,
        "sector_labels": sector_labels, "sector_values": sector_values,
        "settings": get_settings_from_db(db_session)
    })

@router.get("/ideal-portfolio", response_class=HTMLResponse)
def ideal_portfolio_page(
    request: Request,
    db_session: Session = Depends(get_db)
):
    return templates.TemplateResponse("ideal_portfolio.html", {
        "request": request,
        "settings": get_settings_from_db(db_session)
    })

