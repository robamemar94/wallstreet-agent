import yfinance as yf
import pandas as pd
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.dependencies import get_asset_repository, get_settings_from_db
from app.infrastructure.templates import templates
from app.application.services.alerts_service import get_alerts_data, save_alerts_data, run_alerts_scan

router = APIRouter(tags=["Alerts Views"])

@router.get("/alerts", response_class=HTMLResponse)
async def alerts_page(
    request: Request,
    db_session: Session = Depends(get_db)
):
    alerts_data = get_alerts_data(db_session)
    history = alerts_data.get("history", [])
    
    # Sort history descending (newest first)
    history.sort(key=lambda x: x.get("date", ""), reverse=True)
    
    return templates.TemplateResponse("alerts.html", {
        "request": request,
        "history": history,
        "settings": get_settings_from_db(db_session)
    })

@router.post("/api/alerts/scan")
def scan_alerts(
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    db_session: Session = Depends(get_db)
):
    settings = get_settings_from_db(db_session)
    ticker_names = asset_repo.get_all_tickers()
    if not ticker_names:
        return RedirectResponse(url="/alerts", status_code=303)

    ticker_prices = {}
    try:
        data = yf.download(" ".join(ticker_names), period="1d", progress=False)
        if not data.empty and "Close" in data:
            closes = data["Close"]
            if isinstance(closes, pd.Series):
                ticker_prices[ticker_names[0]] = float(closes.iloc[-1])
            else:
                for t in ticker_names:
                    if t in closes:
                        v = closes[t].iloc[-1]
                        if pd.notna(v):
                            ticker_prices[t] = float(v)
    except Exception as e:
        print(f"Error fetching prices for alerts scan: {e}")

    run_alerts_scan(ticker_prices, asset_repo, db_session, settings)
    return RedirectResponse(url="/alerts", status_code=303)

@router.post("/api/alerts/delete")
def delete_alert(
    request: Request,
    ticker: str = Form(...),
    type: str = Form(...),
    date: str = Form(...),
    db_session: Session = Depends(get_db)
):
    alerts_data = get_alerts_data(db_session)
    history = alerts_data.get("history", [])
    
    new_history = []
    for alert in history:
        alert_type = alert.get("type", "VALORACIÓN")
        if alert.get("ticker") == ticker and alert_type == type and alert.get("date") == date:
            continue
        new_history.append(alert)
        
    alerts_data["history"] = new_history
    save_alerts_data(db_session, alerts_data)
    
    return RedirectResponse(url="/alerts", status_code=303)
