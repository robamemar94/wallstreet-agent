import logging
import yfinance as yf
import pandas as pd
import time
import datetime
import concurrent.futures
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.application.services import market_cache, base_valuation_service as bv, distribution_service, dividend_forecast, portfolio_roles_service as roles_service
from app.application.services.portfolio_service import PortfolioService
from app.application.services.performance_service import PerformanceService, annualized, reference_benchmark
from app.infrastructure.dependencies import (
    get_asset_repository,
    get_portfolio_service,
    get_performance_service,
    get_settings_from_db,
)
from app.infrastructure.templates import templates
from app.interfaces.views.market_views import get_currency_symbol, is_reload_request, INDEX_CACHE, ensure_prices_cached, prices_banner_context, ext_day
from app.infrastructure.db.models import DBAsset
from app.interfaces.views.thesis_views import portfolio_exposure
from app.interfaces.views.watchlist_views import proximity
from utils.financial_math import calculate_position_cagr, xirr
# from tools.download_logos import download_logo

logger = logging.getLogger(__name__)

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


def holding_since(ledger: list) -> Optional[datetime.date]:
    """La compra que abrió la posición actual: la primera tras quedarse a 0 acciones (con los splits en cuenta)."""
    shares, start = 0.0, None
    for e in sorted(ledger, key=lambda e: str(e.get('date'))):
        kind = str(getattr(e.get('type'), 'value', e.get('type')))
        if kind == 'BUY':
            if shares <= 1e-4:
                start = str(e.get('date'))[:10]
            shares += e.get('shares') or 0
        elif kind == 'SELL':
            shares = max(0.0, shares - (e.get('shares') or 0))
        elif kind == 'SPLIT':
            shares *= e.get('shares') or 1
    return datetime.date.fromisoformat(start) if start else None


def held_label(first_buy: Optional[datetime.date], today: Optional[datetime.date] = None) -> str:
    """Desde cuándo tienes la posición, corto: «2a 4m», «7m», «12 d»."""
    if not first_buy:
        return ""
    days = ((today or datetime.date.today()) - first_buy).days
    months = int(days / 30.44)
    if months < 1:
        return f"{max(days, 0)} d"
    years, months = divmod(months, 12)
    return f"{years}a {months}m" if years and months else (f"{years}a" if years else f"{months}m")


def day_change(shares: float, price: Optional[float], prev: Optional[float], last_date, currency: str,
               today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """Variación del día de una posición con los precios en directo: en € incluye la de la divisa; si su mercado aún no
    ha abierto hoy (EE. UU. por la mañana), closed=True y sin variación. La usan Contable, la tarjeta del día y Seguimiento."""
    today = today or datetime.date.today()
    ccy = (currency or "USD").upper()
    closed = bool(today.weekday() < 5 and last_date and last_date != today)
    # la de su última sesión (la que se enseña, como no es de hoy, con la luna: stale_pill)
    last_pct = ((price / prev) - 1) * 100 if closed and price and prev else None
    last_abs = (price - prev) if closed and price and prev else None
    if closed or not prev:
        prev = price
    # el cambio de hoy y el de ayer, de la misma fuente: mezclar el de reserva (GLOBAL_CACHE) con el cierre de ayer de
    # Yahoo se leía como un movimiento de la divisa que no existe (+3 % de golpe en todo lo que está en dólares)
    fx = INDEX_CACHE["data"].get(f"{ccy}EUR=X") or {}
    rate = 1.0 if ccy == "EUR" else (fx.get("price") or get_current_rate(ccy))
    rate_prev = rate if closed or ccy == "EUR" or not fx.get("price") else (fx.get("prev") or rate)
    price = price or 0.0
    return {"closed": closed, "last_date": last_date, "rate": rate, "rate_prev": rate_prev, "prev": prev,
            "last_pct": last_pct, "last_abs": last_abs,
            "eur": shares * (price * rate - prev * rate_prev), "pct": ((price / prev) - 1) * 100 if prev else 0.0}

def ext_day_eur(shares: float, ext: Optional[Dict[str, Any]], day: Dict[str, Any], rate: float) -> Optional[Dict[str, Any]]:
    """Var. día € con el horario extendido (EE. UU.): el pre-market, solo (su mercado aún no ha abierto hoy), o la sesión
    corregida con su post-market (de la del cierre anterior al precio del post-market). No entra en las cifras de hoy."""
    if not ext:
        return None
    eur = shares * ext["chg"] * rate
    if ext["kind"] == "pre":
        return {**ext, "eur": eur, "session": None, "total": eur}
    # post-market: la sesión es la de hoy (abierta) o la última, de madrugada (su mercado ya «no ha abierto hoy»)
    session = day["eur"] if not day["closed"] else shares * (day["last_abs"] or 0.0) * rate
    return {**ext, "eur": eur, "session": session, "total": session + eur}


DROP_FROM_HIGH_PCT = 30.0   # Resumen: posiciones que caen al menos esto desde su máximo de 52 semanas


def falls_from_high(hist: pd.DataFrame, tickers: list) -> list:
    """[{ticker, pct}] de las posiciones que caen DROP_FROM_HIGH_PCT % o más desde su máximo de 52 semanas (cierres)."""
    out = []
    for t in tickers:
        if t not in hist.columns:
            continue
        s = hist[t].dropna().iloc[-252:]
        if len(s) and s.max() > 0:
            pct = 100 * (float(s.iloc[-1]) / float(s.max()) - 1)
            if pct <= -DROP_FROM_HIGH_PCT:
                out.append({"ticker": t, "pct": pct})
    return sorted(out, key=lambda d: d["pct"])


def portfolio_overview(positions: Dict[str, Any], div_forecast: Dict[str, Any], held: list, db_session: Session,
                       today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """Pestaña Resumen de la Cartera: lo que más sumó y restó en el mes, lo que viene (resultados y cobros) y lo que pide
    atención (recortes de previsiones y tesis que no están intactas). Solo con datos en caché: no frena la página."""
    from app.application.services import market_cache, estimate_revisions_service
    from app.infrastructure.db.models import DBThesis
    today = today or datetime.date.today()
    month = sorted(((t, p["pnl_eur"].get("1m")) for t, p in positions.items() if p["pnl_eur"].get("1m") is not None), key=lambda x: -x[1])
    horizon = (today + datetime.timedelta(days=30)).isoformat()
    results = []
    for t in held:
        nxt = (market_cache.peek("earnings_v4", t) or {}).get("next")
        if nxt and today.isoformat() <= nxt[:10] <= horizon:
            results.append({"ticker": t, "date": nxt[:10], "days": (datetime.date.fromisoformat(nxt[:10]) - today).days})
    payments = sorted((e for e in div_forecast.get("events", []) if e["date"] >= today), key=lambda e: e["date"])
    theses = [{"ticker": th.ticker, "id": th.id, "verdict": th.verdict} for th in db_session.query(DBThesis).filter(DBThesis.ticker.in_(held)).all()
              if th.verdict in ("VIGILANCIA", "DEBILITADA", "ROTA")]
    from app.application.services import report_service
    reports = report_service.list_reports(db_session)
    last = {k: next((r for r in reports if r["kind"] == k), None) for k in ("W", "M")}
    return {"up": [m for m in month[:5] if m[1] > 0], "down": [m for m in month[::-1][:5] if m[1] < 0], "reports": last,
            "results": sorted(results, key=lambda r: r["date"])[:4], "payments": payments[:max(2, 6 - len(results))],
            "cuts": [n["ticker"] for n in estimate_revisions_service.signal_notices(held)],
            "theses": sorted(theses, key=lambda t: ("ROTA", "DEBILITADA", "VIGILANCIA").index(t["verdict"]))}


def risk_since_start(port: list, bench: list) -> Optional[Dict[str, Any]]:
    """Resumen → Riesgo, desde el inicio del Rendimiento: volatilidad y beta medidas con la cartera (como los informes),
    lo que daba el riesgo asumido frente a lo que sumó tu selección y cuánto está por debajo de su máximo."""
    from app.application.services import report_service
    rk = report_service.risk({"port": port, "bench": bench if len(bench) == len(port) else None, "from": None})
    if not rk:
        return None
    if rk.get("beta") is not None and rk.get("bench_twr") is not None:
        rk["alpha"] = rk["twr"] - rk["beta"] * rk["bench_twr"]
    rk["dd_now"] = 100 * (port[-1] / max(port) - 1) if max(port) else None
    return rk


def _shares_held_at(ledger_entries, as_of_date):
    """Acciones en cartera a fecha `as_of_date`, reconstruidas a partir del ledger persistido (BUY/SELL/SPLIT)."""
    return dividend_forecast.shares_held_at(ledger_entries, as_of_date)

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
        "tx_notes": asset_data.get("tx_notes") or {},   # comentarios de las operaciones
        "total_profit_closed": total_profit_closed,
        "settings": get_settings_from_db(db_session)
    })

EMPTY_METRICS = {"beta": None, "roic": None, "dividend_yield": None, "expected_growth": None, "gross_margin": None,
                 "operating_margin": None, "trailing_pe": None, "forward_pe": None, "peg_ratio": None, "fcf_yield": None,
                 "net_debt_ebitda": None, "current_ratio": None, "company_name": None}


def portfolio_raw(ticker: str) -> dict:
    """Métricas fundamentales y próximo dividendo de yfinance para la cartera. Caché en disco de 12 h: son
    varias llamadas lentas por empresa y cambian poco (la cotización va aparte y sí se refresca a menudo)."""
    def download():
        stock = yf.Ticker(ticker)
        cal = stock.calendar
        info = stock.info or {}
        m = {
            "beta": info.get('beta'),
            "roic": info.get('returnOnInvestment') or info.get('returnOnAssets') or info.get('returnOnEquity'),
            "dividend_yield": info.get('dividendYield'), "gross_margin": info.get('grossMargins'),
            "operating_margin": info.get('operatingMargins'),
            # métricas de valoración y salud financiera
            "trailing_pe": info.get('trailingPE'), "forward_pe": info.get('forwardPE'), "peg_ratio": info.get('pegRatio'),
            "current_ratio": info.get('currentRatio'), "fcf_yield": None,
            "company_name": info.get('longName') or info.get('shortName'),
        }
        fcf, mcap = info.get('freeCashflow'), info.get('marketCap')
        if fcf and mcap and mcap > 0:
            fcf_val = float(fcf)
            # yfinance a veces reporta freeCashflow en la divisa de los estados financieros (financialCurrency),
            # distinta de la de cotización (currency) usada en marketCap. Ej: OMAB reporta en MXN pero cotiza en USD.
            fin_ccy, quote_ccy = info.get('financialCurrency'), info.get('currency')
            if fin_ccy and quote_ccy and fin_ccy != quote_ccy:
                try:
                    fx_rate = yf.Ticker(f"{fin_ccy}{quote_ccy}=X").fast_info['lastPrice']
                    fcf_val = fcf_val * float(fx_rate) if fx_rate else None
                except Exception:
                    fcf_val = None
            m["fcf_yield"] = (fcf_val / float(mcap)) if fcf_val is not None else None
        # Deuda neta / EBITDA
        ebitda, total_debt, total_cash = info.get('ebitda'), info.get('totalDebt'), info.get('totalCash')
        if total_debt is not None and total_cash is not None and ebitda and ebitda > 0:
            m["net_debt_to_ebitda"] = (total_debt - total_cash) / ebitda
        # Crecimiento de ingresos esperado (revenue estimate de +1y si lo hay)
        expected_growth = info.get('revenueGrowth')
        try:
            rev_est = stock.revenue_estimate
            if rev_est is not None and not rev_est.empty and '+1y' in rev_est.index:
                growth_val = rev_est.loc['+1y', 'growth']
                if growth_val is not None and not pd.isna(growth_val):
                    expected_growth = float(growth_val)
        except Exception:
            pass
        m["expected_growth"] = expected_growth

        to_iso = lambda v: (v.date() if hasattr(v, "date") else v).isoformat() if v else None
        ex_ts = info.get('exDividendDate') or info.get('dividendExDate')
        pay_ts = info.get('dividendDate') or info.get('dividendPaymentDate')
        pay = datetime.date.fromtimestamp(pay_ts).isoformat() if pay_ts else None
        if not pay and isinstance(cal, dict) and cal.get('Dividend Date'):
            pay = to_iso(cal.get('Dividend Date'))
        elif not pay and isinstance(cal, pd.DataFrame) and 'Dividend Date' in cal.index:
            pay = to_iso(cal.loc['Dividend Date'].iloc[0])
        div_rate = info.get('dividendRate')
        return {"metrics": {k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in m.items()},
                "ex_div": datetime.date.fromtimestamp(ex_ts).isoformat() if ex_ts else None, "pay_date": pay,
                "div_value": info.get('lastDividendValue') or ((div_rate / 4.0) if div_rate else 0.0)}
    try:
        return market_cache.cached("portfolio_raw", ticker, download)
    except Exception:
        return {}


@router.get("/portfolio", response_class=HTMLResponse)
def portfolio_page(
    request: Request,
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    performance_service: PerformanceService = Depends(get_performance_service),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    db_session: Session = Depends(get_db)
):
    from app.application.services import report_service
    report_service.warm_live()   # los informes en curso, listos para cuando se abra Informes
    portfolio_data = [p.model_dump() for p in portfolio_service.get_active_portfolio()]
    closed_portfolio_data = [p.model_dump() for p in portfolio_service.get_closed_portfolio()]

    settings = get_settings_from_db(db_session)
    perf_start_date = settings.get("performance", {}).get("start_date")

    # 1. Rendimiento histórico (ya tiene su propia caché interna)
    perf_data = performance_service.get_performance_data(perf_start_date)
    
    # F5: se piden precios nuevos y se espera (~3 s), para no tener que recargar otra vez al llegar
    reload_prices = is_reload_request(request) and time.time() - INDEX_CACHE.get("prices_update", 0) > 30

    # 2. Actualizar caché de precios y divisas (compartida con el dashboard vía INDEX_CACHE,
    # para que "Var. Diaria" aquí y "MI CARTERA" en el dashboard nunca diverjan)
    now = time.time()
    tickers = [p['ticker'] for p in portfolio_data] + [p['ticker'] for p in closed_portfolio_data]
    missing_tickers = any(t for t in tickers if t not in GLOBAL_CACHE.get("prices", {}))

    ensure_prices_cached(tickers, asset_repo, wait=reload_prices)
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

            # Ledger y divisa por ticker cargados por adelantado (fuera del ThreadPool) para
            # poder validar, más abajo, que la posición ya existía en la fecha ex-date de cada
            # dividendo próximo. La sesión de SQLAlchemy no es thread-safe para uso concurrente:
            # llamar a asset_repo/db_session dentro de fetch_dividend (ejecutado en varios hilos
            # a la vez) puede lanzar una excepción intermitente que aborta todo el bloque y deja
            # GLOBAL_CACHE["upcoming_dividends"] vacío.
            ledger_by_ticker = {p['ticker']: portfolio_service.get_ledger(p['ticker']) for p in portfolio_data}
            currency_by_ticker = {p['ticker']: asset_repo.get_asset_data(p['ticker']).get("currency", "USD") for p in portfolio_data}

            def fetch_dividend(item):
                ticker = item['ticker']
                shares = item['shares']
                curr = currency_by_ticker.get(ticker, "USD")
                ret_data = {"ticker": ticker, "upcoming_dividend": None, "metrics": dict(EMPTY_METRICS)}
                try:
                    raw = portfolio_raw(ticker)
                    ret_data["metrics"].update(raw.get("metrics") or {})
                    today = datetime.date.today()
                    as_date = lambda v: datetime.date.fromisoformat(v) if v else None
                    ex_date, pay_date_obj, val = as_date(raw.get("ex_div")), as_date(raw.get("pay_date")), raw.get("div_value") or 0.0

                    # We only care about upcoming dividends (either ex-date or pay-date is in the future)
                    if (ex_date and ex_date >= today) or (pay_date_obj and pay_date_obj >= today):
                        # Si el ex-date ya pasó (pero el pago sigue pendiente), hay que
                        # validar que ya teníamos la posición en esa fecha: si la compra
                        # fue posterior al ex-date, ese dividendo no nos corresponde.
                        eligible_shares = shares
                        if ex_date and ex_date < today:
                            eligible_shares = _shares_held_at(ledger_by_ticker.get(ticker, []), ex_date)

                        if val > 0 and eligible_shares > 0.0001:
                            # Prefer sorting by ex-date if available, otherwise pay-date
                            sort_date = ex_date if ex_date else pay_date_obj
                            ret_data["upcoming_dividend"] = {
                                "ticker": ticker,
                                "date": ex_date or sort_date,
                                "pay_date": pay_date_obj,
                                "amount_per_share": val,
                                "total_amount": val * eligible_shares,
                                "currency_code": curr,
                                "sort_date": sort_date
                            }
                except Exception:
                    pass
                return ret_data

            upcoming = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
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
        
        day = day_change(item['shares'], p_price, p_prev, p_last_date, curr)
        p_prev, rate_prev, closed_today = day["prev"], day["rate_prev"], day["closed"]

        current_value = item['shares'] * p_price
        current_value_eur = current_value * rate
        invested_value = item['shares'] * item['average_price']
        # Lo pagado en euros (cambio de cada compra): así el P&L incluye el efecto divisa
        cost_eur = item.get('total_cost_eur') or 0.0
        invested_value_eur = cost_eur if (curr or 'EUR').upper() != 'EUR' and cost_eur > 0 else invested_value * rate
        fx_avg = invested_value_eur / invested_value if invested_value > 0 else rate

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
            'daily_change_total_eur': day["eur"],
            'daily_change_pct': day["pct"],
            'closed_today': closed_today,
            'last_date': p_last_date,
            'last_pct': day["last_pct"], 'last_abs': day["last_abs"],
            'total_profit_closed': item.get('realized_pnl', 0) + item.get('dividends_collected', 0),
            'total_fees_eur': round(total_fees_eur, 2),
        })
        item['total_profit_closed_eur'] = item['total_profit_closed'] * rate
        item['ext_day'] = ext_day_eur(item['shares'], ext_day(ticker), day, rate) if item['shares'] > 0 else None
        item['return_pct'] = (current_value_eur / invested_value_eur - 1) * 100 if invested_value_eur > 0 else 0
        item['pnl_price_eur'] = item['shares'] * (p_price - item['average_price']) * fx_avg
        item['pnl_fx_eur'] = current_value * (rate - fx_avg)

        ledger_entries = portfolio_service.get_ledger(ticker)
        ledger_list = [l.model_dump() for l in ledger_entries] if ledger_entries else []
        item['cagr'] = calculate_position_cagr(ticker, ledger_list, item['return_pct'], is_active=True)
        item['first_buy'] = holding_since(ledger_list)   # desde que se abrió la posición actual (si se cerró, empieza de nuevo)
        item['held_label'] = held_label(item['first_buy'])
        item['held_years'] = (datetime.date.today() - item['first_buy']).days / 365.25 if item['first_buy'] else 0

        total_invested_eur += item['invested_value_eur']
        total_current_value_eur += item['current_value_eur']
        total_prev_value_eur += item['shares'] * p_prev * rate_prev
        total_fees_all_eur += item.get('total_fees_eur', 0.0)
        
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
        

    # 3.1 Calcular efectos precio vs divisa globales
    realized_trades_data = portfolio_service.get_realized_trades(GLOBAL_CACHE.get("rates", {}))
    
    # El desglose acción/divisa de cada fila (paso 3): así la Rent. latente y su globo cuadran
    global_unrealized_price_pnl = sum(item['pnl_price_eur'] for item in portfolio_data)
    global_unrealized_fx_pnl = sum(item['pnl_fx_eur'] for item in portfolio_data)

    global_realized_price_pnl = 0.0
    global_realized_fx_pnl = 0.0
    global_total_dividends = 0.0
    dividends_12m = 0.0   # cobrados en los últimos 12 meses (el Resumen los pone junto a los previstos)
    since_12m = (datetime.date.today() - datetime.timedelta(days=365)).isoformat()

    for trade in realized_trades_data:
        if trade['type'] == 'VENTA':
            global_realized_price_pnl += trade.get('pnl_price_eur', 0.0)
            global_realized_fx_pnl += trade.get('pnl_fx_eur', 0.0)
        elif trade['type'] == 'DIVIDENDO':
            global_total_dividends += trade.get('pnl_eur', 0.0)
            if trade['date'][:10] >= since_12m:
                dividends_12m += trade.get('pnl_eur', 0.0)

    # De la posición actual (desde que se abrió; lo de antes de cerrarla es otra inversión), en €:
    # - resultado total = latente + ventas parciales + dividendos;
    # - TIR: cada compra, venta y dividendo desde su fecha, más lo que vale hoy (solo con un año o más).
    today = datetime.date.today()
    for item in portfolio_data:
        t, since = item['ticker'].upper(), (item.get('first_buy') or datetime.date.min).isoformat()
        sold = divs = 0.0
        flows = []
        for trade in realized_trades_data:
            if trade['ticker'] != t or str(trade['date'])[:10] < since:
                continue
            amount = trade['shares'] * trade['price'] * (trade.get('fx_rate') or 1.0)
            if trade['type'] == 'COMPRA':
                flows.append((trade['date'], -(amount + trade.get('fee_eur', 0.0))))
            elif trade['type'] == 'VENTA':
                sold += trade.get('pnl_eur', 0.0)
                flows.append((trade['date'], amount - trade.get('fee_eur', 0.0)))
            elif trade['type'] == 'DIVIDENDO':
                divs += trade.get('pnl_eur', 0.0)
                flows.append((trade['date'], trade.get('pnl_eur', 0.0)))
        item['sold_pnl_eur'], item['dividends_eur'] = sold, divs
        item['total_result_eur'] = item['current_value_eur'] - item['invested_value_eur'] + sold + divs
        item['irr'] = xirr(flows + [(today, item['current_value_eur'])]) if item.get('held_years', 0) >= 1 else None

    global_total_price_pnl = global_unrealized_price_pnl + global_realized_price_pnl
    global_total_fx_pnl = global_unrealized_fx_pnl + global_realized_fx_pnl
    global_total_profits = global_total_price_pnl + global_total_fx_pnl + global_total_dividends
    # Bº realizado = lo mismo que Historial y Fiscal: ventas en EUR con el cambio de compra y de venta + dividendos
    # (antes: P&L en su moneda × cambio de hoy, que no cuadraba con Historial ni con su propio desglose)
    total_realized_pnl_eur = global_realized_price_pnl + global_realized_fx_pnl + global_total_dividends

    # 4. Distribución (vista «Distribución»): activo, rol, divisa, sector y país
    distribution = None
    if total_current_value_eur > 0:
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

        SECTOR_TRANSLATIONS = distribution_service.SECTOR_ES

        positions = []
        for item in portfolio_data:
            val = item.get('current_value_eur', 0.0)
            if val <= 0:
                continue
            asset_info = asset_repo.get_asset_data(item['ticker'])
            country_raw, sector_raw = asset_info.get("country"), asset_info.get("sector")
            positions.append({
                "ticker": item['ticker'], "name": item.get('company_name') or item['ticker'], "value": val,
                "currency": (asset_info.get("currency") or "USD").upper(), "role": asset_info.get(roles_service.ROLE_KEY),
                "country": COUNTRY_TRANSLATIONS.get(country_raw, country_raw or "Desconocido"),
                "sector": SECTOR_TRANSLATIONS.get(sector_raw, sector_raw or "Desconocido")})
        from app.application.services import cash_service
        _cash = cash_service.status(db_session)
        distribution = distribution_service.build(positions, roles_service.load_roles(), _cash.get("balance", 0.0) if _cash.get("configured") else 0.0)

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

    def frac_pct(val):
        """ROIC, márgenes, crecimiento y rentabilidad FCF: siempre vienen como fracción (0,348 = 34,8 %), también los
        extremos (−27,5 = −2.750 %), así que no se puede adivinar la escala por el tamaño."""
        try:
            return None if val is None or val == "N/A" else float(val) * 100
        except (ValueError, TypeError):
            return None

    # Para la media de la cartera, cada empresa con un tope razonable: un dato extremo de una posición pequeña (un margen
    # del −2.750 % de una empresa casi sin ventas) no hunde la media; en su fila se ve el dato real.
    CAP = {"roic": 100.0, "margin": 100.0, "growth": 100.0, "fcf_yield": 20.0}
    capped = lambda v, k: max(-CAP[k], min(CAP[k], v))

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
        r = frac_pct(asset_info.get("roic"))
        item["roic"] = r
        if r is not None:
            weighted_roic += val_eur * capped(r, "roic")
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
        g = frac_pct(asset_info.get("expected_growth"))
        item["expected_growth"] = g
        if g is not None:
            weighted_growth += val_eur * capped(g, "growth")
            weight_sum_growth += val_eur
            
        # 5. Gross Margin
        gm = frac_pct(asset_info.get("gross_margin"))
        item["gross_margin"] = gm
        if gm is not None:
            weighted_gross_margin += val_eur * capped(gm, "margin")
            weight_sum_gross_margin += val_eur
            
        # 6. Operating Margin
        om = frac_pct(asset_info.get("operating_margin"))
        item["operating_margin"] = om
        if om is not None:
            weighted_operating_margin += val_eur * capped(om, "margin")
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
        fy = frac_pct(asset_info.get("fcf_yield"))
        item["fcf_yield"] = fy
        if fy is not None:
            weighted_fcf_yield += val_eur * capped(fy, "fcf_yield")
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

    # Variación de 1 semana, 1 mes, YTD y 1 año (respetando las fechas de compra y venta) frente al índice de referencia
    period_changes, period_positions, drops = {}, {}, []
    benchmark = (get_settings_from_db(db_session).get("performance") or {}).get("benchmark_ticker") or "URTH"

    # Alfa de Jensen (CAPM) frente al índice de referencia, el mismo de las demás alfas; anualizada solo con un año o más
    ref_bench = reference_benchmark(benchmark)
    port_series, ref_series, perf_dates = perf_data.get("portfolio_uv") or [], (perf_data.get("benchmarks") or {}).get(ref_bench) or [], perf_data.get("dates") or []
    perf_years = (datetime.date.fromisoformat(perf_dates[-1]) - datetime.date.fromisoformat(perf_dates[0])).days / 365.25 if len(perf_dates) > 1 else 0.0
    portfolio_ann_return = annualized(port_series[0], port_series[-1], perf_years) if port_series else 0.0
    benchmark_ann_return = annualized(ref_series[0], ref_series[-1], perf_years) if ref_series else 0.0
    port_alpha = portfolio_ann_return - (port_beta * benchmark_ann_return)
    try:
        from app.application.services import price_monitor_service as pm
        txs, splits = pm.load_transactions(portfolio_service, db_session)   # respeta fechas de compra/venta y splits
        ccys = {("GBP" if t.get("currency") == "GBp" else t.get("currency") or "USD") for t in txs} - {"EUR"}
        hist = pm.download_history(sorted({t["ticker"] for t in txs}) + [f"{c}EUR=X" for c in ccys] + [benchmark])
        _ch = pm.portfolio_changes(hist, txs, splits, benchmark)
        period_changes, period_positions = _ch["periods"], _ch["positions"]
        drops = falls_from_high(hist, list(period_positions))
    except Exception as e:
        logging.getLogger(__name__).warning("No se pudo calcular la variación por periodos: %s", e)

    div_forecast = dividend_forecast.portfolio_forecast(portfolio_service, asset_repo, INDEX_CACHE["data"], portfolio_raw)
    overview = portfolio_overview(period_positions, div_forecast, [p["ticker"] for p in portfolio_data if p.get("shares", 0) > 0], db_session)
    from app.application.services import cash_service
    perf_patrimony = cash_service.patrimony(db_session, perf_data.get("dates") or [], perf_data.get("portfolio_values_eur") or [],
                                            perf_data.get("portfolio_uv") or [])
    overview.update(cash=cash_service.status(db_session), drops=drops, since_start={"pct": perf_data.get("total_return"),
                                              "bench": 100 * (ref_series[-1] / ref_series[0] - 1) if ref_series and ref_series[0] else None,
                                              "start": perf_dates[0] if perf_dates else None},
                    sector=((distribution or {}).get("sector") or [None])[0],
                    top5=sorted(((t, p["weight"]) for t, p in period_positions.items()), key=lambda x: -x[1])[:5],
                    risk=risk_since_start(port_series, ref_series))
    return templates.TemplateResponse("portfolio.html", {
        "request": request, "portfolio": portfolio_data, "closed_portfolio": closed_portfolio_data,
        **prices_banner_context(),
        "period_changes": period_changes, "benchmark": benchmark,
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
        "dividends_12m": dividends_12m,
        "global_total_profits": global_total_profits,
        # Nuevos datos de performance
        "perf_dates":       perf_data["dates"],
        "perf_portfolio":   perf_data["portfolio_uv"],
        "perf_values_eur":  perf_data.get("portfolio_values_eur", []),
        "perf_contributed_eur": perf_data.get("contributed_eur", []),
        "perf_benchmarks":  perf_data.get("benchmarks", {}),
        "perf_benchmark":   perf_data.get("benchmark_uv", []),   # compatibilidad
        "max_drawdown":     perf_data.get("max_drawdown", 0.0),
        "annual_returns":   perf_data.get("annual_returns", []),
        "total_return_twr": perf_data.get("total_return", 0.0),
        "annualized_return": portfolio_ann_return,
        "perf_ref_bench":   ref_bench,
        "perf_alpha_annualized": perf_years >= 1,
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
        "div_data": portfolio_service.get_yearly_dividends(GLOBAL_CACHE.get("rates", {})),
        "div_kpis": portfolio_service.get_dividend_kpis(GLOBAL_CACHE.get("rates", {})),
        "div_by_ticker": portfolio_service.get_dividend_by_ticker(),
        "fiscal_data": portfolio_service.get_fiscal_summary(GLOBAL_CACHE.get("rates", {})),
        "realized_trades": portfolio_service.get_realized_trades(GLOBAL_CACHE.get("rates", {})),
        # solo lo anunciado por la empresa (lo estimado está en la previsión)
        "upcoming_dividends": [{"ticker": e["ticker"], "date": e["ex_date"], "pay_date": e["pay_date"], "amount_per_share": e["amount"],
                                "total_amount": e["amount_local"], "currency_code": e["currency"], "gross_eur": e["gross_eur"]}
                               for e in div_forecast["events"] if not e["estimated"]],
        "div_forecast": div_forecast,
        "latest_dividends": portfolio_service.get_latest_collected_dividends(limit=20),
        "distribution": distribution,
        "settings": settings,
        "perf_start_date": perf_start_date,
        "overview": overview,
        "perf_patrimony": perf_patrimony,
    })

@router.get("/portfolio/decisiones", response_class=HTMLResponse)
def portfolio_decisions_fragment(
    request: Request,
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    db_session: Session = Depends(get_db),
):
    """Pestaña «Decisiones» de la Cartera (se carga aparte: descarga el histórico de precios y del índice).
    Cuenta desde la misma fecha de inicio que el Rendimiento (settings → performance.start_date): lo de antes eran pruebas."""
    review, error, since = decisions_review(portfolio_service, db_session)
    return templates.TemplateResponse("_decisions.html", {"request": request, **review, "error": error, "since": since})


@router.get("/api/portfolio/decisiones/resumen")
def portfolio_decisions_summary(portfolio_service: PortfolioService = Depends(get_portfolio_service), db_session: Session = Depends(get_db)):
    """Lo esencial de Decisiones para el Resumen de la Cartera (se pide al abrirlo: descarga históricos)."""
    review, error, _ = decisions_review(portfolio_service, db_session)
    s = review["summary"]
    ex = lambda o: {"ticker": o["ticker"], "type": o["type"], "date": o["date"][:10], "excess": o["review"]["excess"]}
    return {"buys": s["buys"], "n_ops": s["n_buys"] + s["n_sells"], "with_reason": s["with_reason"],
            "best": [ex(o) for o in s["best"][:1]], "worst": [ex(o) for o in s["worst"][:1]], "error": error}


def decisions_review(portfolio_service: PortfolioService, db_session: Session):
    """Balance de las operaciones (pestaña Decisiones): (review, error, since)."""
    from app.application.services import decision_review_service as dr, price_monitor_service as pm
    since = (get_settings_from_db(db_session).get("performance") or {}).get("start_date")
    txs, splits = pm.load_transactions(portfolio_service, db_session)
    if since:
        # fuera también las empresas de antes que se vendieron después: limpieza de la cartera vieja, no decisiones
        # de la nueva (las de antes que se siguen comprando, sí cuentan)
        before = {t["ticker"] for t in txs if t["type"] == "BUY" and t["date"][:10] < since}
        legacy = {t["ticker"] for t in txs if t["type"] == "SELL" and t["date"][:10] >= since and t["ticker"] in before}
        txs = [t for t in txs if t["date"][:10] >= since and t["ticker"] not in legacy]
    tickers = sorted({t["ticker"] for t in txs})
    assets = {a.ticker: a.data or {} for a in db_session.query(DBAsset).filter(DBAsset.ticker.in_(tickers)).all()}
    error, df = None, None
    try:
        df = dr.download(dr.symbols(txs), min(t["date"] for t in txs)) if txs else None
    except Exception as e:
        logger.exception("Decisiones: fallo al descargar precios")
        error = str(e)
    review = dr.build(df, txs, assets, {t: d.get("company_name") or t for t, d in assets.items()}, splits)
    return review, error, since


@router.get("/portfolio/informes", response_class=HTMLResponse)
def portfolio_reports_fragment(request: Request, key: Optional[str] = None, db_session: Session = Depends(get_db)):
    """Pestaña «Informes» de la Cartera: el archivo (semanal, mensual, trimestral, anual) y el informe elegido (o el último)."""
    from app.application.services import report_service as rs
    reports = rs.list_reports(db_session)
    for kind in reversed(rs.LIVE_KINDS):   # el mes, el trimestre y el año en curso, los primeros de su lista
        try:
            live = rs.live_report(db_session, kind)
            reports.insert(0, {k: live.get(k) for k in ("key", "kind", "label", "start", "end", "live")} | {"pct": (live.get("performance") or {}).get("pct")})
        except Exception:
            logger.exception("Informes: no se pudo calcular el periodo en curso %s", kind)
    chosen = key or next((r["key"] for r in reports if r["kind"] == "M"), reports[0]["key"] if reports else None)
    return templates.TemplateResponse("_reports.html", {"request": request, "reports": reports, "kinds": rs.KINDS,
                                                        "report": rs.get(db_session, chosen) if chosen else None,
                                                        "ai_kinds": rs.AI_KINDS, "pending": rs.due(db_session)})


@router.get("/portfolio/informes/{key}", response_class=HTMLResponse)
def portfolio_report_fragment(request: Request, key: str, db_session: Session = Depends(get_db)):
    """Un informe (el panel de la derecha de la pestaña «Informes»)."""
    from app.application.services import report_service as rs
    return templates.TemplateResponse("_reports.html", {"request": request, "only_report": True, "kinds": rs.KINDS,
                                                        "report": rs.get(db_session, key), "ai_kinds": rs.AI_KINDS})


@router.get("/ideal-portfolio", response_class=HTMLResponse)
def ideal_portfolio_page(
    request: Request,
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    db_session: Session = Depends(get_db)
):
    """Portfolio ideal por roles: cada grupo se llena con las empresas a las que les has dado ese rol."""
    exposure = portfolio_exposure(portfolio_service, asset_repo)
    positions = exposure["positions"]
    held = {i.ticker for i in portfolio_service.get_active_portfolio() if i.shares > 0}
    assets = {a.ticker: a for a in db_session.query(DBAsset).all()}
    data = {t: dict(a.data or {}) for t, a in assets.items()}
    tickers = sorted(t for t, a in assets.items()
                     if roles_service.eligible(data[t], bool(a.is_favorite), t in held) or data[t].get(roles_service.ROLE_KEY))
    tickers += sorted(held - set(tickers))
    ensure_prices_cached(tickers, asset_repo)

    def company(t):
        d, a = data.get(t, {}), assets.get(t)
        role = d.get(roles_service.ROLE_KEY)
        quote = INDEX_CACHE["data"].get(t) or {}
        price = quote.get("price")
        # variación del día, solo si su mercado ya ha cotizado hoy (si no, «–», como en el resto de tablas)
        day_pct = None
        if price and quote.get("prev") and quote.get("last_date") == datetime.date.today():
            day_pct = (price / quote["prev"] - 1) * 100
        val = None
        if role:   # la valoración base solo para las clasificadas (la de las demás no se muestra)
            try:
                val = bv.live(t, d, price)
            except Exception:
                logger.exception("Portfolio ideal: no se pudo evaluar la valoración de %s", t)
        group, drop = proximity(val, price)
        return {"ticker": t, "name": d.get("company_name") or t, "role": role, "held": t in held, "day_pct": day_pct,
                "value_eur": (positions.get(t) or {}).get("value_eur") or 0.0,
                "favorite": bool(a and a.is_favorite), "status": d.get("status"),
                "val": val, "group": group, "drop_pct": drop}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:   # la métrica TTM puede descargarse por empresa
        companies = list(pool.map(company, tickers))
    roles = roles_service.load_roles()
    return templates.TemplateResponse("ideal_portfolio.html", {
        "request": request,
        "settings": get_settings_from_db(db_session),
        "roles": roles,
        "ideal": roles_service.build(roles, companies, exposure["total_eur"]),
        "target": bv.TARGET_RETURN_PCT,
    })

