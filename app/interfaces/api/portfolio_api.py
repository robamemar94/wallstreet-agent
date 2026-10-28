import logging
import os
import datetime
from typing import List, Optional
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
    """EUR por unidad de divisa en esa fecha. Si Yahoo no responde queda vacío y `rebuild_portfolio` lo reintenta."""
    from utils.fx import fx_rate_on
    return fx_rate_on(currency, date_str)

class DeleteTransactionRequest(BaseModel):
    date: str
    type: str
    shares: float
    price: float

class UpdateTransactionRequest(BaseModel):
    old: DeleteTransactionRequest   # la operación tal como está (fecha|tipo|acciones|precio la identifican)
    new: TransactionItem            # cómo debe quedar


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


@router.get("/transaction/{ticker}")
def get_transaction(ticker: str, date: str, type: str, shares: float, price: float,
                    portfolio_service: PortfolioService = Depends(get_portfolio_service),
                    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)):
    """Una operación añadida a mano con todos sus datos y su porqué (para editarla)."""
    t = ticker.upper()
    tx = portfolio_service.find_manual_transaction(Transaction(date=date, type=TransactionType(type.upper()), ticker=t, shares=shares, price=price))
    if not tx:
        raise HTTPException(status_code=404, detail="Solo se pueden editar las operaciones añadidas a mano")
    note = (asset_repo.get_asset_data(t).get(TX_NOTES_KEY) or {}).get(tx_ref(date, type.upper(), shares, price))
    return {"date": str(tx.date)[:10], "type": tx.type.value, "ticker": tx.ticker, "shares": tx.shares, "price": tx.price,
            "currency": tx.currency, "fee": tx.fee, "fx_rate_at_purchase": tx.fx_rate_at_purchase, "broker": tx.broker, "comment": note}


@router.post("/transaction/update/{ticker}")
def update_transaction(ticker: str, item: UpdateTransactionRequest,
                       portfolio_service: PortfolioService = Depends(get_portfolio_service),
                       asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)):
    """Edita una operación añadida a mano. Su porqué y su foto de la valoración pasan a la operación editada."""
    o, n = item.old, item.new
    fx_rate = n.fx_rate_at_purchase
    if fx_rate is None and n.currency.upper() != 'EUR':
        fx_rate = _fetch_fx_rate(n.currency, n.date)
    old_tx = Transaction(date=o.date, type=TransactionType(o.type.upper()), ticker=ticker.upper(), shares=o.shares, price=o.price)
    new_tx = Transaction(date=n.date, type=TransactionType(n.type.upper()), ticker=n.ticker.upper(), shares=n.shares, price=n.price,
                         currency=n.currency, fee=n.fee or 0.0, fx_rate_at_purchase=fx_rate, broker=n.broker or 'AUTO')
    if not portfolio_service.replace_manual_transaction(old_tx, new_tx):
        raise HTTPException(status_code=404, detail="Solo se pueden editar las operaciones añadidas a mano")
    old_ref, new_ref = tx_ref(o.date, o.type.upper(), o.shares, o.price), tx_ref(n.date, n.type.upper(), n.shares, n.price)
    for key in (TX_NOTES_KEY, TX_SNAPS_KEY):
        src = dict(asset_repo.get_asset_data(old_tx.ticker).get(key) or {})
        had = old_ref in src
        value = src.pop(old_ref, None)
        if key == TX_NOTES_KEY and n.comment is not None:   # el porqué del formulario manda (vacío: se borra)
            value = n.comment.strip()[:1000] or None
        if had:
            asset_repo.save_asset_data(old_tx.ticker, key, src)
        if value is not None:
            dst = dict(asset_repo.get_asset_data(new_tx.ticker).get(key) or {})
            dst[new_ref] = value
            asset_repo.save_asset_data(new_tx.ticker, key, dst)
    return {"status": "success", "fx_rate_used": fx_rate}


@router.get("/fee_estimate")
def fee_estimate(broker: str, currency: str, shares: float, price: float, date: str = "",
                 portfolio_service: PortfolioService = Depends(get_portfolio_service)):
    """Comisión estimada para el formulario de operación: la misma cuenta que el recálculo (`broker_fees`)."""
    from app.application.services import broker_fees
    from app.interfaces.views.portfolio_views import get_current_rate
    if shares <= 0 or price <= 0:
        return {"fee": 0.0}
    b = broker_fees.resolve_broker(broker, date or datetime.date.today().isoformat())
    fee = broker_fees.estimate_fee(b, currency, shares, price, get_current_rate(currency), portfolio_service._get_broker_settings())
    return {"fee": round(fee, 2), "broker": b}


@router.get("/check/{ticker}")
def check_operation(ticker: str, portfolio_service: PortfolioService = Depends(get_portfolio_service),
                    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)):
    """Para confirmar una operación antes de guardarla: cotización en caché, su moneda y las acciones que tienes."""
    from app.interfaces.views.market_views import INDEX_CACHE, ensure_prices_cached
    t = ticker.upper()
    try:
        ensure_prices_cached([t], asset_repo)
    except Exception as e:
        logger.warning("Sin cotización para comprobar %s: %s", t, e)
    d = INDEX_CACHE["data"].get(t) or {}
    held = next((p.shares for p in portfolio_service.get_active_portfolio() if p.ticker == t), 0.0)
    return {"ticker": t, "price": d.get("price"), "currency": asset_repo.get_asset_data(t).get("currency"), "held": held,
            "known": bool(d.get("price")) or held > 0}


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


class RoboPlan(BaseModel):
    amount: float
    day: int = 3
    since: Optional[str] = None   # AAAA-MM desde el que vale (este mes por defecto)


class RoboAdjust(BaseModel):
    value: Optional[float] = None
    invested: Optional[float] = None
    cash: Optional[float] = None
    date: Optional[str] = None
    text: Optional[str] = None    # el resumen de MyInvestor pegado: rellena lo que falte y los pesos de cada fondo


@router.get("/roboadvisor")
def robo_status(db: Session = Depends(get_db)):
    from app.application.services import robo_service
    return robo_service.status(db)


@router.post("/roboadvisor/aportacion")
def robo_set_plan(body: RoboPlan, db: Session = Depends(get_db)):
    from app.application.services import robo_service
    try:
        robo_service.set_plan(db, body.amount, body.day, body.since)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return robo_service.status(db)


@router.post("/roboadvisor/ajuste")
def robo_adjust(body: RoboAdjust, db: Session = Depends(get_db)):
    """Lo que dice MyInvestor: a mano (valor e invertido) o pegando su resumen."""
    from app.application.services import robo_service
    parsed = robo_service.parse_summary(body.text) if body.text else {}
    value = body.value or parsed.get("value")
    invested = body.invested or parsed.get("invested")
    cash = body.cash if body.cash is not None else parsed.get("cash")
    try:
        robo_service.add_snapshot(db, value, invested, cash, body.date, parsed.get("funds"), parsed.get("returns"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {**robo_service.status(db), "parsed": parsed}


@router.delete("/roboadvisor/ajuste/{date}")
def robo_delete_adjust(date: str, db: Session = Depends(get_db)):
    from app.application.services import robo_service
    if not robo_service.delete_snapshot(db, date):
        raise HTTPException(status_code=404, detail="Ajuste no encontrado")
    return robo_service.status(db)
