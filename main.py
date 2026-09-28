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
from utils import llm_usage
import json
import os

load_dotenv()
llm_usage.install()

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


@app.middleware("http")
async def llm_usage_middleware(request, call_next):
    # Contabiliza los tokens/coste de Gemini de cada petición y los expone en la cabecera X-LLM-Usage
    if request.url.path.startswith(("/static", "/logos")):
        return await call_next(request)
    with llm_usage.track_usage(f"{request.method} {request.url.path}") as tracker:
        response = await call_next(request)
    if tracker.result:
        response.headers["X-LLM-Usage"] = json.dumps(tracker.result)
    return response


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
