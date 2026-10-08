import logging
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.application.services import alerts_service, base_valuation_service as bv, notice_service, price_monitor_service as pm, split_service
from app.application.services.portfolio_service import PortfolioService
from app.application.services.thesis_review_service import fetch_reported_dates
from app.infrastructure.db.database import get_db
from app.infrastructure.dependencies import get_asset_repository, get_notice_repository, get_portfolio_service, get_settings_from_db, get_watchlist_repository
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.repositories.sqlalchemy_notice_repository import SqlAlchemyNoticeRepository
from app.infrastructure.repositories.sqlalchemy_watchlist_repository import SqlAlchemyWatchlistRepository
from app.infrastructure.templates import templates
from app.interfaces.views.market_views import INDEX_CACHE, ensure_prices_cached, get_currency_symbol
from utils.number_format import es

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Watchlist"])

NOTE_KEY = "watchlist_note"
# Cercanía al precio de compra: caída necesaria para llegar a él (como la antigua página de Oportunidades)
NEAR_PCT, WATCH_PCT = 15, 30
LIST_LIMIT = 6   # avisos visibles en cada lista del panel; el resto tras «Ver N más»


class WatchlistAdd(BaseModel):
    ticker: str
    note: Optional[str] = None


class WatchlistNote(BaseModel):
    note: Optional[str] = None


def _price(ticker: str) -> Optional[Dict[str, float]]:
    d = INDEX_CACHE["data"].get(ticker)
    return d if d and d.get("price") else None


def proximity(val: Optional[Dict[str, Any]], price: Optional[float]) -> tuple:
    """(grupo, caída necesaria en %) respecto al precio de compra de la valoración base."""
    if not val or not price:
        return "sin", None
    drop = round(100 * (1 - val["buy_price"] / price), 1)   # ≤0: ya cotiza por debajo
    return ("zona" if drop <= 0 else "cerca" if drop <= NEAR_PCT else "seguimiento" if drop <= WATCH_PCT else "lejos"), drop


def watchlist_rows(repo: SqlAlchemyWatchlistRepository, asset_repo: SqlAlchemyAssetRepository,
                   held: Set[str], benchmark: str) -> List[Dict[str, Any]]:
    """Watchlist = añadidas a mano + favoritas, con la valoración base de su ficha al precio de hoy.
    Un solo histórico de precios da el precio, la variación del día, la distancia al máximo de 52 semanas y el monitor."""
    manual = {i["ticker"]: i for i in repo.list_items()}
    auto = asset_repo.get_watch_candidates()
    tickers = sorted(set(manual) | set(auto))
    data = {t: asset_repo.get_asset_data(t) for t in tickers}
    try:
        df = pm.download_history(tickers + [benchmark]) if tickers else None
    except Exception:
        logger.exception("Watchlist: fallo al descargar el histórico de precios")
        df = None
    bench = df[benchmark] if df is not None and benchmark in df.columns else None
    raw = pm.download_history(tickers + [benchmark], fill=False) if df is not None else None

    today = date.today().isoformat()

    def row(t):
        d, item = data[t], manual.get(t) or {}
        # solo las sesiones de ese valor: antes de abrir su bolsa la «fila de hoy» es el cierre de ayer repetido
        st = pm.price_stats(raw[t], bench) if raw is not None and t in raw.columns else None
        price = st["price"] if st else None
        origins = [o for o, on in (("manual", t in manual), ("favorita", t in auto)) if on]
        val = bv.live(t, d, price)
        group, drop = proximity(val, price)
        stale = bv.staleness(val["inputs"], fetch_reported_dates(t)) if val else None
        # precio al añadirla, ajustado por los splits posteriores (Yahoo ya ajusta el de hoy)
        added_price = item.get("added_price")
        if added_price:
            added_price = added_price / split_service.factor_since(split_service.fetch_splits(t), item.get("added_at"))
        return {"ticker": t, "name": d.get("company_name") or t, "cur": get_currency_symbol(d.get("currency", "USD"), t),
                "price": price, "held": t in held, "origins": origins, "role": d.get("portfolio_role"), "note": d.get(NOTE_KEY) or "", "stats": st,
                # solo si su mercado ha abierto hoy; si no, la última sesión va en el título
                "day_pct": (st.get("change") or {}).get("1d") if st and st.get("date") == today else None,
                "last_session": st.get("date") if st else None, "last_day_pct": (st.get("change") or {}).get("1d") if st else None,
                "from_high_pct": st.get("from_high_pct") if st else None,
                "added_at": item.get("added_at"), "added_price": added_price,
                "val": val, "group": group, "drop_pct": drop, "stale": stale}

    with ThreadPoolExecutor(max_workers=8) as pool:   # la métrica TTM (si no está en caché) se descarga por empresa
        rows = list(pool.map(row, tickers))
    return sorted(rows, key=lambda r: (r["drop_pct"] is None, r["drop_pct"] if r["drop_pct"] is not None else 0))


@router.get("/watchlist", response_class=HTMLResponse)
def watchlist_page(request: Request, db_session: Session = Depends(get_db)):
    """Página de la Watchlist (la tabla se pide aparte a /watchlist/tabla para no frenar la página)."""
    return templates.TemplateResponse("watchlist.html", {
        "request": request, "settings": get_settings_from_db(db_session), "wl_target": bv.TARGET_RETURN_PCT})


def _names(rows: List[Dict[str, Any]], limit: int = 8) -> str:
    names = ", ".join(r["ticker"] for r in rows[:limit])
    return names + (f" y {len(rows) - limit} más" if len(rows) > limit else "")


def watchlist_attention(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Lo accionable de la watchlist (sin las que ya tienes en cartera), ordenado por prioridad."""
    rows = [r for r in rows if not r["held"]]
    fresh = [r for r in rows if r["val"] and not r["val"]["inputs"].get("legacy")]
    out = []
    for r in fresh:
        v = r["val"]
        cagr = f" · rentabilidad estimada {es(v['cagr'], 1)}%" if v["cagr"] is not None else ""
        if r["group"] == "zona":
            # la señal de compra sale de unos supuestos viejos: que se note antes de comprar
            old = " · valoración anticuada: revísala antes de comprar" if (r["stale"] or {}).get("stale") else ""
            out.append({"prio": 0, "type": "zona", "ticker": r["ticker"], "href": f"/ticker/{r['ticker']}#valoracion", "text": f"Por debajo de su precio de compra al {es(v['target'], 0)}% ({es(v['buy_price'], 2)}; cotiza a {es(r['price'], 2)}){cagr}{old}"})
        elif r["group"] == "cerca":
            out.append({"prio": 1, "type": "cerca", "ticker": r["ticker"], "href": f"/ticker/{r['ticker']}#valoracion", "text": f"A un {es(r['drop_pct'], 1)}% de su precio de compra ({es(v['buy_price'], 2)}){cagr}"})
    for r in fresh:   # el año corregido a mano ya tiene dato real: la corrección ha dejado de aplicarse
        a = r["val"]["anchor"]
        if a and a["state"] == "expired":
            label = bv.METRICS[r["val"]["metric"]]["label"]
            out.append({"prio": 2, "type": "anchor_expired", "ticker": r["ticker"], "ref": str(a["year"]), "href": f"/ticker/{r['ticker']}#valoracion",
                        "text": f"Tu {label} {a['year']} corregido ({es(a['value'], 2)}) ya ha pasado: el de los últimos 12 meses es {es(a['auto'], 2)} "
                                f"({es(100 * (a['auto'] / a['value'] - 1), 1, sign=True)}% frente a tu corrección). La corrección ya no se aplica: revisa el crecimiento"})
    legacy = [r for r in rows if r["val"] and r["val"]["inputs"].get("legacy")]
    if legacy:
        out.append({"prio": 3, "type": "legacy", "ticker": None, "label": f"{len(legacy)} empresas", "text": f"Valoración antigua sin revisar (no generan avisos): {_names(legacy)}"})
    missing = [r for r in rows if not r["val"] and r["price"]]
    if missing:
        out.append({"prio": 3, "type": "missing", "ticker": None, "label": f"{len(missing)} empresas", "text": f"Sin valoración base: {_names(missing)}"})
    return sorted(out, key=lambda a: a["prio"])


def watchlist_monitor(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Monitor de precios de las candidatas (las que tienes en cartera ya salen en el Resumen)."""
    signals = [sig for r in rows if not r["held"] and r["stats"] for sig in pm.price_signals(r["ticker"], r["stats"])]
    return sorted(signals, key=lambda s: (s.get("date") or "", s["ticker"]), reverse=True)


@router.get("/watchlist/tabla", response_class=HTMLResponse)
def watchlist_fragment(
    request: Request,
    repo: SqlAlchemyWatchlistRepository = Depends(get_watchlist_repository),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    db_session: Session = Depends(get_db),
    notice_repo: SqlAlchemyNoticeRepository = Depends(get_notice_repository),
):
    """Contenido de la página Watchlist: avisos, monitor y tabla (se carga aparte para no frenar la página)."""
    held = {i.ticker for i in portfolio_service.get_active_portfolio() if i.shares > 0}
    benchmark = (get_settings_from_db(db_session).get("performance") or {}).get("benchmark_ticker") or "URTH"
    rows = watchlist_rows(repo, asset_repo, held, benchmark)
    # alertas (cambios de zona y caídas) con los precios que ya tenemos, en segundo plano
    alerts_service.scan_in_background({r["ticker"]: r["price"] for r in rows if r["price"]}, {r["ticker"] for r in rows})
    attention, signals, history = notice_service.track(notice_repo, "watchlist", watchlist_attention(rows), watchlist_monitor(rows),
                                                       no_data={r["ticker"] for r in rows if not r["stats"]})
    return templates.TemplateResponse("_watchlist_table.html", {
        "request": request, "rows": rows, "attention": attention, "signals": signals, "signal_history": history,
        "seen_keys": [a["key"] for a in attention if a["seen"]], "dismissed_keys": notice_repo.dismissed_keys("watchlist"),
        "target": bv.TARGET_RETURN_PCT, "metrics": bv.METRICS, "near": NEAR_PCT, "watch": WATCH_PCT, "list_limit": LIST_LIMIT,
    })


@router.post("/api/watchlist")
def add_to_watchlist(
    payload: WatchlistAdd,
    repo: SqlAlchemyWatchlistRepository = Depends(get_watchlist_repository),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
):
    ticker = payload.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Indica un ticker")
    if repo.get_by_ticker(ticker):
        raise HTTPException(status_code=400, detail=f"{ticker} ya está en la watchlist")
    ensure_prices_cached([ticker], asset_repo)
    p = _price(ticker)
    if not p:
        raise HTTPException(status_code=400, detail=f"No hay cotización para {ticker}: revisa el ticker (p. ej. ASML.AS, LIFCO-B.ST)")
    if (payload.note or "").strip():
        asset_repo.save_asset_data(ticker, NOTE_KEY, payload.note.strip())
    return {"status": "success", "item": repo.add_item(ticker, p["price"])}


@router.put("/api/watchlist/{ticker}/note")
def update_watchlist_note(
    ticker: str,
    payload: WatchlistNote,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
):
    asset_repo.save_asset_data(ticker.upper(), NOTE_KEY, (payload.note or "").strip() or None)
    return {"status": "success"}


@router.delete("/api/watchlist/{ticker}")
def remove_from_watchlist(ticker: str, repo: SqlAlchemyWatchlistRepository = Depends(get_watchlist_repository)):
    """Quita la marca «a mano»; si es favorita sigue en la watchlist."""
    if not repo.delete_item(ticker.upper()):
        raise HTTPException(status_code=404, detail="No está añadida a mano en la watchlist")
    return {"status": "success"}


class NoticeSeen(BaseModel):
    keys: List[str]
    seen: bool = True


@router.post("/api/notices/seen")
def mark_notices_seen(payload: NoticeSeen, notice_repo: SqlAlchemyNoticeRepository = Depends(get_notice_repository),
                      db: Session = Depends(get_db)):
    """Marca (o desmarca) avisos como vistos; vuelven a salir como nuevos si desaparecen y reaparecen.
    Un aviso de noticia leído deja también la noticia como leída (campana, Noticias y Centro de mando)."""
    from app.application.services.notification_service import news_ids_in_notices
    from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository
    news_ids = news_ids_in_notices(payload.keys)
    if news_ids and payload.seen:
        SqlAlchemyThesisRepository(db).mark_news_seen(news_ids)
    return {"status": "success", "updated": notice_repo.set_seen(payload.keys, payload.seen)}


@router.post("/api/notices/dismiss")
def dismiss_notices(payload: NoticeSeen, notice_repo: SqlAlchemyNoticeRepository = Depends(get_notice_repository)):
    """Descarta avisos (borrado lógico): no vuelven a salir mientras dure el episodio. seen=false los recupera."""
    return {"status": "success", "updated": notice_repo.set_dismissed(payload.keys, payload.seen)}


@router.delete("/api/notices/{notice_id}")
def delete_notice(notice_id: int, notice_repo: SqlAlchemyNoticeRepository = Depends(get_notice_repository)):
    """Borra una señal del histórico (las activas no se pueden borrar: volverían a salir)."""
    if not notice_repo.delete_inactive(notice_id):
        raise HTTPException(status_code=404, detail="No está en el histórico")
    return {"status": "success"}


class NoticeClear(BaseModel):
    scope: str


@router.post("/api/notices/clear")
def clear_notice_history(payload: NoticeClear, notice_repo: SqlAlchemyNoticeRepository = Depends(get_notice_repository)):
    return {"status": "success", "deleted": notice_repo.clear_history(payload.scope, "signal")}

