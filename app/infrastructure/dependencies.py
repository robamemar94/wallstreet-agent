from fastapi import Depends
from sqlalchemy.orm import Session

from app.infrastructure.db.database import get_db
from app.infrastructure.db.models import DBSetting
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.repositories.sqlalchemy_portfolio_repository import SqlAlchemyPortfolioRepository
from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository
from app.infrastructure.repositories.sqlalchemy_watchlist_repository import SqlAlchemyWatchlistRepository
from app.infrastructure.repositories.sqlalchemy_notice_repository import SqlAlchemyNoticeRepository
from app.application.services import broker_fees
from app.application.services.portfolio_service import PortfolioService
from app.application.services.performance_service import PerformanceService

def _default_quality_rules() -> list:
    """Las 5 Normas de Calidad de serie (config/quality_rules.yaml); las del usuario se guardan en la BD."""
    import os
    import yaml
    path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "quality_rules.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


DEFAULT_SETTINGS = {
    "ui": {"items_per_page": 10, "dark_mode": False},
    "valuation": {"ganga": 0.60, "barata": 0.85, "justo_max": 1.15, "cara_max": 1.40},
    "labels": {
        "ganga": "Ganga",
        "barata": "Barato",
        "justo": "Precio Justo",
        "cara": "Caro",
        "burbuja": "Burbuja"
    },
    "performance": {"benchmark_ticker": "URTH", "cache_duration_min": 60, "start_date": None},
    "alerts": {"price_drop_threshold": 10},
    "brokers": dict(broker_fees.DEFAULTS),   # tarifa escalonada de IBKR; ver broker_fees
    "custom_evaluation_rules": _default_quality_rules(),
}


def get_settings_from_db(db: Session) -> dict:
    db_settings = {row.key: row.value for row in db.query(DBSetting).all()}
    if not db_settings:
        return DEFAULT_SETTINGS
    # Combinar los valores por defecto con los de la base de datos para asegurar compatibilidad
    merged = DEFAULT_SETTINGS.copy()
    merged.update(db_settings)
    
    # Paso de migración/seguridad: si custom_evaluation_rules es un string en la DB o es la lista antigua de 11 normas, migrarlo
    custom_rules = merged.get("custom_evaluation_rules")
    if isinstance(custom_rules, str):
        if "Norma 1:" in custom_rules or "Norma 2:" in custom_rules or "Norma 3:" in custom_rules:
            # Es el string por defecto anterior; lo migramos automáticamente al nuevo array de las 5 normas
            merged["custom_evaluation_rules"] = DEFAULT_SETTINGS["custom_evaluation_rules"]
        else:
            merged["custom_evaluation_rules"] = [
                {
                    "title": "Mis Normas de Calidad (Migradas)",
                    "content": custom_rules
                }
            ]
    elif isinstance(custom_rules, list):
        # Migración automática si contiene las 11 normas antiguas o las 6 iniciales (con Paso 0)
        is_legacy = False
        if len(custom_rules) == 11 or len(custom_rules) == 6:
            is_legacy = True
        elif len(custom_rules) > 0 and isinstance(custom_rules[0], dict):
            first_title = custom_rules[0].get("title", "")
            if "Norma 1:" in first_title or "Moat & Bypass Test" in first_title or "Paso 0:" in first_title:
                is_legacy = True
        
        if is_legacy:
            merged["custom_evaluation_rules"] = DEFAULT_SETTINGS["custom_evaluation_rules"]
            
    return merged


def get_asset_repository(db: Session = Depends(get_db)) -> SqlAlchemyAssetRepository:
    return SqlAlchemyAssetRepository(db)


def get_portfolio_service(db: Session = Depends(get_db)) -> PortfolioService:
    return PortfolioService(SqlAlchemyPortfolioRepository(db))


def get_performance_service(
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
) -> PerformanceService:
    return PerformanceService(portfolio_service)


def get_thesis_repository(db: Session = Depends(get_db)) -> SqlAlchemyThesisRepository:
    return SqlAlchemyThesisRepository(db)


def get_watchlist_repository(db: Session = Depends(get_db)) -> SqlAlchemyWatchlistRepository:
    return SqlAlchemyWatchlistRepository(db)


def get_notice_repository(db: Session = Depends(get_db)) -> SqlAlchemyNoticeRepository:
    return SqlAlchemyNoticeRepository(db)
