"""
Crecimiento de ventas que esperan los analistas para el año fiscal que viene (sin IA, yfinance cacheado 24 h).

Sale de `revenue_estimate` (fila «+1y», columna `growth`): ventas del año que viene frente al año en curso, en %.
La Base de datos solo mira la caché (`growth(t)`) y rellena en segundo plano lo que falta, para no frenar la página.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Optional

from app.application.services import market_cache

logger = logging.getLogger(__name__)

CACHE_KIND = "rev_growth_v1"
CACHE_TTL_H = 24
MAX_WORKERS = 4


def parse(revenue_estimate) -> Optional[float]:
    """De la tabla revenue_estimate de yfinance al crecimiento del año que viene, en % (None si no hay)."""
    if revenue_estimate is None or getattr(revenue_estimate, "empty", True) or "+1y" not in revenue_estimate.index:
        return None
    try:
        value = float(revenue_estimate.loc["+1y", "growth"])
    except (KeyError, TypeError, ValueError):
        return None
    return round(100 * value, 2) if value == value else None   # NaN → None


def _fetch(ticker: str) -> Optional[float]:
    try:
        import yfinance as yf
        return parse(yf.Ticker(ticker).revenue_estimate)
    except Exception:
        logger.debug("Sin previsión de ventas para %s", ticker, exc_info=True)
        return None


def growth(ticker: str, fetch: bool = False) -> Optional[float]:
    """Crecimiento esperado de ventas (%); fetch=False solo mira la caché."""
    if not fetch:
        return market_cache.peek(CACHE_KIND, ticker)
    return market_cache.cached(CACHE_KIND, ticker, lambda: _fetch(ticker), ttl_hours=CACHE_TTL_H)


def needs_refresh(ticker: str) -> bool:
    """Sin intentar nunca o con el intento de hace más de CACHE_TTL_H horas (un None también cuenta como intento)."""
    return time.time() - market_cache.fetched_at(CACHE_KIND, ticker) > CACHE_TTL_H * 3600


_lock = threading.Lock()
_running = False


def backfill_in_background(tickers: Iterable[str]) -> bool:
    """Pide en un hilo la previsión de los valores que no la tienen o está caducada. Una pasada a la vez."""
    global _running
    todo = sorted(t for t in set(tickers) if t and not t.startswith("^") and needs_refresh(t))
    if not todo:
        return False
    with _lock:
        if _running:
            return False
        _running = True

    def run():
        global _running
        try:
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                list(pool.map(lambda t: growth(t, fetch=True), todo))
            logger.info("Previsión de ventas de %d valores", len(todo))
        except Exception:
            logger.exception("Fallo al descargar las previsiones de ventas")
        finally:
            with _lock:
                _running = False

    threading.Thread(target=run, daemon=True, name="sales-estimates").start()
    return True
