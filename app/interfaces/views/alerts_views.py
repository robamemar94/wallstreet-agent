import yfinance as yf
import pandas as pd
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.dependencies import get_asset_repository, get_settings_from_db
from app.infrastructure.templates import templates
from app.application.services.alerts_service import (
    get_alerts_data, save_alerts_data, run_alerts_scan, build_alert_key, purge_old,
)

router = APIRouter(tags=["Alerts Views"])

@router.get("/alerts")
def alerts_page():
    """La antigua página de alertas: su contenido (cambios de zona y caídas) está ahora en Avisos."""
    return RedirectResponse("/avisos", status_code=307)

@router.get("/api/alerts/keys")
def get_alert_keys(db_session: Session = Depends(get_db)):
    """Claves ligeras de todas las alertas activas, para que el badge de
    notificaciones del sidebar (presente en todas las páginas) pueda calcular
    cuántas quedan sin leer comparando contra localStorage."""
    alerts_data = get_alerts_data(db_session)
    history = alerts_data.get("history", [])
    return {
        "keys": [
            {"key": build_alert_key(a), "type": a.get("type", "VALORACIÓN"),
             "read": ("alert|" + build_alert_key(a)) in set(alerts_data.get("read") or [])}
            for a in history if not a.get("silent")   # caídas heredadas de la versión anterior: no cuentan como nuevas
        ]
    }

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
    ep = (alerts_data.get("drops") or {}).get(ticker)
    if type == "CAÍDA" and ep and ep.get("date") == date:   # caída abierta ignorada: no vuelve hasta que empeore otro 10 %
        ep["dismissed"] = True
    save_alerts_data(db_session, alerts_data)
    
    return RedirectResponse(url="/alerts", status_code=303)


@router.post("/api/alerts/clean")
def clean_alerts(months: int = Form(...), db_session: Session = Depends(get_db)):
    """Borra las alertas de más de N meses (las caídas abiertas se conservan: siguen activas)."""
    alerts_data = get_alerts_data(db_session)
    alerts_data["history"] = purge_old(alerts_data.get("history", []), max(1, months) * 30)
    save_alerts_data(db_session, alerts_data)
    return RedirectResponse(url="/alerts", status_code=303)



@router.get("/avisos", response_class=HTMLResponse)
def notices_page(request: Request, db_session: Session = Depends(get_db)):
    """Avisos: todas las notificaciones en un sitio (las mismas que la campana, con el mismo leído), por temas.
    Pendientes (lo que pide algo de ti) y Movimientos (lo que hace el precio); el historial de alertas está en /alerts.
    Al abrirse, la página recalcula en segundo plano los avisos del Centro y de la Watchlist (sin IA) y se refresca."""
    return templates.TemplateResponse("avisos.html", {"request": request, "settings": get_settings_from_db(db_session),
                                                      **notices_context(db_session)})


@router.get("/avisos/lista", response_class=HTMLResponse)
def notices_list_fragment(request: Request, db_session: Session = Depends(get_db)):
    """La lista de Avisos sola (para refrescarla tras recalcular los avisos sin recargar la página)."""
    return templates.TemplateResponse("_avisos_list.html", {"request": request, **notices_context(db_session)})


def _fold(text: str) -> str:
    """Para buscar: minúsculas y sin tildes («Hermès» → «hermes»)."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", (text or "").lower()) if unicodedata.category(c) != "Mn")


def group_by_company(items: list, grouped: set) -> list:
    """En los tipos que se repiten (zona de compra, cambios de zona, movimientos), una línea por empresa y tipo: la más
    reciente, con las anteriores en «older». Sin leer si lo está cualquiera; leer o descartar la línea afecta a todas."""
    out, heads = [], {}
    for i in items:   # vienen de la más reciente a la más antigua
        k = (i["filter"], i.get("ticker")) if i["filter"] in grouped and i.get("ticker") else None
        if k and k in heads:
            head = heads[k]
            head["older"].append(i)
            head["read"] = head["read"] and i["read"]
            if i.get("vigente") and not head.get("vigente"):   # el grupo sigue vigente si lo está cualquiera
                head.update(vigente=True, since=i.get("since"), rule=i.get("rule", ""))
            head["keys"].append(i["key"])
            if i["dismissable"]:
                head["dismiss_keys"].append(i["key"])
            continue
        i = {**i, "older": [], "keys": [i["key"]], "dismiss_keys": [i["key"]] if i["dismissable"] else []}
        if k:
            heads[k] = i
        out.append(i)
    for i in out:
        i["dismissable"] = bool(i["dismiss_keys"])
    return out


def notices_context(db_session: Session) -> dict:
    from app.application.services import notification_service as ns
    from app.infrastructure.db.models import DBAsset
    from app.infrastructure.dependencies import get_notice_repository, get_thesis_repository
    items = [i for i in ns.collect(db_session, get_thesis_repository(db_session), get_notice_repository(db_session))
             if i["group"] != "noticias"]   # las noticias sueltas no son avisos (van en el resumen semanal)
    tickers = {i["ticker"] for i in items if i.get("ticker")}
    names = {a.ticker: (a.data or {}).get("company_name") or a.company_name or ""
             for a in db_session.query(DBAsset).filter(DBAsset.ticker.in_(tickers)).all()}
    for i in items:
        i["filter"] = ns.filter_of(i)
        i["dismissable"] = i["group"] in ("avisos", "monitor")   # alertas y noticias: solo leer
        i["name"] = names.get(i.get("ticker") or "", "")
        i["rule"] = ns.vigente_rule(i) if i.get("vigente") else ""
        i["q"] = _fold(" ".join(x for x in (i.get("ticker") or "", i["name"], i["kind"], i["text"]) if x))   # buscar por nombre también
    items = group_by_company(items, ns.GROUPED_FILTERS)
    filters = [{"key": k, "label": label, "icon": icon, "on": on} for k, label, icon, on in ns.FILTERS]
    import datetime
    today = datetime.date.today()
    day = lambda n: (today - datetime.timedelta(days=n)).isoformat()
    return {"items": items, "filters": filters, "filter_info": {f["key"]: f for f in filters},
            "today": today.isoformat(), "yesterday": day(1), "week_ago": day(6), "month_ago": day(30)}
