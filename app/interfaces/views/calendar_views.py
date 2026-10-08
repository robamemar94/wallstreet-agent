"""Calendario de resultados y dividendos (/calendario)."""
import logging
import datetime
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db
from app.infrastructure.db.models import DBAsset
from app.infrastructure.dependencies import (
    get_portfolio_service,
)
from app.application.services.portfolio_service import PortfolioService
from app.interfaces.views.market_views import INDEX_CACHE, refresh_upcoming_events


logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Calendar Views"])


@router.get("/calendario")
def calendario_page():
    # el calendario es ahora una ventana que se abre desde arriba (junto a la campana) en cualquier página
    return RedirectResponse("/mercado#calendario")


@router.get("/api/calendario")
def calendario_events(
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    db_session: Session = Depends(get_db)
):
    """Eventos del calendario mensual: resultados de cartera, favoritas y seguimiento, y dividendos de la cartera."""
    active = portfolio_service.get_active_portfolio()
    refresh_upcoming_events(db_session, active)
    portfolio_tickers = {p.ticker for p in active}

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

    return {"events": cal_events, "today": today.isoformat()}
