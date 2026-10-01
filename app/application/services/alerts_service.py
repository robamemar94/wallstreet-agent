import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.infrastructure.db.models import DBAlerts
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.application.services import base_valuation_service

logger = logging.getLogger(__name__)

DROP_STEP = 10          # un episodio se abre al caer un 10 % desde el máximo de 52 semanas y avisa de nuevo cada 10 %
RECOVERY_PCT = 5        # se cierra al volver a menos de un 5 % del máximo (no al primer rebote)
HIGH_WINDOW_DAYS = 365
AUTO_CLEAN_DAYS = 180   # las alertas se borran solas a los ~6 meses (las caídas abiertas nunca: siguen activas)


def alert_when(a: Dict[str, Any]) -> str:
    """Fecha de referencia de una alerta: cierre, última actualización o creación."""
    return a.get("closed_date") or a.get("updated") or a.get("date") or ""


def purge_old(history: List[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    return [a for a in history if (a.get("episode") and a.get("status") == "abierta") or alert_when(a) >= cutoff]


def get_alerts_data(db: Session) -> dict:
    db_alert = db.query(DBAlerts).first()
    if db_alert:
        return db_alert.data
    return {"last_statuses": {}, "history": [], "price_drop_refs": {}}


def build_alert_key(alert: dict) -> str:
    """Clave estable por alerta, usada tanto para el atributo data-alert-key
    del HTML como para el endpoint que alimenta el contador de no leídas del
    sidebar. Debe ser idéntica en ambos sitios o el conteo de no leídas queda
    desincronizado con el estado "visto" guardado en localStorage."""
    alert_type = alert.get("type", "VALORACIÓN")
    ticker = alert.get("ticker")
    date = alert.get("date")
    if alert_type == "CAÍDA":
        # episodio: fecha de inicio fija; el escalón cambia al empeorar (y vuelve a salir como nuevo) o al cerrarse
        return f"{ticker}_{date}_{alert.get('escalon')}"
    return f"{ticker}_{date}_{alert.get('old_status')}-{alert.get('new_status')}"


VALUATION_RANK = {"GANGA": 0, "BARATA": 1, "PRECIO JUSTO": 2, "CARA": 3, "BURBUJA": 4}
ZONE_STATUS = {"ganga": "GANGA", "barata": "BARATA", "justo": "PRECIO JUSTO", "cara": "CARA"}


def valuation_direction(old_status: str, new_status: str) -> str:
    """SUBE si la nueva zona de valoración es más cara que la anterior
    (se encarece), BAJA si es más barata (se abarata)."""
    old_rank = VALUATION_RANK.get(old_status, 2)
    new_rank = VALUATION_RANK.get(new_status, 2)
    return "SUBE" if new_rank >= old_rank else "BAJA"


def save_alerts_data(db: Session, data: dict):
    db_alert = db.query(DBAlerts).first()
    if not db_alert:
        db_alert = DBAlerts(id=1, data=data)
        db.add(db_alert)
    else:
        db_alert.data = data
        flag_modified(db_alert, "data")   # el dict puede ser el mismo objeto: sin esto no siempre se guarda
    db.commit()


def eligible_tickers(db: Session, asset_repo: SqlAlchemyAssetRepository) -> Set[str]:
    """Empresas que se vigilan: la watchlist (a mano y favoritas) y la cartera."""
    from app.infrastructure.db.models import DBPortfolioItem, DBWatchlistItem
    held = {p.ticker for p in db.query(DBPortfolioItem).all() if (p.shares or 0) > 0}
    manual = {w.ticker for w in db.query(DBWatchlistItem).all()}
    return held | manual | set(asset_repo.get_watch_candidates())


def _drop_entry(t: str, name: str, ep: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "CAÍDA", "episode": True, "ticker": t, "company_name": name, **ep}


def run_alerts_scan(
    ticker_prices: dict,
    asset_repo: SqlAlchemyAssetRepository,
    db_session: Session,
    settings: dict,
    watch_tickers: Optional[Set[str]] = None,
) -> int:
    """
    Escaneo de alertas con precios ya obtenidos ({ticker: precio}); devuelve cuántas son nuevas.
    · VALORACIÓN: cambio de zona según la valoración base de la ficha.
    · CAÍDA: episodios de caída desde el máximo de 52 semanas (cierres). Uno por empresa: se actualiza en sitio,
      vuelve a salir como nuevo solo al empeorar otro 10 % y se cierra al recuperarse a menos de un 5 % del máximo.
    """
    from app.application.services import price_monitor_service as pm

    eligible = eligible_tickers(db_session, asset_repo) | set(watch_tickers or ())
    prices = {t: float(p) for t, p in ticker_prices.items() if p and t in eligible}
    alerts_data = get_alerts_data(db_session)
    # last_statuses era la zona por PER justo; las zonas por valoración base van en last_zones
    # (la primera pasada solo las registra, sin avisar, para no mezclar métodos)
    last_zones = dict(alerts_data.get("last_zones", {}))
    history = list(alerts_data.get("history", []))
    drops = dict(alerts_data.get("drops", {}))
    migrating = not alerts_data.get("drops_v2")
    if migrating:   # las caídas antiguas (desde máximo histórico, una alerta por escalón) se sustituyen por episodios
        history = [h for h in history if not (h.get("type") == "CAÍDA" and not h.get("episode"))]

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d %H:%M:%S")
    new_count = 0

    benchmark = (settings.get("performance") or {}).get("benchmark_ticker") or "URTH"
    try:
        closes = pm.download_history(sorted(prices) + [benchmark], fill=False) if prices else None
    except Exception as e:
        logger.warning("Alertas: sin histórico de precios: %s", e)
        closes = None
    since = now - timedelta(days=HIGH_WINDOW_DAYS)

    for t, current_price in prices.items():
        db_data = asset_repo.get_asset_data(t)
        name = db_data.get("company_name", t)

        # ── 1. Cambio de zona de valoración ──────────────────────────────
        # Zona de la valoración base de la ficha (la misma que la watchlist): por la rentabilidad estimada
        # a este precio. Las valoraciones antiguas sin revisar no avisan.
        val = base_valuation_service.live(t, db_data, current_price)
        fresh_val = val if val and not val["inputs"].get("legacy") else None
        current_status = ZONE_STATUS.get(fresh_val["zone"]) if fresh_val else None
        if current_status:
            old_status = last_zones.get(t)
            if old_status and old_status != current_status:
                history.append({
                    "type": "VALORACIÓN", "ticker": t, "company_name": name,
                    "old_status": old_status, "new_status": current_status,
                    "cagr": fresh_val["cagr"], "buy_price": fresh_val["buy_price"], "price": round(current_price, 2),
                    "date": today_str,
                })
                new_count += 1
            last_zones[t] = current_status

        # ── 2. Episodio de caída desde el máximo de 52 semanas ───────────
        if closes is None or t not in closes.columns:
            continue
        s = closes[t].dropna()
        s = s[s.index >= since]
        if s.empty:
            continue
        high, high_date = max(float(s.max()), current_price), s.idxmax().date().isoformat()
        drop = 100 * (1 - current_price / high)
        ep = drops.get(t)
        entry_idx = next((i for i, h in enumerate(history) if h.get("episode") and h.get("ticker") == t
                          and h.get("status") == "abierta"), None)

        if ep and drop <= RECOVERY_PCT:   # recuperada: el episodio se cierra y queda en el historial
            if entry_idx is not None:
                history.pop(entry_idx)
            days = (now.date() - datetime.fromisoformat(ep.get("high_date") or ep["date"][:10]).date()).days
            history.append(_drop_entry(t, name, {**ep, "status": "cerrada", "escalon": "cerrada", "closed_date": today_str,
                                                 "drop_pct": round(drop, 1), "current_price": round(current_price, 2),
                                                 "days": days, "date": ep["date"], "silent": False}))
            del drops[t]
            new_count += 1
            continue
        if not ep and drop < DROP_STEP:
            continue

        bench_pct = None
        if benchmark in closes.columns:
            b = closes[benchmark].dropna()
            b_high = b[b.index >= s.idxmax()]
            if len(b_high) > 1:
                bench_pct = round(100 * (b_high.iloc[-1] / b_high.iloc[0] - 1), 1)
        level = int(drop // DROP_STEP) * DROP_STEP
        ep = ep or {"date": today_str, "escalon": level, "max_drop": 0.0, "silent": migrating}
        worse = level > ep["escalon"]
        ep.update({
            "status": "abierta", "high": round(high, 2), "high_date": high_date,
            "drop_pct": round(drop, 1), "max_drop": round(max(ep["max_drop"], drop), 1),
            "current_price": round(current_price, 2),
            "days": (now.date() - datetime.fromisoformat(high_date).date()).days,   # desde el máximo: lo que dura la caída
            "bench_pct": bench_pct, "updated": today_str,
            # distancia al precio de compra (≤0: ya está por debajo)
            "buy_gap": round(100 * (1 - fresh_val["buy_price"] / current_price), 1) if fresh_val else None,
        })
        if worse:   # otro 10 % más abajo: mismo episodio, pero vuelve a salir como nuevo (aunque lo ignoraras)
            ep.update({"escalon": level, "silent": False, "dismissed": False})
        drops[t] = ep
        if ep.get("dismissed"):   # lo ignoraste: se sigue el episodio pero no se muestra
            continue
        if entry_idx is None or worse:
            new_count += 0 if ep.get("silent") else 1
        entry = _drop_entry(t, name, ep)
        if entry_idx is None:
            history.append(entry)
        else:
            history[entry_idx] = entry

    alerts_data["last_zones"] = last_zones
    alerts_data["history"] = purge_old(history, AUTO_CLEAN_DAYS)
    alerts_data["drops"] = drops
    alerts_data["drops_v2"] = True
    for old_key in ("ath_refs", "ath_alerted_levels", "ath_source", "ath_last_backfill", "last_statuses"):
        alerts_data.pop(old_key, None)
    save_alerts_data(db_session, alerts_data)
    return new_count


_scan_lock = threading.Lock()


def scan_in_background(ticker_prices: Dict[str, float], watch_tickers: Optional[Set[str]] = None) -> None:
    """Lanza el escaneo en un hilo con su propia sesión; si ya hay uno en marcha, no hace nada."""
    if not _scan_lock.acquire(blocking=False):
        return

    def run():
        from app.infrastructure.db.database import SessionLocal
        from app.infrastructure.dependencies import get_settings_from_db
        db = SessionLocal()
        try:
            run_alerts_scan(ticker_prices, SqlAlchemyAssetRepository(db), db, get_settings_from_db(db), watch_tickers)
        except Exception:
            logger.exception("Fallo en el escaneo de alertas")
        finally:
            db.close()
            _scan_lock.release()
    threading.Thread(target=run, daemon=True, name="alerts-scan").start()
