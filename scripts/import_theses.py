"""
Importa (o actualiza) tesis de inversión desde sus ficheros YAML.

Uso:
    python scripts/import_theses.py                       # todas las de config/theses/
    python scripts/import_theses.py config/theses/nvda.yaml

Es idempotente: pilares y KPIs se emparejan por 'key', así que editar el YAML y reimportar
actualiza la definición sin perder las observaciones registradas desde la web.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.infrastructure.db.database import SessionLocal
from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository
from app.application.services.thesis_service import load_thesis_yaml, evaluate_thesis


def main(paths):
    db = SessionLocal()
    try:
        repo = SqlAlchemyThesisRepository(db)
        for path in paths:
            thesis = repo.import_spec(load_thesis_yaml(path))
            ev = evaluate_thesis(thesis)
            n_kpis = sum(len(p["kpis"]) for p in thesis["pillars"])
            print(f"{thesis['ticker']}: {len(thesis['pillars'])} pilares, {n_kpis} KPIs, "
                  f"score {ev['health_score']}, cobertura {ev['coverage']}%  <- {path}")
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:] or sorted(glob.glob("config/theses/*.yaml")))
