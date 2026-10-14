"""Ficha de una empresa (/ticker/{ticker}) e histórico de precios para sus gráficos."""
import logging
import re
import datetime
import yfinance as yf
import markdown
import pandas as pd
from typing import Optional
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db
from app.infrastructure.db.models import DBAsset, DBTask, DBThesis
from app.infrastructure.dependencies import (
    get_asset_repository,
    get_portfolio_service,
    get_settings_from_db,
    get_watchlist_repository,
)
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.repositories.sqlalchemy_watchlist_repository import SqlAlchemyWatchlistRepository
from app.application.services import base_valuation_service
from app.application.services.thesis_service import fetch_ttm_fcf_per_share, quote_unit_factor
from app.application.services.portfolio_service import PortfolioService
from app.infrastructure.templates import templates
from app.interfaces.views.market_views import INDEX_CACHE, ensure_prices_cached, get_currency_symbol

from app.application.services import rules_synthesis_service, market_cap_service
from utils.financial_math import calculate_custom_metrics, calculate_cagr_metrics, get_estimated_growth, get_financials_history, get_extended_stats

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Ticker Views"])


def _ticker_market_cap(ticker, info, metrics, asset_repo):
    """Capitalización en USD y categoría para la ficha (precio de la ficha y tipos de cambio de la caché)."""
    try:
        data = asset_repo.get_asset_data(ticker) or {}
        currency = data.get("currency") or info.get("currency") or "USD"
        shares = data.get(market_cap_service.SHARES_KEY) or market_cap_service.shares_from_info(info)
        price = metrics.get("currentPrice") or info.get("currentPrice") or info.get("regularMarketPrice")
        if currency not in ("USD",) and not market_cap_service.to_usd(1.0, currency, INDEX_CACHE["data"]):
            ensure_prices_cached([ticker], asset_repo)   # trae el cruce a EUR de su moneda (y el USD)
        return market_cap_service.describe(shares, price, currency, INDEX_CACHE["data"])
    except Exception as e:
        logger.warning("Capitalización de %s: %s", ticker, e)
        return None


def _valuation_staleness(ticker, bval):
    """¿Toca revisar la valoración base? (dos presentaciones de resultados desde la última revisión)"""
    if not bval:
        return None
    from app.application.services.thesis_review_service import fetch_reported_dates
    return base_valuation_service.staleness(bval, fetch_reported_dates(ticker))


def thesis_strip(db_session: Session, ticker: str):
    """Lo esencial de la tesis para la ficha: estado, salud, cobertura, KPIs en rojo (de los pasos que puntúan), kill
    switches activados y noticias sin leer o de los últimos 30 días (la más relevante primero). None si no tiene tesis."""
    from app.application.services.thesis_service import evaluate_thesis
    from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository
    row = db_session.query(DBThesis.id).filter(DBThesis.ticker == ticker.upper()).first()
    if not row:
        return None
    repo = SqlAlchemyThesisRepository(db_session)
    thesis = repo.get_thesis(row.id)
    ev = evaluate_thesis(thesis)
    reds = [k["name"] for p in ev["pillars"] if p.get("weight", 1.0) != 0 for k in p["kpis"] if k["eval"]["status"] == "red"]
    since = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    news = [n for n in repo.list_news(row.id, limit=50) if not n["seen"] or (n["date"] or "")[:10] >= since]
    news.sort(key=lambda n: n["date"] or "", reverse=True)
    news.sort(key=lambda n: n["materiality"] or 0, reverse=True)   # la más relevante primero; a igualdad, la más reciente
    return {"id": row.id, "verdict": thesis.get("verdict"), "health": ev["health_score"], "health_status": ev["health_status"],
            "coverage": ev["coverage"], "reds": reds, "killed": [k["name"] for k in ev["kill_triggered"]],
            "news": news, "unread": sum(1 for n in news if not n["seen"])}


@router.get("/ticker/{ticker}", response_class=HTMLResponse)
def ticker_page(request: Request, ticker: str, asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository), portfolio_service: PortfolioService = Depends(get_portfolio_service), db_session: Session = Depends(get_db), watchlist_repo: SqlAlchemyWatchlistRepository = Depends(get_watchlist_repository)):
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
    # acciones en circulación: con el precio y el cambio dan la capitalización en USD (aquí y en la BBDD)
    if market_cap_service.needs_refresh(db_d):
        market_cap_service.save_shares(asset_repo.save_asset_data, ticker, market_cap_service.shares_from_info(info))
            
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

    events = upcoming_events(ticker.upper(), info)

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

    # Informes que se están generando ahora: al recargar, la ficha los sigue esperando en vez de ofrecer «Generar ahora»
    from app.interfaces.api.analysis_api import running_tasks
    running = running_tasks(db_session, ticker)

    return templates.TemplateResponse("ticker.html", {
        "request": request, 
        "ticker": ticker, 
        "running_tasks": running,
        "info": info, 
        "currency_symbol": get_currency_symbol(info.get('currency', 'USD'), ticker), 
        "custom_metrics": metrics, 
        "financials_hist": get_financials_history(ticker, stock, db_d), 
        "rules_syn": rules_synthesis_service.status(db_d),
        "mcap": _ticker_market_cap(ticker, info, metrics, asset_repo),
        "dates": dates, 
        "open": op, 
        "high": hi, 
        "low": lo, 
        "close": cl, 
        "db": db_d, "profile": investment_profile(db_d.get("verdict")), 
        "reports_html": reports_h, 
        "reports_meta": reports_m, 
        "is_favorite": is_fav, 
        "settings": get_settings_from_db(db_session),
        "ext_stats": ext_stats,
        "portfolio_item": portfolio_item,
        "portfolio_ledger": portfolio_ledger,
        "portfolio_is_active": portfolio_is_active,
        "portfolio_total_profit_closed": portfolio_total_profit_closed,
        "events": events,
        # Valoración base (un solo escenario, BPA o FCF/acción)
        "bval": base_valuation_service.stored(db_d),
        "bval_stale": _valuation_staleness(ticker, base_valuation_service.stored(db_d)),
        # BPA y dividendo de Yahoo van en la divisa principal; en Londres el precio va en peniques
        "unit_factor": quote_unit_factor(info.get('currency')),
        "bval_ttm": {"eps": (info.get('trailingEps') or 0) * quote_unit_factor(info.get('currency')) or None,
                     "fcf": fetch_ttm_fcf_per_share(ticker)},
        # cierre del último trimestre publicado: la fecha del TTM (el año en curso suma lo que le falta hasta el 31/12)
        "bval_ttm_end": datetime.datetime.fromtimestamp(info['mostRecentQuarter'], datetime.timezone.utc).date().isoformat() if info.get('mostRecentQuarter') else None,
        "bval_estimates": base_valuation_service.analyst_eps_estimates(ticker),
        "bval_target": base_valuation_service.TARGET_RETURN_PCT,
        "bval_years": base_valuation_service.DEFAULT_YEARS,
        "bval_metrics": base_valuation_service.METRICS,
        # pestaña «Tesis» y franja del Resumen ejecutivo: su tesis de inversión, si la tiene
        "thesis_link": thesis_strip(db_session, ticker),
        "in_watchlist": watchlist_repo.get_by_ticker(ticker) is not None,   # añadida a mano
        "watch_auto": ["Tier 1"] if is_fav else (["Tier 2"] if str(db_d.get("watch_tier") or "") == "2" else []),
        "tier": tier_level(is_fav, db_d), "muted": bool(db_d.get("muted")),
        # previsiones de los analistas (BPA a 90 días), de la caché: el indicador del Resumen
        "rev": _estimate_revisions(ticker),
    })


def _estimate_revisions(ticker: str):
    from app.application.services import estimate_revisions_service as est
    rev = est.revisions(ticker, fetch=False)
    return {**rev, "label": est.PERIOD_LABEL.get(rev.get("period")), "cut": bool(est.signal(rev))} if rev else None



def tier_level(is_fav: bool, data: dict) -> dict:
    """Nivel de la empresa para el selector de la ficha."""
    from app.application.services import tier_service
    t = tier_service.tier_of(is_fav, data)
    return {"value": t or "", "levels": tier_service.LEVELS}


def _price_history(ticker: str) -> list:
    """Cierres diarios de 10 años, ajustados por splits pero no por dividendos (comparables con tus precios de compra).
    [[fecha ISO, cierre]]; caché en disco de 12 h."""
    from app.application.services import market_cache

    def download():
        try:
            close = yf.Ticker(ticker).history(period="10y", auto_adjust=False)["Close"].dropna()
            return [[d.date().isoformat(), round(float(v), 4)] for d, v in close.items()]
        except Exception:
            return []
    return market_cache.cached("price_history_10y_v1", ticker, download, ttl_hours=12) or []


@router.get("/api/ticker/{ticker}/precio-valoracion")
def price_valuation_api(ticker: str, db_session: Session = Depends(get_db),
                        portfolio_service: PortfolioService = Depends(get_portfolio_service)):
    """«Precio y valoración» del Resumen de la ficha: el histórico de precios, el precio de compra de la valoración base
    (con su zona y CAGR estimado) y tus compras y ventas de la empresa (ajustadas por splits) para marcarlas."""
    from app.application.services import price_monitor_service as pm
    from app.application.services.decision_review_service import _split_factor
    t = ticker.upper()
    history = _price_history(t)
    asset = db_session.query(DBAsset).filter(DBAsset.ticker == t).first()
    data = (asset.data if asset else None) or {}
    cached = INDEX_CACHE["data"].get(t) or {}
    price = cached.get("price") or (history[-1][1] if history else None)
    val = base_valuation_service.live(t, data, price) if price else None
    txs, splits = pm.load_transactions(portfolio_service, db_session)
    trades = [{"date": x["date"][:10], "type": x["type"], "shares": x["shares"],
               "price": x["price"] / _split_factor(x["date"], splits.get(t, []))} for x in txs if x["ticker"] == t]
    # tu precio medio de compra (si la tienes en cartera); se descarta si no cuadra con los precios (split sin aplicar)
    held = next((i for i in portfolio_service.get_active_portfolio() if i.ticker == t and i.shares > 0), None)
    avg = held.average_price if held and held.average_price else None
    if avg and price and not (0.2 < avg / price < 5):
        avg = None
    return {"ticker": t, "currency": get_currency_symbol(data.get("currency", "USD"), t), "price": price,
            "history": history, "trades": trades, "avg_cost": avg,
            "valuation": {k: val.get(k) for k in ("buy_price", "cagr", "margin_pct", "zone", "target", "metric", "years")} if val else None}


def _insiders(ticker: str) -> dict:
    from app.application.services import insider_service
    return insider_service.summary(ticker.upper())


@router.get("/ticker/{ticker}/directivos", response_class=HTMLResponse)
def insiders_fragment(request: Request, ticker: str, db_session: Session = Depends(get_db)):
    """«Directivos y acciones» del Resumen ejecutivo de la ficha (se pide aparte: Yahoo puede tardar)."""
    from app.application.services import insider_service
    s = _insiders(ticker)
    # acciones en circulación: mejor de las cuentas anuales de la ficha (oficiales, 10 años); si no, la serie de Yahoo
    s["shares_source"] = "Yahoo Finance (mensual, ajustado por splits)"
    asset = db_session.query(DBAsset).filter(DBAsset.ticker == ticker.upper()).first()
    if asset:
        try:
            fin = insider_service.shares_from_financials(get_financials_history(ticker.upper(), yf.Ticker(ticker.upper()), asset.data or {}))
        except Exception:
            fin = None
        if fin:
            s.update(shares=fin["shares"], windows=fin["windows"], series=fin["series"], shares_source=fin["source"])
    # serie de acciones en circulación como línea SVG (sin librerías): puntos en un lienzo de 300 × 80 (se estira)
    pts, vals = "", [v for _, v in s["series"]]
    if len(vals) > 1:
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1
        pts = " ".join(f"{300 * i / (len(vals) - 1):.1f},{76 - 72 * (v - lo) / span:.1f}" for i, v in enumerate(vals))
    from app.application.services import estimate_revisions_service as est
    rev = est.revisions(ticker.upper())
    return templates.TemplateResponse("_insiders.html", {"request": request, "ticker": ticker.upper(), **s, "spark": pts,
                                                         "signal_days": insider_service.SIGNAL_DAYS, "rev": rev,
                                                         "rev_period": est.PERIOD_LABEL.get((rev or {}).get("period")),
                                                         "rev_cut": est.signal(rev)})


@router.get("/api/history/{ticker}")
def get_history(ticker: str, period: str = "10y"):
    """
    Endpoint para obtener datos históricos de precios para el gráfico dinámico.
    1D y 5D, intradía (cada 5 y 30 minutos); el resto, diario.
    """
    try:
        stock = yf.Ticker(ticker)
        interval = {"1d": "5m", "5d": "30m"}.get(period, "1d")
        hist = stock.history(period=period, interval=interval)
        if hist.empty:
            return {"status": "error", "message": "No se encontraron datos para el período."}

        # Formatear para el gráfico
        dates = hist.index.strftime('%Y-%m-%d %H:%M' if interval != "1d" else '%Y-%m-%d').tolist()
        closes = hist['Close'].tolist()

        return {"status": "success", "dates": dates, "prices": closes}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# Perfil de inversión: el arquetipo del veredicto de la IA, con un nombre sobrio (sin emoji ni jerga) y su justificación
PROFILE_NAMES = [   # (palabra clave en el arquetipo, nombre); en orden: «COMPOUNDER "OUTSIDER"» antes que «COMPOUNDER»
    ("MONOPOLIO", "Franquicia dominante"), ("OUTSIDER", "Compounder con gestión excepcional"),
    ("CLÁSICO", "Compounder"), ("CLASICO", "Compounder"), ("DORMIDO", "Franquicia infraexplotada"),
    ("MALGESTIONADA", "Franquicia mal gestionada"), ("LECHERA", "Generadora de caja madura"),
    ("TURNAROUND", "En reestructuración"), ("TRAMPA", "Trampa de valor"), ("DESTRUCTOR", "Destructora de capital"),
]


def investment_profile(verdict: Optional[str]) -> Optional[dict]:
    """{name, why} a partir del informe de veredicto («Arquetipo de Inversión: …» y «Justificación del Arquetipo: …»)."""
    if not verdict:
        return None
    m = re.search(r"Arquetipo de Inversi[oó]n[^:\n]*:\**\s*(.+)", verdict)
    line = (m.group(1) if m else verdict).upper()
    name = next((n for key, n in PROFILE_NAMES if key in line), None)
    if not name:
        return None
    w = re.search(r"Justificaci[oó]n del Arquetipo[^:\n]*:\**\s*(.+)", verdict)
    why = re.sub(r"[*_`]+", "", w.group(1)).strip() if w else None
    return {"name": name, "why": why}


def upcoming_events(ticker: str, info: dict) -> list:
    """Próximos resultados (la misma fecha que la agenda, cacheada) y fecha ex-dividendo, para la franja del precio.
    [{label, date (ISO), detail}], el más cercano primero."""
    from app.application.services.thesis_review_service import _earnings_info
    today = datetime.date.today()
    events = []
    try:
        nxt = (_earnings_info(ticker) or {}).get("next")
        if nxt and str(nxt)[:10] >= today.isoformat():
            events.append({"label": "Resultados", "date": str(nxt)[:10], "detail": "Presentación de resultados"})
    except Exception:
        pass
    ts = info.get("exDividendDate")
    if ts:
        try:
            ex = datetime.datetime.fromtimestamp(ts).date()
            if ex >= today:
                amount = info.get("lastDividendValue")
                events.append({"label": "Ex-dividendo", "date": ex.isoformat(),
                               "detail": f"Último dividendo {amount} {get_currency_symbol(info.get('currency', 'USD'))}" if amount else "Fecha ex-dividendo"})
        except (OSError, ValueError, OverflowError):
            pass
    for e in events:
        e["days"] = (datetime.date.fromisoformat(e["date"]) - today).days
    return sorted(events, key=lambda e: e["date"])
