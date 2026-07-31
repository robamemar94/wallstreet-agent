from datetime import datetime
import pandas as pd
import yfinance as yf
from sqlalchemy.orm import Session

from app.infrastructure.db.models import DBAlerts
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository

ATH_BACKFILL_INTERVAL_DAYS = 30


def _fetch_historical_ath(tickers: list[str]) -> dict:
    """
    Descarga el histórico completo de precios y devuelve el máximo (ATH real)
    por ticker. No lanza excepción: si falla, devuelve {} y el llamador conserva
    el ATH auto-trackeado que ya tuviera.
    """
    if not tickers:
        return {}
    highs = {}
    try:
        data = yf.download(" ".join(tickers), period="max", progress=False)
        if data.empty or "High" not in data:
            return {}
        col_high = data["High"]
        if isinstance(col_high, pd.Series):
            m = col_high.max()
            if pd.notna(m):
                highs[tickers[0]] = float(m)
        else:
            for t in tickers:
                if t in col_high:
                    m = col_high[t].max()
                    if pd.notna(m):
                        highs[t] = float(m)
    except Exception as e:
        print(f"Error fetching historical ATH: {e}")
    return highs


def get_alerts_data(db: Session) -> dict:
    db_alert = db.query(DBAlerts).first()
    if db_alert:
        return db_alert.data
    return {"last_statuses": {}, "history": [], "price_drop_refs": {}}


def save_alerts_data(db: Session, data: dict):
    db_alert = db.query(DBAlerts).first()
    if not db_alert:
        db_alert = DBAlerts(id=1, data=data)
        db.add(db_alert)
    else:
        db_alert.data = data
    db.commit()


def run_alerts_scan(
    ticker_prices: dict,
    asset_repo: SqlAlchemyAssetRepository,
    db_session: Session,
    settings: dict,
) -> int:
    """
    Ejecuta el scan de alertas con precios ya obtenidos.
    ticker_prices: {ticker: float} — precios actuales.
    Devuelve el número de alertas nuevas generadas.
    """
    val_cfg = settings.get("valuation", {
        "ganga": 0.60, "barata": 0.85, "justo_max": 1.15, "cara_max": 1.40
    })
    drop_threshold = float(settings.get("alerts", {}).get("price_drop_threshold", 10))

    alerts_data        = get_alerts_data(db_session)
    last_statuses      = dict(alerts_data.get("last_statuses", {}))
    history            = list(alerts_data.get("history", []))
    ath_refs           = dict(alerts_data.get("ath_refs", {}))
    ath_alerted_levels = dict(alerts_data.get("ath_alerted_levels", {}))
    ath_source         = dict(alerts_data.get("ath_source", {}))
    ath_last_backfill  = dict(alerts_data.get("ath_last_backfill", {}))

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d %H:%M:%S")
    today_date_str = now.strftime("%Y-%m-%d")
    new_count = 0

    # ── 0. Backfill del ATH histórico real (una vez por ticker, refrescado cada
    #        ATH_BACKFILL_INTERVAL_DAYS días) para que la caída se calcule desde
    #        el máximo histórico de verdad y no desde "el máximo visto por la app" ──
    tickers_needing_backfill = []
    for t in ticker_prices:
        last_bf = ath_last_backfill.get(t)
        stale = True
        if last_bf:
            try:
                stale = (now - datetime.strptime(last_bf, "%Y-%m-%d")).days >= ATH_BACKFILL_INTERVAL_DAYS
            except ValueError:
                stale = True
        if ath_source.get(t) != "historical" or stale:
            tickers_needing_backfill.append(t)

    historical_highs = _fetch_historical_ath(tickers_needing_backfill)
    for t in tickers_needing_backfill:
        historical_max = historical_highs.get(t)
        if historical_max is None:
            continue
        prev_ath = ath_refs.get(t)
        new_ath = max(prev_ath, historical_max) if prev_ath is not None else historical_max
        if prev_ath is None or new_ath > prev_ath:
            ath_alerted_levels[t] = []
        ath_refs[t] = new_ath
        ath_source[t] = "historical"
        ath_last_backfill[t] = today_date_str

    for t, current_price in ticker_prices.items():
        if current_price is None:
            continue

        db_data = asset_repo.get_asset_data(t)
        if db_data.get("status") not in ["ACCEPTED", "STANDBY"]:
            continue

        # ── 1. Alerta de cambio de zona de valoración ──────────────────────
        fair_pe_data = db_data.get("fair_pe_data", {})
        fair_pe   = fair_pe_data.get("fair_forward_pe")
        market_pe = db_data.get("last_market_fwd_pe")

        current_status = None
        try:
            if fair_pe and market_pe:
                ratio = float(market_pe) / float(fair_pe)
                if   ratio <  val_cfg["ganga"]:     current_status = "GANGA"
                elif ratio <  val_cfg["barata"]:    current_status = "BARATA"
                elif ratio <= val_cfg["justo_max"]: current_status = "PRECIO JUSTO"
                elif ratio <= val_cfg["cara_max"]:  current_status = "CARA"
                else:                               current_status = "BURBUJA"
        except (ValueError, TypeError):
            pass

        if current_status:
            old_status = last_statuses.get(t)
            if old_status and old_status != current_status:
                history.append({
                    "type": "VALORACIÓN",
                    "ticker": t,
                    "company_name": db_data.get("company_name", t),
                    "old_status": old_status,
                    "new_status": current_status,
                    "date": today_str,
                })
                new_count += 1
            last_statuses[t] = current_status

        # ── 2. Alertas de caída desde ATH: una única alerta "viva" por ticker ──
        # Se actualiza (sustituye) mientras el precio siga cayendo a un escalón
        # más profundo, y se elimina en cuanto se recupera a un nuevo máximo
        # (aunque sea intermedio, no hace falta superar el ATH absoluto). Así
        # no se acumulan en el historial caídas ya resueltas.

        ath = ath_refs.get(t)
        prev_level = ath_alerted_levels.get(t)
        if isinstance(prev_level, list):
            # Migración del formato antiguo (lista de escalones alertados)
            prev_level = max(prev_level) if prev_level else None

        if ath is None:
            # Primera vez: inicializar ATH al precio actual, sin alertar
            ath_refs[t] = current_price
            ath_alerted_levels[t] = None
        elif current_price > ath:
            # Nuevo máximo (intermedio o absoluto) → la caída anterior se resuelve
            ath_refs[t] = current_price
            ath_alerted_levels[t] = None
            if prev_level is not None:
                history = [h for h in history if not (h.get("type") == "CAÍDA" and h.get("ticker") == t)]
        else:
            deepest_level = None
            for level in range(10, 61, 10):
                if current_price <= ath * (1 - level / 100):
                    deepest_level = level

            if deepest_level and (prev_level is None or deepest_level > prev_level):
                # Ha caído a un escalón más profundo que el último aviso: se
                # sustituye la alerta anterior de este ticker por la nueva.
                history = [h for h in history if not (h.get("type") == "CAÍDA" and h.get("ticker") == t)]
                history.append({
                    "type": "CAÍDA",
                    "ticker": t,
                    "company_name": db_data.get("company_name", t),
                    "drop_pct": round((ath - current_price) / ath * 100, 1),
                    "escalon": deepest_level,
                    "ath": round(ath, 2),
                    "current_price": round(current_price, 2),
                    "date": today_str,
                })
                new_count += 1
                prev_level = deepest_level

            ath_alerted_levels[t] = prev_level

    alerts_data["last_statuses"]      = last_statuses
    alerts_data["history"]            = history
    alerts_data["ath_refs"]           = ath_refs
    alerts_data["ath_alerted_levels"] = ath_alerted_levels
    alerts_data["ath_source"]         = ath_source
    alerts_data["ath_last_backfill"]  = ath_last_backfill
    # Mantener price_drop_refs por compatibilidad con datos anteriores
    alerts_data["price_drop_refs"]    = alerts_data.get("price_drop_refs", {})
    save_alerts_data(db_session, alerts_data)

    return new_count
