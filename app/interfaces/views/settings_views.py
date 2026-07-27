from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
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

@router.post("/api/settings")
async def update_settings(
    settings: dict,
    db_session: Session = Depends(get_db)
):
    try:
        # Basic validation could be added here
        save_settings_to_db(db_session, settings)
        return {"status": "success", "message": "Configuración guardada correctamente"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
