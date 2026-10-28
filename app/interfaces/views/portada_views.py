"""Portada (Mercado + Centro de mando): Mi Cartera, Bandeja de hoy, Agenda, noticias de la semana y notas del Centro."""
import datetime
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.application.services import agenda_service, base_valuation_service, cache_warmer, dividend_forecast, news_summary_service, notice_service, split_service, thesis_review_service
from app.application.services.thesis_ai_service import radar_week_start
from app.application.services.portfolio_service import PortfolioService
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
from app.interfaces.views.market_views import INDEX_CACHE, ensure_prices_cached, get_currency_symbol
from app.interfaces.views.thesis_views import news_weeks
from app.interfaces.views.seguimiento_views import chart_rows, history_symbols, live_day, positions_tracking

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Portada"])


def portfolio_agenda(pt: Dict[str, Any], portfolio_service: PortfolioService, asset_repo: SqlAlchemyAssetRepository) -> Dict[str, Any]:
    """Agenda de la cartera (Mercado): próximos resultados y dividendos, y resumen de los últimos 7 y 30 días."""
    from app.application.services.thesis_review_service import _earnings_info
    from app.interfaces.views.portfolio_views import portfolio_raw
    today = datetime.date.today()
    held = pt["open_tickers"]
    ensure_prices_cached(sorted(held), asset_repo)   # cotización y cambio de hoy para pasar los dividendos a euros
    prices = INDEX_CACHE["data"]
    forecast = dividend_forecast.portfolio_forecast(portfolio_service, asset_repo, prices, portfolio_raw, today)
    results = [{"ticker": r["ticker"], "name": r.get("name"), "date": r["next"]["date"], "thesis_id": r["thesis_id"], "held": True,
                "time": r["next"].get("time")}
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
        info = _earnings_info(t)
        nxt = results_calendar.next_results(t, asset_repo.get_asset_data(t).get("company_name") or t, info.get("next"), info.get("next_time"))
        if nxt and nxt.get("date"):
            results.append({"ticker": t, "date": nxt["date"][:10]})
    ensure_prices_cached(held, asset_repo)
    forecast = dividend_forecast.portfolio_forecast(portfolio_service, asset_repo, INDEX_CACHE["data"], portfolio_raw, today)
    period, periods, benchmark = None, {}, None
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
    # el selector 7 / 30 días / YTD / 1 año de la tarjeta «Mi Cartera»: lo ganado en cada periodo frente al índice
    out["periods"] = {k: {"pct": p.get("pct"), "abs_eur": p.get("abs_eur"), "benchmark_pct": p.get("benchmark_pct")}
                      for k, p in (periods or {}).items() if k in MC_PERIODS and p and p.get("pct") is not None}
    spark, since_start = portfolio_since_start(portfolio_service, db_session, benchmark)
    out["spark"] = spark
    if since_start:
        out["periods"]["max"] = since_start
    return out


MC_PERIODS = ("7d", "30d", "ytd", "1y")


def portfolio_since_start(portfolio_service: PortfolioService, db_session: Session,
                          benchmark: Optional[str]) -> tuple:
    """Del Rendimiento (su caché, 1 h), para la tarjeta «Mi Cartera»: la minigráfica (valor por participación, sin
    aportaciones ni retiradas; la tarjeta recorta el periodo elegido) y «Máx», lo mismo que «Desde el inicio» de
    Cartera, con lo ganado en € sin contar lo aportado."""
    from app.application.services.performance_service import PerformanceService, reference_benchmark
    try:
        start = (get_settings_from_db(db_session).get("performance") or {}).get("start_date")
        perf = PerformanceService(portfolio_service).get_performance_data(start)
    except Exception as e:
        logger.warning("Mi Cartera: sin datos del Rendimiento: %s", e)
        return None, None
    dates, uv = perf.get("dates") or [], perf.get("portfolio_uv") or []
    if len(dates) < 2 or len(uv) != len(dates):
        return None, None
    spark = {"dates": dates, "uv": [round(v, 4) for v in uv]}
    values, contributed = perf.get("portfolio_values_eur") or [], perf.get("contributed_eur") or []
    ref = (perf.get("benchmarks") or {}).get(reference_benchmark(benchmark)) or []
    since_start = {"pct": perf.get("total_return"), "since": dates[0],
                   "abs_eur": (values[-1] - contributed[-1]) - (values[0] - contributed[0]) if values and contributed else None,
                   "benchmark_pct": 100 * (ref[-1] / ref[0] - 1) if ref and ref[0] else None}
    return spark, since_start

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
    tier2 = {t for t, a in assets.items() if str((a.data or {}).get("watch_tier") or "") == "2"}
    tickers = sorted((favs | following | watch | set(theses)) - set(open_tickers))
    cache_warmer.prefetch(earnings=tickers)   # caché en disco de 12 h: solo descarga lo que falte
    results, meta, reported = [], {}, []
    for t in tickers:
        data, info = asset_repo.get_asset_data(t), _earnings_info(t)
        tid = (theses.get(t) or {}).get("id")
        last = info.get("last") or {}
        if last.get("date"):
            reported.append({"ticker": t, "date": last["date"], "surprise_pct": last.get("surprise_pct"), "thesis_id": tid})
        nxt = results_calendar.next_results(t, data.get("company_name") or t, info.get("next"), info.get("next_time"))
        # nivel: ordena el día (Tier 1 primero) y da el formato (Tier 1 destacado; Tier 2, Tier 3 y el resto, igual, en gris)
        tier = "T1" if t in favs else ("T2" if t in tier2 else ("T3" if t in following else None))
        if nxt and nxt.get("date"):
            results.append({"ticker": t, "name": data.get("company_name") or t, "date": nxt["date"][:10], "thesis_id": tid, "time": nxt.get("time"),
                            "tier": tier, "rank": 0 if tier == "T1" else 1})
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
        weeks.setdefault(d - datetime.timedelta(days=d.weekday()), []).append({**i, "date": d, "rank": i.get("rank", 0)})
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
    from app.application.services import results_calendar
    missing = results_calendar.pending(db_session, set(pt["open_tickers"]))
    return templates.TemplateResponse("_portada_agenda.html", {
        "request": request, "agenda": agenda, "other": other,
        # fechas por buscar con IA: el botón solo sale si hay varias cerca (una sola llamada para todas)
        "dates_pending": [t for t, _ in missing] if len(missing) >= results_calendar.MIN_BATCH else [],
        # 30 días, por semanas (tu cartera con los cobros; lo que sigues, resultados)
        "weeks_mine": agenda_weeks(agenda["mes"]["upcoming"], [d for d in agenda["mes"]["dividends"] if d.get("kind") != "ex"]),
        "weeks_other": agenda_weeks(other["mes"]["upcoming"]),
        "theses_ids": {t: th["id"] for t, th in pt["theses"].items()}})


@router.post("/api/agenda/fechas")
def agenda_find_dates(portfolio_service: PortfolioService = Depends(get_portfolio_service), db_session: Session = Depends(get_db)):
    """Busca con IA, en una sola llamada, las fechas de resultados que faltan (cartera y favoritas con su ciclo ya cerca)."""
    from app.application.services import results_calendar
    held = {i.ticker for i in portfolio_service.get_active_portfolio() if i.shares > 0}
    missing = results_calendar.pending(db_session, held)
    if len(missing) < results_calendar.MIN_BATCH:
        return {"searched": 0, "found": 0}
    try:
        return {"searched": len(missing), "found": results_calendar.refresh(missing)}
    except Exception as e:
        logger.warning("No se pudieron buscar las fechas de resultados: %s", e)
        raise HTTPException(status_code=502, detail="No se pudo consultar la IA")


RELEASE_LABEL = {"pre": "Antes de abrir", "time": "En sesión", "after": "Tras el cierre"}   # en este orden


def results_today(db_session: Session, today: Optional[datetime.date] = None) -> List[Dict[str, Any]]:   # [{label, tickers}]
    """Mensaje «Hoy presentan» de la Bandeja: las empresas en cartera o que sigues (favoritas, en seguimiento, watchlist o
    con tesis) con resultados hoy, cartera primero. Solo lo ya guardado (las mismas fechas que la Agenda): nunca descarga
    nada ni llama a la IA, para que la Bandeja siga siendo rápida. No es un aviso: no se marca leído ni se descarta."""
    from app.application.services import market_cache, results_calendar
    from app.infrastructure.db.models import DBAsset
    today = today or datetime.date.today()
    held = thesis_review_service.held_tickers(db_session)
    assets = db_session.query(DBAsset).all()
    theses = {t["ticker"]: t["id"] for t in SqlAlchemyThesisRepository(db_session).list_theses()}
    followed = ({a.ticker for a in assets if a.is_favorite or (a.data or {}).get("status") == "ACCEPTED"}
                | {i["ticker"] for i in SqlAlchemyWatchlistRepository(db_session).list_items()} | set(theses))
    out = []
    for t in sorted(held | followed):
        info = market_cache.peek("earnings_v4", t) or {}
        nxt = results_calendar.next_results(t, t, info.get("next"), info.get("next_time"))
        if not nxt or (nxt.get("date") or "")[:10] != today.isoformat():
            continue
        tm = nxt.get("time") or {}
        kind = tm.get("kind") if tm.get("kind") in RELEASE_LABEL else None
        out.append({"ticker": t, "held": t in held, "kind": kind, "madrid": tm.get("madrid") if kind else None,
                    "href": f"/tesis/{theses[t]}" if t in theses else f"/ticker/{t}"})
    # por momento de publicación (antes de abrir, en sesión, tras el cierre, sin hora); dentro, la cartera primero
    order = list(RELEASE_LABEL) + [None]
    groups = []
    for kind in order:
        items = sorted((r for r in out if r["kind"] == kind), key=lambda r: (not r["held"], r["ticker"]))
        if items:
            hours = sorted({r["madrid"] for r in items if r["madrid"]})
            label = RELEASE_LABEL.get(kind, "Sin hora")
            groups.append({"label": f"{label} ({', '.join(hours)})" if hours else label, "tickers": items})
    return groups


def centro_inbox(db_session: Session) -> Dict[str, Any]:
    """Bandeja del Centro: los mismos avisos que la página Avisos (mismo leído), sin «Otros» ni noticias (las noticias
    van en su columna), por grupos. Se calcula después de sincronizar los avisos de esta carga."""
    from app.interfaces.views.alerts_views import notices_context
    ctx = notices_context(db_session)
    items = [i for i in ctx["items"] if i["filter"] != "otros"]
    try:
        presenting = results_today(db_session)
    except Exception as e:   # el mensaje es un extra: si falla, la Bandeja sigue
        logger.warning("Bandeja: sin «Hoy presentan»: %s", e)
        presenting = []
    return {"inbox": items, "inbox_filters": [f for f in ctx["filters"] if f["key"] != "otros"],
            "inbox_today": ctx["today"], "results_today": presenting, "inbox_subs": bandeja_subs(items)}


_PCT = re.compile(r"([+-−]?\d+(?:[.,]\d+)?)\s?%")
_SIGMA = re.compile(r"(\d+(?:,\d+)?)σ")
_ZONE_TO = re.compile(r"Pasa de \w+ a (\w+)", re.I)


def bandeja_compact(i: Dict[str, Any]) -> Dict[str, Any]:
    """Para la Bandeja resumida: el subtipo del aviso (una línea por subtipo), su cifra clave, si es un movimiento
    raro para ese valor (σ ≥ 2) y si ya no se cumple. La frase completa sigue en el globo y en el detalle."""
    text, typ, kind = i.get("text") or "", i.get("type"), i.get("kind") or ""
    m = _PCT.search(text)
    num = float(m.group(1).replace("−", "-").replace(".", "").replace(",", ".")) if m else None
    sig = _SIGMA.search(text)
    sigma = float(sig.group(1).replace(",", ".")) if sig else None
    sub, label, metric = typ or kind, kind, ""
    if i.get("filter") == "precio":
        if typ == "anormal":
            day = "|1d" in (i.get("key") or "") or "en el día" in text
            sub, label = ("dia", "En el día") if day else ("5d", "En 5 sesiones")
            metric = (m.group(1) + " %") if m else ""
        elif typ == "nivel":
            sub, label = ("max52", "Máximo de 52 s") if "máximo" in text else ("min52", "Mínimo de 52 s")
        elif "desde el máximo" in text:
            sub, label = "lejos", "Lejos del máximo"
            num = -abs(num) if num is not None else None
            metric = f"−{m.group(1)} %" if m else ""
    elif i.get("filter") == "comprar" and _ZONE_TO.search(text):
        to = _ZONE_TO.search(text).group(1).lower()
        sub, label = "to_" + to, "Pasa a " + to
        cagr = re.search(r"CAGR estimado ([\d,]+%)", text)
        metric = cagr.group(1).replace("%", " %") if cagr else ""
        num = float(cagr.group(1)[:-1].replace(",", ".")) if cagr else 0.0
    elif m:
        metric = m.group(1) + " %"
    if metric and metric[0] not in "+-−" and i.get("filter") == "precio" and num is not None and num > 0:
        metric = "+" + metric
    metric = metric.replace("-", "−")
    direction = "down" if metric[:1] == "−" else ("up" if metric[:1] == "+" else "")
    return {"sub": sub or "otros", "label": label or "Otros", "metric": metric, "dir": direction,
            "size": abs(num) if num is not None else 0.0, "strong": bool(sigma and sigma >= 2),
            "stale": "ya no se cumple" in text}


def bandeja_subs(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Los avisos de cada grupo, por subtipo y empresa: {grupo: [{sub, label, chips: [{ticker, items, bj}]}]}.
    Dentro de cada subtipo, primero lo que baja (▼) y luego lo que sube (▲), de mayor a menor; lo que ya no se cumple,
    al final de su lado. Sin destacar ninguno: todo en una línea."""
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for i in items:
        bj = bandeja_compact(i)
        sub = out.setdefault(i["filter"], {}).setdefault(bj["sub"], {"sub": bj["sub"], "label": bj["label"], "chips": {}})
        chip = sub["chips"].setdefault(i.get("ticker") or i.get("kind") or "", {"ticker": i.get("ticker"), "items": [], "bj": bj})
        chip["items"].append(i)
    res = {}
    for g, subs in out.items():
        rows = []
        for sub in subs.values():
            chips = sorted(sub["chips"].values(), key=lambda c: ({"down": 0, "up": 1}.get(c["bj"]["dir"], 2), c["bj"]["stale"],
                                                                  -c["bj"]["size"]))
            rows.append({**sub, "chips": chips})
        res[g] = sorted(rows, key=lambda r: -max(c["bj"]["size"] for c in r["chips"]))
    return res


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
