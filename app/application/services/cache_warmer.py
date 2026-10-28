"""Descarga en paralelo los datos lentos (FCF/BPA TTM, dividendo y próximo dividendo anunciado, fechas de resultados, estimaciones de analistas,
máximo histórico) para dejarlos en la caché en disco. Al arrancar y luego cada REWARM_HOURS, para que no caduquen."""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

logger = logging.getLogger(__name__)
MAX_WORKERS = 8
REWARM_HOURS = 6   # por debajo de la caducidad de las cachés (12 h los datos de mercado, 24 h el máximo histórico)


def prefetch(earnings: Iterable[str] = (), fcf: Iterable[str] = (), valuation: Iterable[str] = (),
             splits: Iterable[str] = (), estimates: Iterable[str] = (), ath: Iterable[str] = (),
             insiders: Iterable[str] = ()) -> None:
    """earnings: fechas de resultados · fcf: FCF/acción TTM · valuation: BPA TTM y dividendo (valoración base) · splits
    · estimates: BPA estimado por los analistas y cómo cambia (previsiones) · ath: máximo histórico · insiders: directivos y acciones en circulación."""
    from app.application.services.insider_service import shares_series, splits_all, trades
    from app.application.services.base_valuation_service import _market_info, analyst_eps_estimates
    from app.application.services.estimate_revisions_service import revisions
    from app.application.services.sales_estimate_service import growth as sales_growth
    from app.application.services.price_monitor_service import all_time_high
    from app.application.services.thesis_review_service import _earnings_info
    from app.application.services.thesis_service import fetch_ttm_fcf_per_share
    from app.application.services.split_service import fetch_splits
    jobs = ([(_earnings_info, t) for t in set(earnings)] + [(fetch_ttm_fcf_per_share, t) for t in set(fcf)]
            + [(_market_info, t) for t in set(valuation)] + [(fetch_splits, t) for t in set(splits)]
            + [(analyst_eps_estimates, t) for t in set(estimates)] + [(revisions, t) for t in set(estimates)]
            + [(lambda t: sales_growth(t, fetch=True), t) for t in set(estimates)]
            + [(all_time_high, t) for t in set(ath)]
            + [(trades, t) for t in set(insiders)] + [(shares_series, t) for t in set(insiders)]
            + [(splits_all, t) for t in set(insiders)])
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        list(pool.map(lambda job: job[0](job[1]), jobs))


def warm_up_in_background() -> None:
    """Al arrancar y cada REWARM_HOURS: cartera, tesis y watchlist, para que ninguna visita espere a yfinance."""
    def run():
        from app.infrastructure.db.database import SessionLocal
        from app.infrastructure.db.models import DBAsset, DBPortfolioItem, DBThesis, DBWatchlistItem
        from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
        db = SessionLocal()
        try:
            held = [p.ticker for p in db.query(DBPortfolioItem).all() if (p.shares or 0) > 0]
            theses = [t.ticker for t in db.query(DBThesis).all()]
            watch = [w.ticker for w in db.query(DBWatchlistItem).all()] + list(SqlAlchemyAssetRepository(db).get_watch_candidates())
            # en seguimiento: sus resultados salen en la vista «Resto» de la agenda de Mercado
            following = [a.ticker for a in db.query(DBAsset).all() if (a.data or {}).get("status") == "ACCEPTED"]
        finally:
            db.close()
        prefetch(earnings=held + theses + watch + following, fcf=held + theses + watch, valuation=held + watch, splits=held + theses + watch,
                 estimates=held + watch, ath=held, insiders=held + watch)
        # próximo dividendo anunciado de la cartera (previsión de dividendos y agenda de Mercado)
        from app.interfaces.views.portfolio_views import portfolio_raw
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            list(pool.map(portfolio_raw, set(held)))
        logger.info("Caché de datos de mercado calentada (%d valores)", len(set(held + theses + watch)))

    def loop():
        while True:
            _safe(run)
            time.sleep(REWARM_HOURS * 3600)
    threading.Thread(target=loop, daemon=True, name="cache-warmer").start()


def _safe(fn) -> None:
    try:
        fn()
    except Exception:
        logger.exception("Fallo al calentar la caché de datos de mercado")
