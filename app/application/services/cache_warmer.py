"""Descarga en paralelo los datos lentos (FCF/BPA TTM, dividendo, fechas de resultados) para dejarlos en la caché en disco."""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

logger = logging.getLogger(__name__)
MAX_WORKERS = 8


def prefetch(earnings: Iterable[str] = (), fcf: Iterable[str] = (), valuation: Iterable[str] = (),
             splits: Iterable[str] = ()) -> None:
    """earnings: fechas de resultados · fcf: FCF/acción TTM · valuation: BPA TTM y dividendo (valoración base) · splits."""
    from app.application.services.base_valuation_service import _market_info
    from app.application.services.thesis_review_service import _earnings_info
    from app.application.services.thesis_service import fetch_ttm_fcf_per_share
    from app.application.services.split_service import fetch_splits
    jobs = ([(_earnings_info, t) for t in set(earnings)] + [(fetch_ttm_fcf_per_share, t) for t in set(fcf)]
            + [(_market_info, t) for t in set(valuation)] + [(fetch_splits, t) for t in set(splits)])
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        list(pool.map(lambda job: job[0](job[1]), jobs))


def warm_up_in_background() -> None:
    """Al arrancar: cartera, tesis y watchlist, para que la primera visita del día no espere a yfinance."""
    def run():
        from app.infrastructure.db.database import SessionLocal
        from app.infrastructure.db.models import DBPortfolioItem, DBThesis, DBWatchlistItem
        from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
        db = SessionLocal()
        try:
            held = [p.ticker for p in db.query(DBPortfolioItem).all() if (p.shares or 0) > 0]
            theses = [t.ticker for t in db.query(DBThesis).all()]
            watch = [w.ticker for w in db.query(DBWatchlistItem).all()] + list(SqlAlchemyAssetRepository(db).get_watch_candidates())
        finally:
            db.close()
        prefetch(earnings=held + theses + watch, fcf=held + theses + watch, valuation=held + watch, splits=held + theses + watch)
        logger.info("Caché de datos de mercado calentada (%d valores)", len(set(held + theses + watch)))
    threading.Thread(target=lambda: _safe(run), daemon=True, name="cache-warmer").start()


def _safe(fn) -> None:
    try:
        fn()
    except Exception:
        logger.exception("Fallo al calentar la caché de datos de mercado")
