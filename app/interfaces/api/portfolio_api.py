import logging
import os
import datetime
from typing import List, Optional
import yfinance as yf
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db
from app.application.services.portfolio_service import PortfolioService
from app.domain.models import Transaction, TransactionType
from app.infrastructure.dependencies import get_portfolio_service, get_asset_repository, get_settings_from_db
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.application.services import portfolio_roles_service as roles_service
from app.application.services.decision_review_service import TX_NOTES_KEY, TX_SNAPS_KEY, tx_ref
from app.interfaces.views.settings_views import save_settings_to_db

PERF_CACHE_FILE = "data/performance_cache.json"

logger = logging.getLogger(__name__)

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
    comment: Optional[str] = None  # opcional: el porqué de la operación


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

@router.get("/suggest_role/{ticker}")
async def suggest_role(ticker: str, asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)):
    """La IA propone el rol de cartera (CORE, GROWTH, QUALITY, OPTIONALITY) según el análisis guardado."""
    import json
    from langchain_google_genai import ChatGoogleGenerativeAI

    ticker_upper = ticker.upper()
    asset_data = asset_repo.get_asset_data(ticker_upper)
    if not asset_data:
        raise HTTPException(status_code=404, detail="El activo no está analizado en la base de datos.")

    roles = roles_service.load_roles()
    defs = [{"key": r["key"], "name": r["name"], "objective": r["objective"], "question": r["question"],
             "traits": r["traits"], "examples": r["examples"]} for r in roles]
    company_name = asset_data.get("company_name", ticker_upper)
    sector = asset_data.get("sector", "N/A")
    moat_desc = (asset_data.get("moat") or asset_data.get("moat_analyst", ""))[:1000]
    fin_desc = (asset_data.get("fin") or asset_data.get("financial_analyst", ""))[:1000]

    prompt = f"""
    Eres un analista de asignación de capital. Clasifica la empresa en UNO de los roles de cartera definidos.

    EMPRESA:
    - Ticker: {ticker_upper}
    - Nombre: {company_name}
    - Sector: {sector}
    - Resumen de Moat/Ventajas: {moat_desc}
    - Resumen Financiero/Salud: {fin_desc}

    ROLES:
    {json.dumps(defs, indent=2, ensure_ascii=False)}

    Responde EXACTAMENTE con un objeto JSON (sin bloques de código ni texto adicional):
    {{"role": "KEY_DEL_ROL", "justification": "2 frases en español explicando por qué encaja en ese rol y no en los otros."}}
    """
    try:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        llm = ChatGoogleGenerativeAI(model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"), google_api_key=api_key, temperature=0.1)
        text_res = llm.invoke(prompt).content.strip()
        if text_res.startswith("```"):
            text_res = text_res.split("\n", 1)[1]
        if text_res.endswith("```"):
            text_res = text_res.rsplit("\n", 1)[0]
        if text_res.startswith("json"):
            text_res = text_res.split("json", 1)[1]
        match = json.loads(text_res.strip())
        role = next((r for r in roles if r["key"] == match.get("role")), None)
        if not role:
            raise ValueError(f"rol desconocido: {match.get('role')}")
        return {"role": role["key"], "role_name": role["name"], "justification": match.get("justification", "")}
    except Exception as e:
        logger.error(f"Error en la sugerencia de rol de {ticker_upper}: {e}")
        raise HTTPException(status_code=502, detail="La IA no ha podido proponer un rol; inténtalo de nuevo.")

def _save_snapshot(db: Session, asset_repo, tx: Transaction) -> None:
    """Foto de la valoración al operar (zona de compra y veredicto de la tesis), para el balance de decisiones."""
    from app.application.services import decision_review_service
    from app.infrastructure.db.models import DBThesis
    ticker = tx.ticker.upper()
    try:
        thesis = db.query(DBThesis).filter(DBThesis.ticker == ticker).first()
        data = asset_repo.get_asset_data(ticker)
        snap = decision_review_service.snapshot(ticker, data, tx.price, getattr(thesis, "verdict", None) if thesis else None)
        if snap:
            snaps = dict(data.get(TX_SNAPS_KEY) or {})
            snaps[tx_ref(tx.date, tx.type.value, tx.shares, tx.price)] = snap
            asset_repo.save_asset_data(ticker, TX_SNAPS_KEY, snaps)
    except Exception as e:   # la operación ya está guardada; sin foto solo falta ese dato en el balance
        logger.warning("No se pudo guardar la foto de la valoración de %s: %s", ticker, e)


def _save_comment(db: Session, asset_repo, portfolio_service, tx: Transaction, comment: str, held_before: float) -> None:
    """Guarda el comentario con la operación y, si la empresa tiene tesis, también en su diario de decisiones."""
    from app.infrastructure.db.models import DBThesis
    from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository
    from app.interfaces.api.thesis_api import DecisionCreate, add_decision
    ticker, kind = tx.ticker.upper(), tx.type.value
    ref = tx_ref(tx.date, kind, tx.shares, tx.price)
    notes = dict(asset_repo.get_asset_data(ticker).get(TX_NOTES_KEY) or {})
    notes[ref] = comment
    asset_repo.save_asset_data(ticker, TX_NOTES_KEY, notes)
    thesis = db.query(DBThesis.id).filter(DBThesis.ticker == ticker).first()
    if not thesis:
        return
    if kind == "BUY":
        action = "aumentar" if held_before > 0 else "comprar"
    else:
        action = "vender" if held_before - tx.shares <= 1e-9 else "reducir"
    try:
        add_decision(thesis.id, DecisionCreate(date=str(tx.date)[:10], action=action, reason=comment, price=tx.price,
                                               shares=tx.shares, transaction_ref=ref),
                     SqlAlchemyThesisRepository(db), asset_repo, portfolio_service)
    except Exception as e:   # el comentario ya está guardado con la operación
        logger.warning("No se pudo anotar la decisión en la tesis de %s: %s", ticker, e)


# Sin async: el recálculo de la cartera es síncrono (BD y Yahoo); en un async bloquearía todo el servidor mientras dura
@router.post("/add")
def add_portfolio_item(
    item: TransactionItem,
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    db: Session = Depends(get_db),
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
        comment = (item.comment or "").strip()
        held_before = 0.0
        if comment:
            held_before = next((p.shares for p in portfolio_service.get_active_portfolio() if p.ticker == item.ticker.upper()), 0.0)
        portfolio_service.add_manual_transaction(tx)
        if comment:
            _save_comment(db, asset_repo, portfolio_service, tx, comment, held_before)
        _save_snapshot(db, asset_repo, tx)
        return {"status": "success", "fx_rate_used": fx_rate}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{ticker}")
def delete_portfolio_item(
    ticker: str,
    portfolio_service: PortfolioService = Depends(get_portfolio_service)
):
    portfolio_service.delete_ticker_data(ticker)
    return {"status": "success"}

@router.post("/transaction/delete/{ticker}")
def delete_transaction(
    ticker: str, 
    item: DeleteTransactionRequest,
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
):
    tx = Transaction(
        date=item.date,
        type=TransactionType(item.type.upper()),
        ticker=ticker,
        shares=item.shares,
        price=item.price
    )
    portfolio_service.delete_manual_transaction(tx)
    # su comentario y su foto de la valoración, si los tenía, se van con ella
    ref = tx_ref(item.date, item.type.upper(), item.shares, item.price)
    for key in (TX_NOTES_KEY, TX_SNAPS_KEY):
        values = dict(asset_repo.get_asset_data(ticker.upper()).get(key) or {})
        if values.pop(ref, None) is not None:
            asset_repo.save_asset_data(ticker.upper(), key, values)
    return {"status": "success"}


class TxNote(BaseModel):
    ticker: str
    refs: List[str]   # «fecha|tipo|acciones|precio» de cada orden (las del mismo día van juntas: una decisión)
    note: Optional[str] = None


@router.post("/transaction/note")
def save_transaction_note(item: TxNote, asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)):
    """El porqué de una operación ya hecha (desde el balance de decisiones). Vacío: se borra.
    No se anota en el diario de la tesis: allí cada decisión lleva la foto de la tesis del día en que se tomó."""
    ticker = item.ticker.upper()
    notes = dict(asset_repo.get_asset_data(ticker).get(TX_NOTES_KEY) or {})
    note = (item.note or "").strip()[:1000]
    for ref in item.refs:
        if note:
            notes[ref] = note
        else:
            notes.pop(ref, None)
    asset_repo.save_asset_data(ticker, TX_NOTES_KEY, notes)
    return {"status": "success", "note": note or None}

@router.post("/transaction/edit-broker")
def edit_transaction_broker(
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
def rebuild_portfolio_endpoint(
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


@router.post("/informes/{key}/comentario")
def report_ai_comment(key: str, db: Session = Depends(get_db)):
    """Comentario de IA de un informe (mensual, trimestral o anual): una llamada a Gemini sin búsqueda, solo al pulsar."""
    from app.application.services import report_service
    if key[:1] not in report_service.AI_KINDS:
        raise HTTPException(status_code=400, detail="El comentario de IA es para los informes mensuales, trimestrales y anuales")
    try:
        return {"status": "success", "ai": report_service.ai_comment(db, key)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Comentario de IA del informe %s", key)
        raise HTTPException(status_code=502, detail=f"La IA no ha podido comentar el informe: {e}")


class ReportGoals(BaseModel):
    text: Optional[str] = None


@router.post("/informes/{key}/objetivos")
def report_goals(key: str, item: ReportGoals, db: Session = Depends(get_db)):
    """Tus objetivos para el año siguiente (informe anual)."""
    from app.application.services import report_service
    try:
        report_service.save_goals(db, key, item.text or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success"}


@router.post("/informes/generar")
def generate_reports():
    """Genera ya los informes pendientes (en segundo plano)."""
    from app.application.services import report_service
    return {"status": "started" if report_service.trigger() else "running"}


# --- Liquidez del bróker (cash_service): saldo de partida, aportaciones y retiradas; compras, ventas y dividendos, solos ---

class CashBalance(BaseModel):
    amount: float
    date: Optional[str] = None


class CashMovement(BaseModel):
    type: str   # 'deposit' | 'withdrawal'
    amount: float
    date: Optional[str] = None
    note: str = ""


@router.get("/liquidez")
def cash_status(db: Session = Depends(get_db)):
    from app.application.services import cash_service
    return cash_service.status(db)


@router.post("/liquidez/saldo")
def cash_set_balance(body: CashBalance, db: Session = Depends(get_db)):
    from app.application.services import cash_service
    cash_service.set_balance(db, body.amount, body.date)
    return cash_service.status(db)


@router.post("/liquidez/movimiento")
def cash_add_movement(body: CashMovement, db: Session = Depends(get_db)):
    from app.application.services import cash_service
    try:
        cash_service.add_movement(db, body.type, body.amount, body.date, body.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return cash_service.status(db)


@router.delete("/liquidez/movimiento/{move_id}")
def cash_delete_movement(move_id: str, db: Session = Depends(get_db)):
    from app.application.services import cash_service
    if not cash_service.delete_movement(db, move_id):
        raise HTTPException(status_code=404, detail="Movimiento no encontrado")
    return cash_service.status(db)
