"""
Lanzamiento automático del radar semanal de noticias.

Se dispara al arrancar la app y al entrar en /tesis: las tesis cuyo último radar tiene 7 días o más se consultan
en un hilo en segundo plano (nunca dos a la vez). Desactivable con THESIS_NEWS_AUTO=false.
"""
import logging
import os
import threading

from app.application.services import thesis_ai_service
from app.application.services.thesis_service import evaluate_thesis
from app.infrastructure.db.database import SessionLocal
from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_running = False


def auto_enabled() -> bool:
    return os.getenv("THESIS_NEWS_AUTO", "true").lower() not in ("0", "false", "no", "off")


def is_running() -> bool:
    return _running


def run_news_for_thesis(repo: SqlAlchemyThesisRepository, thesis: dict) -> dict:
    """Consulta el radar de una tesis, guarda el resumen y añade las noticias nuevas a la sección Noticias."""
    ev = evaluate_thesis(thesis)
    digest = thesis_ai_service.run_news_digest(thesis, ev)
    digest["new_items"] = repo.add_news(thesis["id"], digest["items"], [p["name"] for p in ev["pillars"]])
    repo.set_extra(thesis["id"], "news_digest", digest)
    return digest


def _run_due() -> None:
    global _running
    db = SessionLocal()
    try:
        repo = SqlAlchemyThesisRepository(db)
        for thesis in repo.list_theses():
            if not thesis_ai_service.news_due(thesis["extra"].get("news_digest")):
                continue
            try:
                digest = run_news_for_thesis(repo, thesis)
                logger.info("Radar automático %s: %d noticias nuevas", thesis["ticker"], digest["new_items"])
            except Exception:
                db.rollback()
                logger.exception("Radar automático falló para %s", thesis["ticker"])
    finally:
        db.close()
        with _lock:
            _running = False


def trigger_due_news() -> bool:
    """Lanza en segundo plano el radar de las tesis pendientes. Devuelve True si se ha lanzado."""
    global _running
    if not auto_enabled():
        return False
    with _lock:
        if _running:
            return False
        _running = True
    threading.Thread(target=_run_due, name="thesis-news-radar", daemon=True).start()
    return True
