import datetime
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.application.services import cache_warmer, price_scheduler, news_summary_service, thesis_news_runner, thesis_review_service, results_calendar
from app.application.services.thesis_ai_service import NEWS_MIN_MATERIALITY, business_week, news_due, next_radar, radar_week_start
from app.application.services.portfolio_service import PortfolioService
from app.application.services.thesis_service import (
    compute_exposure, decision_outcome, evaluate_thesis, fetch_ttm_fcf_per_share, implied_cagr, live_valuation,
    render_document,
)
from app.infrastructure.db.database import get_db
from app.infrastructure.dependencies import (
    get_asset_repository, get_portfolio_service, get_settings_from_db, get_thesis_repository,
)
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository
from app.infrastructure.templates import templates
from app.interfaces.views.market_views import INDEX_CACHE, ensure_prices_cached, get_currency_symbol

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Theses Views"])


_startup_done = False


@router.on_event("startup")
def _thesis_startup() -> None:
    # FastAPI puede lanzar dos veces los «startup» declarados en un router: solo una vez por proceso
    global _startup_done
    if _startup_done:
        return
    _startup_done = True
    from app.infrastructure.db.database import SessionLocal
    db = SessionLocal()
    try:
        stale = SqlAlchemyThesisRepository(db).fail_stale_reviews()
        if stale:
            logger.warning("%d revisiones de resultados interrumpidas por el reinicio marcadas como error", stale)
    finally:
        db.close()
    # Radar semanal automático: los sábados desde las 8:00, comprobado al arrancar y cada hora
    thesis_news_runner.start_scheduler()
    from app.application.services import report_service
    report_service.start_scheduler()   # informes semanales, mensuales, trimestrales y anuales (sin IA)
    from app.application.services import financials_update_service
    financials_update_service.start_scheduler()   # el año fiscal nuevo en tus cuentas, desde Yahoo (gratis, si cuadra)
    from app.application.services import backup_service
    backup_service.start_scheduler()   # copia de seguridad completa cada día (BBDD, tesis, CSV, ajustes)
    # Datos de mercado lentos de cartera, tesis y watchlist: se descargan ya para que la primera visita no espere
    cache_warmer.warm_up_in_background()
    # Precios: en la apertura y el cierre de Europa y EE. UU., y cada 5 min durante la sesión
    price_scheduler.start_scheduler()

def _next_earnings(ticker: str) -> Optional[datetime.date]:
    """Próxima fecha de resultados: la misma que la Agenda (Yahoo y, si no la tiene o la da muy lejos, la fecha buscada
    y guardada; nunca llama a la IA)."""
    info = thesis_review_service._earnings_info(ticker) or {}
    nxt = results_calendar.next_results(ticker, ticker, info.get("next"), info.get("next_time"))
    try:
        when = datetime.date.fromisoformat(nxt["date"]) if nxt and nxt.get("date") else None
    except ValueError:
        return None
    return when if when and when >= datetime.date.today() else None


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
    today, last = datetime.date.today(), d.get("last_date")
    # su mercado aún no ha abierto hoy: la variación es la de su última sesión (se enseña apagada, no como la de hoy)
    not_open = bool(today.weekday() < 5 and last and last != today)
    return {"price": d["price"], "pct": pct, "not_open": not_open, "last_iso": last.isoformat() if not_open else None}


def _card(thesis: Dict[str, Any], asset_repo: Optional[SqlAlchemyAssetRepository] = None) -> Dict[str, Any]:
    ev = evaluate_thesis(thesis)
    currency = (asset_repo.get_asset_data(thesis["ticker"]).get("currency") if asset_repo else None) or "USD"
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
        "cur": get_currency_symbol(currency, thesis["ticker"]),
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
                rows.append({"pillar": p["name"] if p["name"] != k["name"] else None, "kpi": k,
                         "cells": [by_period.get(per) for per in ordered]})
    return {"periods": ordered, "rows": rows}


def _news_context(thesis: Dict[str, Any]) -> Dict[str, Any]:
    digest = thesis["extra"].get("news_digest")
    if digest:  # radares guardados con un umbral anterior: mostrar solo lo que pasa el actual
        digest = {**digest, "items": [i for i in digest.get("items", []) if i.get("materiality", 0) >= NEWS_MIN_MATERIALITY]}
    next_run = next_radar(digest)
    return {"digest": digest, "due": news_due(digest), "next_run": next_run}


def _reviews_context(repo: SqlAlchemyThesisRepository, thesis: Dict[str, Any], selected_id: Optional[int],
                     exposure: Dict[str, Any]) -> Dict[str, Any]:
    reviews = repo.list_reviews(thesis["id"])
    done = [r for r in reviews if r["status"] in ("ready", "applied", "discarded") and r["report"]]
    selected = next((r for r in reviews if r["id"] == selected_id), None) if selected_id else \
        next((r for r in reviews if r["status"] in ("running", "ready", "error", "applied")), None)
    kpis = {k["id"]: k for p in thesis["pillars"] for k in p["kpis"]}
    weights = {tk: p["weight"] for tk, p in exposure["positions"].items()}
    # la revisión anterior a la elegida (por periodo cerrado): para «Frente a …»
    prev = None
    if selected and selected["report"] and selected["status"] in ("ready", "applied", "discarded"):
        end = selected["report"].get("period_end") or selected["created_at"][:10]
        older = [r for r in done if r["id"] != selected["id"] and (r["report"].get("period_end") or r["created_at"][:10]) < end]
        prev = max(older, key=lambda r: r["report"].get("period_end") or r["created_at"][:10], default=None)
    notices = [n for n in thesis_review_service.compute_notices(repo, weights) if n["thesis_id"] == thesis["id"]]
    # lo que dejó para los próximos resultados la última revisión que cuenta (no las descartadas)
    last = next((r for r in done if r["status"] != "discarded"), None)
    return {
        "next_checks": {"period": last["report"].get("period"), "items": last["report"]["next_checks"]}
        if last and last["report"].get("next_checks") else None,
        "reviews": reviews,
        "review": selected,
        "review_running": thesis_review_service.is_running(thesis["id"]) or any(r["status"] == "running" for r in reviews),
        "credibility": thesis_review_service.credibility_score([r["report"] for r in done]),
        "kpi_names": {i: k["name"] for i, k in kpis.items()},
        "kpi_units": {i: k.get("unit") or "" for i, k in kpis.items()},
        "thesis_notices": notices,
        "changes": thesis_review_service.review_changes(prev["report"] if prev else None, selected["report"], kpis) if prev else None,
        "position": exposure["positions"].get(thesis["ticker"]),
        "tracking": thesis["ticker"] not in thesis_review_service.held_tickers(repo.db),   # sin acciones: en seguimiento
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
    notices = thesis_review_service.compute_notices(repo, {tk: p["weight"] for tk, p in positions.items()})
    unreviewed = {n["thesis_id"]: n for n in notices if n["type"] == "new_results"}   # resultados publicados sin revisar
    cards = [{**_card(t, asset_repo), "position": positions.get(t["ticker"]), "new_results": unreviewed.get(t["id"])}
             for t in theses]
    # de cartera arriba; las de empresas que sigues sin tenerlas, en su propio apartado
    held = thesis_review_service.held_tickers(db_session)
    tracking = [c for c in cards if c["thesis"]["ticker"] not in held]
    cards = [c for c in cards if c["thesis"]["ticker"] in held]
    with_thesis = {t["ticker"] for t in theses}
    uncovered = sorted(([tk, p] for tk, p in positions.items() if tk not in with_thesis and p["weight"] >= 5),
                       key=lambda x: -x[1]["weight"])
    return templates.TemplateResponse("theses.html", {
        "request": request,
        "cards": cards,
        "tracking_cards": tracking,
        "uncovered": uncovered,
        "unread": repo.unread_news_count(),
        "notices": notices,
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
    card = _card(thesis, asset_repo)

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


NEWS_OPEN_WEEKS = 4   # semanas del radar a la vista; las anteriores, plegadas en «Anteriores»



def news_weeks(items: List[Dict[str, Any]], now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """Agrupa las noticias por la semana del radar que las encontró (la del sábado de esa pasada, más las que se
    fuercen a mitad de semana), de la más reciente a la más antigua. Cada una se rotula con la semana hábil que repasa."""
    from app.application.services.news_summary_service import week_of
    current = radar_week_start(now).date()
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for n in items:
        groups.setdefault(week_of(n["found_at"]), []).append(n)
    out = []
    for i, (week, news) in enumerate(sorted(groups.items(), reverse=True)):
        start = datetime.date.fromisoformat(week)
        out.append({"start": start, "days": business_week(start), "current": start == current, "folded": i >= NEWS_OPEN_WEEKS, "news": news,
                    "unread": sum(1 for n in news if not n.get("seen", True))})
    return out


MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def week_label(days) -> str:
    """«del 5 al 9 oct» (o «del 29 sep al 3 oct»): la semana hábil que repasa el radar."""
    a, b = days
    return (f"del {a.day} al {b.day} {MESES_CORTOS[b.month - 1]}" if a.month == b.month
            else f"del {a.day} {MESES_CORTOS[a.month - 1]} al {b.day} {MESES_CORTOS[b.month - 1]}")


@router.get("/noticias", response_class=HTMLResponse)
def news_page(
    request: Request,
    ticker: Optional[str] = None,
    semana: Optional[str] = None,
    repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository),
    db_session: Session = Depends(get_db),
):
    """Noticias de las tesis de UNA semana del radar (de serie, la última; ?semana=AAAA-MM-DD elige otra) y, al lado, el
    resumen semanal (solo se guarda el de la última semana) o el radar de la empresa elegida."""
    thesis_news_runner.trigger_due_news()
    theses = repo.list_theses()
    selected = next((t for t in theses if t["ticker"] == (ticker or "").upper()), None)
    thesis_id = selected["id"] if selected else None
    weeks = news_weeks(repo.list_news(thesis_id=thesis_id, limit=None))   # todas, por semanas (de la más reciente)
    if not any(w["current"] for w in weeks):   # la última semana siempre se puede elegir, aunque no tenga noticias
        start = radar_week_start().date()
        weeks.insert(0, {"start": start, "days": business_week(start), "current": True, "folded": False, "news": [], "unread": 0})
    options = [{"key": w["start"].isoformat(), "label": week_label(w["days"]), "current": w["current"], "count": len(w["news"]),
                "unread": w["unread"]} for w in weeks]
    chosen = next((w for w in weeks if w["start"].isoformat() == semana), weeks[0] if weeks else None)
    items = chosen["news"] if chosen else []
    unread_by_ticker = {}
    for n in repo.list_news(unread_only=True, limit=500):
        unread_by_ticker[n["ticker"]] = unread_by_ticker.get(n["ticker"], 0) + 1
    radars = [{"ticker": t["ticker"], "id": t["id"], **_news_context(t)} for t in theses]
    summary = news_summary_service.load(db_session)
    return templates.TemplateResponse("news.html", {
        "request": request,
        "items": items,
        "week": chosen, "week_key": chosen["start"].isoformat() if chosen else None,
        "week_text": week_label(chosen["days"]) if chosen else "",
        "week_options": options,
        "unread_ids": [n["id"] for n in items if not n["seen"]],
        "tickers": [t["ticker"] for t in theses],
        "selected": selected["ticker"] if selected else None,
        "radars": radars,
        "radar_running": thesis_news_runner.is_running(),
        "radar_progress": thesis_news_runner.status()["radar_progress"],
        "min_materiality": NEWS_MIN_MATERIALITY,
        "summary": summary,
        "summary_running": news_summary_service.is_running(),
        "unread_by_ticker": unread_by_ticker,
        "settings": get_settings_from_db(db_session),
    })


# --- Logos de empresa ---

@router.get("/logo/{ticker}")
def company_logo(ticker: str):
    from fastapi.responses import FileResponse, Response
    from app.application.services import logo_service
    path = logo_service.ensure_logo(ticker)
    if not path:
        return Response(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "max-age=86400"})


@router.post("/api/logo/{ticker}")
async def upload_company_logo(ticker: str, file: UploadFile = File(...)):
    from app.application.services import logo_service
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Sube una imagen (PNG, JPG, SVG…)")
    try:
        logo_service.save_upload(ticker, await file.read())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success"}


# --- Seguimiento de posiciones y Centro de mando (pestaña de la portada) ---
