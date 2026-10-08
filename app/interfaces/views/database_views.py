"""Base de datos de empresas (/database), búsqueda y variación por periodo."""
import logging
import time
import datetime
import yfinance as yf
import pandas as pd
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db
from app.infrastructure.db.models import DBAsset, DBPortfolioItem
from app.infrastructure.dependencies import (
    get_asset_repository,
    get_settings_from_db,
)
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.templates import templates
from app.interfaces.views.market_views import INDEX_CACHE, ensure_prices_cached, get_currency_symbol, get_region_for_asset

from app.application.services import market_cap_service

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Database Views"])


# Caché de variación por periodo para la tabla de /database (1D sigue usando INDEX_CACHE, sin tocar)
PERIOD_CACHE = {}  # {period: {"data": {ticker: pct}, "update": timestamp}}
PERIOD_TTL = 3600  # 1 hora: estos periodos no cambian intradía salvo el propio precio actual
PERIOD_START_DATES = {
    "1w": lambda today: today - datetime.timedelta(days=7),
    "1m": lambda today: today - datetime.timedelta(days=30),
    "3m": lambda today: today - datetime.timedelta(days=91),
    "6m": lambda today: today - datetime.timedelta(days=182),
    "ytd": lambda today: datetime.date(today.year, 1, 1),
    "3y": lambda today: today - datetime.timedelta(days=3 * 365),
    "5y": lambda today: today - datetime.timedelta(days=5 * 365),
}

def get_period_variacion(tickers: list, period: str) -> dict:
    """Devuelve {ticker: pct_change} para un periodo (1w/1m/3m/6m/ytd/3y/5y), con caché TTL.
    No sustituye a INDEX_CACHE (1D): es un cálculo aparte sobre una ventana histórica más larga.
    """
    now = time.time()
    tickers = list(set([t for t in tickers if t and not t.startswith('^')]))
    if not tickers or period not in PERIOD_START_DATES:
        return {}

    cache_entry = PERIOD_CACHE.get(period, {"data": {}, "update": 0})
    stale = now - cache_entry["update"] > PERIOD_TTL or any(t not in cache_entry["data"] for t in tickers)
    if not stale:
        return cache_entry["data"]

    start_date = PERIOD_START_DATES[period](datetime.date.today())
    result = dict(cache_entry["data"])
    try:
        data = yf.download(tickers, start=start_date.isoformat(), progress=False)
        if not data.empty and 'Close' in data:
            closes = data['Close']
            if isinstance(closes, pd.Series):
                closes = pd.DataFrame({tickers[0]: closes})
            for t in closes.columns:
                try:
                    col = closes[t].dropna()
                    if len(col) >= 2:
                        first = float(col.iloc[0])
                        last = float(col.iloc[-1])
                        result[t] = ((last / first) - 1) * 100 if first > 0 else 0
                except Exception:
                    continue
        PERIOD_CACHE[period] = {"data": result, "update": now}
    except Exception as e:
        print(f"Error fetching period variacion ({period}): {e}")
    return result


@router.get("/database", response_class=HTMLResponse)
async def database_page(
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    db_session: Session = Depends(get_db)
):
    settings = get_settings_from_db(db_session)
    val_cfg = settings.get("valuation", {"ganga": 0.60, "barata": 0.85, "justo_max": 1.15, "cara_max": 1.40})
    session_indices = ["^GSPC", "^IXIC", "^IBEX", "^STOXX50E", "URTH"]
    dates_found = [INDEX_CACHE["data"][idx]["last_date"] for idx in session_indices if idx in INDEX_CACHE["data"]]
    latest_market_date = max(dates_found) if dates_found else datetime.date.today()

    active_portfolio_tickers = {
        p.ticker for p in db_session.query(DBPortfolioItem).filter(DBPortfolioItem.is_closed == False).all()
    }

    tickers = []
    assets = db_session.query(DBAsset).all()
    ensure_prices_cached([a.ticker for a in assets], asset_repo)
    # capitalización: los que aún no tienen acciones en circulación (o son viejas) se descargan en segundo plano
    market_cap_service.backfill_in_background([a.ticker for a in assets if market_cap_service.needs_refresh(a.data or {})])
    for asset in assets:
        t = asset.ticker
        db_d = asset.data or {}
        scores = {k: db_d.get(f"{k}_score", "-") for k in ["fin", "cap", "moat", "verdict"]}
        
        # Calcular Nota General dinámica como el promedio de todas las notas numéricas activas
        report_keys = [
            "fin", "cap", "moat", "verdict", "mgmt", "rules", "pe_forensic", "model", "thesis",
            "audit_income", "audit_balance", "audit_cashflow"
        ]
        
        # Calcular puntuación de Normas de Calidad si no existe una nota global de rules
        # Sumando los 5 pilares de calidad (cada uno sobre 2.0, total sobre 10.0)
        rules_s = db_d.get("rules_score", "-").split("/")[0].strip()
        if rules_s == "-":
            pilar_scores = []
            for i in range(1, 6):
                p_s = db_d.get(f"rules_pilar{i}_score", "-").split("/")[0].strip()
                if p_s != "-":
                    try:
                        pilar_scores.append(float(p_s))
                    except:
                        pass
            if len(pilar_scores) == 5:   # solo con los 5 pilares: una suma parcial no es una nota
                rules_s = f"{sum(pilar_scores):.1f}"

        valid_scores = []
        for k in report_keys:
            if k == "rules" and rules_s != "-":
                val_s = rules_s
            else:
                val_s = db_d.get(f"{k}_score", "-").split("/")[0].strip()
                
            if val_s != "-" and val_s != "N/A" and val_s != "":
                try:
                    valid_scores.append(float(val_s))
                except:
                    pass
                    
        general_score = "-"
        if valid_scores:
            avg_score = sum(valid_scores) / len(valid_scores)
            general_score = f"{avg_score:.1f}"

        # Crear tooltip con el desglose de las tres notas clave solicitadas por el usuario
        tooltip_parts = []
        for k, label in [("moat", "Franquicia y Moat"), ("fin", "Auditoría Financiera Forense"), ("cap", "Dirección y Capital Allocation")]:
            val_s = db_d.get(f"{k}_score", "-").split("/")[0].strip()
            if val_s != "-" and val_s != "N/A" and val_s != "":
                tooltip_parts.append(f"{label}: {val_s}")
                
        score_tooltip = " | ".join(tooltip_parts) if tooltip_parts else "Sin notas registradas"

        fpe_d = db_d.get("fair_pe_data", {})
        fpe, mpe = fpe_d.get("fair_forward_pe"), db_d.get("last_market_fwd_pe")
        val = {"status": "N/A", "fair": fpe or "-", "market": mpe or "-", "ratio": 999}
        if fpe and mpe:
            r = float(mpe)/float(fpe)
            val["ratio"] = r
            if r < val_cfg["ganga"]: val["status"] = "Ganga"
            elif r < val_cfg["barata"]: val["status"] = "Barato"
            elif r <= val_cfg["justo_max"]: val["status"] = "Precio Justo"
            elif r <= val_cfg["cara_max"]: val["status"] = "Caro"
            else: val["status"] = "Burbuja"
        
        m_data = {"price": 0, "change": 0, "pct": 0}
        if t in INDEX_CACHE["data"]:
            d = INDEX_CACHE["data"][t]
            p_price = d["price"]
            p_prev = d["prev"]
            p_last_date = d.get("last_date")
            today_date = datetime.date.today()
            is_weekday = today_date.weekday() < 5
            
            if is_weekday and p_last_date and p_last_date != today_date:
                p_prev = p_price
                
            change = p_price - p_prev
            pct = ((p_price / p_prev) - 1) * 100 if p_prev > 0 else 0
            m_data = {"price": p_price, "change": change, "pct": pct}

        db_status = db_d.get("status")
        if not db_status or db_status == "None" or db_status == "":
            db_status = "PENDING"

        tickers.append({
            "name": t, 
            "company_name": asset.company_name or "-", 
            "country": db_d.get("country", "-"), 
            "region": get_region_for_asset(t, db_d.get("country", "-")), 
            "sector": db_d.get("sector", "-"), 
            "subsector": db_d.get("industry", db_d.get("subsector", "-")),
            "currency_symbol": get_currency_symbol(db_d.get("currency", "USD"), t), 
            "market_data": m_data, 
            "status": db_status, 
            "scores": scores, 
            "general_score": general_score, 
            "score_tooltip": score_tooltip,
            "rules_score": rules_s if rules_s != "-" else "-",
            "valuation": val, 
            "is_favorite": asset.is_favorite,
            "is_portfolio": t in active_portfolio_tickers,
            "role": db_d.get("portfolio_role"),
            "mcap": market_cap_service.describe(db_d.get(market_cap_service.SHARES_KEY), m_data["price"] or None,
                                                db_d.get("currency"), INDEX_CACHE["data"]),
        })

    return templates.TemplateResponse("database.html", {
        "request": request, 
        "tickers": tickers, 
        "regions": sorted(list(set(t["region"] for t in tickers if t.get("region") and t["region"] != "-"))), 
        "sectores": sorted(list(set(t["sector"] for t in tickers if t.get("sector") and t["sector"] != "-"))), 
        "subsectores": sorted(list(set(t["subsector"] for t in tickers if t.get("subsector") and t["subsector"] != "-"))),
        "countries": sorted(list(set(t["country"] for t in tickers if t.get("country") and t["country"] != "-"))),
        "cap_tiers": [{"key": k, "name": n, "range": market_cap_service.TIER_RANGES[k]} for k, n, _ in market_cap_service.TIERS],
        "settings": settings
    })

@router.post("/search")
async def search(ticker: str = Form(...)):
    return RedirectResponse(url=f"/ticker/{ticker.upper()}", status_code=303)


@router.get("/api/database/variacion")
def api_database_variacion(period: str, db_session: Session = Depends(get_db)):
    """
    Variación de precio por periodo (1w/1m/3m/6m/ytd/3y/5y) para todos los tickers de /database.
    El 1D no pasa por aquí: sigue calculándose con INDEX_CACHE como hasta ahora.
    """
    if period not in PERIOD_START_DATES:
        return {"status": "error", "message": "Periodo no válido"}
    tickers = [row[0] for row in db_session.query(DBAsset.ticker).all()]
    data = get_period_variacion(tickers, period)
    return {"status": "success", "period": period, "data": data}
