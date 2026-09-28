"""
Radar semanal de noticias para todas las tesis (pensado para cron).

Solo consulta las tesis cuyo último radar tiene 7 días o más, así que puede ejecutarse a diario sin coste extra.
    python scripts/weekly_thesis_news.py            # tesis pendientes
    python scripts/weekly_thesis_news.py --force    # todas

Ejemplo de crontab (lunes 8:00):
    0 8 * * 1  cd /ruta/a/agent && .venv/bin/python scripts/weekly_thesis_news.py >> data/thesis_news.log 2>&1
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

from app.application.services import thesis_ai_service
from app.application.services.thesis_service import evaluate_thesis
from app.infrastructure.db.database import SessionLocal
from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository
from utils import llm_usage


def main(force: bool = False) -> None:
    load_dotenv()
    llm_usage.install()
    db = SessionLocal()
    try:
        repo = SqlAlchemyThesisRepository(db)
        for thesis in repo.list_theses():
            if not force and not thesis_ai_service.news_due(thesis["extra"].get("news_digest")):
                print(f"{thesis['ticker']}: radar al día, se omite")
                continue
            try:
                digest = thesis_ai_service.run_news_digest(thesis, evaluate_thesis(thesis))
            except Exception as e:
                print(f"{thesis['ticker']}: ERROR {e}")
                continue
            repo.set_extra(thesis["id"], "news_digest", digest)
            cost = (digest.get("usage") or {}).get("cost_usd")
            print(f"{thesis['ticker']}: {len(digest['items'])} noticias relevantes" + (f", coste ${cost:.4f}" if cost is not None else ""))
    finally:
        db.close()


if __name__ == "__main__":
    main(force="--force" in sys.argv)
