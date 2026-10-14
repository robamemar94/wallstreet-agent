"""
Lanzamiento automático del radar semanal de noticias.

Todas las tesis van juntas: una vez por semana, el sábado desde las 8:00 (repasa la semana hábil que acaba de cerrar). Se comprueba al arrancar la app,
cada hora mientras está encendida y al entrar en /tesis; las pendientes se consultan en un hilo en segundo plano
(nunca dos pasadas a la vez). Dentro de la pasada, las llamadas a la IA van en paralelo (THESIS_NEWS_WORKERS, 3 por
defecto) y lo que se guarda en la base de datos, en el hilo de la pasada según van acabando.
Desactivable con THESIS_NEWS_AUTO=false.
"""
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from app.application.services import thesis_ai_service
from app.application.services.thesis_service import evaluate_thesis
from app.infrastructure.db.database import SessionLocal
from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository

logger = logging.getLogger(__name__)

CHECK_EVERY_S = 3600

_lock = threading.Lock()
_running = False
_scheduler_started = False
_progress = {"done": 0, "total": 0}
_last_run = None   # {"finished_at", "ran", "new_items", "failed"}: para el aviso «radar terminado» de la web


def auto_enabled() -> bool:
    return os.getenv("THESIS_NEWS_AUTO", "true").lower() not in ("0", "false", "no", "off")


def is_running() -> bool:
    return _running


def workers() -> int:
    try:
        return max(1, int(os.getenv("THESIS_NEWS_WORKERS", "3")))
    except ValueError:
        return 3


def status() -> dict:
    """Estado del radar para la web: en curso (con cuántas van) y el resultado de la última pasada."""
    return {"radar_running": _running, "radar_progress": dict(_progress) if _running else None, "radar_last": _last_run}


def held_tickers(db) -> set:
    from app.application.services.thesis_review_service import held_tickers as held
    return held(db)


def fetch_digest(thesis: dict) -> tuple:
    """Solo la consulta a la IA (sin tocar la base de datos): se puede lanzar en paralelo."""
    ev = evaluate_thesis(thesis)
    return ev, thesis_ai_service.run_news_digest(thesis, ev)


def save_digest(repo: SqlAlchemyThesisRepository, thesis: dict, ev: dict, digest: dict) -> dict:
    """Guarda el resumen del radar y añade las noticias nuevas a la sección Noticias."""
    digest["new_items"] = repo.add_news(thesis["id"], digest["items"], [p["name"] for p in ev["pillars"]])
    repo.set_extra(thesis["id"], "news_digest", digest)
    return digest


def run_news_for_thesis(repo: SqlAlchemyThesisRepository, thesis: dict) -> dict:
    """Consulta el radar de una tesis, guarda el resumen y añade las noticias nuevas a la sección Noticias."""
    return save_digest(repo, thesis, *fetch_digest(thesis))


def _run_due() -> None:
    global _running, _last_run
    db = SessionLocal()
    ran, new_items, failed = 0, 0, set()
    try:
        repo = SqlAlchemyThesisRepository(db)
        held = held_tickers(db)
        due = [t for t in repo.list_theses() if radar_due(t, held)]
        _progress.update(done=0, total=len(due))
        with ThreadPoolExecutor(max_workers=workers(), thread_name_prefix="thesis-news") as pool:
            futures = {pool.submit(fetch_digest, t): t for t in due}
            for fut in as_completed(futures):
                thesis = futures[fut]
                try:
                    digest = save_digest(repo, thesis, *fut.result())
                    ran += 1
                    new_items += digest["new_items"]
                    logger.info("Radar automático %s: %d noticias nuevas", thesis["ticker"], digest["new_items"])
                except Exception:
                    db.rollback()
                    failed.add(thesis["ticker"])
                    logger.exception("Radar automático falló para %s", thesis["ticker"])
                _progress["done"] += 1
        # resumen semanal conjunto cuando ya no queda ninguna tesis pendiente (otra pasada en curso, p. ej. tras un
        # reinicio, lo lanzará al acabar; las que han fallado no lo bloquean) y si el último es de una semana anterior
        if ran and not any(radar_due(t, held) for t in repo.list_theses() if t["ticker"] not in failed):
            from app.application.services import news_summary_service
            if news_summary_service.due_after_radar(news_summary_service.load(db)):
                news_summary_service.generate_in_background()
    finally:
        db.close()
        if ran or failed:
            _last_run = {"finished_at": datetime.now().isoformat(timespec="seconds"), "ran": ran,
                         "new_items": new_items, "failed": sorted(failed)}
        with _lock:
            _running = False


def radar_due(thesis: dict, held: set) -> bool:
    """Toca radar si no se ha hecho desde el sábado y no es la semana de resultados (entonces lo cubre la revisión de resultados).
    Solo las tesis de cartera (held: empresas con acciones): las de seguimiento y las cerradas no tienen radar
    automático (se puede pedir a mano desde la tesis)."""
    from app.application.services.thesis_review_service import in_results_week
    from app.application.services.thesis_service import AUTO_RADAR_STATUSES
    if thesis["ticker"] not in held or (thesis.get("status") or "ACTIVA") not in AUTO_RADAR_STATUSES:
        return False
    if not thesis_ai_service.news_due(thesis["extra"].get("news_digest")):
        return False
    if in_results_week(thesis["ticker"]):
        logger.info("Radar de %s aplazado: semana de resultados", thesis["ticker"])
        return False
    return True


def any_due() -> bool:
    """¿Hay alguna tesis a la que le toque radar? (base de datos + calendario cacheado; sin llamar a la IA)"""
    db = SessionLocal()
    try:
        held = held_tickers(db)
        return any(radar_due(t, held) for t in SqlAlchemyThesisRepository(db).list_theses())
    finally:
        db.close()


def trigger_due_news() -> bool:
    """Lanza en segundo plano el radar de las tesis pendientes. Devuelve True si se ha lanzado.
    Si no hay ninguna pendiente no se lanza nada (y la web no muestra «radar en curso»)."""
    global _running
    if not auto_enabled() or _running:
        return False
    if not any_due():
        return False
    with _lock:
        if _running:
            return False
        _running = True
    threading.Thread(target=_run_due, name="thesis-news-radar", daemon=True).start()
    return True


def start_scheduler() -> None:
    """Comprueba ahora y cada hora si hay radar pendiente (el sábado desde las 8:00 aunque no se abra /tesis)."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def loop():
        while True:
            try:
                trigger_due_news()
            except Exception:
                logger.exception("Fallo al comprobar el radar semanal")
            time.sleep(CHECK_EVERY_S)
    threading.Thread(target=loop, daemon=True, name="thesis-news-scheduler").start()
