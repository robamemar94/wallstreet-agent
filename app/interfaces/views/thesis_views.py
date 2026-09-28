import datetime
import logging
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.application.services import thesis_news_runner, thesis_review_service
from app.application.services.thesis_ai_service import NEWS_INTERVAL_DAYS, NEWS_MIN_MATERIALITY, news_due
from app.application.services.portfolio_service import PortfolioService
from app.application.services.thesis_service import (
    compute_exposure, decision_outcome, evaluate_thesis, fetch_ttm_fcf_per_share, implied_cagr, live_valuation,
    render_document,
)
from app.infrastructure.db.database import get_db
from app.infrastructure.dependencies import get_asset_repository, get_portfolio_service, get_settings_from_db, get_thesis_repository
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository
from app.infrastructure.templates import templates
from app.interfaces.views.market_views import INDEX_CACHE, ensure_prices_cached

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Theses Views"])


@router.on_event("startup")
def _thesis_startup() -> None:
    from app.infrastructure.db.database import SessionLocal
    db = SessionLocal()
    try:
        stale = SqlAlchemyThesisRepository(db).fail_stale_reviews()
        if stale:
            logger.warning("%d revisiones de resultados interrumpidas por el reinicio marcadas como error", stale)
    finally:
        db.close()
    # Radar semanal automático: consulta en segundo plano las tesis que llevan 7+ días sin radar
    thesis_news_runner.trigger_due_news()

EARNINGS_TTL = 12 * 3600
_EARNINGS_CACHE: Dict[str, Any] = {}


def _next_earnings(ticker: str) -> Optional[datetime.date]:
    """Próxima fecha de resultados: primero la caché del calendario de la home, si no yfinance (cacheado 12h)."""
    today = datetime.date.today()
    for e in INDEX_CACHE.get("upcoming_events", []):
        if e.get("ticker") == ticker and e.get("type") == "Resultados" and e["date"] >= today:
            return e["date"]
    cached = _EARNINGS_CACHE.get(ticker)
    if cached and time.time() - cached[0] < EARNINGS_TTL:
        return cached[1]
    result = None
    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar
        dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
        future = sorted(d for d in (dates or []) if isinstance(d, datetime.date) and d >= today)
        result = future[0] if future else None
    except Exception as e:
        logger.warning("No se pudo obtener el calendario de %s: %s", ticker, e)
    _EARNINGS_CACHE[ticker] = (time.time(), result)
    return result


def portfolio_exposure(portfolio_service: PortfolioService, asset_repo: SqlAlchemyAssetRepository) -> Dict[str, Any]:
    """Valor en EUR y peso de cada posición abierta (precios y FX de la caché de la home)."""
    try:
        items = [i for i in portfolio_service.get_active_portfolio() if i.shares > 0]
        ensure_prices_cached([i.ticker for i in items], asset_repo)
        data = INDEX_CACHE["data"]
        prices = {i.ticker: data[i.ticker]["price"] for i in items if i.ticker in data}
        fx = {c: data[f"{c}EUR=X"]["price"] for c in {"GBP" if i.currency_code == "GBp" else i.currency_code for i in items}
              if f"{c}EUR=X" in data}
        return compute_exposure([{"ticker": i.ticker, "shares": i.shares, "currency": i.currency_code,
                                  "cost_eur": i.total_cost_eur} for i in items], prices, fx)
    except Exception as e:
        logger.warning("No se pudo calcular la exposición de la cartera: %s", e)
        return {"positions": {}, "total_eur": 0.0}


def _price(ticker: str) -> Optional[Dict[str, float]]:
    d = INDEX_CACHE["data"].get(ticker)
    if not d:
        return None
    pct = ((d["price"] / d["prev"]) - 1) * 100 if d.get("prev") else 0.0
    return {"price": d["price"], "pct": pct}


def _card(thesis: Dict[str, Any]) -> Dict[str, Any]:
    ev = evaluate_thesis(thesis)
    price = _price(thesis["ticker"])
    ref = thesis.get("reference_price")
    nxt = _next_earnings(thesis["ticker"])
    return {
        "thesis": thesis,
        "ev": ev,
        "price": price,
        "vs_reference": ((price["price"] / ref) - 1) * 100 if price and ref else None,
        "next_earnings": nxt,
        "days_to_earnings": (nxt - datetime.date.today()).days if nxt else None,
        "last_event": thesis["events"][0] if thesis["events"] else None,
        "news": _news_context(thesis),
    }


def _periods_grid(ev: Dict[str, Any]) -> Dict[str, Any]:
    """Matriz KPI × periodo con el semáforo de cada observación (para el mapa de evolución)."""
    periods = {}
    for p in ev["pillars"]:
        for k in p["kpis"]:
            for o in k["observations"]:
                periods.setdefault(o["period"], o["date"])
    ordered = [p for p, _ in sorted(periods.items(), key=lambda x: x[1])]
    rows = []
    for p in ev["pillars"]:
        for k in p["kpis"]:
            by_period = {o["period"]: o for o in k["observations"]}
            if by_period:
                rows.append({"pillar": p["name"], "kpi": k, "cells": [by_period.get(per) for per in ordered]})
    return {"periods": ordered, "rows": rows}


def _news_context(thesis: Dict[str, Any]) -> Dict[str, Any]:
    digest = thesis["extra"].get("news_digest")
    if digest:  # radares guardados con un umbral anterior: mostrar solo lo que pasa el actual
        digest = {**digest, "items": [i for i in digest.get("items", []) if i.get("materiality", 0) >= NEWS_MIN_MATERIALITY]}
    next_run = None
    if digest and digest.get("created_at"):
        next_run = (datetime.datetime.fromisoformat(digest["created_at"]) + datetime.timedelta(days=NEWS_INTERVAL_DAYS)).date()
    return {"digest": digest, "due": news_due(digest), "next_run": next_run}


def _reviews_context(repo: SqlAlchemyThesisRepository, thesis: Dict[str, Any], selected_id: Optional[int],
                     exposure: Dict[str, Any]) -> Dict[str, Any]:
    reviews = repo.list_reviews(thesis["id"])
    done = [r for r in reviews if r["status"] in ("ready", "applied", "discarded") and r["report"]]
    selected = next((r for r in reviews if r["id"] == selected_id), None) if selected_id else \
        next((r for r in reviews if r["status"] in ("running", "ready", "error", "applied")), None)
    kpis = {k["id"]: k for p in thesis["pillars"] for k in p["kpis"]}
    weights = {tk: p["weight"] for tk, p in exposure["positions"].items()}
    notices = [n for n in thesis_review_service.compute_notices(repo, weights) if n["thesis_id"] == thesis["id"]]
    return {
        "reviews": reviews,
        "review": selected,
        "review_running": thesis_review_service.is_running(thesis["id"]) or any(r["status"] == "running" for r in reviews),
        "credibility": thesis_review_service.credibility_score([r["report"] for r in done]),
        "kpi_names": {i: k["name"] for i, k in kpis.items()},
        "kpi_units": {i: k.get("unit") or "" for i, k in kpis.items()},
        "thesis_notices": notices,
        "position": exposure["positions"].get(thesis["ticker"]),
        "decisions": [{**d, "outcome": decision_outcome(d, (_price(thesis["ticker"]) or {}).get("price"))}
                      for d in repo.list_decisions(thesis["id"])],
        "recent_transactions": repo.transactions_for(thesis["ticker"])[:10],
    }


@router.get("/tesis", response_class=HTMLResponse)
def theses_page(
    request: Request,
    repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    db_session: Session = Depends(get_db),
):
    theses = repo.list_theses()
    thesis_news_runner.trigger_due_news()
    ensure_prices_cached([t["ticker"] for t in theses], asset_repo)
    exposure = portfolio_exposure(portfolio_service, asset_repo)
    positions = exposure["positions"]
    cards = [{**_card(t), "position": positions.get(t["ticker"])} for t in theses]
    with_thesis = {t["ticker"] for t in theses}
    uncovered = sorted(([tk, p] for tk, p in positions.items() if tk not in with_thesis and p["weight"] >= 5),
                       key=lambda x: -x[1]["weight"])
    return templates.TemplateResponse("theses.html", {
        "request": request,
        "cards": cards,
        "uncovered": uncovered,
        "unread": repo.unread_news_count(),
        "notices": thesis_review_service.compute_notices(repo, {tk: p["weight"] for tk, p in positions.items()}),
        "radar_running": thesis_news_runner.is_running(),
        "settings": get_settings_from_db(db_session),
    })


@router.get("/tesis/{thesis_id}", response_class=HTMLResponse)
def thesis_detail_page(
    request: Request,
    thesis_id: int,
    review: Optional[int] = None,
    repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    db_session: Session = Depends(get_db),
):
    thesis = repo.get_thesis(thesis_id)
    if not thesis:
        raise HTTPException(status_code=404, detail="La tesis no existe")
    ensure_prices_cached([thesis["ticker"]], asset_repo)
    card = _card(thesis)

    valuation = thesis["extra"].get("valuation") or {}
    current = card["price"]["price"] if card["price"] else None
    scenarios = []
    for s in valuation.get("scenarios", []):
        cagr = implied_cagr(current, s.get("price"), valuation.get("target_year"))
        scenarios.append({**s, "implied_cagr": cagr * 100 if cagr is not None else None})

    return templates.TemplateResponse("thesis_detail.html", {
        "request": request,
        **card,
        "valuation": valuation,
        "scenarios": scenarios,
        "live": live_valuation(valuation, fetch_ttm_fcf_per_share(thesis["ticker"]), current),
        "grid": _periods_grid(card["ev"]),
        "document": render_document(thesis["extra"].get("document_md")),
        "news": _news_context(thesis),
        "news_items": repo.list_news(thesis_id=thesis_id, limit=15),
        "radar_running": thesis_news_runner.is_running(),
        **_reviews_context(repo, thesis, review, portfolio_exposure(portfolio_service, asset_repo)),
        "today": datetime.date.today().isoformat(),
        "settings": get_settings_from_db(db_session),
    })


@router.get("/noticias", response_class=HTMLResponse)
def news_page(
    request: Request,
    ticker: Optional[str] = None,
    repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository),
    db_session: Session = Depends(get_db),
):
    thesis_news_runner.trigger_due_news()
    theses = repo.list_theses()
    selected = next((t for t in theses if t["ticker"] == (ticker or "").upper()), None)
    items = repo.list_news(thesis_id=selected["id"] if selected else None)
    radars = [{"ticker": t["ticker"], "id": t["id"], **_news_context(t)} for t in theses]
    return templates.TemplateResponse("news.html", {
        "request": request,
        "items": items,
        "unread_ids": [n["id"] for n in items if not n["seen"]],
        "tickers": [t["ticker"] for t in theses],
        "selected": selected["ticker"] if selected else None,
        "radars": radars,
        "radar_running": thesis_news_runner.is_running(),
        "min_materiality": NEWS_MIN_MATERIALITY,
        "settings": get_settings_from_db(db_session),
    })
