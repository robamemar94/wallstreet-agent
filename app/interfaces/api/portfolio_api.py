import os
import datetime
from typing import Optional
import yfinance as yf
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db
from app.infrastructure.db.models import DBSetting
from app.application.services.portfolio_service import PortfolioService
from app.domain.models import Transaction, TransactionType
from app.infrastructure.dependencies import get_portfolio_service, get_asset_repository, get_settings_from_db
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.interfaces.views.settings_views import save_settings_to_db

PERF_CACHE_FILE = "data/performance_cache.json"

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])

class TransactionItem(BaseModel):
    date: str
    type: str  # 'BUY' or 'SELL'
    ticker: str
    shares: float
    price: float
    currency: str = 'USD'
    fee: float = 0.0
    fx_rate_at_purchase: Optional[float] = None  # si None, se auto-fetcha
    broker: str = 'IBKR'


def _fetch_fx_rate(currency: str, date_str: str) -> Optional[float]:
    """Obtiene el tipo EUR/moneda para la fecha dada. Retorna EUR por unidad de divisa."""
    if currency.upper() == 'EUR':
        return 1.0
    try:
        import datetime
        d = datetime.date.fromisoformat(date_str)
        end = d + datetime.timedelta(days=5)
        ticker_sym = f"{currency.upper()}EUR=X"
        hist = yf.Ticker(ticker_sym).history(start=d.isoformat(), end=end.isoformat())
        if not hist.empty:
            return float(hist['Close'].iloc[0])
        # Fallback: tipo actual
        info = yf.Ticker(ticker_sym).info
        return float(info.get('regularMarketPrice') or info.get('previousClose') or 1.0)
    except Exception:
        return None

class DeleteTransactionRequest(BaseModel):
    date: str
    type: str
    shares: float
    price: float

class EditTransactionRequest(BaseModel):
    ticker: str
    date: str
    type: str
    shares: float
    price: float
    new_broker: str

class IdealPortfolioData(BaseModel):
    groups: list

@router.get("/ideal")
async def get_ideal_portfolio(
    db: Session = Depends(get_db),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    portfolio_service: PortfolioService = Depends(get_portfolio_service)
):
    setting = db.query(DBSetting).filter(DBSetting.key == "ideal_portfolio").first()
    
    # Cargar tickers de la cartera real
    try:
        active_items = portfolio_service.get_active_portfolio()
        active_tickers = {item.ticker.upper() for item in active_items if item.shares > 0}
    except Exception:
        active_tickers = set()

    # Cargar datos guardados o estructura por defecto
    if setting:
        portfolio_data = setting.value
    else:
        portfolio_data = {
            "groups": [
                {
                    "id": "gr_core_def",
                    "name": "CORE & DEFENSA",
                    "categories": [
                        {
                            "id": "cat_cons_def",
                            "name": "Consumo Defensivo",
                            "role_description": "Aportar dividendos crecientes y estabilidad patrimonial en cualquier fase del ciclo.",
                            "ideal_ticker": "KO",
                            "candidates": ["KO", "PEP"]
                        },
                        {
                            "id": "cat_infra",
                            "name": "Infraestructura & Concesiones",
                            "role_description": "Monopolios físicos con flujos ultra-predecibles ligados a la inflación.",
                            "ideal_ticker": "OMAB",
                            "candidates": ["OMAB", "PAC"]
                        }
                    ]
                },
                {
                    "id": "gr_growth_dis",
                    "name": "CRECIMIENTO & MOTORES",
                    "categories": [
                        {
                            "id": "cat_tech_sec",
                            "name": "Tecnología Secular",
                            "role_description": "Empresas líderes que capitalizan megatendencias (SaaS, Nube, Inteligencia Artificial).",
                            "ideal_ticker": "MSFT",
                            "candidates": ["MSFT", "GOOG", "AMZN"]
                        }
                    ]
                }
            ]
        }

    # Cargar todos los activos de la cartera real para sacar pesos reales
    total_portfolio_value = 0.0
    active_portfolio_values = {}
    
    try:
        active_items = portfolio_service.get_active_portfolio()
        for item in active_items:
            ticker_upper = item.ticker.upper()
            shares = float(item.shares)
            if shares > 0:
                # Obtener precio actual de la base de datos de activos
                asset_data = asset_repo.get_asset_data(ticker_upper)
                current_price = None
                if asset_data:
                    m_data = asset_data.get("market_data") or {}
                    current_price = m_data.get("current_price") or asset_data.get("last_market_price")
                
                if not current_price:
                    current_price = item.average_price
                    
                try:
                    current_price = float(current_price)
                except Exception:
                    current_price = float(item.average_price or 0.0)
                    
                item_value = shares * current_price
                active_portfolio_values[ticker_upper] = item_value
                total_portfolio_value += item_value
    except Exception as e:
        logger.warning(f"No se pudieron calcular los valores de la cartera real: {e}")

    # Enriquecer candidatos con métricas en tiempo real de la base de datos
    for group in portfolio_data.get("groups", []):
        # Calcular posiciones totales por grupo
        group_total_pos = 0
        group_real_value = 0.0
        group_tickers = set()
        
        for category in group.get("categories", []):
            # Sumar posiciones al total del grupo
            cat_positions = int(category.get("positions") or 1)
            group_total_pos += cat_positions
            
            # Recolectar todos los candidatos y estrellas de este grupo para calcular peso
            for t in category.get("candidates", []):
                group_tickers.add(t.upper())
            for t in category.get("ideal_tickers", []):
                group_tickers.add(t.upper())
            
            # 1. Enriquecer todos los candidatos alternativos/alternativas
            enriched_candidates = []
            candidates_list = category.get("candidates", [])
            for ticker in candidates_list:
                ticker_upper = ticker.upper()
                asset_data = asset_repo.get_asset_data(ticker_upper)
                
                name = asset_data.get("company_name", ticker_upper) if asset_data else ticker_upper
                fpe_d = asset_data.get("fair_pe_data", {}) if asset_data else {}
                
                fair_pe = fpe_d.get("fair_forward_pe")
                current_pe = fpe_d.get("current_forward_pe") or (asset_data.get("last_market_fwd_pe") if asset_data else None)
                
                # Conversión segura a float
                try:
                    fair_pe = float(fair_pe) if fair_pe is not None else None
                except Exception:
                    fair_pe = None
                    
                try:
                    current_pe = float(current_pe) if current_pe is not None else None
                except Exception:
                    current_pe = None
                    
                diff_pct = None
                if fair_pe and current_pe:
                    diff_pct = round(((current_pe / fair_pe) - 1) * 100, 1)

                enriched_candidates.append({
                    "ticker": ticker_upper,
                    "name": name,
                    "is_in_real_portfolio": ticker_upper in active_tickers,
                    "current_pe": current_pe,
                    "fair_pe": fair_pe,
                    "diff_pct": diff_pct
                })
            category["candidates_enriched"] = enriched_candidates
            
            # 2. Enriquecer los candidatos ideales múltiples (ideal_tickers)
            ideal_list = category.get("ideal_tickers", [])
            # Retrocompatibilidad: si existía el campo ideal_ticker (string), añadirlo a la lista si no está
            single_ideal = category.get("ideal_ticker")
            if single_ideal and single_ideal not in ideal_list:
                ideal_list.append(single_ideal)
                category["ideal_tickers"] = ideal_list
                
            enriched_ideals = []
            for ideal in ideal_list:
                ideal_upper = ideal.upper()
                asset_data = asset_repo.get_asset_data(ideal_upper)
                name = asset_data.get("company_name", ideal_upper) if asset_data else ideal_upper
                fpe_d = asset_data.get("fair_pe_data", {}) if asset_data else {}
                
                fair_pe = fpe_d.get("fair_forward_pe")
                current_pe = fpe_d.get("current_forward_pe") or (asset_data.get("last_market_fwd_pe") if asset_data else None)
                
                # Conversión segura a float
                try:
                    fair_pe = float(fair_pe) if fair_pe is not None else None
                except Exception:
                    fair_pe = None
                    
                try:
                    current_pe = float(current_pe) if current_pe is not None else None
                except Exception:
                    current_pe = None
                    
                diff_pct = None
                if fair_pe and current_pe:
                    diff_pct = round(((current_pe / fair_pe) - 1) * 100, 1)

                enriched_ideals.append({
                    "ticker": ideal_upper,
                    "name": name,
                    "is_in_real_portfolio": ideal_upper in active_tickers,
                    "current_pe": current_pe,
                    "fair_pe": fair_pe,
                    "diff_pct": diff_pct
                })
            category["ideals_enriched"] = enriched_ideals

        # Sumar pesos de todos los candidatos activos que pertenecen a este grupo
        for t in group_tickers:
            group_real_value += active_portfolio_values.get(t, 0.0)

        group["total_positions"] = group_total_pos
        group["real_value"] = group_real_value
        if total_portfolio_value > 0:
            group["real_weight_pct"] = round((group_real_value / total_portfolio_value) * 100, 1)
        else:
            group["real_weight_pct"] = 0.0

    return portfolio_data

@router.post("/ideal")
async def save_ideal_portfolio(data: IdealPortfolioData, db: Session = Depends(get_db)):
    setting = db.query(DBSetting).filter(DBSetting.key == "ideal_portfolio").first()
    if not setting:
        setting = DBSetting(key="ideal_portfolio", value=data.model_dump())
        db.add(setting)
    else:
        setting.value = data.model_dump()
    db.commit()
    return {"status": "success"}

@router.get("/suggest_category/{ticker}")
async def suggest_category(
    ticker: str,
    db_session: Session = Depends(get_db),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    portfolio_service: PortfolioService = Depends(get_portfolio_service)
):
    import os
    import json
    from langchain_google_genai import ChatGoogleGenerativeAI
    
    ticker_upper = ticker.upper()
    asset_data = asset_repo.get_asset_data(ticker_upper)
    if not asset_data:
        raise HTTPException(status_code=404, detail="El activo no está analizado en la base de datos.")
        
    # Cargar la configuración del portfolio ideal
    setting = db_session.query(DBSetting).filter(DBSetting.key == "ideal_portfolio").first()
    if not setting or not setting.value:
        raise HTTPException(status_code=400, detail="Por favor, crea primero tu estructura en el Portfolio Ideal.")
        
    # Compilar las categorías disponibles
    categories = []
    for group in setting.value.get("groups", []):
        for cat in group.get("categories", []):
            categories.append({
                "group_name": group.get("name"),
                "category_id": cat.get("id"),
                "category_name": cat.get("name"),
                "description": cat.get("role_description", "")
            })
            
    if not categories:
        raise HTTPException(status_code=400, detail="No hay categorías estratégicas definidas en tu Portfolio Ideal.")

    # Resumen de la empresa para contextualizar
    company_name = asset_data.get("company_name", ticker_upper)
    sector = asset_data.get("sector", "N/A")
    moat_desc = (asset_data.get("moat") or asset_data.get("moat_analyst", ""))[:1000]
    fin_desc = (asset_data.get("fin") or asset_data.get("financial_analyst", ""))[:1000]
    
    # Prompt de clasificación
    prompt = f"""
    Eres un analista de asignación de capital de nivel de socio en un hedge fund. Tu objetivo es emparejar una empresa analizada con la MEJOR categoría estratégica de nuestro Portfolio Ideal.

    DATOS DE LA EMPRESA A CLASIFICAR:
    - Ticker: {ticker_upper}
    - Nombre: {company_name}
    - Sector: {sector}
    - Resumen de Moat/Ventajas: {moat_desc}
    - Resumen Financiero/Salud: {fin_desc}

    CATEGORÍAS DISPONIBLES EN MI PORTFOLIO IDEAL (Cajones de Inversión):
    {json.dumps(categories, indent=2, ensure_ascii=False)}

    INSTRUCCIONES DE RESPUESTA:
    1. Analiza con rigor en cuál de las categorías anteriores encaja mejor esta empresa según su modelo de negocio, ventajas competitivas y resiliencia.
    2. Responde EXACTAMENTE con un objeto JSON (y NADA más de texto, sin bloques de código ```json ni explicaciones adicionales) con el siguiente formato:
    {{
        "category_id": "id_de_la_categoria_seleccionada",
        "category_name": "nombre_de_la_categoria_seleccionada",
        "justification": "Escribe una justificación brillante de exactamente 2 frases en español explicando por qué este negocio se alinea perfectamente con el propósito estratégico de este cajón."
    }}
    """
    
    try:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        llm = ChatGoogleGenerativeAI(model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"), google_api_key=api_key, temperature=0.1)
        response = llm.invoke(prompt)
        text_res = response.content.strip()
        
        # Limpiar bloques markdown
        if text_res.startswith("```"):
            text_res = text_res.split("\n", 1)[1]
        if text_res.endswith("```"):
            text_res = text_res.rsplit("\n", 1)[0]
        if text_res.startswith("json"):
            text_res = text_res.split("json", 1)[1]
        text_res = text_res.strip()
        
        match_data = json.loads(text_res)
        return match_data
    except Exception as e:
        logger.error(f"Error en emparejador IA de portfolio ideal: {e}")
        fallback_cat = categories[0]
        return {
            "category_id": fallback_cat["category_id"],
            "category_name": fallback_cat["category_name"],
            "justification": f"Clasificación automática de respaldo. {company_name} ({ticker_upper}) opera en el sector de {sector}."
        }

@router.post("/add")
async def add_portfolio_item(
    item: TransactionItem,
    portfolio_service: PortfolioService = Depends(get_portfolio_service)
):
    try:
        fx_rate = item.fx_rate_at_purchase
        if fx_rate is None and item.currency.upper() != 'EUR':
            fx_rate = _fetch_fx_rate(item.currency, item.date)

        tx = Transaction(
            date=item.date,
            type=TransactionType(item.type.upper()),
            ticker=item.ticker,
            shares=item.shares,
            price=item.price,
            currency=item.currency,
            fee=item.fee or 0.0,
            fx_rate_at_purchase=fx_rate,
            broker=item.broker or 'AUTO'
        )
        portfolio_service.add_manual_transaction(tx)
        return {"status": "success", "fx_rate_used": fx_rate}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{ticker}")
async def delete_portfolio_item(
    ticker: str,
    portfolio_service: PortfolioService = Depends(get_portfolio_service)
):
    portfolio_service.delete_ticker_data(ticker)
    return {"status": "success"}

@router.post("/transaction/delete/{ticker}")
async def delete_transaction(
    ticker: str, 
    item: DeleteTransactionRequest,
    portfolio_service: PortfolioService = Depends(get_portfolio_service)
):
    tx = Transaction(
        date=item.date,
        type=TransactionType(item.type.upper()),
        ticker=ticker,
        shares=item.shares,
        price=item.price
    )
    portfolio_service.delete_manual_transaction(tx)
    return {"status": "success"}

@router.post("/transaction/edit-broker")
async def edit_transaction_broker(
    request: EditTransactionRequest,
    portfolio_service: PortfolioService = Depends(get_portfolio_service)
):
    try:
        # Cargar transacciones manuales
        txs = portfolio_service.repository.load_manual_transactions()
        found = False
        for tx in txs:
            if tx.ticker == request.ticker and tx.date == request.date and tx.type.value == request.type.upper() \
               and abs(tx.shares - request.shares) < 0.001 and abs(tx.price - request.price) < 0.001:
                tx.broker = request.new_broker.upper()
                tx.fee = 0.0  # Forzar recalculo de comisión en rebuild_portfolio
                found = True
                break
                
        if found:
            portfolio_service.repository.save_manual_transactions(txs)
            portfolio_service.rebuild_portfolio()
            return {"status": "success"}
        else:
            raise HTTPException(status_code=404, detail="Transacción no encontrada")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/performance/refresh")
async def refresh_performance_cache():
    """Elimina la caché de performance para forzar recálculo en la próxima carga."""
    try:
        if os.path.exists(PERF_CACHE_FILE):
            os.remove(PERF_CACHE_FILE)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PerformanceStartDateBody(BaseModel):
    start_date: Optional[str] = None  # 'YYYY-MM-DD'; None/"" para quitar el corte


@router.post("/performance/start-date")
async def set_performance_start_date(
    body: PerformanceStartDateBody,
    db_session: Session = Depends(get_db)
):
    """Fija (o elimina) una fecha de inicio personalizada para el cálculo de rendimiento
    (pestaña Rendimiento de /portfolio). Las operaciones anteriores a esa fecha se ignoran
    en la serie de Valor Liquidativo, tratando la posición que ya existía en esa fecha como
    el punto de partida — útil para descartar los primeros movimientos "de prueba"."""
    start_date = (body.start_date or "").strip() or None
    if start_date:
        try:
            datetime.date.fromisoformat(start_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de fecha inválido (usa YYYY-MM-DD)")

    try:
        settings = get_settings_from_db(db_session)
        perf_settings = dict(settings.get("performance", {}))
        perf_settings["start_date"] = start_date
        save_settings_to_db(db_session, {"performance": perf_settings})
        if os.path.exists(PERF_CACHE_FILE):
            os.remove(PERF_CACHE_FILE)
        return {"status": "success", "start_date": start_date}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rebuild")
async def rebuild_portfolio_endpoint(
    portfolio_service: PortfolioService = Depends(get_portfolio_service)
):
    """Reconstruye el portfolio desde cero (transacciones + dividendos + splits)."""
    try:
        portfolio_service.rebuild_portfolio()
        if os.path.exists(PERF_CACHE_FILE):
            os.remove(PERF_CACHE_FILE)
        return {"status": "success", "message": "Portfolio reconstruido correctamente"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
