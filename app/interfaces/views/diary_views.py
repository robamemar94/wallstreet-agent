"""
Diario de una empresa (panel lateral de la ficha): el fragmento con la línea de tiempo y la API de las notas.
El porqué de las operaciones se guarda con /api/portfolio/transaction/note (el mismo de Decisiones).
"""
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.application.services import company_notes_service as notes_service
from app.application.services.portfolio_service import PortfolioService
from app.infrastructure.db.database import get_db
from app.infrastructure.dependencies import get_asset_repository, get_portfolio_service, get_settings_from_db
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.templates import templates

router = APIRouter()


class NoteIn(BaseModel):
    text: str
    date: Optional[str] = None


@router.get("/ticker/{ticker}/diario", response_class=HTMLResponse)
def diary_fragment(request: Request, ticker: str, db_session: Session = Depends(get_db),
                   portfolio_service: PortfolioService = Depends(get_portfolio_service),
                   asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)):
    from app.application.services import price_monitor_service as pm
    from app.interfaces.views.market_views import get_currency_symbol
    from app.interfaces.views.watchlist_views import NOTE_KEY
    t = ticker.upper()
    data = asset_repo.get_asset_data(t) or {}
    txs, splits = pm.load_transactions(portfolio_service, db_session)
    return templates.TemplateResponse("_diario.html", {
        "request": request, "ticker": t, "fixed_note": data.get(NOTE_KEY) or "", "today": datetime.date.today().isoformat(),
        "items": notes_service.timeline(db_session, t, txs, data, splits.get(t, [])),
        "cur": get_currency_symbol(data.get("currency", "USD"), t)})


@router.get("/diario", response_class=HTMLResponse)
def diary_page(request: Request, ticker: Optional[str] = None, db_session: Session = Depends(get_db),
               portfolio_service: PortfolioService = Depends(get_portfolio_service)):
    """Pantalla Diario: lo que has escrito de todas las empresas (o de una, ?ticker=): notas y porqués de tus operaciones;
    una nota nueva eligiendo la empresa y las operaciones sin porqué, para anotarlo ahí mismo."""
    from app.application.services import price_monitor_service as pm, report_service
    from app.infrastructure.db.models import DBAsset, DBWatchlistItem
    from app.interfaces.views.market_views import get_currency_symbol
    txs, splits = pm.load_transactions(portfolio_service, db_session)
    held = [i.ticker for i in portfolio_service.get_active_portfolio() if i.shares > 0]
    watch = [w.ticker for w in db_session.query(DBWatchlistItem).all()]
    traded = {t["ticker"] for t in txs}
    assets = {a.ticker: a.data or {} for a in db_session.query(DBAsset).filter(DBAsset.ticker.in_(traded)).all()}
    pending = notes_service.missing_reasons(txs, assets, report_service.start_date(db_session).isoformat(), splits)
    t = ticker.upper() if ticker else None
    entries = notes_service.written(db_session, txs, assets, t, splits)
    for p in pending + [e for e in entries if e["kind"] == "op"]:
        p["cur"] = get_currency_symbol((assets.get(p["ticker"]) or {}).get("currency", "USD"), p["ticker"])
    return templates.TemplateResponse("diario.html", {
        "request": request, "settings": get_settings_from_db(db_session), "ticker": t,
        "notes": entries, "pending": pending,
        "choices": sorted(set(held) | set(watch) | set(notes_service.noted_tickers(db_session))),
        "today": datetime.date.today().isoformat()})


def _bad(e: ValueError):
    raise HTTPException(status_code=400, detail=str(e) or "Fecha no válida")


@router.post("/api/ticker/{ticker}/notas")
def add_note(ticker: str, item: NoteIn, db_session: Session = Depends(get_db)):
    try:
        return {"status": "success", "note": notes_service.add(db_session, ticker, item.text, item.date)}
    except ValueError as e:
        _bad(e)


@router.post("/api/notas/{note_id}")
def update_note(note_id: int, item: NoteIn, db_session: Session = Depends(get_db)):
    try:
        note = notes_service.update(db_session, note_id, item.text, item.date)
    except ValueError as e:
        _bad(e)
    if not note:
        raise HTTPException(status_code=404, detail="La nota no existe")
    return {"status": "success", "note": note}


@router.delete("/api/notas/{note_id}")
def delete_note(note_id: int, db_session: Session = Depends(get_db)):
    if not notes_service.delete(db_session, note_id):
        raise HTTPException(status_code=404, detail="La nota no existe")
    return {"status": "success"}
