"""
Pantalla Roboadvisor (MyInvestor), en pestañas: Resumen (cifras y evolución en € o en %, frente al índice y a la cartera
de acciones), Por meses, Fondos (detalle y exposición por zonas), Aportaciones y Ajustar. Los datos y las cuentas están en
`robo_service`; la API (aportación, ajustes) en `/api/portfolio/roboadvisor…`.
"""
import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.application.services import robo_service
from app.application.services.performance_service import PerformanceService
from app.infrastructure.db.database import get_db
from app.infrastructure.dependencies import get_portfolio_service, get_settings_from_db
from app.infrastructure.templates import templates

router = APIRouter()


@router.get("/roboadvisor", response_class=HTMLResponse)
def robo_page(request: Request, db_session: Session = Depends(get_db)):
    settings = get_settings_from_db(db_session)
    st = robo_service.status(db_session)
    charts, funds, bands = {}, {"funds": [], "zones": []}, []
    if st.get("configured"):
        # la cartera de acciones, sin el efecto de compras y ventas: el valor por participación del Rendimiento (en caché)
        perf = PerformanceService(get_portfolio_service(db_session)).get_performance_data(
            (settings.get("performance") or {}).get("start_date"))
        data = robo_service.load(db_session)
        charts = robo_service.compare(st["series"], data["annual"], perf.get("dates"), perf.get("portfolio_uv"))
        funds = robo_service.fund_details(data, st["value"])
        bands = robo_service.periods(data, st["series"], st["value"])
    return templates.TemplateResponse("roboadvisor.html", {
        "request": request, "settings": settings, "st": st, "charts": charts, "fd": funds, "bands": bands,
        "today": datetime.date.today().isoformat()})
