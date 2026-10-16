from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from app.infrastructure.db.database import get_db
from app.infrastructure.templates import templates
from app.infrastructure.dependencies import get_asset_repository, get_portfolio_service, get_settings_from_db
from app.application.services.portfolio_service import PortfolioService
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.interfaces.views.market_views import INDEX_CACHE, get_currency_symbol, get_region_for_asset, ensure_prices_cached, price_decimals

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

def _parse_score(raw) -> Optional[float]:
    s = str(raw or "-").split("/")[0].strip()
    if s in ("-", "N/A", ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None

def _compute_media_score(db_d: dict) -> Optional[float]:
    # Normas de Calidad: nota global o suma de los 5 pilares (cada uno sobre 2.0)
    rules = _parse_score(db_d.get("rules_score"))
    if rules is None:
        pilares = [_parse_score(db_d.get(f"rules_pilar{i}_score")) for i in range(1, 6)]
        if all(p is not None for p in pilares):   # solo con los 5: una suma parcial no es una nota
            rules = round(sum(pilares), 1)
    verdict = _parse_score(db_d.get("verdict_score"))
    valid = [v for v in (rules, verdict) if v is not None]
    return sum(valid) / len(valid) if valid else None

@router.get("/lists", response_class=HTMLResponse)
def lists_page(
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    db_session: Session = Depends(get_db)
):
    """Listas: grupos de empresas parecidas. Solo entran en la watchlist las que sigues («Seguir en la watchlist»); el
    objetivo (cuántas posiciones y qué peso quieres) es opcional. Cada lista propone la mejor candidata para entrar."""
    from concurrent.futures import ThreadPoolExecutor
    from app.application.services import base_valuation_service as bv, tier_service
    from app.interfaces.views.thesis_views import portfolio_exposure
    from app.interfaces.views.watchlist_views import proximity
    exposure = portfolio_exposure(portfolio_service, asset_repo)["positions"]
    targets = tier_service.targets(db_session)
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
            not_open, last_date = False, None

            if asset.ticker in INDEX_CACHE["data"]:
                import datetime
                d = INDEX_CACHE["data"][asset.ticker]
                p_price = d["price"]
                p_prev = d["prev"]
                change = p_price - p_prev
                pct = ((p_price / p_prev) - 1) * 100 if p_prev > 0 else 0
                # su mercado aún no ha abierto hoy: es la variación de su última sesión (se enseña con stale_pill)
                today, last_date = datetime.date.today(), d.get("last_date")
                not_open = bool(today.weekday() < 5 and last_date and last_date != today)
            
            # Nota media de calidad (Normas + Veredicto), igual que en la tabla BBDD
            media = _compute_media_score(db_d)
            held = asset.ticker in exposure

            assets_data.append({
                "ticker": asset.ticker,
                "media": media,
                "company_name": db_d.get("company_name", asset.company_name or "-"),
                "price": p_price,
                "pct": pct,
                "change_abs": change, "not_open": not_open, "last_date": last_date,
                "symbol": get_currency_symbol(db_d.get("currency", "USD"), asset.ticker),
                "dec": price_decimals(db_d.get("currency")),
                "region": get_region_for_asset(asset.ticker, db_d.get("country", "-")),
                "country": db_d.get("country") or "-",
                "status": db_d.get("status") or "NONE",
                "is_favorite": asset.is_favorite,
                "tier": tier_service.tier_of(asset.is_favorite, db_d, held), "held": held,
                "weight": (exposure.get(asset.ticker) or {}).get("weight"), "_data": db_d,
            })
        # valoración base al precio de hoy (CAGR estimado y cuánto falta para el precio de compra), en paralelo
        def value(a):
            val = bv.live(a["ticker"], a.pop("_data"), a["price"]) if a["price"] else None
            group, drop = proximity(val, a["price"])
            a.update(cagr=(val or {}).get("cagr"), zone=(val or {}).get("zone"), drop_pct=drop, group=group)
            return a
        with ThreadPoolExecutor(max_workers=8) as pool:
            assets_data = list(pool.map(value, assets_data))
        meta = targets.get(str(clist.id)) or {}
        target = tier_service.objective(meta)
        held_rows = [a for a in assets_data if a["held"]]
        best = tier_service.best_candidate(assets_data)
            
        lists_data.append({
            "id": clist.id,
            "name": clist.name,
            "description": clist.description or "",
            "assets": assets_data,
            "target": target, "held_n": len(held_rows), "weight": sum(a["weight"] or 0 for a in held_rows),
            "best": best, "muted": bool(meta.get("muted")), "watch": bool(meta.get("watch")),
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


class ThemeTarget(BaseModel):
    n: Optional[int] = None          # posiciones que quieres en la lista
    weight: Optional[float] = None   # peso de la cartera (%)


@router.put("/api/lists/{list_id}/objetivo")
def set_theme_target(list_id: int, payload: ThemeTarget, db_session: Session = Depends(get_db)):
    """El objetivo (opcional) de una lista: cuántas posiciones y qué peso. Vacío: sin objetivo."""
    from app.application.services import tier_service
    return {"status": "success", "target": tier_service.set_target(db_session, list_id, payload.n, payload.weight)}


class ManyToTheme(BaseModel):
    tickers: list
    list_id: Optional[int] = None   # lista existente…
    new_name: Optional[str] = None  # …o uno nuevo con este nombre


@router.post("/api/temas/anadir")
def add_many_to_theme(payload: ManyToTheme, asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)):
    """Añade varias empresas a una lista (existente o nueva), p. ej. desde la selección múltiple de la base de datos."""
    list_id = payload.list_id
    if not list_id:
        name = (payload.new_name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Elige una lista o escribe el nombre de la nueva")
        from app.infrastructure.db.models import DBCompanyList
        if asset_repo.db.query(DBCompanyList).filter(DBCompanyList.name == name).first():
            raise HTTPException(status_code=400, detail=f"Ya existe una lista llamada «{name}»: elígela")
        list_id = asset_repo.create_company_list(name, None).id
    added = [t for t in {str(t).strip().upper() for t in payload.tickers if t} if asset_repo.add_ticker_to_list(list_id, t)]
    return {"status": "success", "list_id": list_id, "added": sorted(added)}


class ThemeWatch(BaseModel):
    watch: bool = True


@router.put("/api/lists/{list_id}/watchlist")
def watch_theme(list_id: int, payload: ThemeWatch, db_session: Session = Depends(get_db)):
    """Sigue (o no) una lista en la watchlist: sus empresas entran en ella (y en sus avisos)."""
    from app.application.services import tier_service
    return {"status": "success", "watch": tier_service.set_theme_watch(db_session, list_id, payload.watch)}


class ThemeMute(BaseModel):
    muted: bool = True


@router.put("/api/lists/{list_id}/silencio")
def mute_theme(list_id: int, payload: ThemeMute, db_session: Session = Depends(get_db)):
    """Silencia (o no) los avisos de las empresas de una lista."""
    from app.application.services import tier_service
    return {"status": "success", "muted": tier_service.set_theme_muted(db_session, list_id, payload.muted)}
