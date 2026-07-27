from datetime import datetime
from sqlalchemy.orm import Session

from app.infrastructure.db.models import DBAlerts
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository


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

    today_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_count = 0

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

        # ── 2. Alertas de caída desde ATH por escalones del 10% ──────────────

        ath = ath_refs.get(t)
        if ath is None:
            # Primera vez: inicializar ATH al precio actual, sin alertar
            ath_refs[t] = current_price
            ath_alerted_levels[t] = []
        elif current_price > ath:
            # Nuevo máximo histórico → actualizar ATH y resetear escalones
            ath_refs[t] = current_price
            ath_alerted_levels[t] = []
        else:
            alerted = set(ath_alerted_levels.get(t, []))
            # Comprobar cada escalón del 10% al 60%
            for level in range(10, 61, 10):
                threshold_price = ath * (1 - level / 100)
                if current_price <= threshold_price and level not in alerted:
                    color_cat = "warning"
                    if 30 <= level <= 40:
                        color_cat = "caution"
                    elif level >= 50:
                        color_cat = "danger"

                    history.append({
                        "type": "CAÍDA",
                        "ticker": t,
                        "company_name": db_data.get("company_name", t),
                        "drop_pct": round((ath - current_price) / ath * 100, 1),
                        "escalon": level,
                        "ath": round(ath, 2),
                        "current_price": round(current_price, 2),
                        "date": today_str,
                        "color_category": color_cat,
                    })
                    alerted.add(level)
                    new_count += 1
                # Si el precio recupera por encima del escalón anterior, desbloquearlo
                if level > 10:
                    prev_level = level - 10
                    if current_price > ath * (1 - prev_level / 100) and level in alerted:
                        alerted.discard(level)
            ath_alerted_levels[t] = list(alerted)

    alerts_data["last_statuses"]      = last_statuses
    alerts_data["history"]            = history
    alerts_data["ath_refs"]           = ath_refs
    alerts_data["ath_alerted_levels"] = ath_alerted_levels
    # Mantener price_drop_refs por compatibilidad con datos anteriores
    alerts_data["price_drop_refs"]    = alerts_data.get("price_drop_refs", {})
    save_alerts_data(db_session, alerts_data)

    return new_count
