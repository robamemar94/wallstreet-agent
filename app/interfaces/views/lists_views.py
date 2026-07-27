from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session

from app.infrastructure.db.database import get_db
from app.infrastructure.templates import templates
from app.infrastructure.dependencies import get_asset_repository, get_settings_from_db
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.interfaces.views.market_views import INDEX_CACHE, get_currency_symbol, get_region_for_asset, ensure_prices_cached

router = APIRouter(tags=["Company Lists"])

# Schemas para la API de Listas
class ListCreate(BaseModel):
    name: str
    description: Optional[str] = None

class ListUpdate(BaseModel):
    name: str
    description: Optional[str] = None

class TickerPayload(BaseModel):
    ticker: str

@router.get("/lists", response_class=HTMLResponse)
async def lists_page(
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    db_session: Session = Depends(get_db)
):
    # 1. Obtener todas las listas personalizadas de la DB
    custom_lists = asset_repo.get_company_lists()
    
    # Asegurar que todas las cotizaciones de los tickers en las listas estén cargadas y actualizadas en caché
    all_list_tickers = list(set([a.ticker for clist in custom_lists for a in clist.assets]))
    ensure_prices_cached(all_list_tickers, asset_repo)
    
    # 2. Preparar los datos con precios en tiempo real para el template
    lists_data = []
    for clist in custom_lists:
        assets_data = []
        for asset in clist.assets:
            # Datos fundamentales guardados
            db_d = asset_repo.get_asset_data(asset.ticker)
            
            # Obtener datos de precio en tiempo real (de INDEX_CACHE si existen)
            p_price = 0.0
            p_prev = 0.0
            pct = 0.0
            change = 0.0
            
            if asset.ticker in INDEX_CACHE["data"]:
                d = INDEX_CACHE["data"][asset.ticker]
                p_price = d["price"]
                p_prev = d["prev"]
                change = p_price - p_prev
                pct = ((p_price / p_prev) - 1) * 100 if p_prev > 0 else 0
            
            assets_data.append({
                "ticker": asset.ticker,
                "company_name": db_d.get("company_name", asset.company_name or "-"),
                "price": p_price,
                "pct": pct,
                "change_abs": change,
                "symbol": get_currency_symbol(db_d.get("currency", "USD"), asset.ticker),
                "region": get_region_for_asset(asset.ticker, db_d.get("country", "-")),
                "status": db_d.get("status") or "NONE",
                "is_favorite": asset.is_favorite
            })
            
        lists_data.append({
            "id": clist.id,
            "name": clist.name,
            "description": clist.description or "",
            "assets": assets_data
        })
        
    return templates.TemplateResponse("lists.html", {
        "request": request,
        "lists": lists_data,
        "settings": get_settings_from_db(db_session)
    })

# API: Crear lista
@router.post("/api/lists")
async def create_list(
    payload: ListCreate,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="El nombre de la lista no puede estar vacío")
    try:
        new_list = asset_repo.create_company_list(payload.name, payload.description)
        return {
            "status": "success",
            "message": "Lista creada correctamente",
            "list": {
                "id": new_list.id,
                "name": new_list.name,
                "description": new_list.description
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API: Eliminar lista
@router.delete("/api/lists/{list_id}")
async def delete_list(
    list_id: int,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    success = asset_repo.delete_company_list(list_id)
    if not success:
        raise HTTPException(status_code=404, detail="La lista especificada no existe")
    return {"status": "success", "message": "Lista eliminada correctamente"}

# API: Editar / Actualizar lista
@router.put("/api/lists/{list_id}")
async def update_list(
    list_id: int,
    payload: ListUpdate,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="El nombre de la lista no puede estar vacío")
    try:
        updated = asset_repo.update_company_list(list_id, payload.name, payload.description)
        if not updated:
            raise HTTPException(status_code=404, detail="La lista especificada no existe")
        return {
            "status": "success",
            "message": "Lista actualizada correctamente",
            "list": {
                "id": updated.id,
                "name": updated.name,
                "description": updated.description
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API: Añadir ticker a lista
@router.post("/api/lists/{list_id}/add")
async def add_ticker_to_list(
    list_id: int,
    payload: TickerPayload,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    ticker_upper = payload.ticker.strip().upper()
    success = asset_repo.add_ticker_to_list(list_id, ticker_upper)
    if not success:
        raise HTTPException(status_code=400, detail="No se pudo añadir el ticker a la lista (¿ya existe o el ticker no es válido?)")
    return {"status": "success", "message": f"Ticker {ticker_upper} añadido correctamente"}

# API: Quitar ticker de lista
@router.post("/api/lists/{list_id}/remove")
async def remove_ticker_from_list(
    list_id: int,
    payload: TickerPayload,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    ticker_upper = payload.ticker.strip().upper()
    success = asset_repo.remove_ticker_from_list(list_id, ticker_upper)
    if not success:
        raise HTTPException(status_code=400, detail="No se pudo eliminar el ticker de la lista")
    return {"status": "success", "message": f"Ticker {ticker_upper} eliminado correctamente"}

# API: Consultar listas de un ticker
@router.get("/api/ticker/{ticker}/lists")
async def get_ticker_lists(
    ticker: str,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    ticker_upper = ticker.strip().upper()
    all_lists = asset_repo.get_company_lists()
    ticker_lists = asset_repo.get_lists_for_ticker(ticker_upper)
    
    ticker_list_ids = {l.id for l in ticker_lists}
    
    result = []
    for clist in all_lists:
        result.append({
            "id": clist.id,
            "name": clist.name,
            "description": clist.description,
            "is_member": clist.id in ticker_list_ids
        })
        
    return result
