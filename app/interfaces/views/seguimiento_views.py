"""Seguimiento de las posiciones (Cartera → Posiciones → Seguimiento) y los históricos que comparte con la portada."""
import datetime
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.application.services import agenda_service, base_valuation_service, cache_warmer, thesis_review_service
from app.application.services.portfolio_service import PortfolioService
from app.application.services.thesis_service import (
    fetch_ttm_fcf_per_share, live_valuation,
)
from app.infrastructure.db.database import get_db
from app.infrastructure.dependencies import (
    get_asset_repository, get_portfolio_service, get_settings_from_db, get_thesis_repository,
)
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository
from app.infrastructure.templates import templates
from app.interfaces.views.market_views import INDEX_CACHE, ensure_prices_cached, get_currency_symbol, ext_quote, ext_day

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Seguimiento"])


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
