import logging
import time
import datetime
import concurrent.futures
import threading
import yfinance as yf
import pandas as pd
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db
from app.infrastructure.db.models import DBAsset, DBTransaction
from app.domain.models import TransactionType
from app.infrastructure.dependencies import (
    get_asset_repository,
    get_portfolio_service,
    get_performance_service,
    get_settings_from_db,
)
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.application.services import market_cache
from app.application.services.portfolio_service import PortfolioService
from app.application.services.performance_service import PerformanceService
from app.infrastructure.templates import templates

from app.application.services.alerts_service import scan_in_background

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Market Views"])

# Cache global simple para la página de inicio
# prices_update: timestamp del último fetch de precios (TTL corto)
# last_update: timestamp del último fetch de eventos (TTL largo)
INDEX_CACHE = {"data": {}, "last_update": 0, "prices_update": 0, "upcoming_events": []}

PRICE_TTL = 120   # precios: 2 minutos
EVENT_TTL = 600   # eventos: 10 minutos

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

def ensure_prices_cached(tickers: list, asset_repo, wait: bool = False) -> None:
    """Asegura que los tickers provistos tengan su precio y variación en INDEX_CACHE.
    Si faltan, se descargan (y se espera); si solo están viejos, se muestran y se actualizan en segundo plano.
    wait=True (F5): se descargan ya y se espera, para que la página salga con los nuevos sin recargarse después."""
    tickers = list(set([t for t in tickers if t and not t.startswith('^')]))
    if not tickers:
        return
    if not INDEX_CACHE["data"]:
        _load_price_snapshot()   # tras un reinicio: lo último guardado, mientras se actualiza
    currencies = set()
    for t in tickers:
        try:
            currencies.add(asset_repo.get_asset_data(t).get("currency", "USD"))
        except Exception:
            currencies.add("USD")
    symbols = tickers + [f"{c}EUR=X" for c in currencies if c != "EUR"]
    missing = [t for t in symbols if t not in INDEX_CACHE["data"] and t not in INDEX_CACHE.get("unavailable", ())]
    try:
        if missing or wait:
            refresh_index_prices(symbols, [])
        elif time.time() - INDEX_CACHE.get("prices_update", 0) > 300:
            start_price_refresh(symbols, [])
    except Exception as e:
        print(f"Error fetching prices in ensure_prices_cached: {e}")


_price_refresh_lock = threading.Lock()


def _load_price_snapshot() -> None:
    snap = market_cache.load_snapshot()
    if not snap:
        return
    INDEX_CACHE["data"].update({t: {**d, "last_date": datetime.date.fromisoformat(d["last_date"]) if d.get("last_date") else None}
                                for t, d in snap.get("data", {}).items() if t not in INDEX_CACHE["data"]})
    INDEX_CACHE["prices_update"] = snap.get("updated", 0)


def refresh_index_prices(symbols: list, alert_tickers: list) -> None:
    """Precio y cierre anterior de cada valor (una sola descarga de 5 sesiones) → INDEX_CACHE y foto en disco."""
    data = yf.download(sorted(set(symbols)), period="5d", progress=False)
    if data.empty or "Close" not in data:
        return
    closes = data["Close"]
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(symbols[0])
    # cierre de hoy vacío en Yahoo para bolsas que ya han cotizado hoy (p. ej. Tokio): se completa con el intradía
    from app.application.services.session_fill import fill_today
    closes = fill_today(closes)
    fresh = {}
    for t in closes.columns:
        col_close = closes[t].dropna()
        if col_close.empty:
            continue
        last_date = col_close.index[-1].date()
        price = float(col_close.iloc[-1])
        prev = float(col_close.iloc[-2]) if len(col_close) > 1 else price
        if len(col_close) > 1:
            # a veces Yahoo no trae la sesión anterior: si hay un hueco raro, cierre anterior de fast_info
            gap = (last_date - col_close.index[-2].date()).days
            if gap > (3 if last_date.weekday() == 0 else 1):
                try:
                    prev = float(yf.Ticker(t).fast_info["previous_close"])
                except Exception:
                    pass
        fresh[t] = {"price": price, "prev": prev, "last_date": last_date}
    INDEX_CACHE["data"].update(fresh)
    INDEX_CACHE["unavailable"] = (set(INDEX_CACHE.get("unavailable", ())) - set(fresh)) | (set(symbols) - set(fresh))
    INDEX_CACHE["prices_update"] = time.time()
    market_cache.save_snapshot({"updated": INDEX_CACHE["prices_update"], "data": {
        t: {**d, "last_date": d["last_date"].isoformat() if d.get("last_date") else None} for t, d in INDEX_CACHE["data"].items()}})
    # alertas con los precios recién descargados (en segundo plano: descarga histórico)
    if alert_tickers:
        scan_in_background({t: INDEX_CACHE["data"][t]["price"] for t in alert_tickers if t in INDEX_CACHE["data"]})


MARKET_INDICES = {"^GSPC": "S&P 500", "^IXIC": "NASDAQ", "URTH": "MSCI World", "^IBEX": "IBEX 35", "^STOXX50E": "Eurostoxx 50", "BTC-USD": "Bitcoin"}


def price_symbols(asset_repo, portfolio_tickers: list) -> tuple:
    """Todo lo que la app cotiza (valores conocidos + cartera, índices y FX a EUR) y, aparte, los valores (para alertas)."""
    involved = list(set(asset_repo.get_all_tickers() + list(portfolio_tickers)))
    currencies = {asset_repo.get_asset_data(t).get("currency", "USD") for t in involved}
    return involved + list(MARKET_INDICES) + [f"{c}EUR=X" for c in currencies if c != "EUR"], involved


def refresh_prices_locked(symbols: list, alert_tickers: list) -> None:
    """Descarga ya (bloqueando), salvo que otra descarga esté en curso: para el actualizador programado."""
    if not _price_refresh_lock.acquire(blocking=False):
        return
    INDEX_CACHE["refreshing"] = True
    try:
        refresh_index_prices(symbols, alert_tickers)
    finally:
        INDEX_CACHE["refreshing"] = False
        _price_refresh_lock.release()


def start_price_refresh(symbols: list, alert_tickers: list) -> None:
    if not _price_refresh_lock.acquire(blocking=False):
        return
    INDEX_CACHE["refreshing"] = True

    def run():
        try:
            refresh_index_prices(symbols, alert_tickers)
        except Exception as e:
            logging.warning("Mercado: fallo al actualizar precios: %s", e)
        finally:
            INDEX_CACHE["refreshing"] = False
            _price_refresh_lock.release()
    threading.Thread(target=run, daemon=True, name="price-refresh").start()


def event_raw(ticker: str) -> dict:
    """Fechas de dividendo y resultados de yfinance, en la caché en disco (12 h): son lentas de pedir."""
    def download():
        stock = yf.Ticker(ticker)
        info, cal = stock.info or {}, stock.calendar
        to_iso = lambda v: (v.date() if hasattr(v, "date") else v).isoformat() if v else None
        out = {"ex_div": None, "div_amount": None, "pay_date": None, "earnings": None}
        dts = info.get("exDividendDate") or info.get("dividendExDate")
        if dts:
            out["ex_div"] = datetime.date.fromtimestamp(dts).isoformat()
            out["div_amount"] = info.get("lastDividendValue") or ((info.get("dividendRate") or 0) / 4.0)
            pay_ts = info.get("dividendDate") or info.get("dividendPaymentDate")
            if pay_ts:
                out["pay_date"] = datetime.date.fromtimestamp(pay_ts).isoformat()
            elif isinstance(cal, dict) and cal.get("Dividend Date"):
                out["pay_date"] = to_iso(cal.get("Dividend Date"))
            elif isinstance(cal, pd.DataFrame) and "Dividend Date" in cal.index:
                out["pay_date"] = to_iso(cal.loc["Dividend Date"].iloc[0])
        ed = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
        elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
            ed = cal.loc["Earnings Date"].iloc[0]
        if isinstance(ed, list):
            ed = ed[0] if ed else None
        out["earnings"] = to_iso(ed)
        return out
    try:
        return market_cache.cached("event_raw", ticker, download)
    except Exception:
        return {}


def prices_banner_context() -> dict:
    """Variables de _prices_banner.html."""
    updated = INDEX_CACHE.get("prices_update", 0)
    return {"prices_updated": updated, "prices_refreshing": bool(INDEX_CACHE.get("refreshing")), "now_ts": time.time(),
            "prices_time": datetime.datetime.fromtimestamp(updated or time.time()).strftime("%H:%M")}


@router.get("/api/mercado/prices-status")
def prices_status():
    return {"updated": INDEX_CACHE.get("prices_update", 0), "refreshing": bool(INDEX_CACHE.get("refreshing"))}


@router.get("/")
def home():
    # La portada es Mercado (índices, actividad del día, eventos)
    return RedirectResponse("/mercado")


def refresh_upcoming_events(db_session: Session, active_portfolio) -> None:
    """Próximos resultados y dividendos de la cartera, las favoritas y las de seguimiento → INDEX_CACHE["upcoming_events"]
    (lo usan el calendario y Mercado). Lento: refresca cada EVENT_TTL segundos."""
    now = time.time()
    if now - INDEX_CACHE["last_update"] <= EVENT_TTL:
        return
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
                raw = event_raw(ticker)
                today = datetime.date.today()
                as_date = lambda v: datetime.date.fromisoformat(v) if v else None
                d = as_date(raw.get("ex_div"))
                if d:
                    pay_date_obj = as_date(raw.get("pay_date"))
                    # "Próximo a cobrar": mientras el pago no se haya realizado, sigue siendo
                    # relevante aunque la fecha ex-dividendo ya haya pasado (fallback a ex-div
                    # si no conocemos la fecha de pago).
                    pending = (pay_date_obj >= today) if pay_date_obj else (d >= today)

                    # Acciones que teníamos en la fecha ex-dividendo (no las actuales):
                    # si compramos después del ex-div, ese dividendo concreto no nos corresponde.
                    shares_at_exdiv = shares_as_of(ticker, d) if item["is_portfolio"] else 0

                    if pending and (not item["is_portfolio"] or shares_at_exdiv > 0.0001):
                        evs.append({"ticker": ticker, "type": "Dividendo", "date": d, "pay_date": pay_date_obj, "amount": (raw.get("div_amount") or 0) * shares_at_exdiv, "is_portfolio": item["is_portfolio"], "is_favorite": item["is_favorite"]})

                ed = as_date(raw.get("earnings"))
                if ed and ed >= today:
                    evs.append({"ticker": ticker, "type": "Resultados", "date": ed, "is_portfolio": item["is_portfolio"], "is_favorite": item["is_favorite"]})
                return evs
            except Exception:
                return []

        print(f"[events] fetching for {len(event_items)} items: {[x['ticker'] for x in event_items]}")
        all_evs = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
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


@router.get("/mercado", response_class=HTMLResponse)
def index(
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    performance_service: PerformanceService = Depends(get_performance_service),
    db_session: Session = Depends(get_db)
):
    settings = get_settings_from_db(db_session)
    active_portfolio = portfolio_service.get_active_portfolio()
    indices_to_fetch = MARKET_INDICES

    now = time.time()

    # Forzar refresco si los eventos cacheados son del formato antiguo (sin is_portfolio)
    cached_evs = INDEX_CACHE.get("upcoming_events", [])
    if cached_evs and "is_portfolio" not in cached_evs[0]:
        INDEX_CACHE["last_update"] = 0

    # ── PRECIOS: se muestra al instante lo último que hay y se actualiza en segundo plano ──
    symbols, all_involved = price_symbols(asset_repo, [p.ticker for p in active_portfolio])
    if not INDEX_CACHE["data"]:
        _load_price_snapshot()
    # los que Yahoo no devolvió en la última descarga no fuerzan otra (si no, se actualizaría en cada visita)
    missing = [t for t in symbols if t not in INDEX_CACHE["data"] and t not in INDEX_CACHE.get("unavailable", ())]
    age = now - INDEX_CACHE.get("prices_update", 0)
    if not INDEX_CACHE["data"] or len(missing) > len(symbols) / 2 or (is_reload_request(request) and age > 30):
        refresh_index_prices(symbols, all_involved)        # primera vez o F5: se espera (~6 s) y sale ya con los nuevos
    elif age > PRICE_TTL or missing:
        start_price_refresh(symbols, all_involved)         # precios viejos: se actualizan sin esperar (aviso para recargar)

    refresh_upcoming_events(db_session, active_portfolio)

    # 3. Portfolio Performance
    today = datetime.date.today()
    session_indices = ["^GSPC", "^IXIC", "^IBEX", "^STOXX50E", "URTH"]
    dates_found = [INDEX_CACHE["data"][idx]["last_date"] for idx in session_indices if idx in INDEX_CACHE["data"]]
    latest_market_date = max(dates_found) if dates_found else today

    # la misma cuenta que Cartera (day_change): en € incluye lo que se ha movido la divisa desde ayer; antes se usaba el
    # cambio de hoy para los dos precios y la cifra no coincidía con la variación 1D de Cartera
    from app.interfaces.views.portfolio_views import day_change
    port_now, port_prev = 0, 0
    is_weekday = today.weekday() < 5
    for item in active_portfolio:
        t = item.ticker
        if t in INDEX_CACHE["data"]:
            d = INDEX_CACHE["data"][t]
            day = day_change(item.shares, d["price"], d["prev"], d.get("last_date"), asset_repo.get_asset_data(t).get("currency", "USD"), today)
            v_now = item.shares * d["price"] * day["rate"]
            port_now += v_now; port_prev += v_now - day["eur"]

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

    # 4. Movers y mapa de calor
    all_daily_movers, fav_movers, heatmap_data = [], [], []
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
            

    # Sort ALL valid daily movers by PCT Descending
    sorted_movers = sorted(all_daily_movers, key=lambda x: x["pct"], reverse=True)
    top_gainers = [m for m in sorted_movers if m["pct"] >= 0]
    top_losers = sorted([m for m in sorted_movers if m["pct"] < 0], key=lambda x: x["pct"])
    
    # Portfolio Specific Movers
    sorted_port_movers = sorted(port_daily_movers, key=lambda x: x["pct"], reverse=True)
    
    fav_movers = sorted(fav_movers, key=lambda x: x["pct"])
    port_fav_movers = sorted(port_fav_movers, key=lambda x: x["pct"])

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

    return templates.TemplateResponse("index.html", {
        "request": request,
        "top_gainers": top_gainers, "top_losers": top_losers, "fav_movers": fav_movers,
        "custom_lists": custom_lists_movers,
        "port_all": sorted_port_movers,
        "market_indices": market_indices,
        "heatmap_data": heatmap_data,
        "latest_market_date": latest_market_date,   # «sesión» en la tarjeta Mi Cartera (antes salía vacío)
        "settings": settings,
        **prices_banner_context(),
    })

@router.get("/oportunidades")
def oportunidades_page():
    # Oportunidades se unificó con la Watchlist (valoración base, favoritas y añadidas a mano)
    return RedirectResponse("/watchlist")
