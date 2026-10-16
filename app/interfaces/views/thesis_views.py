import datetime
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.application.services import agenda_service, base_valuation_service, cache_warmer, price_scheduler, dividend_forecast, news_summary_service, notice_service, split_service, thesis_news_runner, thesis_review_service
from app.application.services.thesis_ai_service import NEWS_MIN_MATERIALITY, business_week, news_due, next_radar, radar_week_start
from app.application.services.portfolio_service import PortfolioService
from app.application.services.thesis_service import (
    compute_exposure, decision_outcome, evaluate_thesis, fetch_ttm_fcf_per_share, implied_cagr, live_valuation,
    render_document,
)
from app.infrastructure.db.database import get_db
from app.infrastructure.dependencies import (
    get_asset_repository, get_notice_repository, get_portfolio_service, get_settings_from_db, get_thesis_repository,
    get_watchlist_repository,
)
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.repositories.sqlalchemy_notice_repository import SqlAlchemyNoticeRepository
from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository
from app.infrastructure.repositories.sqlalchemy_watchlist_repository import SqlAlchemyWatchlistRepository
from app.infrastructure.templates import templates
from app.interfaces.views.market_views import INDEX_CACHE, ensure_prices_cached, get_currency_symbol, ext_quote, ext_day

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
    # Datos de mercado lentos de cartera, tesis y watchlist: se descargan ya para que la primera visita no espere
    cache_warmer.warm_up_in_background()
    # Precios: en la apertura y el cierre de Europa y EE. UU., y cada 5 min durante la sesión
    price_scheduler.start_scheduler()

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
                rows.append({"pillar": p["name"], "kpi": k, "cells": [by_period.get(per) for per in ordered]})
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
    cards = [{**_card(t, asset_repo), "position": positions.get(t["ticker"])} for t in theses]
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

def history_symbols(txs, thesis_tickers, benchmark: str) -> list:
    """Históricos que necesita el seguimiento: valores operados y con tesis, el índice y los cambios a euros.
    Lo comparten las secciones de Mercado y la Cartera (misma lista, misma caché de 30 min)."""
    currencies = {("GBP" if t.get("currency") == "GBp" else t.get("currency") or "USD") for t in txs} - {"EUR"}
    return sorted({t["ticker"] for t in txs} | set(thesis_tickers)) + [benchmark] + [f"{c}EUR=X" for c in currencies]


def positions_tracking(repo: SqlAlchemyThesisRepository, asset_repo: SqlAlchemyAssetRepository,
                       portfolio_service: PortfolioService, db_session: Session) -> Dict[str, Any]:
    """Seguimiento de las posiciones (y de las empresas con tesis): variación y contribución por periodo, peso,
    distancia al máximo de 52 semanas, CAGR de la valoración base, próximos resultados y señales de precio.
    Lo usan las secciones de Mercado («Requiere tu atención», monitor, agenda) y la vista «Seguimiento» de la Cartera."""
    from app.application.services import price_monitor_service as pm, results_calendar
    from app.application.services.thesis_review_service import _earnings_info

    settings = get_settings_from_db(db_session)
    benchmark = (settings.get("performance") or {}).get("benchmark_ticker") or "URTH"
    txs, splits = pm.load_transactions(portfolio_service, db_session)
    theses = {t["ticker"]: t for t in repo.list_theses()}
    open_tickers = {i.ticker for i in portfolio_service.get_active_portfolio() if i.shares > 0}
    tickers = sorted({t["ticker"] for t in txs} | set(theses))
    symbols = history_symbols(txs, theses, benchmark)

    error, df = None, None
    try:
        df = pm.download_history(symbols)
    except Exception as e:
        logger.exception("Mercado: fallo al descargar precios")
        error = str(e)

    changes = pm.portfolio_changes(df, txs, splits, benchmark, rolling_days=agenda_service.ROLLING_DAYS) if df is not None else {"periods": {}, "positions": {}, "total_eur": 0}
    weights = {t: p["weight"] for t, p in changes["positions"].items()}
    raw = pm.download_history(symbols, fill=False) if df is not None else None
    stale = pm.stale_symbols(raw, sorted(open_tickers)) if df is not None else {}
    # última sesión de cada valor: si es anterior a la de hoy, su mercado aún no ha abierto (EE. UU. por la mañana)
    last_session = {t: raw[t].last_valid_index().date().isoformat() for t in tickers
                    if raw is not None and t in raw.columns and raw[t].last_valid_index() is not None}
    session_today = df.index[-1].date().isoformat() if df is not None and len(df.index) else None
    stale_note = ("Yahoo no ha dado hoy el precio de " + ", ".join(f"{t} (se usa el del {d[8:10]}/{d[5:7]})" for t, d in stale.items())
                  + ".") if stale else None
    if changes.get("missing"):   # faltan precios: los pesos están inflados, no se avisa de concentración
        error = error or f"faltan los de {', '.join(changes['missing'])}. El valor de la cartera y los pesos de hoy están incompletos."
    rows, signals = [], []
    row_tickers = sorted(open_tickers | set(theses))
    # los datos lentos (FCF/BPA TTM, fechas de resultados) se piden en paralelo y quedan en la caché en disco
    cache_warmer.prefetch(earnings=row_tickers, fcf=theses, valuation=row_tickers, splits=row_tickers)
    for t in row_tickers:
        st = pm.price_stats(df[t], df[benchmark] if benchmark in df.columns else None) if df is not None and t in df.columns else None
        thesis, data = theses.get(t), asset_repo.get_asset_data(t)
        live = live_valuation(thesis["extra"].get("valuation") or {}, fetch_ttm_fcf_per_share(t), st["price"]) if thesis and st else None
        # CAGR estimado: la valoración base de la ficha al precio de hoy, como en la watchlist
        cagr, cagr_src, cagr_flag = None, None, None
        bval = base_valuation_service.live(t, data, st["price"]) if st else None
        if bval and bval["cagr"] is not None:
            cagr, cagr_src = bval["cagr"], (f"Valoración base de la ficha ({base_valuation_service.METRICS[bval['metric']]['label']}, {bval['years']} años)"
                                            + (f" · {bval['anchor']['year']} corregido a mano" if (bval["anchor"] or {}).get("state") == "active" else ""))
            # como en la Watchlist: que se note si el CAGR sale de unos supuestos viejos
            stale = base_valuation_service.staleness(bval["inputs"], thesis_review_service.fetch_reported_dates(t))
            if bval["inputs"].get("legacy"):
                cagr_flag = ("legacy", "Valoración antigua (de 3 escenarios) sin revisar: revísala y guárdala en la ficha")
            elif stale and stale["stale"]:
                dmy = lambda d: f"{d[8:10]}/{d[5:7]}/{d[2:4]}"
                cagr_flag = ("stale", f"Valoración anticuada: revisada el {dmy(stale['reviewed'])} y ha presentado resultados "
                                      f"{len(stale['results_since'])} veces desde entonces" if stale["by_calendar"] else
                                      f"Valoración anticuada: sin revisar desde el {dmy(stale['reviewed'])} (más de 7 meses)")
        row_signals = pm.price_signals(t, st, thesis, live, None if changes.get("missing") else weights.get(t)) if st else []
        signals += row_signals
        company = data.get("company_name") or t
        rows.append({
            "ticker": t, "name": company, "thesis_id": (thesis or {}).get("id"),
            "last_session": last_session.get(t), "closed_today": bool(session_today and last_session.get(t) and last_session[t] < session_today),
            "cur": get_currency_symbol(data.get("currency", "USD"), t), "stats": st, "position": changes["positions"].get(t),
            "cagr": cagr, "cagr_src": cagr_src, "cagr_flag": cagr_flag, "cagr_anchor": (bval or {}).get("anchor") if (bval and (bval["anchor"] or {}).get("state") == "active") else None,
            "next": results_calendar.next_results(t, company, _earnings_info(t).get("next")),
        })
    rows.sort(key=lambda r: -(r["position"] or {}).get("weight", -1))
    today = datetime.date.today()
    for r in rows:   # días que faltan para los próximos resultados
        if r["next"] and r["next"].get("date"):
            r["next"] = {**r["next"], "days": (datetime.date.fromisoformat(r["next"]["date"][:10]) - today).days}
    return {"settings": settings, "benchmark": benchmark, "error": error, "stale_note": stale_note, "changes": changes,
            "weights": weights, "rows": rows, "signals": signals, "theses": theses, "open_tickers": open_tickers,
            "txs": txs, "splits": splits, "row_tickers": row_tickers}



def chart_rows(rows):
    """Datos por posición para la tabla de seguimiento, el mapa y las tarjetas (variación y contribución por periodo)."""
    return [{"ticker": r["ticker"], "weight": r["position"]["weight"], "contribution": dict(r["position"]["contribution"]),
             "pnl_eur": dict(r["position"]["pnl_eur"]), "change": dict((r["stats"] or {}).get("change", {})),
             "closed_today": r["closed_today"], "last_session": r["last_session"],
             "price": (r["stats"] or {}).get("price"), "cur": r["cur"]} for r in rows if r["position"]]


def live_day(chart, periods, portfolio_service: PortfolioService, asset_repo: SqlAlchemyAssetRepository) -> Dict[str, Any]:
    """El día con los precios en directo y el mismo cálculo que Contable y su tarjeta (el histórico va con 30 min de
    caché y cuenta la divisa de los mercados que aún no han abierto). Corrige chart en sitio; devuelve los periodos."""
    from app.interfaces.views.portfolio_views import day_change, ext_day_eur
    shares = {i.ticker: i.shares for i in portfolio_service.get_active_portfolio() if i.shares > 0}
    ensure_prices_cached(list(shares), asset_repo)
    days = {}
    for c in chart:
        p = INDEX_CACHE["data"].get(c["ticker"])
        if p and c["ticker"] in shares:
            days[c["ticker"]] = day_change(shares[c["ticker"]], p["price"], p.get("prev"), p.get("last_date"),
                                           asset_repo.get_asset_data(c["ticker"]).get("currency", "USD"))
    total_day = sum(d["eur"] for d in days.values())
    prev_total = sum(shares[t] * d["prev"] * d["rate_prev"] for t, d in days.items())
    for c in chart:
        d = days.get(c["ticker"])
        if d:
            c["closed_today"], c["price"] = d["closed"], INDEX_CACHE["data"][c["ticker"]]["price"]
            if d["last_date"]:
                c["last_session"] = d["last_date"].isoformat() if hasattr(d["last_date"], "isoformat") else str(d["last_date"])
            c["change"]["1d"], c["pnl_eur"]["1d"] = (None if d["closed"] else d["pct"]), d["eur"]
            c["last_pct"] = d["last_pct"]
            c["ext"] = ext_quote(c["ticker"]) if d["closed"] else None   # EE. UU.: pre/post-market
            c["ext_day"] = ext_day_eur(shares[c["ticker"]], ext_day(c["ticker"]), d, d["rate"])   # Var. día € fuera de horario
            c["contribution"]["1d"] = 100 * d["eur"] / prev_total if prev_total else None
    periods = dict(periods)
    if days:
        periods["1d"] = {**periods.get("1d", {}), "abs_eur": total_day, "pct": 100 * total_day / prev_total if prev_total else None}
    return periods


@router.get("/portfolio/seguimiento", response_class=HTMLResponse)
def portfolio_tracking_fragment(
    request: Request,
    repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    db_session: Session = Depends(get_db),
):
    """Vista «Seguimiento» de la tabla de posiciones de la Cartera (se carga aparte: descarga el histórico de precios)."""
    from concurrent.futures import ThreadPoolExecutor
    from app.application.services import price_monitor_service as pm
    pt = positions_tracking(repo, asset_repo, portfolio_service, db_session)
    rows = [r for r in pt["rows"] if r["ticker"] in pt["open_tickers"]]
    from app.application.services import estimate_revisions_service as est
    with ThreadPoolExecutor(max_workers=8) as pool:   # toda la serie de cada valor y las previsiones: una vez al día (caché en disco)
        aths = dict(zip([r["ticker"] for r in rows], pool.map(pm.all_time_high, [r["ticker"] for r in rows])))
        revs = dict(zip([r["ticker"] for r in rows], pool.map(est.revisions, [r["ticker"] for r in rows])))
    for r in rows:
        price = (INDEX_CACHE["data"].get(r["ticker"]) or {}).get("price") or (r["stats"] or {}).get("price")
        r["ath"], r["from_ath"] = aths.get(r["ticker"]), pm.from_all_time_high(aths.get(r["ticker"]), price)
        r["rev"], r["rev_cut"] = revs.get(r["ticker"]), est.signal(revs.get(r["ticker"]))
        th = pt["theses"].get(r["ticker"])
        r["thesis_verdict"] = th.get("verdict") if th else None
    chart = chart_rows(rows)
    periods = live_day(chart, pt["changes"]["periods"], portfolio_service, asset_repo)
    return templates.TemplateResponse("_positions_tracking.html", {
        "request": request, "rows": rows, "error": pt["error"], "stale_note": pt["stale_note"],
        "periods": periods, "benchmark": pt["benchmark"], "chart": chart,
    })


def portfolio_agenda(pt: Dict[str, Any], portfolio_service: PortfolioService, asset_repo: SqlAlchemyAssetRepository) -> Dict[str, Any]:
    """Agenda de la cartera (Mercado): próximos resultados y dividendos, y resumen de los últimos 7 y 30 días."""
    from app.application.services.thesis_review_service import _earnings_info
    from app.interfaces.views.portfolio_views import portfolio_raw
    today = datetime.date.today()
    held = pt["open_tickers"]
    ensure_prices_cached(sorted(held), asset_repo)   # cotización y cambio de hoy para pasar los dividendos a euros
    prices = INDEX_CACHE["data"]
    forecast = dividend_forecast.portfolio_forecast(portfolio_service, asset_repo, prices, portfolio_raw, today)
    results = [{"ticker": r["ticker"], "date": r["next"]["date"], "thesis_id": r["thesis_id"], "held": True}
               for r in pt["rows"] if r["ticker"] in held and r["next"] and r["next"].get("date")]

    def to_eur(ticker: str, amount: float) -> Optional[float]:
        cur = asset_repo.get_asset_data(ticker).get("currency") or "USD"
        ccy = "GBP" if cur == "GBp" else cur
        fx = 1.0 if ccy == "EUR" else (prices.get(f"{ccy}EUR=X") or {}).get("price")
        return amount * (0.01 if cur == "GBp" else 1.0) * fx if fx else None

    month_start = today - datetime.timedelta(days=max(agenda_service.ROLLING_DAYS))   # lo que cubre la agenda
    collected = []
    for item in portfolio_service.get_active_portfolio() + portfolio_service.get_closed_portfolio():
        for e in portfolio_service.get_ledger(item.ticker):
            if getattr(e.type, "value", e.type) == "DIVIDEND" and e.date[:10] >= month_start.isoformat():
                eur = to_eur(item.ticker, e.total)
                if eur is not None:
                    collected.append({"ticker": item.ticker, "date": e.date[:10], "gross_eur": eur})
    reported = []
    for t in sorted(held):
        last = _earnings_info(t).get("last")
        if last and last.get("date"):
            reported.append({"ticker": t, "date": last["date"], "surprise_pct": last.get("surprise_pct"),
                             "thesis_id": (pt["theses"].get(t) or {}).get("id")})
    # el día, con los precios en directo y el mismo cálculo que la Cartera (sin variación si su mercado no ha abierto)
    chart = chart_rows(pt["rows"])
    periods = live_day(chart, pt["changes"].get("periods") or {}, portfolio_service, asset_repo)
    live_pnl = {c["ticker"]: c["pnl_eur"].get("1d") for c in chart}
    positions = {t: {**p, "pnl_eur": {**(p.get("pnl_eur") or {}), "1d": live_pnl.get(t, (p.get("pnl_eur") or {}).get("1d"))}}
                 for t, p in (pt["changes"].get("positions") or {}).items()}
    agenda = agenda_service.build_agenda(today, results, forecast["events"], periods, positions, collected, pt["txs"], reported,
                                         symbol=lambda cur, t: get_currency_symbol(cur or "USD", t))
    agenda["semana"]["done"] = agenda_service.week_done(today, reported, collected)   # lo ya pasado de esta semana
    today_past = agenda["hoy"]["past"]
    today_past["closed"] = sorted(c["ticker"] for c in chart if c["closed_today"] and c["ticker"] in held)
    # el índice, también en directo: antes de que abra su bolsa el histórico repite el cierre de ayer (0,00 %)
    from app.interfaces.views.portfolio_views import day_change
    benchmark = pt["benchmark"]
    ensure_prices_cached([benchmark], asset_repo)
    b = INDEX_CACHE["data"].get(benchmark) or {}
    # el índice se refresca con otras listas de valores y puede quedarse atrás: si es de una sesión anterior a la de
    # las posiciones en su misma moneda (su bolsa ya ha abierto), se descarga ya
    bcur = asset_repo.get_asset_data(benchmark).get("currency") or "USD"
    same = [INDEX_CACHE["data"].get(t, {}).get("last_date") for t in held if (asset_repo.get_asset_data(t).get("currency") or "USD") == bcur]
    newest = max((d for d in same if d), default=None)
    if b.get("last_date") and newest and b["last_date"] < newest:
        ensure_prices_cached([benchmark], asset_repo, wait=True)
        b = INDEX_CACHE["data"].get(benchmark) or {}
    if b.get("price"):
        bday = day_change(1, b["price"], b.get("prev"), b.get("last_date"), "USD")
        today_past["benchmark_closed"] = bday["closed"]
        today_past["benchmark_pct"] = None if bday["closed"] else bday["pct"]
    return agenda


@router.get("/api/centro/pulso")
def centro_pulse(
    repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository),
    notice_repo: SqlAlchemyNoticeRepository = Depends(get_notice_repository),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    db_session: Session = Depends(get_db),
):
    """Resumen del Centro de mando para la tarjeta «Mi Cartera» de Mercado. Va aparte de la página (que no espere a
    los históricos) y con lo ya guardado: avisos, calendario de resultados, previsión de dividendos y precios."""
    from app.application.services import notification_service, price_monitor_service as pm, results_calendar
    from app.application.services.thesis_review_service import _earnings_info
    from app.interfaces.views.portfolio_views import portfolio_raw
    today = datetime.date.today()
    held = sorted(i.ticker for i in portfolio_service.get_active_portfolio() if i.shares > 0)
    notifications = notification_service.collect(db_session, repo, notice_repo)
    results = []
    for t in held:
        nxt = results_calendar.next_results(t, asset_repo.get_asset_data(t).get("company_name") or t, _earnings_info(t).get("next"))
        if nxt and nxt.get("date"):
            results.append({"ticker": t, "date": nxt["date"][:10]})
    ensure_prices_cached(held, asset_repo)
    forecast = dividend_forecast.portfolio_forecast(portfolio_service, asset_repo, INDEX_CACHE["data"], portfolio_raw, today)
    period, periods = None, {}
    try:   # mismos valores que el Centro de mando: comparte su caché de históricos
        settings = get_settings_from_db(db_session)
        benchmark = (settings.get("performance") or {}).get("benchmark_ticker") or "URTH"
        txs, splits = pm.load_transactions(portfolio_service, db_session)
        df = pm.download_history(history_symbols(txs, {t["ticker"] for t in repo.list_theses()}, benchmark))
        periods = pm.portfolio_changes(df, txs, splits, benchmark, rolling_days=(7, agenda_service.PULSE_RESULTS_DAYS))["periods"]
        period = periods.get(f"{agenda_service.PULSE_RESULTS_DAYS}d")
    except Exception as e:
        logger.warning("Resumen del Centro: sin variación de 30 días: %s", e)
    out = agenda_service.pulse(today, notifications, results, forecast["events"], period)
    out["benchmark"] = benchmark if period else None
    # el selector 7 / 30 días de la tarjeta «Mi Cartera»: lo ganado en cada periodo frente al índice
    out["periods"] = {k: {"pct": p.get("pct"), "abs_eur": p.get("abs_eur"), "benchmark_pct": p.get("benchmark_pct")}
                      for k, p in (periods or {}).items() if k in ("7d", "30d") and p and p.get("pct") is not None}
    return out


def other_agenda(open_tickers, theses: Dict[str, Any], asset_repo: SqlAlchemyAssetRepository,
                 watch_repo: SqlAlchemyWatchlistRepository, db_session: Session) -> Dict[str, Any]:
    """Vista «Resto» de la agenda: próximos resultados de las empresas que sigues sin tenerlas (favoritas, en seguimiento,
    en la watchlist o con tesis). Con cada una, sus marcas y, si está en la watchlist, a qué distancia está de su
    precio de compra."""
    from app.application.services import results_calendar
    from app.application.services.thesis_review_service import _earnings_info
    from app.infrastructure.db.models import DBAsset
    from app.interfaces.views.watchlist_views import proximity
    assets = {a.ticker: a for a in db_session.query(DBAsset).all()}
    favs = {t for t, a in assets.items() if a.is_favorite}
    following = {t for t, a in assets.items() if (a.data or {}).get("status") == "ACCEPTED"}
    watch = favs | {i["ticker"] for i in watch_repo.list_items()}
    tickers = sorted((favs | following | watch | set(theses)) - set(open_tickers))
    cache_warmer.prefetch(earnings=tickers)   # caché en disco de 12 h: solo descarga lo que falte
    results, meta, reported = [], {}, []
    for t in tickers:
        data, info = asset_repo.get_asset_data(t), _earnings_info(t)
        tid = (theses.get(t) or {}).get("id")
        last = info.get("last") or {}
        if last.get("date"):
            reported.append({"ticker": t, "date": last["date"], "surprise_pct": last.get("surprise_pct"), "thesis_id": tid})
        nxt = results_calendar.next_results(t, data.get("company_name") or t, info.get("next"))
        if nxt and nxt.get("date"):
            results.append({"ticker": t, "date": nxt["date"][:10], "thesis_id": tid})
        zone = None
        price = (INDEX_CACHE["data"].get(t) or {}).get("price")
        if t in watch and price:
            group, drop = proximity(base_valuation_service.live(t, data, price), price)
            zone = {"group": group, "drop": drop} if group != "sin" else None
        meta[t] = {"fav": t in favs, "watch": t in watch, "following": t in following, "zone": zone}
    out = {**agenda_service.build_other_agenda(datetime.date.today(), results), "meta": meta}
    out["semana"]["done"] = agenda_service.week_done(datetime.date.today(), reported)   # lo ya pasado de esta semana
    return out


def watchlist_day(open_tickers, theses: Dict[str, Any], asset_repo: SqlAlchemyAssetRepository,
                  watch_repo: SqlAlchemyWatchlistRepository) -> Dict[str, Any]:
    """Watchlist del día (añadidas a mano + favoritas): en zona de compra, a punto de entrar y movimientos fuertes de
    hoy, con los precios en directo."""
    from app.interfaces.views.portfolio_views import day_change
    from app.interfaces.views.watchlist_views import proximity
    tickers = sorted(set(asset_repo.get_watch_candidates()) | {i["ticker"] for i in watch_repo.list_items()})
    ensure_prices_cached(tickers, asset_repo)
    items = []
    for t in tickers:
        p = INDEX_CACHE["data"].get(t) or {}
        if not p.get("price"):
            continue
        data = asset_repo.get_asset_data(t)
        day = day_change(1, p["price"], p.get("prev"), p.get("last_date"), data.get("currency", "USD"))
        group, drop = proximity(base_valuation_service.live(t, data, p["price"]), p["price"])
        items.append({"ticker": t, "thesis_id": (theses.get(t) or {}).get("id"), "held": t in open_tickers,
                      "drop": drop, "day_pct": None if day["closed"] else day["pct"], "closed": day["closed"]})
    return agenda_service.watchlist_today(items)


def centro_context(repo: SqlAlchemyThesisRepository, notice_repo: SqlAlchemyNoticeRepository,
                   asset_repo: SqlAlchemyAssetRepository, watch_repo: SqlAlchemyWatchlistRepository,
                   portfolio_service: PortfolioService, db_session: Session, since: Optional[str] = None) -> Dict[str, Any]:
    """Pestaña «Centro de mando» de la portada: el titular del día (y lo nuevo desde la última visita), «Requiere tu
    atención», el monitor de precios, las últimas noticias de las tesis, la agenda (cartera y resto), el resumen y la
    watchlist del día. since: última visita (fecha y hora local ISO, la guarda el navegador)."""
    pt = positions_tracking(repo, asset_repo, portfolio_service, db_session)
    settings, benchmark, error, stale_note, changes = pt["settings"], pt["benchmark"], pt["error"], pt["stale_note"], pt["changes"]
    weights, rows, signals, theses, open_tickers = pt["weights"], pt["rows"], pt["signals"], pt["theses"], pt["open_tickers"]
    txs, splits, row_tickers = pt["txs"], pt["splits"], pt["row_tickers"]

    # «Requiere tu atención»: lo accionable, ordenado por prioridad
    notices = thesis_review_service.compute_notices(repo, {} if changes.get("missing") else weights)
    prio = {"portfolio_risk": 0, "new_results": 1, "review_ready": 2, "decision_missing": 3, "decision_review": 4}
    attention = [{"prio": 4 if n.get("tracking") else prio.get(n["type"], 5), "ticker": n["ticker"], "text": n["text"], "href": f"/tesis/{n['thesis_id']}",
                  "type": n["type"]} for n in notices]
    # splits que Yahoo aplica a los precios pero no a la cartera (hasta reconstruirla) ni a lo anotado a mano
    first_buy = {}
    for tx in txs:
        if tx["ticker"] in open_tickers and (tx["ticker"] not in first_buy or tx["date"] < first_buy[tx["ticker"]]):
            first_buy[tx["ticker"]] = tx["date"]
    attention += split_service.portfolio_notices(first_buy, splits)
    manual = [(t, "precio de referencia de la tesis", th.get("reference_date"), f"/tesis/{th['id']}")
              for t, th in theses.items() if th.get("reference_price")]
    for t in row_tickers:
        bval = asset_repo.get_asset_data(t).get(base_valuation_service.ASSET_KEY) or {}
        if bval.get("metric_value"):
            manual.append((t, "valor de partida fijado a mano de la valoración", bval.get("last_updated"), f"/ticker/{t}#valoracion"))
        if (bval.get("anchor") or {}).get("value"):
            manual.append((t, f"valor corregido a mano del {bval['anchor']['year']} en la valoración", bval["anchor"].get("set_on"), f"/ticker/{t}#valoracion"))
    attention += split_service.manual_value_notices(manual)
    for n in repo.list_news(unread_only=True, limit=20):
        attention.append({"prio": 1 if n["materiality"] >= 5 else 3, "ticker": n["ticker"], "type": "news",
                          "text": f"Noticia {n['materiality']}/5: {n['title']}", "href": "/noticias", "ref": n["id"]})
    for s in signals:
        if s["kind"] in ("oportunidad", "cara", "concentración"):
            th = theses.get(s["ticker"])
            attention.append({"prio": 2, "ticker": s["ticker"], "type": s["kind"], "text": s["text"],
                              "href": f"/tesis/{th['id']}#valoracion" if th else "/portfolio"})
    from app.application.services.insider_service import signal_notices
    attention += signal_notices(sorted(open_tickers))   # compras de directivos en bolsa (cartera)
    from app.application.services import estimate_revisions_service
    attention += estimate_revisions_service.signal_notices(sorted(open_tickers))   # los analistas recortan previsiones
    attention += agenda_service.upcoming_results_notices(
        [{"ticker": r["ticker"], "date": r["next"]["date"], "thesis_id": r["thesis_id"]} for r in rows if r["next"] and r["next"].get("date")],
        open_tickers, datetime.date.today())
    attention.sort(key=lambda a: a["prio"])

    # vistos e histórico: lo que deja de aparecer no se borra
    monitor = sorted((s for s in signals if s["kind"] in ("anormal", "propio", "nivel")),
                     key=lambda s: (s.get("date") or "", s["ticker"]), reverse=True)
    no_data = {r["ticker"] for r in rows if not r["stats"]}   # Yahoo no devolvió su precio: sus señales siguen activas
    attention, monitor, signal_history = notice_service.track(notice_repo, "resumen", attention, monitor, no_data=no_data)
    return {
        "settings": settings, "error": error, "benchmark": benchmark, "stale_note": stale_note,
        "changes": changes, "signals": monitor, "signal_history": signal_history, "attention": attention,
        "held": sorted(open_tickers),
    }


@router.get("/portada/bandeja", response_class=HTMLResponse)
def home_inbox_fragment(request: Request, db_session: Session = Depends(get_db)):
    """Bandeja de hoy de la portada, con los avisos ya guardados (rápida). Se refresca cuando termina la sincronización."""
    return templates.TemplateResponse("_portada_bandeja.html", {"request": request, **centro_inbox(db_session),
                                                                "hidden": bandeja_hidden(db_session)})


BANDEJA_HIDDEN_KEY = "bandeja_hidden"   # {"day": "AAAA-MM-DD", "keys": ["grupo|TICKER", …]}: lo quitado con la ✕ hoy


def bandeja_hidden(db: Session) -> List[str]:
    """Lo que se ha quitado hoy de la Bandeja (grupo|empresa). En BBDD, no en el navegador: vale en cualquier navegador o
    dirección (localhost / 127.0.0.1) y no se pierde al recargar. Al día siguiente empieza limpia."""
    from app.infrastructure.db.models import DBSetting
    row = db.get(DBSetting, BANDEJA_HIDDEN_KEY)
    v = row.value if row and isinstance(row.value, dict) else {}
    return list(v.get("keys") or []) if v.get("day") == datetime.date.today().isoformat() else []


@router.post("/api/portada/bandeja/quitar")
def bandeja_hide(body: Dict[str, Any], db_session: Session = Depends(get_db)):
    """Quita líneas de la Bandeja de hoy ({keys: ["precio|ANET", …]}). Siguen en Avisos y sin descartar."""
    from app.infrastructure.db.models import DBSetting
    keys = sorted(set(bandeja_hidden(db_session)) | {str(k) for k in body.get("keys") or []})
    row = db_session.get(DBSetting, BANDEJA_HIDDEN_KEY)
    value = {"day": datetime.date.today().isoformat(), "keys": keys}
    if row:
        row.value = value
    else:
        db_session.add(DBSetting(key=BANDEJA_HIDDEN_KEY, value=value))
    db_session.commit()
    return {"keys": keys}


def agenda_weeks(results: List[Dict[str, Any]], dividends: Optional[List[Dict[str, Any]]] = None,
                 today: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
    """La agenda de 30 días por semanas (lunes a domingo): [{label «Esta semana» / «La próxima» / «En 2 semanas»,
    span «del lun 26 oct al dom 1 nov», items: [{ticker, date, kind, gross_eur}]}]."""
    weeks: Dict[datetime.date, List[Dict[str, Any]]] = {}
    for i in list(results) + list(dividends or []):
        d = i["date"] if isinstance(i["date"], datetime.date) else datetime.date.fromisoformat(str(i["date"])[:10])
        weeks.setdefault(d - datetime.timedelta(days=d.weekday()), []).append({**i, "date": d})
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    this_monday = (today or datetime.date.today()) - datetime.timedelta(days=(today or datetime.date.today()).weekday())
    out = []
    for monday in sorted(weeks):
        sunday = monday + datetime.timedelta(days=6)
        n = (monday - this_monday).days // 7   # 0 esta semana, 1 la próxima…
        label = "Esta semana" if n <= 0 else "La próxima" if n == 1 else f"En {n} semanas"
        span = f"del lun {monday.day} {meses[monday.month - 1]} al dom {sunday.day} {meses[sunday.month - 1]}"
        out.append({"label": label, "span": span, "items": sorted(weeks[monday], key=lambda x: x["date"])})
    return out


@router.get("/portada/agenda", response_class=HTMLResponse)
def home_agenda_fragment(request: Request,
                         repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository),
                         asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
                         watch_repo: SqlAlchemyWatchlistRepository = Depends(get_watchlist_repository),
                         portfolio_service: PortfolioService = Depends(get_portfolio_service),
                         db_session: Session = Depends(get_db)):
    """Agenda de la portada (cartera y resto, 7 y 30 días), aparte de la Bandeja y de la sincronización de avisos."""
    pt = positions_tracking(repo, asset_repo, portfolio_service, db_session)
    agenda = portfolio_agenda(pt, portfolio_service, asset_repo)
    other = other_agenda(pt["open_tickers"], pt["theses"], asset_repo, watch_repo, db_session)
    return templates.TemplateResponse("_portada_agenda.html", {
        "request": request, "agenda": agenda, "other": other,
        # 30 días, por semanas (tu cartera con los cobros; lo que sigues, resultados)
        "weeks_mine": agenda_weeks(agenda["mes"]["upcoming"], [d for d in agenda["mes"]["dividends"] if d.get("kind") != "ex"]),
        "weeks_other": agenda_weeks(other["mes"]["upcoming"]),
        "theses_ids": {t: th["id"] for t, th in pt["theses"].items()}})


def centro_inbox(db_session: Session) -> Dict[str, Any]:
    """Bandeja del Centro: los mismos avisos que la página Avisos (mismo leído), sin «Otros» ni noticias (las noticias
    van en su columna), por grupos. Se calcula después de sincronizar los avisos de esta carga."""
    from app.interfaces.views.alerts_views import notices_context
    ctx = notices_context(db_session)
    items = [i for i in ctx["items"] if i["filter"] != "otros"]
    return {"inbox": items, "inbox_filters": [f for f in ctx["filters"] if f["key"] != "otros"],
            "inbox_today": ctx["today"]}


@router.get("/api/noticias/semana")
def week_news_api(repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository), db_session: Session = Depends(get_db)):
    """Noticias de la barra de arriba (botón junto al calendario): las de esta semana del radar y, de antes, las que siguen
    sin leer; con el titular del resumen semanal. Sin IA: lo ya guardado."""
    c = centro_news(repo, db_session)
    week = next((w for w in c["news_weeks"] if w["current"]), None)
    pick = lambda n: {k: n.get(k) for k in ("id", "ticker", "date", "title", "url", "materiality", "seen")}
    items = [pick(n) for n in (week["news"] if week else [])]
    old = [pick(n) for n in c["news_old_unread"]]
    return {"headline": c["news_headline"], "week": items, "old_unread": old,
            "unread": sum(1 for n in items + old if not n["seen"])}


def centro_news(repo: SqlAlchemyThesisRepository, db_session: Session) -> Dict[str, Any]:
    """Noticias del Centro de mando: esta semana del radar y la anterior, separadas, y de antes solo las que siguen
    sin leer. Las antiguas ya leídas están en /noticias."""
    week = radar_week_start()
    since = (week - datetime.timedelta(days=7)).isoformat(timespec="seconds")
    summary = news_summary_service.load(db_session) or {}
    weeks = news_weeks(repo.list_news(since=since, limit=None))
    return {"news_week": week.date(), "news_weeks": weeks, "news_has_current": any(w["current"] for w in weeks),
            "news_old_unread": repo.list_news(unread_only=True, before=since, limit=None),
            # (los resúmenes anteriores al cambio guardan el lunes de la pasada: week_of los lleva a su sábado)
            "news_headline": summary.get("headline") if summary.get("week") and
            news_summary_service.week_of(summary["week"] + "T23:59:00") == week.date().isoformat() else None}


@router.get("/mercado/centro", response_class=HTMLResponse)
def mercado_centro_fragment(
    request: Request,
    repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository),
    notice_repo: SqlAlchemyNoticeRepository = Depends(get_notice_repository),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    watch_repo: SqlAlchemyWatchlistRepository = Depends(get_watchlist_repository),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    db_session: Session = Depends(get_db),
    since: Optional[str] = None,
):
    """Sincronización del Centro de mando: recalcula los avisos de la cartera (la pide la portada en segundo plano y
    Avisos al abrirse). Devuelve solo las notas; la Bandeja y la Agenda van en /portada/bandeja y /portada/agenda."""
    return templates.TemplateResponse("_mercado_centro.html", {
        "request": request, **centro_context(repo, notice_repo, asset_repo, watch_repo, portfolio_service, db_session, since)})


@router.get("/centro")
def command_center_page():
    # el Centro de mando es la mitad de arriba de la portada; la watchlist, las tesis y las noticias van en el menú
    return RedirectResponse("/mercado")
