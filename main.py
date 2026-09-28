import logging
import uvicorn
from fastapi import FastAPI
from dotenv import load_dotenv

from app.interfaces.views.market_views import router as market_views_router
from app.interfaces.views.portfolio_views import router as portfolio_views_router
from app.interfaces.views.alerts_views import router as alerts_views_router
from app.interfaces.api.portfolio_api import router as portfolio_api_router
from app.interfaces.api.analysis_api import router as analysis_api_router
from app.interfaces.views.settings_views import router as settings_router
from app.interfaces.views.lists_views import router as lists_router
from app.interfaces.views.thesis_views import router as thesis_views_router
from app.interfaces.api.thesis_api import router as thesis_api_router
from app.infrastructure.db.database import SessionLocal
from app.infrastructure.dependencies import get_settings_from_db
from fastapi.staticfiles import StaticFiles
import os

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

app = FastAPI(title="Alpha-Flow")

# Mount static files
if not os.path.exists("static"):
    os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

if os.path.exists("logos_cache"):
    app.mount("/logos", StaticFiles(directory="logos_cache"), name="logos")


@app.middleware("http")
async def settings_middleware(request, call_next):
    db = SessionLocal()
    try:
        request.state.settings = get_settings_from_db(db)
    finally:
        db.close()
    return await call_next(request)


app.include_router(market_views_router)
app.include_router(portfolio_views_router)
app.include_router(alerts_views_router)
app.include_router(portfolio_api_router, prefix="/api")
app.include_router(analysis_api_router, prefix="/api")
app.include_router(settings_router)
app.include_router(lists_router)
app.include_router(thesis_views_router)
app.include_router(thesis_api_router, prefix="/api")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
