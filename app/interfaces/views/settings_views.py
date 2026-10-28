from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db
from app.infrastructure.db.models import DBSetting
from app.infrastructure.dependencies import get_settings_from_db
from app.infrastructure.templates import templates

router = APIRouter(tags=["Settings"])

def save_settings_to_db(db: Session, settings: dict):
    for key, value in settings.items():
        db_setting = db.query(DBSetting).filter(DBSetting.key == key).first()
        if not db_setting:
            db_setting = DBSetting(key=key, value=value)
            db.add(db_setting)
        else:
            db_setting.value = value
    db.commit()

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    db_session: Session = Depends(get_db)
):
    settings = get_settings_from_db(db_session)
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings": settings
    })

def merge_sections(current: dict, incoming: dict) -> dict:
    """Cada apartado que llega se fusiona con el guardado: lo que el formulario no envía se conserva
    (p. ej. performance.start_date, que se fija desde Mi Portfolio y no desde Configuración)."""
    return {key: {**current[key], **value} if isinstance(value, dict) and isinstance(current.get(key), dict) else value
            for key, value in incoming.items()}


@router.post("/api/settings")
async def update_settings(
    settings: dict,
    db_session: Session = Depends(get_db)
):
    try:
        save_settings_to_db(db_session, merge_sections(get_settings_from_db(db_session), settings))
        return {"status": "success", "message": "Configuración guardada correctamente"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Uso de IA: costes y presupuesto ---

@router.get("/settings/ia")
def ai_usage_old_url(request: Request):
    """Antigua dirección (dentro de Configuración): enlaces guardados y avisos ya enviados."""
    q = request.url.query
    return RedirectResponse("/ia" + (f"?{q}" if q else ""), status_code=301)


@router.get("/ia", response_class=HTMLResponse)
def ai_usage_page(request: Request, periodo: str = "mes", db_session: Session = Depends(get_db)):
    from app.application.services import ai_usage_service
    row = db_session.get(DBSetting, ai_usage_service.BUDGET_KEY)
    return templates.TemplateResponse("settings_ai.html", {
        "request": request,
        "settings": get_settings_from_db(db_session),
        "u": ai_usage_service.dashboard(row.value if row else {}, periodo),
    })


class AIBudget(BaseModel):
    monthly_usd: float = Field(ge=0)
    alert_pct: float = Field(default=80, ge=1, le=100)


@router.post("/api/settings/ai-budget")
def save_ai_budget(payload: AIBudget, db_session: Session = Depends(get_db)):
    """Presupuesto mensual de IA en USD (0 = sin presupuesto) y % al que avisa."""
    from app.application.services import ai_usage_service
    save_settings_to_db(db_session, {ai_usage_service.BUDGET_KEY: payload.model_dump()})
    return {"status": "success"}


@router.get("/api/backup/status")
def backup_status():
    """Copias de seguridad (Ajustes): última, cuántas hay y dónde."""
    from app.application.services import backup_service
    return backup_service.status()


@router.post("/api/backup/run")
def backup_run():
    """Hace una copia completa ahora (también la duplica en BACKUP_EXTRA_DIR, si está configurada)."""
    from app.application.services import backup_service
    try:
        m = backup_service.create_backup(extra=backup_service.extra_dir())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo hacer la copia: {e}")
    return {"status": "success", "file": m["file"], "bytes": m["bytes"], "extra_error": m.get("extra_error")}
