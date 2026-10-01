import datetime
import logging
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.application.services import base_valuation_service, cache_warmer, news_summary_service, notice_service, split_service, thesis_news_runner, thesis_review_service
from app.application.services.thesis_ai_service import NEWS_INTERVAL_DAYS, NEWS_MIN_MATERIALITY, news_due
from app.application.services.portfolio_service import PortfolioService
from app.application.services.thesis_service import (
    compute_exposure, decision_outcome, evaluate_thesis, fetch_ttm_fcf_per_share, implied_cagr, live_valuation,
    render_document,
)
from app.infrastructure.db.database import get_db
from app.infrastructure.dependencies import (
    get_asset_repository, get_notice_repository, get_portfolio_service, get_settings_from_db, get_thesis_repository,
)
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.repositories.sqlalchemy_notice_repository import SqlAlchemyNoticeRepository
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
    # Radar semanal automático: consulta en segundo plano las tesis que llevan 7+ días sin radar
    thesis_news_runner.trigger_due_news()
    # Datos de mercado lentos de cartera, tesis y watchlist: se descargan ya para que la primera visita no espere
    cache_warmer.warm_up_in_background()

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
    cards = [{**_card(t, asset_repo), "position": positions.get(t["ticker"])} for t in theses]
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
    # noticias: todas o las de la empresa elegida; a la derecha, el resumen semanal o el del radar de esa empresa
    items = repo.list_news(thesis_id=selected["id"] if selected else None)
    unread_by_ticker = {}
    for n in repo.list_news(unread_only=True, limit=500):
        unread_by_ticker[n["ticker"]] = unread_by_ticker.get(n["ticker"], 0) + 1
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
        "summary": news_summary_service.load(db_session),
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


# --- Centro de mando ---

def positions_tracking(repo: SqlAlchemyThesisRepository, asset_repo: SqlAlchemyAssetRepository,
                       portfolio_service: PortfolioService, db_session: Session) -> Dict[str, Any]:
    """Seguimiento de las posiciones (y de las empresas con tesis): variación y contribución por periodo, peso,
    distancia al máximo de 52 semanas, CAGR de la valoración base, próximos resultados y señales de precio.
    Lo usan el Centro de mando (resumen y mapa) y la vista «Seguimiento» de la Cartera."""
    from app.application.services import price_monitor_service as pm, results_calendar
    from app.application.services.thesis_review_service import _earnings_info

    settings = get_settings_from_db(db_session)
    benchmark = (settings.get("performance") or {}).get("benchmark_ticker") or "URTH"
    txs, splits = pm.load_transactions(portfolio_service, db_session)
    theses = {t["ticker"]: t for t in repo.list_theses()}
    open_tickers = {i.ticker for i in portfolio_service.get_active_portfolio() if i.shares > 0}
    tickers = sorted({t["ticker"] for t in txs} | set(theses))
    currencies = {("GBP" if t.get("currency") == "GBp" else t.get("currency") or "USD") for t in txs} - {"EUR"}

    error, df = None, None
    try:
        df = pm.download_history(tickers + [benchmark] + [f"{c}EUR=X" for c in currencies])
    except Exception as e:
        logger.exception("Centro de mando: fallo al descargar precios")
        error = str(e)

    changes = pm.portfolio_changes(df, txs, splits, benchmark) if df is not None else {"periods": {}, "positions": {}, "total_eur": 0}
    weights = {t: p["weight"] for t, p in changes["positions"].items()}
    raw = pm.download_history(tickers + [benchmark] + [f"{c}EUR=X" for c in currencies], fill=False) if df is not None else None
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
    from app.interfaces.views.portfolio_views import day_change
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
    with ThreadPoolExecutor(max_workers=8) as pool:   # toda la serie de cada valor: una vez al día (caché en disco)
        aths = dict(zip([r["ticker"] for r in rows], pool.map(pm.all_time_high, [r["ticker"] for r in rows])))
    for r in rows:
        price = (INDEX_CACHE["data"].get(r["ticker"]) or {}).get("price") or (r["stats"] or {}).get("price")
        r["ath"], r["from_ath"] = aths.get(r["ticker"]), pm.from_all_time_high(aths.get(r["ticker"]), price)
        th = pt["theses"].get(r["ticker"])
        r["thesis_verdict"] = th.get("verdict") if th else None
    chart = chart_rows(rows)
    periods = live_day(chart, pt["changes"]["periods"], portfolio_service, asset_repo)
    return templates.TemplateResponse("_positions_tracking.html", {
        "request": request, "rows": rows, "error": pt["error"], "stale_note": pt["stale_note"],
        "periods": periods, "benchmark": pt["benchmark"], "chart": chart,
    })


@router.get("/centro", response_class=HTMLResponse)
def command_center_page(
    request: Request,
    repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository),
    notice_repo: SqlAlchemyNoticeRepository = Depends(get_notice_repository),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    db_session: Session = Depends(get_db),
):
    pt = positions_tracking(repo, asset_repo, portfolio_service, db_session)
    settings, benchmark, error, stale_note, changes = pt["settings"], pt["benchmark"], pt["error"], pt["stale_note"], pt["changes"]
    weights, rows, signals, theses, open_tickers = pt["weights"], pt["rows"], pt["signals"], pt["theses"], pt["open_tickers"]
    txs, splits, row_tickers = pt["txs"], pt["splits"], pt["row_tickers"]

    # «Requiere tu atención»: lo accionable, ordenado por prioridad
    notices = thesis_review_service.compute_notices(repo, {} if changes.get("missing") else weights)
    prio = {"portfolio_risk": 0, "new_results": 1, "review_ready": 2, "decision_missing": 3, "decision_review": 4}
    attention = [{"prio": prio.get(n["type"], 5), "ticker": n["ticker"], "text": n["text"], "href": f"/tesis/{n['thesis_id']}",
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
    attention.sort(key=lambda a: a["prio"])

    today = datetime.date.today().isoformat()
    upcoming = sorted(({"ticker": r["ticker"], "thesis_id": r["thesis_id"], **r["next"]} for r in rows
                       if r["next"] and r["next"]["date"] >= today), key=lambda u: u["date"])[:10]
    # vistos e histórico: lo que deja de aparecer no se borra
    monitor = sorted((s for s in signals if s["kind"] in ("anormal", "propio", "nivel")),
                     key=lambda s: (s.get("date") or "", s["ticker"]), reverse=True)
    no_data = {r["ticker"] for r in rows if not r["stats"]}   # Yahoo no devolvió su precio: sus señales siguen activas
    attention, monitor, signal_history = notice_service.track(notice_repo, "resumen", attention, monitor, no_data=no_data)
    return templates.TemplateResponse("command_center.html", {
        "request": request, "settings": settings, "error": error, "benchmark": benchmark, "stale_note": stale_note,
        "changes": changes, "rows": rows, "signals": monitor, "signal_history": signal_history,
        "attention": attention, "news": repo.list_news(limit=8), "upcoming": upcoming,
        "theses_ids": {t: th["id"] for t, th in theses.items()},
        "wl_target": base_valuation_service.TARGET_RETURN_PCT,
        "held": sorted(open_tickers),   # solo sus avisos funcionan como bandeja (leído / no leído)
    })
