import logging
import re
import time
import datetime
import concurrent.futures
import os
import yfinance as yf
import markdown
import pandas as pd
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db
from app.infrastructure.db.models import DBAsset, DBTask, DBPortfolioItem, DBTransaction
from app.domain.models import TransactionType
from app.infrastructure.dependencies import (
    get_asset_repository,
    get_portfolio_service,
    get_performance_service,
    get_settings_from_db,
)
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.application.services.portfolio_service import PortfolioService
from app.application.services.performance_service import PerformanceService
from app.infrastructure.templates import templates

from utils.parsers import parse_summary
from utils.financial_math import calculate_custom_metrics, calculate_cagr_metrics, get_estimated_growth, get_financials_history, get_extended_stats
from app.application.services.alerts_service import get_alerts_data, run_alerts_scan

router = APIRouter(tags=["Market Views"])

# Cache global simple para la página de inicio
# prices_update: timestamp del último fetch de precios (TTL corto)
# last_update: timestamp del último fetch de eventos (TTL largo)
INDEX_CACHE = {"data": {}, "last_update": 0, "prices_update": 0, "upcoming_events": []}

PRICE_TTL = 120   # precios: 2 minutos
EVENT_TTL = 600   # eventos: 10 minutos

# Caché de variación por periodo para la tabla de /database (1D sigue usando INDEX_CACHE, sin tocar)
PERIOD_CACHE = {}  # {period: {"data": {ticker: pct}, "update": timestamp}}
PERIOD_TTL = 3600  # 1 hora: estos periodos no cambian intradía salvo el propio precio actual
PERIOD_START_DATES = {
    "1w": lambda today: today - datetime.timedelta(days=7),
    "1m": lambda today: today - datetime.timedelta(days=30),
    "3m": lambda today: today - datetime.timedelta(days=91),
    "6m": lambda today: today - datetime.timedelta(days=182),
    "ytd": lambda today: datetime.date(today.year, 1, 1),
    "3y": lambda today: today - datetime.timedelta(days=3 * 365),
    "5y": lambda today: today - datetime.timedelta(days=5 * 365),
}

def get_period_variacion(tickers: list, period: str) -> dict:
    """Devuelve {ticker: pct_change} para un periodo (1w/1m/3m/6m/ytd/3y/5y), con caché TTL.
    No sustituye a INDEX_CACHE (1D): es un cálculo aparte sobre una ventana histórica más larga.
    """
    now = time.time()
    tickers = list(set([t for t in tickers if t and not t.startswith('^')]))
    if not tickers or period not in PERIOD_START_DATES:
        return {}

    cache_entry = PERIOD_CACHE.get(period, {"data": {}, "update": 0})
    stale = now - cache_entry["update"] > PERIOD_TTL or any(t not in cache_entry["data"] for t in tickers)
    if not stale:
        return cache_entry["data"]

    start_date = PERIOD_START_DATES[period](datetime.date.today())
    result = dict(cache_entry["data"])
    try:
        data = yf.download(tickers, start=start_date.isoformat(), progress=False)
        if not data.empty and 'Close' in data:
            closes = data['Close']
            if isinstance(closes, pd.Series):
                closes = pd.DataFrame({tickers[0]: closes})
            for t in closes.columns:
                try:
                    col = closes[t].dropna()
                    if len(col) >= 2:
                        first = float(col.iloc[0])
                        last = float(col.iloc[-1])
                        result[t] = ((last / first) - 1) * 100 if first > 0 else 0
                except Exception:
                    continue
        PERIOD_CACHE[period] = {"data": result, "update": now}
    except Exception as e:
        print(f"Error fetching period variacion ({period}): {e}")
    return result

def is_reload_request(request: Request) -> bool:
    """Detecta si la petición es un refresco de página (F5 o Ctrl+F5)"""
    cache_control = request.headers.get("cache-control", "").lower()
    pragma = request.headers.get("pragma", "").lower()
    return "no-cache" in cache_control or "max-age=0" in cache_control or "no-cache" in pragma

def get_currency_symbol(currency_code, ticker=""):
    symbols = {'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥', 'CAD': 'C$', 'AUD': 'A$', 'CHF': 'Fr', 'CNY': '¥', 'HKD': 'HK$', 'NZD': 'NZ$', 'SEK': 'kr', 'KRW': '₩', 'SGD': 'S$', 'NOK': 'kr', 'MXN': '$', 'INR': '₹'}
    symbol = symbols.get(currency_code)
    if symbol: return symbol
    if ticker.endswith('.MC') or ticker.endswith('.PA') or ticker.endswith('.AS') or ticker.endswith('.MI'): return '€'
    if ticker.endswith('.L'): return '£'
    return '$'

def get_region_for_asset(ticker, country):
    country_lower = country.lower() if country else ""
    europe = ["united kingdom", "germany", "france", "italy", "spain", "netherlands", "switzerland", "sweden", "poland", "belgium", "austria", "denmark", "finland", "ireland", "norway", "portugal", "greece", "czech republic", "hungary"]
    
    # 1. Check ticker suffixes for Europe
    eur_suffixes = ('.MC', '.PA', '.AS', '.MI', '.WA', '.L', '.ST', '.DE', '.SW', '.AT', '.OL')
    if ticker.upper().endswith(eur_suffixes):
        return "Europa"
        
    # 2. Check country name
    if "united states" in country_lower or "usa" in country_lower:
        return "USA"
    elif any(c in country_lower for c in europe):
        return "Europa"
        
    # 3. Fallback for tickers without suffix (mostly USA)
    if country_lower in ("", "-"):
        if '.' not in ticker:
            return "USA"
            
    return "Otros"

def ensure_prices_cached(tickers: list, asset_repo) -> None:
    """Asegura que los tickers provistos tengan su precio y variación cargados en INDEX_CACHE."""
    import time
    import pandas as pd
    import yfinance as yf
    
    now = time.time()
    PRICE_TTL = 300 # 5 minutos
    
    # Filtrar tickers vacíos o inválidos
    tickers = list(set([t for t in tickers if t and not t.startswith('^')]))
    if not tickers:
        return
        
    price_stale = (
        now - INDEX_CACHE.get("prices_update", 0) > PRICE_TTL
        or any(t for t in tickers if t not in INDEX_CACHE["data"])
    )
    
    if price_stale:
        try:
            # Obtener monedas de cada asset para descargar tipos de cambio
            currencies = []
            for t in tickers:
                try:
                    currencies.append(asset_repo.get_asset_data(t).get("currency", "USD"))
                except:
                    currencies.append("USD")
            currencies = set(currencies)
            fx_list = [f"{c}EUR=X" for c in currencies if c != "EUR"]
            
            all_symbols = tickers + fx_list
            data = yf.download(all_symbols, period="5d", progress=False)
            
            # Intentar descargar 1d para el último dato intradiario más preciso
            try:
                latest_data = yf.download(all_symbols, period="1d", progress=False)
                if not data.empty and not latest_data.empty:
                    last_idx = data.index[-1]
                    if last_idx in latest_data.index:
                        for col in data.columns:
                            if col in latest_data.columns:
                                val = latest_data.at[last_idx, col]
                                if not pd.isna(val):
                                    data.at[last_idx, col] = val
            except:
                pass
                
            if not data.empty and 'Close' in data:
                closes = data['Close']
                
                # Convertir a DataFrame si es una sola Serie (caso de un solo ticker total)
                if isinstance(closes, pd.Series):
                    closes = pd.DataFrame({tickers[0]: closes})
                    
                for t in closes.columns:
                    try:
                        col_close = closes[t].dropna()
                        if not col_close.empty:
                            last_date = col_close.index[-1].date()
                            price = float(col_close.iloc[-1])
                            prev = float(col_close.iloc[-2]) if len(col_close) > 1 else price
                            
                            p_last_date = col_close.index[-1].date()
                            p_prev = prev
                            if len(col_close) > 1:
                                calendar_gap = (col_close.index[-1].date() - col_close.index[-2].date()).days
                                expected_gap = 3 if last_date.weekday() == 0 else 1
                                if calendar_gap > expected_gap:
                                    try:
                                        p_prev = float(yf.Ticker(t).fast_info["previous_close"])
                                    except Exception:
                                        pass
                                        
                            INDEX_CACHE["data"][t] = {
                                "price": price,
                                "prev": p_prev,
                                "last_date": last_date
                            }
                    except:
                        continue
                
                INDEX_CACHE["prices_update"] = now
        except Exception as e:
            print(f"Error fetching prices in ensure_prices_cached: {e}")

@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    performance_service: PerformanceService = Depends(get_performance_service),
    db_session: Session = Depends(get_db)
):
    # F5: solo invalidar caché de precios (eventos son lentos, se cachean por separado)
    if is_reload_request(request):
        INDEX_CACHE["prices_update"] = 0
        INDEX_CACHE["data"] = {}

    settings = get_settings_from_db(db_session)
    val_cfg = settings.get("valuation", {"ganga": 0.60, "barata": 0.85, "justo_max": 1.15, "cara_max": 1.40})

    ticker_names = asset_repo.get_all_tickers()
    active_portfolio = portfolio_service.get_active_portfolio()
    all_involved = list(set(ticker_names + [p.ticker for p in active_portfolio]))

    indices_to_fetch = {"^GSPC": "S&P 500", "^IXIC": "NASDAQ", "URTH": "MSCI World", "^IBEX": "IBEX 35", "^STOXX50E": "Eurostoxx 50", "BTC-USD": "Bitcoin"}

    now = time.time()

    # Forzar refresco si los eventos cacheados son del formato antiguo (sin is_portfolio)
    cached_evs = INDEX_CACHE.get("upcoming_events", [])
    if cached_evs and "is_portfolio" not in cached_evs[0]:
        INDEX_CACHE["last_update"] = 0

    # ── PRECIOS: refresco rápido cada PRICE_TTL segundos ──────────────────────
    price_stale = (
        now - INDEX_CACHE.get("prices_update", 0) > PRICE_TTL
        or any(t for t in all_involved if t not in INDEX_CACHE["data"])
    )
    if price_stale:
        try:
            currencies = {asset_repo.get_asset_data(t).get("currency", "USD") for t in all_involved}
            fx_list = [f"{c}EUR=X" for c in currencies if c != "EUR"]

            data = yf.download(all_involved + list(indices_to_fetch.keys()) + fx_list, period="5d", progress=False)
            try:
                latest_data = yf.download(all_involved + list(indices_to_fetch.keys()) + fx_list, period="1d", progress=False)
                if not data.empty and not latest_data.empty:
                    last_idx = data.index[-1]
                    if last_idx in latest_data.index:
                        for col in data.columns:
                            if col in latest_data.columns:
                                val = latest_data.at[last_idx, col]
                                if not pd.isna(val):
                                    data.at[last_idx, col] = val
            except Exception:
                pass
            if not data.empty and 'Close' in data:
                closes = data['Close']
                
                for t in closes.columns:
                    try:
                        col_close = closes[t].dropna()
                        if not col_close.empty:
                            last_date = col_close.index[-1].date()
                            price = float(col_close.iloc[-1])
                            prev = float(col_close.iloc[-2]) if len(col_close) > 1 else price
                            
                            # Fallback if Yahoo historical bars are lagging or missing the previous trading day
                            if len(col_close) > 1:
                                calendar_gap = (col_close.index[-1].date() - col_close.index[-2].date()).days
                                expected_gap = 3 if last_date.weekday() == 0 else 1
                                if calendar_gap > expected_gap:
                                    try:
                                        prev_fast = yf.Ticker(t).fast_info.get("previous_close")
                                        if prev_close_val := prev_close_val if False else prev_close_val if False else prev_close_val if False else prev_close_val if False else None: pass
                                        # Let's clean up
                                    except Exception:
                                        pass
                            # Clean python logic for previous close fallback:
                            p_last_date = col_close.index[-1].date()
                            p_prev = prev
                            if len(col_close) > 1:
                                calendar_gap = (col_close.index[-1].date() - col_close.index[-2].date()).days
                                expected_gap = 3 if last_date.weekday() == 0 else 1
                                if calendar_gap > expected_gap:
                                    try:
                                        p_prev = float(yf.Ticker(t).fast_info["previous_close"])
                                    except Exception:
                                        pass
                                    
                            INDEX_CACHE["data"][t] = {
                                "price": price,
                                "prev": p_prev,
                                "last_date": last_date
                            }
                    except: continue

                INDEX_CACHE["prices_update"] = now

                # Scan automático de alertas con los precios recién descargados
                try:
                    ticker_prices_for_alerts = {
                        t: INDEX_CACHE["data"][t]["price"]
                        for t in all_involved
                        if t in INDEX_CACHE["data"]
                    }
                    run_alerts_scan(ticker_prices_for_alerts, asset_repo, db_session, settings)
                except Exception as ae:
                    print(f"Auto alert scan error: {ae}")

        except Exception as e:
            print(f"Price fetch error: {e}")

    # ── EVENTOS: refresco lento cada EVENT_TTL segundos ───────────────────────
    if now - INDEX_CACHE["last_update"] > EVENT_TTL:
        try:
            _asset_meta = {
                a.ticker: {"is_fav": bool(a.is_favorite), "status": (a.data or {}).get("status")}
                for a in db_session.query(DBAsset).all()
            }
            event_items = []
            _ev_seen = set()
            for p in active_portfolio:
                event_items.append({"ticker": p.ticker, "shares": p.shares, "is_portfolio": True, "is_favorite": _asset_meta.get(p.ticker, {}).get("is_fav", False)})
                _ev_seen.add(p.ticker)
            for t, meta in _asset_meta.items():
                if t in _ev_seen:
                    continue
                if meta.get("is_fav") or meta.get("status") == "ACCEPTED":
                    event_items.append({"ticker": t, "shares": 0, "is_portfolio": False, "is_favorite": meta.get("is_fav", False)})
                    _ev_seen.add(t)

            # Histórico de transacciones (BUY/SELL/SPLIT) de los tickers en cartera,
            # para saber cuántas acciones teníamos realmente en la fecha ex-dividendo
            # (no las que tenemos ahora, que pueden haber cambiado desde entonces).
            _portfolio_tickers = [p.ticker for p in active_portfolio]
            _tx_by_ticker = {}
            if _portfolio_tickers:
                _tx_rows = (
                    db_session.query(DBTransaction)
                    .filter(DBTransaction.ticker.in_(_portfolio_tickers))
                    .all()
                )
                for tx in _tx_rows:
                    try:
                        tx_date = datetime.datetime.strptime(tx.date[:10], "%Y-%m-%d").date()
                    except Exception:
                        continue
                    _tx_by_ticker.setdefault(tx.ticker, []).append((tx_date, tx.type, tx.shares))
                for t in _tx_by_ticker:
                    _tx_by_ticker[t].sort(key=lambda x: x[0])

            def shares_as_of(ticker, as_of_date):
                total = 0.0
                for tx_date, tx_type, tx_shares in _tx_by_ticker.get(ticker, []):
                    if tx_date > as_of_date:
                        break
                    if tx_type == TransactionType.SPLIT:
                        if tx_shares > 0:
                            total *= tx_shares
                    elif tx_type == TransactionType.BUY:
                        total += tx_shares
                    elif tx_type == TransactionType.SELL:
                        total -= tx_shares
                return total

            def fetch_event(item):
                ticker = item["ticker"]
                evs = []
                try:
                    stock = yf.Ticker(ticker)
                    today = datetime.date.today()
                    info = stock.info
                    cal = stock.calendar

                    dts = info.get('exDividendDate') or info.get('dividendExDate')
                    if dts:
                        d = datetime.date.fromtimestamp(dts)
                        div_amount = info.get('lastDividendValue')
                        if not div_amount:
                            div_rate = info.get('dividendRate')
                            if div_rate:
                                div_amount = div_rate / 4.0
                            else:
                                div_amount = 0

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

                        # "Próximo a cobrar": mientras el pago no se haya realizado, sigue siendo
                        # relevante aunque la fecha ex-dividendo ya haya pasado (fallback a ex-div
                        # si no conocemos la fecha de pago).
                        pending = (pay_date_obj >= today) if pay_date_obj else (d >= today)

                        # Acciones que teníamos en la fecha ex-dividendo (no las actuales):
                        # si compramos después del ex-div, ese dividendo concreto no nos corresponde.
                        shares_at_exdiv = shares_as_of(ticker, d) if item["is_portfolio"] else 0

                        if pending and (not item["is_portfolio"] or shares_at_exdiv > 0.0001):
                            evs.append({"ticker": ticker, "type": "Dividendo", "date": d, "pay_date": pay_date_obj, "amount": div_amount * shares_at_exdiv, "is_portfolio": item["is_portfolio"], "is_favorite": item["is_favorite"]})

                    ed = None
                    if isinstance(cal, dict): ed = cal.get('Earnings Date')
                    elif isinstance(cal, pd.DataFrame) and 'Earnings Date' in cal.index: ed = cal.loc['Earnings Date'].iloc[0]
                    if ed:
                        if isinstance(ed, list): ed = ed[0]
                        if hasattr(ed, 'date'): ed = ed.date()
                        if ed >= today: evs.append({"ticker": ticker, "type": "Resultados", "date": ed, "is_portfolio": item["is_portfolio"], "is_favorite": item["is_favorite"]})
                    return evs
                except: return []

            print(f"[events] fetching for {len(event_items)} items: {[x['ticker'] for x in event_items]}")
            all_evs = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
                for res in ex.map(fetch_event, event_items): all_evs.extend(res)
            print(f"[events] found {len(all_evs)} raw events: {[(e['ticker'], e['type']) for e in all_evs]}")

            unique_evs = []
            seen = set()
            for e in all_evs:
                k = (e['ticker'], e['type'], e['date'])
                if k not in seen: unique_evs.append(e); seen.add(k)
            unique_evs.sort(key=lambda x: x["date"])
            INDEX_CACHE["upcoming_events"] = unique_evs
            INDEX_CACHE["last_update"] = now

        except Exception as e:
            print(f"Event fetch error: {e}")

    # 3. Portfolio Performance
    today = datetime.date.today()
    session_indices = ["^GSPC", "^IXIC", "^IBEX", "^STOXX50E", "URTH"]
    dates_found = [INDEX_CACHE["data"][idx]["last_date"] for idx in session_indices if idx in INDEX_CACHE["data"]]
    latest_market_date = max(dates_found) if dates_found else today

    port_now, port_prev = 0, 0
    is_weekday = today.weekday() < 5
    for item in active_portfolio:
        t = item.ticker
        if t in INDEX_CACHE["data"]:
            d = INDEX_CACHE["data"][t]
            rate = 1.0
            curr = asset_repo.get_asset_data(t).get("currency", "USD")
            if curr != "EUR": rate = INDEX_CACHE["data"].get(f"{curr}EUR=X", {"price": 1.0})["price"]
            
            p_price = d["price"]
            p_prev = d["prev"]
            p_last_date = d.get("last_date")
            if is_weekday and p_last_date and p_last_date != today:
                p_prev = p_price
                
            v_now = item.shares * p_price * rate
            v_prev = item.shares * p_prev * rate
            port_now += v_now; port_prev += v_prev

    port_pct = ((port_now / port_prev) - 1) * 100 if port_prev > 0 else 0
    market_indices = [{"name": "MI CARTERA", "price": port_now, "change": port_now - port_prev, "pct": port_pct, "is_portfolio": True}]
    for sym, name in indices_to_fetch.items():
        if sym in INDEX_CACHE["data"]:
            d = INDEX_CACHE["data"][sym]
            p_price = d["price"]
            p_prev = d["prev"]
            p_last_date = d.get("last_date")
            if is_weekday and p_last_date and p_last_date != today:
                p_prev = p_price
            change = p_price - p_prev
            pct = (change / p_prev) * 100 if p_prev > 0 else 0
            market_indices.append({"name": name, "price": p_price, "change": change, "pct": pct, "is_portfolio": False})

    # 4. Movers, Heatmap, Opportunities
    all_daily_movers, fav_movers, heatmap_data, near_opportunities = [], [], [], []
    port_daily_movers, port_fav_movers = [], []
    portfolio_tickers = {p.ticker for p in active_portfolio}
    portfolio_shares = {p.ticker: p.shares for p in active_portfolio}

    for t in all_involved:
        db_d = asset_repo.get_asset_data(t)
        asset_obj = db_session.query(DBAsset).filter(DBAsset.ticker == t).first()
        is_fav = asset_obj.is_favorite if asset_obj else False

        if t in INDEX_CACHE["data"]:
            d = INDEX_CACHE["data"][t]
            p_price = d["price"]
            p_prev = d["prev"]
            p_last_date = d.get("last_date")
            if is_weekday and p_last_date and p_last_date != today:
                p_prev = p_price
            change = p_price - p_prev
            pct = ((p_price / p_prev) - 1) * 100 if p_prev > 0 else 0

            mover_item = {
                "name": t, "company_name": db_d.get("company_name", "-"),
                "pct": pct, "price": p_price, "change_abs": change,
                "symbol": get_currency_symbol(db_d.get("currency", "USD"), t),
                "region": get_region_for_asset(t, db_d.get("country", "-")),
                "status": db_d.get("status") or "NONE"
            }

            if db_session:
                asset_obj = db_session.query(DBAsset).filter(DBAsset.ticker == t).first()
                if asset_obj:
                    # Incluimos también si el activo es favorito
                    mover_item["is_favorite"] = asset_obj.is_favorite

            if db_d.get("status") in ["ACCEPTED", "STANDBY"] or t in portfolio_tickers:
                shares = portfolio_shares.get(t, 0)
                curr = db_d.get("currency", "USD")
                fx = INDEX_CACHE["data"].get(f"{curr}EUR=X", {}).get("price", 1.0) if curr != "EUR" else 1.0
                val_eur = d["price"] * shares * fx if t in portfolio_tickers else 0.0

                heatmap_data.append({
                    "ticker": t,
                    "pct": pct,
                    "sector": db_d.get("sector", "Otros"),
                    "industry": db_d.get("industry", db_d.get("sector", "Otros")),
                    "is_portfolio": t in portfolio_tickers,
                    "value_eur": val_eur
                })
                
            all_daily_movers.append(mover_item)
            
            if is_fav and pct != 0.0:
                fav_movers.append(mover_item)
                if t in portfolio_tickers:
                    port_fav_movers.append(mover_item)
            
            if t in portfolio_tickers:
                shares = portfolio_shares.get(t, 0)
                curr = db_d.get("currency", "USD")
                fx = INDEX_CACHE["data"].get(f"{curr}EUR=X", {}).get("price", 1.0) if curr != "EUR" else 1.0
                mover_item["total_change_eur"] = change * shares * fx
                port_daily_movers.append(mover_item)
            
            # Oportunidades de compra — ACCEPTED, sin cartera, con PE de compra fijado
            if t in ticker_names and db_d.get("status") == "ACCEPTED" and t not in portfolio_tickers:
                fpe_d = db_d.get("fair_pe_data", {})
                buy_pe = fpe_d.get("fair_forward_pe")
                market_pe = db_d.get("last_market_fwd_pe")
                cache_t = INDEX_CACHE["data"].get(t, {})
                current_price = cache_t.get("price")
                if buy_pe and market_pe:
                    try:
                        buy_pe_f, market_pe_f = float(buy_pe), float(market_pe)
                        if buy_pe_f > 0 and market_pe_f > 0:
                            # precio_compra = PE_compra × EPS_forward (independiente del precio live)
                            # Prioridad: EPS directo → implied_EPS del snapshot guardado
                            forward_eps = db_d.get("last_forward_eps")
                            if not forward_eps:
                                ref_p = db_d.get("last_market_fwd_pe_ref_price")
                                if ref_p and market_pe_f > 0:
                                    forward_eps = float(ref_p) / market_pe_f
                            if forward_eps:
                                compra_pe = buy_pe_f * val_cfg.get("barata", 0.85)
                                buy_price = compra_pe * float(forward_eps)
                            else:
                                buy_price = None
                            if not buy_price or not current_price:
                                continue
                            drop_needed = round((current_price - buy_price) / current_price * 100, 1)
                            prev = cache_t.get("prev", current_price)
                            p_last_date = cache_t.get("last_date")
                            if is_weekday and p_last_date and p_last_date != today:
                                prev = current_price
                            ch_abs = round(current_price - prev, 2) if (current_price and prev) else 0.0
                            ch_pct = round((current_price / prev - 1) * 100, 2) if (current_price and prev) else 0.0
                            if drop_needed <= 30:
                                near_opportunities.append({
                                    "ticker": t,
                                    "company_name": db_d.get("company_name", t),
                                    "current_price": current_price,
                                    "buy_price": round(buy_price, 2),
                                    "drop_needed": drop_needed,
                                    "change_abs": ch_abs,
                                    "change_pct": ch_pct,
                                    "currency_symbol": get_currency_symbol(db_d.get("currency", "USD"), t),
                                })
                    except (ValueError, TypeError):
                        pass

    # Sort ALL valid daily movers by PCT Descending
    sorted_movers = sorted(all_daily_movers, key=lambda x: x["pct"], reverse=True)
    top_gainers = [m for m in sorted_movers if m["pct"] >= 0]
    top_losers = sorted([m for m in sorted_movers if m["pct"] < 0], key=lambda x: x["pct"])
    
    # Portfolio Specific Movers
    sorted_port_movers = sorted(port_daily_movers, key=lambda x: x["pct"], reverse=True)
    
    fav_movers = sorted(fav_movers, key=lambda x: x["pct"])
    port_fav_movers = sorted(port_fav_movers, key=lambda x: x["pct"])
    near_opportunities = sorted(near_opportunities, key=lambda x: x["drop_needed"])[:10]

    _today = datetime.date.today()
    evs = [
        {
            **e,
            "is_portfolio": e.get("is_portfolio", False),
            "is_favorite": e.get("is_favorite", False),
            "days_remaining": (e["date"] - _today).days,
            "pay_days_remaining": (e["pay_date"] - _today).days if e.get("pay_date") else None,
        }
        for e in INDEX_CACHE.get("upcoming_events", [])
    ]

    # Widget de próximos eventos para el dashboard
    _asset_status_map = {a.ticker: (a.data or {}).get("status") for a in db_session.query(DBAsset).all()}
    cal_preview = []
    for e in evs:
        t = e["ticker"]
        is_port = e.get("is_portfolio", False)
        is_fav  = e.get("is_favorite", False)
        st = _asset_status_map.get(t, "")
        if e["type"] == "Dividendo":
            if not is_port: continue
            cat = "dividend"
        elif is_port:   cat = "portfolio"
        elif is_fav:    cat = "favorite"
        elif st == "ACCEPTED": cat = "seguimiento"
        else: continue
        cal_preview.append({**e, "category": cat})
    cal_preview = sorted(cal_preview, key=lambda x: x["date"])[:8]

    # Generar los 7 días de la semana actual (Lunes a Domingo)
    start_of_week = _today - datetime.timedelta(days=_today.weekday())
    week_days = []
    day_names = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    for i in range(7):
        d = start_of_week + datetime.timedelta(days=i)
        day_events = []
        for e in evs:
            if e["date"] == d:
                # Solo dividendos de cartera (los que vamos a cobrar)
                if e["type"] == "Dividendo" and not e.get("is_portfolio", False):
                    continue
                day_events.append({
                    "ticker": e["ticker"],
                    "type": e["type"], # "Resultados" o "Dividendo"
                    "is_portfolio": e.get("is_portfolio", False),
                    "is_favorite": e.get("is_favorite", False),
                    "amount": e.get("amount") # solo para dividendos
                })
        week_days.append({
            "date": d,
            "day_name": day_names[i],
            "day_num": d.day,
            "is_today": d == _today,
            "events": day_events
        })

    # Serializar todos los eventos válidos a formato compatible con JSON en JS (para paginado de semanas)
    serialized_evs = []
    for e in evs:
        # Solo dividendos de cartera (los que vamos a cobrar)
        if e["type"] == "Dividendo" and not e.get("is_portfolio", False):
            continue
        serialized_evs.append({
            "ticker": e["ticker"],
            "type": e["type"],
            "date": e["date"].isoformat(), # string 'YYYY-MM-DD'
            "is_portfolio": e.get("is_portfolio", False),
            "is_favorite": e.get("is_favorite", False),
            "amount": e.get("amount")
        })

    # 5. Listas personalizadas para el Dashboard
    custom_lists_movers = []
    try:
        for clist in asset_repo.get_company_lists():
            clist_movers = []
            for asset in clist.assets:
                if asset.ticker in INDEX_CACHE["data"]:
                    d = INDEX_CACHE["data"][asset.ticker]
                    p_price = d["price"]
                    p_prev = d["prev"]
                    p_last_date = d.get("last_date")
                    if is_weekday and p_last_date and p_last_date != today:
                        p_prev = p_price
                    change = p_price - p_prev
                    pct = ((p_price / p_prev) - 1) * 100 if p_prev > 0 else 0
                    
                    db_d = asset_repo.get_asset_data(asset.ticker)
                    
                    mover_item = {
                        "name": asset.ticker,
                        "company_name": db_d.get("company_name", asset.company_name or "-"),
                        "pct": pct, "price": p_price, "change_abs": change,
                        "symbol": get_currency_symbol(db_d.get("currency", "USD"), asset.ticker),
                        "region": get_region_for_asset(asset.ticker, db_d.get("country", "-")),
                        "status": db_d.get("status") or "NONE",
                        "is_favorite": asset.is_favorite
                    }
                    clist_movers.append(mover_item)
                    
            # Ordenar de menor a mayor pct (igual que favoritos)
            clist_movers = sorted(clist_movers, key=lambda x: x["pct"])
            
            custom_lists_movers.append({
                "id": clist.id,
                "name": clist.name,
                "movers": clist_movers
            })
    except Exception as e:
        logging.error(f"Error calculating custom lists movers: {e}")

    # Alertas de caída (todas, para tabla paginada en dashboard)
    drop_alerts = []
    alerts_last_statuses = {}
    try:
        _alerts_data = get_alerts_data(db_session)
        all_drop_alerts = sorted(
            [a for a in _alerts_data.get("history", []) if a.get("type") == "CAÍDA"],
            key=lambda x: x.get("date", ""),
            reverse=True
        )

        # Filtramos solo las que están en seguimiento
        all_assets = db_session.query(DBAsset).all()
        seguimiento_tickers = {
            asset.ticker for asset in all_assets if (asset.data or {}).get("status") == "ACCEPTED"
        }
        
        logging.info(f"Tickers en seguimiento: {seguimiento_tickers}")
        logging.info(f"Alertas de caída antes de filtrar: {[a['ticker'] for a in all_drop_alerts]}")
        
        drop_alerts = [alert for alert in all_drop_alerts if alert["ticker"] in seguimiento_tickers]
        logging.info(f"Alertas de caída después de filtrar: {[a['ticker'] for a in drop_alerts]}")

        alerts_last_statuses = _alerts_data.get("last_statuses", {})
    except Exception as e:
        print(f"Error processing drop alerts: {e}")

    return templates.TemplateResponse("index.html", {
        "request": request,
        "top_gainers": top_gainers, "top_losers": top_losers, "fav_movers": fav_movers,
        "custom_lists": custom_lists_movers,
        "port_all": sorted_port_movers,
        "market_indices": market_indices, "near_opportunities": near_opportunities,
        "upcoming_earnings": [e for e in evs if e["type"] == "Resultados"],
        "upcoming_dividends": [e for e in evs if e["type"] == "Dividendo" and e.get("is_portfolio", False)],
        "week_days": week_days,
        "upcoming_events_json": serialized_evs,
        "heatmap_data": heatmap_data,
        "drop_alerts": drop_alerts,
        "alerts_last_statuses": alerts_last_statuses,
        "settings": settings,
    })

@router.get("/database", response_class=HTMLResponse)
async def database_page(
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    db_session: Session = Depends(get_db)
):
    settings = get_settings_from_db(db_session)
    val_cfg = settings.get("valuation", {"ganga": 0.60, "barata": 0.85, "justo_max": 1.15, "cara_max": 1.40})
    session_indices = ["^GSPC", "^IXIC", "^IBEX", "^STOXX50E", "URTH"]
    dates_found = [INDEX_CACHE["data"][idx]["last_date"] for idx in session_indices if idx in INDEX_CACHE["data"]]
    latest_market_date = max(dates_found) if dates_found else datetime.date.today()

    active_portfolio_tickers = {
        p.ticker for p in db_session.query(DBPortfolioItem).filter(DBPortfolioItem.is_closed == False).all()
    }

    tickers = []
    assets = db_session.query(DBAsset).all()
    ensure_prices_cached([a.ticker for a in assets], asset_repo)
    for asset in assets:
        t = asset.ticker
        db_d = asset.data or {}
        scores = {k: db_d.get(f"{k}_score", "-") for k in ["fin", "cap", "moat", "verdict"]}
        
        # Calcular Nota General dinámica como el promedio de todas las notas numéricas activas
        report_keys = [
            "fin", "cap", "moat", "verdict", "mgmt", "rules", "pe_forensic", "model", "thesis",
            "audit_income", "audit_balance", "audit_cashflow"
        ]
        
        # Calcular puntuación de Normas de Calidad si no existe una nota global de rules
        # Sumando los 5 pilares de calidad (cada uno sobre 2.0, total sobre 10.0)
        rules_s = db_d.get("rules_score", "-").split("/")[0].strip()
        if rules_s == "-":
            pilar_scores = []
            for i in range(1, 6):
                p_s = db_d.get(f"rules_pilar{i}_score", "-").split("/")[0].strip()
                if p_s != "-":
                    try:
                        pilar_scores.append(float(p_s))
                    except:
                        pass
            if pilar_scores:
                rules_s = f"{sum(pilar_scores):.1f}"

        valid_scores = []
        for k in report_keys:
            if k == "rules" and rules_s != "-":
                val_s = rules_s
            else:
                val_s = db_d.get(f"{k}_score", "-").split("/")[0].strip()
                
            if val_s != "-" and val_s != "N/A" and val_s != "":
                try:
                    valid_scores.append(float(val_s))
                except:
                    pass
                    
        general_score = "-"
        if valid_scores:
            avg_score = sum(valid_scores) / len(valid_scores)
            general_score = f"{avg_score:.1f}"

        # Crear tooltip con el desglose de las tres notas clave solicitadas por el usuario
        tooltip_parts = []
        for k, label in [("moat", "Franquicia y Moat"), ("fin", "Auditoría Financiera Forense"), ("cap", "Dirección y Capital Allocation")]:
            val_s = db_d.get(f"{k}_score", "-").split("/")[0].strip()
            if val_s != "-" and val_s != "N/A" and val_s != "":
                tooltip_parts.append(f"{label}: {val_s}")
                
        score_tooltip = " | ".join(tooltip_parts) if tooltip_parts else "Sin notas registradas"

        fpe_d = db_d.get("fair_pe_data", {})
        fpe, mpe = fpe_d.get("fair_forward_pe"), db_d.get("last_market_fwd_pe")
        val = {"status": "N/A", "fair": fpe or "-", "market": mpe or "-", "ratio": 999}
        if fpe and mpe:
            r = float(mpe)/float(fpe)
            val["ratio"] = r
            if r < val_cfg["ganga"]: val["status"] = "Ganga"
            elif r < val_cfg["barata"]: val["status"] = "Barato"
            elif r <= val_cfg["justo_max"]: val["status"] = "Precio Justo"
            elif r <= val_cfg["cara_max"]: val["status"] = "Caro"
            else: val["status"] = "Burbuja"
        
        m_data = {"price": 0, "change": 0, "pct": 0}
        if t in INDEX_CACHE["data"]:
            d = INDEX_CACHE["data"][t]
            p_price = d["price"]
            p_prev = d["prev"]
            p_last_date = d.get("last_date")
            today_date = datetime.date.today()
            is_weekday = today_date.weekday() < 5
            
            if is_weekday and p_last_date and p_last_date != today_date:
                p_prev = p_price
                
            change = p_price - p_prev
            pct = ((p_price / p_prev) - 1) * 100 if p_prev > 0 else 0
            m_data = {"price": p_price, "change": change, "pct": pct}

        db_status = db_d.get("status")
        if not db_status or db_status == "None" or db_status == "":
            db_status = "PENDING"

        tickers.append({
            "name": t, 
            "company_name": asset.company_name or "-", 
            "country": db_d.get("country", "-"), 
            "region": get_region_for_asset(t, db_d.get("country", "-")), 
            "sector": db_d.get("sector", "-"), 
            "subsector": db_d.get("industry", db_d.get("subsector", "-")),
            "currency_symbol": get_currency_symbol(db_d.get("currency", "USD"), t), 
            "market_data": m_data, 
            "status": db_status, 
            "scores": scores, 
            "general_score": general_score, 
            "score_tooltip": score_tooltip,
            "rules_score": rules_s if rules_s != "-" else "-",
            "valuation": val, 
            "is_favorite": asset.is_favorite,
            "is_portfolio": t in active_portfolio_tickers
        })

    return templates.TemplateResponse("database.html", {
        "request": request, 
        "tickers": tickers, 
        "regions": sorted(list(set(t["region"] for t in tickers if t.get("region") and t["region"] != "-"))), 
        "sectores": sorted(list(set(t["sector"] for t in tickers if t.get("sector") and t["sector"] != "-"))), 
        "subsectores": sorted(list(set(t["subsector"] for t in tickers if t.get("subsector") and t["subsector"] != "-"))),
        "countries": sorted(list(set(t["country"] for t in tickers if t.get("country") and t["country"] != "-"))),
        "settings": settings
    })

@router.post("/search")
async def search(ticker: str = Form(...)):
    return RedirectResponse(url=f"/ticker/{ticker.upper()}", status_code=303)

@router.get("/ticker/{ticker}", response_class=HTMLResponse)
def ticker_page(request: Request, ticker: str, asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository), portfolio_service: PortfolioService = Depends(get_portfolio_service), db_session: Session = Depends(get_db)):
    stock = yf.Ticker(ticker)
    
    # ── CARGA RESILIENTE DE INFO DE YAHOO FINANCE (EVITA EL CRASH POR RATE LIMIT 429) ──
    info = {}
    db_d = asset_repo.get_asset_data(ticker)
    
    try:
        info = stock.info
        if not info:
            info = {}
    except Exception as e:
        print(f"yfinance info rate limit or network error for {ticker}: {e}")
        # Intentamos reconstruir info con los datos guardados previamente en base de datos para no dejar campos en blanco
        info = {
            'longName': db_d.get('company_name', ticker),
            'shortName': db_d.get('company_name', ticker),
            'country': db_d.get('country', '-'),
            'sector': db_d.get('sector', '-'),
            'industry': db_d.get('industry', '-'),
            'currency': db_d.get('currency', 'USD'),
            'forwardPE': db_d.get('last_market_fwd_pe', 'N/A'),
            'currentPrice': db_d.get('last_market_fwd_pe_ref_price', 0.0),
            'regularMarketPrice': db_d.get('last_market_fwd_pe_ref_price', 0.0),
            'forwardEps': db_d.get('last_forward_eps', 0.0)
        }
        
    mapping = {'company_name': ['longName', 'shortName'], 'country': ['country'], 'sector': ['sector'], 'industry': ['industry'], 'currency': ['currency']}
    for db_k, i_ks in mapping.items():
        for ik in i_ks:
            if info.get(ik): asset_repo.save_asset_data(ticker, db_k, info.get(ik)); break
            
    # Carga resiliente del historial de precios
    dates, op, hi, lo, cl = [], [], [], [], []
    try:
        hist = stock.history(period="10y")
        if not hist.empty:
            dates = hist.index.strftime('%Y-%m-%d').tolist()
            op = hist['Open'].tolist()
            hi = hist['High'].tolist()
            lo = hist['Low'].tolist()
            cl = hist['Close'].tolist()
    except Exception as e:
        print(f"yfinance history rate limit or network error for {ticker}: {e}")
        
    # Carga resiliente de las métricas derivadas
    metrics = {}
    try:
        metrics = calculate_custom_metrics(info, cl, stock)
        metrics.update(calculate_cagr_metrics(stock))
        metrics['estRevGrowth'] = get_estimated_growth(stock)
    except Exception as e:
        print(f"Error calculating derived metrics for {ticker}: {e}")
        metrics = {
            'revenue_cagr_5y': 'N/A', 'fcf_cagr_5y': 'N/A', 'eps_cagr_5y': 'N/A',
            'revenue_cagr_3y': 'N/A', 'fcf_cagr_3y': 'N/A', 'eps_cagr_3y': 'N/A',
            'estRevGrowth': 'N/A'
        }
        
    if info.get('forwardPE'):
        try:
            asset_repo.save_asset_data(ticker, "last_market_fwd_pe", info.get('forwardPE'))
            ref_price = info.get('currentPrice') or info.get('regularMarketPrice')
            if ref_price:
                asset_repo.save_asset_data(ticker, "last_market_fwd_pe_ref_price", float(ref_price))
        except Exception:
            pass
            
    if info.get('forwardEps'):
        try:
            if float(info.get('forwardEps')) > 0:
                asset_repo.save_asset_data(ticker, "last_forward_eps", float(info.get('forwardEps')))
        except Exception:
            pass
            
    db_d = asset_repo.get_asset_data(ticker) # Recargamos db_d con las posibles actualizaciones de metadatos de info
    
    # Auto-migración / Alias dinámico para activos analizados con la versión anterior (evita que el usuario pierda sus informes)
    if not db_d.get("franchise") and (db_d.get("moat") or db_d.get("model")):
        moat_text = db_d.get("moat", "")
        model_text = db_d.get("model", "")
        combined_franchise = ""
        if model_text:
            combined_franchise += model_text + "\n\n"
        if moat_text:
            combined_franchise += moat_text
            
        db_d["franchise"] = combined_franchise
        db_d["franchise_score"] = db_d.get("moat_score") or db_d.get("model_score") or "N/A"
        db_d["franchise_pros"] = db_d.get("moat_pros") or db_d.get("model_pros") or []
        db_d["franchise_cons"] = db_d.get("moat_cons") or db_d.get("model_cons") or []
        db_d["franchise_rejection"] = db_d.get("moat_rejection") or db_d.get("model_rejection")
        
    if not db_d.get("mgmt_alloc") and (db_d.get("cap") or db_d.get("mgmt")):
        cap_text = db_d.get("cap", "")
        mgmt_text = db_d.get("mgmt", "")
        combined_mgmt = ""
        if mgmt_text:
            combined_mgmt += mgmt_text + "\n\n"
        if cap_text:
            combined_mgmt += cap_text
            
        db_d["mgmt_alloc"] = combined_mgmt
        db_d["mgmt_alloc_score"] = db_d.get("cap_score") or db_d.get("mgmt_score") or "N/A"
        db_d["mgmt_alloc_pros"] = db_d.get("cap_pros") or db_d.get("mgmt_pros") or []
        db_d["mgmt_alloc_cons"] = db_d.get("cap_cons") or db_d.get("mgmt_cons") or []
        db_d["mgmt_alloc_rejection"] = db_d.get("cap_rejection") or db_d.get("mgmt_rejection")

    reports_h, reports_m = {}, {}
    for k in ["model", "audit", "fin", "cap", "mgmt", "moat", "verdict", "rules", "pe_forensic", "val", "thesis", "franchise", "mgmt_alloc", "technical", "thesis_destroyer"]:
        # Sub-informes de auditoría y de normas de calidad se deben procesar siempre si existen en db_d
        if k == "audit":
            for sub in ["income", "balance", "cashflow"]:
                key_sub = f"audit_{sub}"
                if key_sub in db_d:
                    reports_h[key_sub] = markdown.markdown(db_d[key_sub], extensions=['tables', 'fenced_code'])
        elif k == "rules":
            for sub in ["pilar1", "pilar2", "pilar3", "pilar4", "pilar5"]:
                key_sub = f"rules_{sub}"
                if key_sub in db_d:
                    reports_h[key_sub] = markdown.markdown(db_d[key_sub], extensions=['tables', 'fenced_code'])

        if k in db_d:
            reports_h[k] = markdown.markdown(db_d[k], extensions=['tables', 'fenced_code'])
            
            # Buscar la tarea más reciente de este tipo para ver si es legacy
            last_task = db_session.query(DBTask).filter(DBTask.ticker == ticker.upper(), DBTask.report_type == k, DBTask.status == 'success').order_by(DBTask.updated_at.desc()).first()
            is_legacy = last_task.is_legacy if last_task else False
            
            r_score = db_d.get(f"{k}_score", "N/A")
            r_pros = db_d.get(f"{k}_pros", [])
            r_cons = db_d.get(f"{k}_cons", [])

            # Auto-corrección dinámica sobre la marcha si el reporte crudo existe pero no se guardó el score
            if (r_score == "N/A" or not r_pros) and db_d.get(k):
                from utils.parsers import parse_summary
                _, parsed_score, parsed_pros, parsed_cons, _ = parse_summary(db_d[k])
                if parsed_score != "N/A" or parsed_pros:
                    r_score = parsed_score
                    r_pros = parsed_pros
                    r_cons = parsed_cons
                    # Guardarlo en base de datos para persistirlo
                    asset_repo.save_asset_data(ticker, f"{k}_score", parsed_score)
                    asset_repo.save_asset_data(ticker, f"{k}_pros", parsed_pros)
                    asset_repo.save_asset_data(ticker, f"{k}_cons", parsed_cons)

            reports_m[k] = {
                "score": r_score, 
                "pros": r_pros, 
                "cons": r_cons, 
                "rejection": db_d.get(f"{k}_rejection"),
                "is_legacy": is_legacy,
                "date": last_task.updated_at if last_task else ""
            }

            if k == "technical" and db_d.get(k):
                raw_text = db_d[k]
                traffic_lights = {}
                for row_name, key_name in [
                    ("Momentum (RSI/MACD/Bollinger)", "momentum"),
                    ("Estructura (SMA/Soportes/Semanal)", "estructura"),
                    ("Price Action (Vela/Volumen)", "price_action"),
                ]:
                    escaped_name = re.escape(row_name)
                    pattern = rf"\|\s*\*?\*?{escaped_name}\*?\*?\s*\|\s*([^|]+)\|\s*([^|]+)\|"
                    match = re.search(pattern, raw_text, re.IGNORECASE)
                    if match:
                        traffic_lights[key_name] = {
                            "classification": match.group(1).strip().strip('*`').replace('**', ''),
                            "justification": match.group(2).strip().strip('*`').replace('**', ''),
                        }

                # La fila de Puntuación Clínica tiene las columnas invertidas respecto a las demás:
                # 1ª columna = nota numérica (1-10), 2ª columna = frase de justificación.
                pc_pattern = rf"\|\s*\*?\*?{re.escape('PUNTUACIÓN \"CLÍNICA\"')}\*?\*?\s*\|\s*([^|]+)\|\s*([^|]+)\|"
                pc_match = re.search(pc_pattern, raw_text, re.IGNORECASE)
                if pc_match:
                    traffic_lights["puntuacion_clinica"] = {
                        "score": pc_match.group(1).strip().strip('*`').replace('**', ''),
                        "justification": pc_match.group(2).strip().strip('*`').replace('**', ''),
                    }
                reports_m[k]["traffic_lights"] = traffic_lights

                # Matiz del veredicto ESPERAR Y OBSERVAR: sesgo direccional y gatillo de reevaluación
                wait_nuance = {}
                bias_match = re.search(r"Sesgo[^*:\n]*:?\*{0,2}\s*\*{0,2}(ALCISTA|NEUTRAL|BAJISTA)", raw_text, re.IGNORECASE)
                if bias_match:
                    wait_nuance["bias"] = bias_match.group(1).strip().upper()
                trigger_match = re.search(r"Gatillo de Reevaluaci[oó]n[^*:\n]*:?\*{0,2}\s*(.+)", raw_text, re.IGNORECASE)
                if trigger_match:
                    trigger_line = trigger_match.group(1).strip(" *").replace('**', '')
                    dist_match = re.search(r"[-+]?\d+(?:[.,]\d+)?\s*%", trigger_line)
                    if dist_match:
                        wait_nuance["distance"] = dist_match.group(0).strip()
                        # Recorta la cláusula que introduce la distancia del texto del gatillo para no duplicarla
                        clause_match = re.search(r"Indica tambi[eé]n|Distancia Actual", trigger_line, re.IGNORECASE)
                        if clause_match:
                            trigger_line = trigger_line[:clause_match.start()].strip(" .:,")
                    wait_nuance["trigger"] = trigger_line
                if wait_nuance:
                    reports_m[k]["wait_nuance"] = wait_nuance
    
    asset_obj = db_session.query(DBAsset).filter(DBAsset.ticker == ticker.upper()).first()
    is_fav = asset_obj.is_favorite if asset_obj else False
    
    ext_stats = get_extended_stats(info, stock, db_d)

    # N NUEVA LÓGICA PARA EL CALENDARIO DE EVENTOS
    events_df = get_calendar_events(ticker)
    events_table_html = events_df.to_html(
        classes='table table-sm table-hover mb-0 table-professional',
        index=False,
        border=0,
        justify='left'
    )

    # ── CARGA DE DATOS DE PORTAFOLIO PARA LA PESTAÑA 'MI CARTERA' ──
    portfolio_item = None
    portfolio_ledger = []
    portfolio_is_active = False
    portfolio_total_profit_closed = 0.0

    try:
        from app.interfaces.views.portfolio_views import get_current_rate, GLOBAL_CACHE
        from utils.financial_math import calculate_position_cagr
        from app.infrastructure.db.models import DBTransaction

        ticker_upper = ticker.upper()
        portfolio_data = [p.model_dump() for p in portfolio_service.get_active_portfolio()]
        closed_portfolio_data = [p.model_dump() for p in portfolio_service.get_closed_portfolio()]
        ledger_data = [l.model_dump() for l in portfolio_service.get_ledger(ticker_upper)]

        item = next((p for p in portfolio_data if p['ticker'] == ticker_upper), None)
        is_active = True
        if not item:
            item = next((p for p in closed_portfolio_data if p['ticker'] == ticker_upper), None)
            is_active = False

        if item:
            portfolio_item = item
            portfolio_is_active = is_active
            portfolio_ledger = ledger_data

            current_price_val = metrics.get('currentPrice') or info.get('currentPrice') or info.get('regularMarketPrice') or item.get('average_price', 0.0)
            item['current_price'] = current_price_val

            if is_active:
                item['current_value'] = item['shares'] * current_price_val
                item['invested_value'] = item['shares'] * item['average_price']
                if item['invested_value'] > 0:
                    item['return_pct'] = ((item['current_value'] / item['invested_value']) - 1) * 100
                    item['return_abs'] = item['current_value'] - item['invested_value']
                else:
                    item['return_pct'] = 0.0
                    item['return_abs'] = 0.0

            currency_code = db_d.get("currency", item.get("currency_code", "USD")).upper()
            currency_symbol = get_currency_symbol(currency_code, ticker_upper)
            item['currency_symbol'] = currency_symbol

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

            if is_active and currency_code != 'EUR':
                total_cost_eur = item.get('total_cost_eur', 0.0)
                if total_cost_eur > 0 and item.get('total_cost', 0) > 0:
                    fx_avg_purchase = total_cost_eur / item['total_cost']
                else:
                    fx_avg_purchase = rate
                current_value_eur = item['current_value'] * rate
                invested_value_eur = total_cost_eur if total_cost_eur > 0 else item['invested_value'] * fx_avg_purchase
                total_pnl_eur = current_value_eur - invested_value_eur

                pnl_price_eur = item['shares'] * (current_price_val - item['average_price']) * fx_avg_purchase
                pnl_fx_eur = item['shares'] * current_price_val * (rate - fx_avg_purchase)

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

            ticker_trades = portfolio_service.get_realized_trades(GLOBAL_CACHE.get("rates", {}))
            ticker_trades = [t for t in ticker_trades if t['ticker'] == ticker_upper]

            manual_txs = db_session.query(DBTransaction).filter(DBTransaction.ticker == ticker_upper).all()

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
            portfolio_total_profit_closed = realized + dividends

            if is_active:
                return_pct = item.get('return_pct', 0.0)
            else:
                invested_val = item.get('total_sell_revenue', 0.0) - item.get('realized_pnl', 0.0) - item.get('total_fees', 0.0)
                if invested_val > 0:
                    return_pct = (portfolio_total_profit_closed / invested_val) * 100
                else:
                    return_pct = 0.0
            item['cagr'] = calculate_position_cagr(ticker_upper, ledger_data, return_pct, is_active=is_active)

    except Exception as e:
        print(f"Error loading portfolio data for ticker view {ticker}: {e}")

    return templates.TemplateResponse("ticker.html", {
        "request": request, 
        "ticker": ticker, 
        "info": info, 
        "currency_symbol": get_currency_symbol(info.get('currency', 'USD'), ticker), 
        "custom_metrics": metrics, 
        "financials_hist": get_financials_history(ticker, stock, db_d), 
        "dates": dates, 
        "open": op, 
        "high": hi, 
        "low": lo, 
        "close": cl, 
        "db": db_d, 
        "reports_html": reports_h, 
        "reports_meta": reports_m, 
        "is_favorite": is_fav, 
        "settings": get_settings_from_db(db_session),
        "ext_stats": ext_stats,
        "portfolio_item": portfolio_item,
        "portfolio_ledger": portfolio_ledger,
        "portfolio_is_active": portfolio_is_active,
        "portfolio_total_profit_closed": portfolio_total_profit_closed,
        "events_table_html": events_table_html,
    })



@router.get("/calendario", response_class=HTMLResponse)
def calendario_page(
    request: Request,
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    db_session: Session = Depends(get_db)
):
    settings = get_settings_from_db(db_session)
    portfolio_tickers = {p.ticker for p in portfolio_service.get_active_portfolio()}

    asset_meta = {
        a.ticker: {"is_fav": bool(a.is_favorite), "status": (a.data or {}).get("status")}
        for a in db_session.query(DBAsset).all()
    }

    today = datetime.date.today()
    raw_evs = INDEX_CACHE.get("upcoming_events", [])

    cal_events = []
    for e in raw_evs:
        t = e["ticker"]
        is_portfolio = t in portfolio_tickers
        is_fav = asset_meta.get(t, {}).get("is_fav", False)
        status = asset_meta.get(t, {}).get("status", "")

        if e["type"] == "Dividendo" and not is_portfolio:
            continue  # dividendos solo para cartera

        ev_type = e["type"]  # "Resultados" | "Dividendo"

        # Clasificación para color
        if ev_type == "Dividendo":
            category = "dividend"
        elif is_portfolio:
            category = "portfolio"
        elif is_fav:
            category = "favorite"
        elif status == "ACCEPTED":
            category = "seguimiento"
        else:
            continue  # no mostrar otros

        cal_events.append({
            "ticker": t,
            "type": ev_type,
            "category": category,
            "date": e["date"].isoformat(),
            "is_portfolio": is_portfolio,
            "is_favorite": is_fav,
            "amount": e.get("amount"),
            "pay_date": e["pay_date"].isoformat() if e.get("pay_date") else None,
        })

    return templates.TemplateResponse("calendario.html", {
        "request": request,
        "cal_events_json": cal_events,
        "today": today.isoformat(),
        "settings": settings,
    })


@router.get("/oportunidades", response_class=HTMLResponse)
def oportunidades_page(
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    db_session: Session = Depends(get_db)
):
    # Si es una recarga del navegador (F5), forzar la expiración de la caché de precios
    if is_reload_request(request):
        INDEX_CACHE["last_update"] = 0
        INDEX_CACHE["data"] = {}

    settings = get_settings_from_db(db_session)
    val_cfg = settings.get("valuation", {"ganga": 0.60, "barata": 0.85, "justo_max": 1.15, "cara_max": 1.40})

    # Tickers ya en cartera activa → no mostrar como oportunidad
    portfolio_tickers = {p.ticker for p in portfolio_service.get_active_portfolio()}

    # Solo activos ACEPTADOS y que no estén ya en cartera
    valid_assets = [
        a for a in db_session.query(DBAsset).all()
        if (a.data or {}).get("status") == "ACCEPTED"
        and a.ticker not in portfolio_tickers
    ]
    tickers_valid = [a.ticker for a in valid_assets]

    # ── Fetch precios en batch (yf.download) ──────────────────────────────────
    now = time.time()
    missing = any(t for t in tickers_valid if t not in INDEX_CACHE["data"])
    if missing or now - INDEX_CACHE.get("prices_update", 0) > PRICE_TTL:
        try:
            if tickers_valid:
                raw = yf.download(tickers_valid, period="5d", progress=False)
                try:
                    latest_raw = yf.download(tickers_valid, period="1d", progress=False)
                    if not raw.empty and not latest_raw.empty:
                        last_idx = raw.index[-1]
                        if last_idx in latest_raw.index:
                            for col in raw.columns:
                                if col in latest_raw.columns:
                                    val = latest_raw.at[last_idx, col]
                                    if not pd.isna(val):
                                        raw.at[last_idx, col] = val
                except Exception:
                    pass
                if not raw.empty and "Close" in raw:
                    closes = raw["Close"]
                    if isinstance(closes, pd.Series):
                        closes = closes.to_frame(name=tickers_valid[0])
                    for t in closes.columns:
                        try:
                            col_close = closes[t].dropna()
                            if not col_close.empty:
                                last_date = col_close.index[-1].date()
                                price = float(col_close.iloc[-1])
                                prev = float(col_close.iloc[-2]) if len(col_close) > 1 else price
                                
                                # Fallback if Yahoo historical bars are lagging or missing the previous trading day
                                p_prev = prev
                                if len(col_close) > 1:
                                    calendar_gap = (col_close.index[-1].date() - col_close.index[-2].date()).days
                                    expected_gap = 3 if last_date.weekday() == 0 else 1
                                    if calendar_gap > expected_gap:
                                        try:
                                            p_prev = float(yf.Ticker(t).fast_info["previous_close"])
                                        except Exception:
                                            pass
                                        
                                INDEX_CACHE["data"][t] = {
                                    "price": price,
                                    "prev": p_prev,
                                    "last_date": last_date,
                                }
                        except Exception:
                            continue
                    INDEX_CACHE["prices_update"] = now
        except Exception as e:
            print(f"oportunidades price fetch error: {e}")

    # ── Fetch forward PE calculado desde datos de Yahoo Finance (en paralelo) ──
    # Estrategia: calculamos nosotros precio_actual / EPS_forward_consenso (+1y).
    # Es más fiable que leer el campo forwardPE directamente porque usamos el
    # precio en tiempo real y el EPS que los analistas estiman explícitamente.
    # Fallback: forwardPE del info si no hay estimaciones de analistas.
    def _fetch_fwd_pe(ticker: str):
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            price = info.get("currentPrice") or info.get("regularMarketPrice")

            # Fuente 1: EPS forward del consenso de analistas (totalmente independiente del precio)
            forward_eps = None
            pe_calculated = None
            try:
                est = stock.earnings_estimate
                if est is not None and not est.empty:
                    for idx in ("+1y", "0y"):
                        if idx in est.index:
                            val = est.loc[idx, "avg"]
                            if val is not None and not pd.isna(val) and float(val) > 0:
                                forward_eps = float(val)
                                if price:
                                    pe_calculated = float(price) / forward_eps
                                break
            except Exception:
                pass

            # Fuente 2: forwardEps del info de Yahoo (también independiente del precio)
            if forward_eps is None:
                fe = info.get("forwardEps")
                if fe and float(fe) > 0:
                    forward_eps = float(fe)

            # forwardPE directo de yfinance
            pe_yahoo = None
            raw = info.get("forwardPE")
            if raw and float(raw) > 0:
                pe_yahoo = float(raw)

            # Media de los PE disponibles
            values = [v for v in (pe_calculated, pe_yahoo) if v is not None]
            if not values:
                return ticker, None, forward_eps, price
            return ticker, round(sum(values) / len(values), 2), forward_eps, price
        except Exception:
            return ticker, None, None, None

    live_fwd_pe: dict = {}
    live_fwd_eps: dict = {}
    live_fwd_ref_price: dict = {}
    if tickers_valid:
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
            for t, pe, eps, ref_price in ex.map(_fetch_fwd_pe, tickers_valid):
                live_fwd_pe[t] = pe
                if pe is not None:
                    asset_repo.save_asset_data(t, "last_market_fwd_pe", pe)
                    if ref_price is not None:
                        # Precio de referencia del snapshot → permite calcular implied_eps estable
                        live_fwd_ref_price[t] = ref_price
                        asset_repo.save_asset_data(t, "last_market_fwd_pe_ref_price", ref_price)
                if eps is not None:
                    live_fwd_eps[t] = eps
                    asset_repo.save_asset_data(t, "last_forward_eps", eps)

    # Umbrales de proximidad
    NEAR_THRESHOLD  = 15   # ≤15%  → cerca
    WATCH_THRESHOLD = 30   # ≤30%  → en seguimiento
    FAR_THRESHOLD   = 50   # ≤50%  → lejos de compra (>50% no se muestra)

    # Determinar si el mercado ha operado hoy (para validar el cambio diario)
    opp_today = datetime.date.today()
    session_indices = ["^GSPC", "^IXIC", "^IBEX", "^STOXX50E", "URTH"]
    dates_found = [INDEX_CACHE["data"][idx]["last_date"] for idx in session_indices if idx in INDEX_CACHE["data"]]
    opp_market_date = max(dates_found) if dates_found else None
    market_open_today = (opp_market_date == opp_today)

    in_zone, near_list, watch_list, far_list = [], [], [], []

    for asset in valid_assets:
        t = asset.ticker
        db_d = asset.data or {}
        fpe_d = db_d.get("fair_pe_data", {})
        buy_pe = fpe_d.get("fair_forward_pe")           # PE de compra marcado por el usuario
        market_pe = live_fwd_pe.get(t) or db_d.get("last_market_fwd_pe")  # PE forward en vivo (con fallback a BD)
        if not buy_pe or not market_pe:
            continue
        try:
            buy_pe_f = float(buy_pe)
            market_pe_f = float(market_pe)
            if buy_pe_f <= 0 or market_pe_f <= 0:
                continue

            cache_t = INDEX_CACHE["data"].get(t, {})
            current_price = cache_t.get("price")

            # precio_compra = PE_compra × EPS_forward (independiente del precio de mercado)
            # Prioridad: EPS directo (estable) → implied_EPS del snapshot guardado (estable)
            forward_eps = (
                live_fwd_eps.get(t)
                or db_d.get("last_forward_eps")
            )
            if not forward_eps:
                # Implied EPS desde snapshot guardado: ref_price/ref_pe son valores fijos
                ref_p = live_fwd_ref_price.get(t) or db_d.get("last_market_fwd_pe_ref_price")
                ref_pe = db_d.get("last_market_fwd_pe")
                if ref_p and ref_pe and float(ref_pe) > 0:
                    forward_eps = float(ref_p) / float(ref_pe)
            if forward_eps:
                compra_pe = round(buy_pe_f * val_cfg.get("barata", 0.85), 1)
                buy_price = round(compra_pe * float(forward_eps), 2)
                ganga_pe = round(buy_pe_f * val_cfg.get("ganga", 0.70), 1)
                ganga_price = round(ganga_pe * float(forward_eps), 2)
            else:
                compra_pe = None
                buy_price = None
                ganga_pe = None
                ganga_price = None

            # % que debe caer el precio actual para llegar al precio de compra (Barato)
            # negativo = ya está por debajo del precio de compra
            if current_price and buy_price:
                drop_needed = round((current_price - buy_price) / current_price * 100, 1)
            else:
                drop_needed = None

            prev = cache_t.get("prev", current_price)
            p_last_date = cache_t.get("last_date")
            is_weekday = opp_today.weekday() < 5
            if is_weekday and p_last_date and p_last_date != opp_today:
                prev = current_price
            ch_abs = round(current_price - prev, 2) if (current_price and prev) else 0.0
            ch_pct = round((current_price / prev - 1) * 100, 2) if (current_price and prev) else 0.0

            # Calcular estado de valoración basado en los ratios de val_cfg
            ratio = market_pe_f / buy_pe_f
            if ratio < val_cfg.get("ganga", 0.70):
                val_status = "GANGA"
            elif ratio < val_cfg.get("barata", 0.85):
                val_status = "BARATA"
            elif ratio <= val_cfg.get("justo_max", 1.15):
                val_status = "PRECIO_JUSTO"
            elif ratio <= val_cfg.get("cara_max", 1.30):
                val_status = "CARO"
            else:
                val_status = "BURBUJA"

            item = {
                "ticker": t,
                "company_name": db_d.get("company_name") or asset.company_name or t,
                "sector": db_d.get("sector", "-"),
                "current_price": current_price,
                "market_pe": round(market_pe_f, 1),
                "buy_pe": round(buy_pe_f, 1),
                "compra_pe": compra_pe,
                "ganga_pe": ganga_pe,
                "buy_price": buy_price,
                "ganga_price": ganga_price,
                "drop_needed": drop_needed,
                "change_abs": ch_abs,
                "change_pct": ch_pct,
                "valuation_status": val_status,
                "currency_symbol": get_currency_symbol(db_d.get("currency", "USD"), t),
            }

            if drop_needed is None:
                continue
            elif drop_needed <= 0:
                in_zone.append(item)
            elif drop_needed <= NEAR_THRESHOLD:
                near_list.append(item)
            elif drop_needed <= WATCH_THRESHOLD:
                watch_list.append(item)
            elif drop_needed <= FAR_THRESHOLD:
                far_list.append(item)
            # > FAR_THRESHOLD: demasiado lejos, no mostrar

        except (ValueError, TypeError):
            continue

    in_zone.sort(key=lambda x: x["drop_needed"])
    near_list.sort(key=lambda x: x["drop_needed"])
    watch_list.sort(key=lambda x: x["drop_needed"])
    far_list.sort(key=lambda x: x["drop_needed"])

    # Acciones válidas SIN PE de compra configurado (para poder configurarlas desde aquí)
    def _day_change(ticker):
        c = INDEX_CACHE["data"].get(ticker, {})
        price = c.get("price")
        prev  = c.get("prev", price)
        p_last_date = c.get("last_date")
        is_weekday = opp_today.weekday() < 5
        if not price or not prev:
            return price, None, None
        if is_weekday and p_last_date and p_last_date != opp_today:
            prev = price
        return price, round(price - prev, 2), round((price / prev - 1) * 100, 2)

    no_pe_list = sorted([
        {
            "ticker": a.ticker,
            "company_name": (a.data or {}).get("company_name") or a.company_name or a.ticker,
            "sector": (a.data or {}).get("sector", "-"),
            "market_pe": round(live_fwd_pe.get(a.ticker) or 0, 1),
            "currency_symbol": get_currency_symbol((a.data or {}).get("currency", "USD"), a.ticker),
            "current_price": _day_change(a.ticker)[0],
            "change_abs":    _day_change(a.ticker)[1],
            "change_pct":    _day_change(a.ticker)[2],
            "buy_pe": None,
            "compra_pe": None,
            "ganga_pe": None,
            "buy_price": None,
            "ganga_price": None,
            "drop_needed": None,
            "valuation_status": "N/A",
        }
        for a in valid_assets
        if not (a.data or {}).get("fair_pe_data", {}).get("fair_forward_pe")
    ], key=lambda x: x["ticker"])

    return templates.TemplateResponse("oportunidades.html", {
        "request": request,
        "in_zone": in_zone,
        "near_list": near_list,
        "watch_list": watch_list,
        "far_list": far_list,
        "no_pe_list": no_pe_list,
        "near_threshold": NEAR_THRESHOLD,
        "watch_threshold": WATCH_THRESHOLD,
        "far_threshold": FAR_THRESHOLD,
        "settings": settings,
    })


@router.get("/comparar", response_class=HTMLResponse)
def comparar_page(
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    db_session: Session = Depends(get_db),
):
    from fastapi import Query as FQuery
    raw_t = request.query_params.getlist("t")
    selected = [t.upper() for t in raw_t[:4] if t.strip()]

    comparisons = []
    for ticker in selected:
        data = asset_repo.get_asset_data(ticker)
        name = data.get("company_name") or ticker
        # Extract numeric scores
        def parse_score(s):
            try:
                return float(str(s).split("/")[0].strip())
            except Exception:
                return None
        try:
            stock = yf.Ticker(ticker)
            price = stock.fast_info.get("lastPrice") or stock.fast_info.get("last_price")
        except Exception:
            price = None
        comparisons.append({
            "ticker": ticker,
            "name": name,
            "sector": data.get("sector", "—"),
            "country": data.get("country", "—"),
            "currency": data.get("currency", "USD"),
            "status": data.get("status", "—"),
            "price": round(price, 2) if price else None,
            "fwd_pe": data.get("last_market_fwd_pe"),
            "fair_pe": (data.get("fair_pe_data") or {}).get("fair_forward_pe"),
            "fin_score": parse_score(data.get("audit_score")),
            "cap_score": parse_score(data.get("mgmt_alloc_score")),
            "moat_score": parse_score(data.get("franchise_score")),
            "verdict_score": parse_score(data.get("verdict_score")),
            "fin_pros": (data.get("audit_pros") or [])[:3],
            "fin_cons": (data.get("audit_cons") or [])[:2],
            "moat_pros": (data.get("franchise_pros") or [])[:2],
            "verdict_pros": (data.get("verdict_pros") or [])[:2],
            "verdict_cons": (data.get("verdict_cons") or [])[:2],
        })

    all_tickers = sorted([
        {"ticker": a.ticker, "name": (a.data or {}).get("company_name") or a.company_name or a.ticker}
        for a in db_session.query(DBAsset).all()
    ], key=lambda x: x["ticker"])
    
    # ── CARGA DEL HISTORIAL DE COMPARACIONES IA (ARCHIVO) ──
    import os
    import json
    
    comparisons_dir = "/home/rameneiros/wallstreet/agent/data/comparisons"
    os.makedirs(comparisons_dir, exist_ok=True)
    
    archived_comparisons = []
    for fname in os.listdir(comparisons_dir):
        if fname.endswith(".json") and fname.startswith("comparison_"):
            try:
                with open(os.path.join(comparisons_dir, fname), "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                    archived_comparisons.append({
                        "id": cdata.get("id"),
                        "timestamp": cdata.get("timestamp"),
                        "tickers": cdata.get("tickers", []),
                        "prompt": cdata.get("prompt", ""),
                        "filename": fname
                    })
            except Exception:
                pass
                
    # Sort archived comparisons by timestamp descending
    archived_comparisons.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    
    loaded_comparison = None
    archive_id = request.query_params.get("archive_id")
    if archive_id:
        for arch in archived_comparisons:
            if arch["id"] == archive_id:
                try:
                    with open(os.path.join(comparisons_dir, arch["filename"]), "r", encoding="utf-8") as f:
                        loaded_comparison = json.load(f)
                        import markdown
                        loaded_comparison["analysis_html"] = markdown.markdown(loaded_comparison.get("analysis", ""), extensions=['tables', 'fenced_code'])
                except Exception:
                    pass

    return templates.TemplateResponse("comparar.html", {
        "request": request,
        "comparisons": comparisons,
        "selected": selected,
        "all_tickers": all_tickers,
        "settings": get_settings_from_db(db_session),
        "archived_comparisons": archived_comparisons,
        "loaded_comparison": loaded_comparison,
    })


@router.post("/api/comparar/generate")
async def generate_comparison(
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    db_session: Session = Depends(get_db)
):
    import uuid
    import os
    import json
    from datetime import datetime
    from google import genai
    from google.genai import types
    
    try:
        body = await request.json()
        tickers = [t.upper() for t in body.get("tickers", []) if t.strip()]
        prompt_text = body.get("prompt", "").strip()
        
        if not tickers:
            return {"status": "error", "message": "No se han seleccionado tickers para comparar."}
            
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return {"status": "error", "message": "GEMINI_API_KEY no configurada."}
            
        # Gather full details for each ticker
        ticker_details = []
        for ticker in tickers:
            db_d = asset_repo.get_asset_data(ticker)
            
            # Format raw financial numbers as a structured text block
            fin_numbers = ""
            for statement_key, statement_name in [
                ("income_statement", "Cuenta de Resultados (P&L)"),
                ("balance_sheet", "Balance de Situación"),
                ("cash_flow", "Flujo de Caja (Cash Flow)")
            ]:
                st = db_d.get(statement_key, {})
                if st:
                    fin_numbers += f"  - {statement_name}:\n"
                    for metric, yearly_vals in sorted(st.items()):
                        vals_str = ", ".join([f"{yr}: {val}" for yr, val in sorted(yearly_vals.items())])
                        fin_numbers += f"    * {metric}: {vals_str}\n"
            
            ticker_details.append({
                "ticker": ticker,
                "name": db_d.get("company_name", ticker),
                "sector": db_d.get("sector", "—"),
                "country": db_d.get("country", "—"),
                "currency": db_d.get("currency", "USD"),
                "status": db_d.get("status", "—"),
                "fwd_pe": db_d.get("last_market_fwd_pe"),
                "financial_tables_text": fin_numbers,
                "verdict_report": db_d.get("verdict", ""),
                "thesis_report": db_d.get("thesis", ""),
            })
            
        # Construct the context block for Gemini without pre-existing scores or Fair P/E
        context = "SISTEMA DE ANÁLISIS DE CARTERA - COMPARACIÓN MULTI-ACTIVO DE CALIDAD PURA (SIN SESGO NI VALORACIÓN)\n\n"
        for td in ticker_details:
            context += f"=== DATOS E INFORMES DE: {td['ticker']} ({td['name']}) ===\n"
            context += f"Sector: {td['sector']} | País: {td['country']} | Divisa: {td['currency']}\n"
            context += f"Referencia de Mercado: Forward P/E actual: {td['fwd_pe']}x\n\n"
            
            if td['financial_tables_text']:
                context += f"--- TABLAS FINANCIERAS HISTÓRICAS (DATOS NUMÉRICOS REALES) ---\n{td['financial_tables_text']}\n\n"
                
            if td['verdict_report']:
                context += f"--- INFORME DE ANÁLISIS DE CALIDAD ---\n{td['verdict_report'][:3500]}\n\n"
                
            if td['thesis_report']:
                context += f"--- INFORME DE TESIS Y MODELO DE NEGOCIO ---\n{td['thesis_report'][:3500]}\n\n"
            context += "===================================================\n\n"
            
        system_prompt = """Eres el Director de Inversiones de un fondo de inversión de capital riesgo y de inversión en calidad empresarial (Quality/Moat Investing) ultra-exigente. 
Tu objetivo es realizar un análisis comparativo totalmente enfocado en la CALIDAD DEL NEGOCIO de las empresas proporcionadas, sin dejarte influenciar por valoraciones, precios de mercado o múltiplos (salvo como simple referencia del Forward P/E actual). 

ATENCIÓN CRÍTICA: NO debes emitir ninguna opinión sobre si las acciones están caras o baratas, ni estimar PERs Justos, ni calcular márgenes de seguridad, descuentos o primas de valoración. El análisis debe ser 100% sobre la EXCELENCIA EMPRESARIAL PURA, evaluando y comparando las ventajas de los negocios y su solidez financiera.

PAUTAS DEL INFORME COMPARATIVO:
1. Sé extremadamente analítico, riguroso, cuantitativo y crítico. Evita vaguedades y rodeos corporativos.
2. ANALIZA LAS CIFRAS REALES DE CRECIMIENTO Y RENTABILIDAD: Evalúa y compara numéricamente la evolución histórica, estabilidad y tendencias de las partidas financieras de cada empresa (Ingresos, Margen Bruto, Margen Operativo, Beneficio Neto, Free Cash Flow, etc.) que aparecen en el contexto, primando el crecimiento y la consistencia.
3. EVALÚA EL FOSO DEFENSIVO (MOAT): Determina la robustez del modelo de negocio, las barreras de entrada (foso competitivo), la estabilidad del foso y la eficiencia de la asignación de capital del management (ROIC, reinversión) a partir de los informes textuales provistos.
4. EMITE TUS PROPIAS NOTAS DE CALIDAD (SÉ CRÍTICO): Evalúa y emite tu propio criterio de puntuación de 1 a 10 para cada empresa en base a tres conceptos clave: (a) Franquicia y Moat, (b) Auditoría Financiera Forense, y (c) Dirección y Capital Allocation. Justifica tus notas en una tabla comparativa resumen dentro del informe.
5. EMITE UN VEREDICTO DE CALIDAD DEL NEGOCIO: Decreta cuál de las opciones tiene el negocio de mayor calidad absoluta para el horizonte de largo plazo (con mayor foso, solidez financiera, potencial de crecimiento y rentabilidad), justificándolo con argumentos puramente empresariales, operativos y financieros, con total independencia del precio de la acción en el mercado.
6. Estructura tu respuesta en un formato Markdown impecable, usando títulos claros, listas, negritas, y tablas comparativas de resumen para una lectura ejecutiva perfecta."""

        user_instruction = f"""Recuerda: Nuestra filosofía de inversión está orientada estrictamente al LARGO PLAZO, buscando la EXCELENCIA EN LA CALIDAD DEL NEGOCIO y el POTENCIAL DE CRECIMIENTO DE BENEFICIOS FUTUROS Y FLUJO DE CAJA (compounders de calidad).

Por favor, realiza la comparación y el veredicto basándote en el siguiente prompt o instrucción del usuario:
{prompt_text}

Aquí tienes los datos e informes contextuales de las empresas seleccionadas para analizar (evalúa minuciosamente las cifras contables reales y los argumentos de foso descritos, sin calificaciones previas):
{context}"""

        # Call Gemini
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
            contents=user_instruction,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.2
            )
        )
        
        analysis_markdown = response.text.strip()
        
        # Save comparison
        comp_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()
        
        comparison_record = {
            "id": comp_id,
            "timestamp": timestamp,
            "tickers": tickers,
            "prompt": prompt_text,
            "analysis": analysis_markdown
        }
        
        comparisons_dir = "/home/rameneiros/wallstreet/agent/data/comparisons"
        os.makedirs(comparisons_dir, exist_ok=True)
        with open(os.path.join(comparisons_dir, f"comparison_{comp_id}.json"), "w", encoding="utf-8") as f:
            json.dump(comparison_record, f, ensure_ascii=False, indent=2)
            
        return {"status": "success", "id": comp_id}
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/api/comparar/delete/{comp_id}")
def delete_comparison(comp_id: str):
    import os
    comparisons_dir = "/home/rameneiros/wallstreet/agent/data/comparisons"
    fname = f"comparison_{comp_id}.json"
    file_path = os.path.join(comparisons_dir, fname)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "error", "message": "No se encontró el informe en el archivo."}


@router.get("/api/history/{ticker}")
def get_history(ticker: str, period: str = "10y"):
    """
    Endpoint para obtener datos históricos de precios para el gráfico dinámico.
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)
        if hist.empty:
            return {"status": "error", "message": "No se encontraron datos para el período."}

        # Formatear para el gráfico
        dates = hist.index.strftime('%Y-%m-%d').tolist()
        closes = hist['Close'].tolist()

        return {"status": "success", "dates": dates, "prices": closes}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/api/database/variacion")
def api_database_variacion(period: str, db_session: Session = Depends(get_db)):
    """
    Variación de precio por periodo (1w/1m/3m/6m/ytd/3y/5y) para todos los tickers de /database.
    El 1D no pasa por aquí: sigue calculándose con INDEX_CACHE como hasta ahora.
    """
    if period not in PERIOD_START_DATES:
        return {"status": "error", "message": "Periodo no válido"}
    tickers = [row[0] for row in db_session.query(DBAsset.ticker).all()]
    data = get_period_variacion(tickers, period)
    return {"status": "success", "period": period, "data": data}


def get_calendar_events(ticker: str):
    """
    Obtiene los próximos eventos (earnings y dividendos) para un ticker
    y devuelve un DataFrame de pandas.
    """
    import pandas as pd
    from datetime import datetime

    try:
        stock = yf.Ticker(ticker)
        events = []

        # 1. Obtener calendario de earnings
        try:
            calendar = stock.calendar
            if calendar is not None and 'Earnings Date' in calendar.columns and not calendar.empty:
                earnings_date = calendar['Earnings Date'][0]
                date_str = earnings_date.strftime('%d de %B, %Y') if not isinstance(earnings_date, str) else earnings_date
                events.append({'Evento': 'Presentación de Resultados', 'Fecha': date_str, 'Detalles': 'Conference Call post-cierre'})
        except Exception:
            pass

        # 2. Obtener información de dividendos
        try:
            info = stock.info
            ex_div_date_ts = info.get('exDividendDate')
            if ex_div_date_ts:
                ex_div_date = datetime.fromtimestamp(ex_div_date_ts).date()
                if ex_div_date >= datetime.today().date():
                     events.append({
                        'Evento': 'Fecha Ex-Dividendo',
                        'Fecha': ex_div_date.strftime('%d de %B, %Y'),
                        'Detalles': f"Dividendo aprox. {info.get('lastDividendValue', 'N/A')} {get_currency_symbol(info.get('currency', 'USD'))}"
                    })
        except Exception:
            pass
        
        if not events:
            return pd.DataFrame([{'Evento': 'No hay eventos próximos disponibles.', 'Fecha': '', 'Detalles': ''}])

        df = pd.DataFrame(events)
        return df.sort_values(by='Fecha')

    except Exception:
        return pd.DataFrame([{'Evento': 'Error al cargar eventos.', 'Fecha': '', 'Detalles': ''}])
