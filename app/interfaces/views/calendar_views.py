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
from app.application.services import market_cache
from app.interfaces.views.market_views import INDEX_CACHE, refresh_upcoming_events


logger = logging.getLogger(__name__)

PAST_DAYS = 400   # lo ya ocurrido que se carga (resultados presentados y dividendos cobrados), para ver meses anteriores

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
            "past": e["date"] < today,   # ex-dividendo ya pasado (pendiente de cobro)
        })

    cal_events += past_events(portfolio_service, asset_meta, portfolio_tickers, today)
    return {"events": cal_events, "today": today.isoformat()}


def past_events(portfolio_service: PortfolioService, asset_meta: dict, portfolio_tickers: set,
                today: datetime.date) -> list:
    """Lo ya ocurrido, para enseñarlo apagado y tachado: resultados presentados (de la caché de resultados, sin pedir nada
    a Yahoo; la sorpresa del BPA en la última) de cartera, favoritas y seguimiento, y dividendos cobrados (del libro)."""
    start = (today - datetime.timedelta(days=PAST_DAYS)).isoformat()
    out = []
    for t, meta in asset_meta.items():
        held = t in portfolio_tickers
        category = "portfolio" if held else ("favorite" if meta["is_fav"] else ("seguimiento" if meta["status"] == "ACCEPTED" else None))
        info = market_cache.peek("earnings_v2", t) if category else None
        if not info:
            continue
        last = info.get("last") or {}
        for d in info.get("reported") or []:
            if start <= d < today.isoformat():
                out.append({"ticker": t, "type": "Resultados", "category": category, "date": d, "is_portfolio": held,
                            "is_favorite": meta["is_fav"], "past": True,
                            "surprise_pct": last.get("surprise_pct") if d == last.get("date") else None})
    rates = lambda cur: 1.0 if cur == "EUR" else ((INDEX_CACHE["data"].get(f"{cur}EUR=X") or {}).get("price"))
    for item in portfolio_service.get_active_portfolio() + portfolio_service.get_closed_portfolio():
        for e in portfolio_service.get_ledger(item.ticker):
            if getattr(e.type, "value", e.type) != "DIVIDEND" or not (start <= e.date[:10] < today.isoformat()):
                continue
            cur = item.currency_code or "USD"
            fx = rates("GBP" if cur == "GBp" else cur)
            out.append({"ticker": item.ticker, "type": "Dividendo", "category": "dividend", "date": e.date[:10],
                        "is_portfolio": True, "is_favorite": False, "past": True, "collected": True,
                        "amount": e.total * (0.01 if cur == "GBp" else 1.0) * fx if fx else None, "pay_date": None})
    return out
