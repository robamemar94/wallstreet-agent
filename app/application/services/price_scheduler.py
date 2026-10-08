"""
Actualización de precios en segundo plano siguiendo el horario de los mercados.

- Apertura: Europa a las 9:05 (Yahoo da los precios europeos con ~15 min de retraso: en el primer minuto aún no hay
  nada nuevo) y EE. UU. a las 9:31 de Nueva York (precios en tiempo real: basta con que haya habido operaciones).
- Durante la sesión: cada INTERVAL_MIN minutos mientras algún mercado esté abierto.
- Cierre: Europa a las 17:40 (tras la subasta de cierre) y EE. UU. a las 16:05, para guardar el cierre bueno.
- Noches y fines de semana: nada. Los festivos no se tienen en cuenta (Yahoo devuelve lo mismo y no pasa nada).

Así, al abrir Mercado, Cartera o Watchlist los precios ya están frescos, y las alertas de precio saltan aunque no se
esté mirando la app. Solo funciona mientras el servidor esté en marcha.
"""
import datetime
import logging
import threading
import time
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

INTERVAL_MIN = 5
CHECK_SECONDS = 30
RETRY_SECONDS = 120   # si Yahoo falla, no reintentar en cada vuelta
# (zona horaria, ventana de la sesión en la que se refresca cada INTERVAL_MIN, momentos fijos de apertura y cierre)
MARKETS = {
    "Europa": (ZoneInfo("Europe/Madrid"), (datetime.time(9, 5), datetime.time(17, 40)), (datetime.time(9, 5), datetime.time(17, 40))),
    "EE. UU.": (ZoneInfo("America/New_York"), (datetime.time(9, 31), datetime.time(16, 5)), (datetime.time(9, 31), datetime.time(16, 5))),
}


def _last_fixed_moment(now: datetime.datetime):
    """Última apertura o cierre ya pasado (de hoy o de los días hábiles anteriores), con su nombre."""
    best = None
    for name, (tz, (start, _), fixed) in MARKETS.items():
        local = now.astimezone(tz)
        for back in range(5):
            day = local.date() - datetime.timedelta(days=back)
            if day.weekday() >= 5:
                continue
            for t in fixed:
                moment = datetime.datetime.combine(day, t, tz)
                if moment <= local and (best is None or moment > best[0]):
                    best = (moment, f"{name} {'apertura' if t == start else 'cierre'}")
    return best


def refresh_reason(now: datetime.datetime, last_update: float) -> str | None:
    """Motivo para descargar precios ahora (None si no toca). now con zona horaria; last_update en epoch (0 = nunca)."""
    # apertura y cierre: una vez, en cuanto se pasa la hora y la última descarga es anterior (también al arrancar
    # por la mañana: recoge el cierre de ayer)
    last = _last_fixed_moment(now)
    if last and last_update < last[0].timestamp():
        return last[1]
    for name, (tz, (start, end), _) in MARKETS.items():
        local = now.astimezone(tz)
        if local.weekday() < 5 and start <= local.time() <= end and now.timestamp() - last_update >= INTERVAL_MIN * 60:
            return f"{name} sesión"
    return None


def start_scheduler() -> None:
    """Hilo que cada CHECK_SECONDS mira si toca descargar precios (los de toda la app: cartera, watchlist, índices, FX)."""
    last_attempt = 0.0

    def tick():
        nonlocal last_attempt
        from app.interfaces.views import market_views as mv
        if not mv.INDEX_CACHE["data"]:
            mv._load_price_snapshot()   # tras un reinicio: la hora de la última descarga guardada
        reason = refresh_reason(datetime.datetime.now(datetime.timezone.utc), mv.INDEX_CACHE.get("prices_update", 0))
        if not reason or time.time() - last_attempt < RETRY_SECONDS:
            return
        last_attempt = time.time()
        from app.infrastructure.db.database import SessionLocal
        from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
        from app.infrastructure.repositories.sqlalchemy_portfolio_repository import SqlAlchemyPortfolioRepository
        db = SessionLocal()
        try:
            held = [p.ticker for p in SqlAlchemyPortfolioRepository(db).load_active_portfolio()]
            symbols, involved = mv.price_symbols(SqlAlchemyAssetRepository(db), held)
        finally:
            db.close()
        started = time.time()
        mv.refresh_prices_locked(symbols, involved)
        logger.info("Precios actualizados (%s): %d símbolos en %.1f s", reason, len(symbols), time.time() - started)

    def loop():
        while True:
            try:
                tick()
            except Exception as e:
                logger.warning("Actualización programada de precios fallida: %s", e)
            time.sleep(CHECK_SECONDS)
    threading.Thread(target=loop, daemon=True, name="price-scheduler").start()
