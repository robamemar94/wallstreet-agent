"""Panel de notificaciones: alertas, noticias de las tesis y avisos de «Requiere tu atención» en un solo sitio,
con un único estado de leído guardado en la BBDD.

Claves: «alert|<clave de la alerta>», «news|<id>» y la clave del aviso o de la señal del monitor (notices).
"""
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.application.services import alerts_service
from utils.number_format import es

NEWS_LIMIT = 60
ZONE_LABEL = {"GANGA": "ganga", "BARATA": "barata", "PRECIO JUSTO": "justo", "CARA": "cara", "BURBUJA": "burbuja"}
# avisos de «Requiere tu atención» que ya salen por otra vía (la noticia en sí)
SKIP_ATTENTION = {"news"}
KIND_LABEL = {"zona": "zona de compra", "cerca": "cerca de compra", "legacy": "valoraciones por revisar",
              "missing": "sin valoración", "split": "split", "split_manual": "split", "portfolio_risk": "riesgo en cartera",
              "new_results": "resultados", "review_ready": "revisión lista", "decision_missing": "decisión pendiente",
              "decision_review": "revisar decisión", "concentración": "concentración",
              "upcoming_results": "próximos resultados", "ia_budget": "presupuesto IA"}


def _alert_item(a: Dict[str, Any], read: set) -> Dict[str, Any]:
    key = "alert|" + alerts_service.build_alert_key(a)
    if a.get("type") == "CAÍDA":
        if a.get("status") == "cerrada":
            kind, text = "recuperada", f"Recuperada: llegó a −{a.get('max_drop')}% desde el máximo de 52 semanas"
        else:
            text = f"Cae un {a.get('drop_pct')}% desde el máximo de 52 semanas" if a.get("episode") else f"Cae un {a.get('drop_pct')}% desde máximos"
            kind = "caída"
    else:
        kind = "valoración"
        text = f"Pasa de {ZONE_LABEL.get(a.get('old_status'), a.get('old_status'))} a {ZONE_LABEL.get(a.get('new_status'), a.get('new_status'))}"
        if a.get("cagr") is not None:
            text += f" · CAGR estimado {es(a['cagr'], 1)}%"
    return {"key": key, "group": "alertas", "kind": kind, "ticker": a.get("ticker"), "text": text,
            "date": alerts_service.alert_when(a), "href": "/alerts", "read": key in read,
            "counts": kind != "valoración" or into_buy_zone(a.get("old_status"), a.get("new_status"))}


BUY_ZONES = {"BARATA", "GANGA"}


def into_buy_zone(old_status, new_status) -> bool:
    """Un cambio de zona de valoración solo enciende los globos si se abarata hacia barata o ganga
    (justo → barata, barata → ganga…); el resto se ve en la lista pero no suma."""
    return new_status in BUY_ZONES and alerts_service.valuation_direction(old_status, new_status) == "BAJA"


def held_tickers(db: Session) -> set:
    from app.infrastructure.db.models import DBPortfolioItem
    return {p.ticker for p in db.query(DBPortfolioItem).all() if (p.shares or 0) > 0}


def collect(db: Session, thesis_repo, notice_repo) -> List[Dict[str, Any]]:
    """Todas las notificaciones, las más recientes primero. «portfolio»: es de una empresa que tienes en cartera
    (solo esas cuentan en los globos; las demás se ven en el panel pero no suman)."""
    held = held_tickers(db)
    data = alerts_service.get_alerts_data(db)
    read = set(data.get("read") or [])
    items = [_alert_item(a, read) for a in data.get("history", []) if not a.get("silent")]
    for n in thesis_repo.list_news(limit=NEWS_LIMIT):
        items.append({"key": f"news|{n['id']}", "group": "noticias", "kind": f"noticia {n['materiality']}/5", "ticker": n["ticker"],
                      "text": n["title"], "date": n.get("found_at") or n.get("date") or "",
                      "href": f"/noticias?ticker={n['ticker']}", "url": n.get("url"), "read": n["seen"]})
    sync_budget_notice(db, notice_repo)
    for a in notice_repo.active_attention():
        if a["kind"] in SKIP_ATTENTION:
            continue
        items.append({"key": a["key"], "group": "avisos", "kind": KIND_LABEL.get(a["kind"], (a["kind"] or "aviso").replace("_", " ")),
                      "ticker": a["ticker"] or ("Watchlist" if a["scope"] == "watchlist" else ""), "text": a["text"], "date": a["first_seen"],
                      "href": a.get("href") or ("/watchlist" if a["scope"] == "watchlist" else "/mercado#centro"),
                      "read": bool(a["seen_at"])})
    for sig in notice_repo.active_signals():   # monitor de precios: movimientos anormales, propios y máximos/mínimos
        items.append({"key": sig["key"], "group": "monitor", "kind": f"monitor · {sig['kind']}", "ticker": sig["ticker"],
                      "text": sig["text"], "date": sig["first_seen"], "tone": sig.get("tone"),
                      "href": "/watchlist" if sig["scope"] == "watchlist" else "/mercado#centro", "read": bool(sig["seen_at"])})
    for i in items:
        i["portfolio"] = i["ticker"] in held or i["kind"] == KIND_LABEL["ia_budget"]   # el presupuesto siempre cuenta
    items.sort(key=lambda i: i["date"] or "", reverse=True)
    return items


def sync_budget_notice(db: Session, notice_repo) -> None:
    """Aviso de presupuesto de IA (mes en curso): se abre al llegar al umbral y se cierra al cambiar de mes."""
    from app.application.services import ai_usage_service
    from app.infrastructure.db.models import DBSetting
    row = db.get(DBSetting, ai_usage_service.BUDGET_KEY)
    notice = ai_usage_service.budget_notice(row.value if row else {})
    notice_repo.sync("ia", "attention", [notice] if notice else [])


def unread_counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
    """No leídas de empresas en cartera (las que cuentan en la campana y en Tesis) y, aparte, todas las no leídas
    y todas las alertas (el globo de «Alertas» cuenta cualquier empresa vigilada). Las que no cuentan («counts»
    falso: cambios de valoración que no van hacia zona de compra) no suman en ninguno.
    «quiet»: las no leídas que no van en el número de la campana (de fuera de cartera o que no cuentan): la campana
    las avisa con un punto."""
    out = {"total": 0, "alertas": 0, "noticias": 0, "avisos": 0, "monitor": 0, "all": 0, "alertas_all": 0, "quiet": 0}
    for i in items:
        if not i["read"] and not (i.get("portfolio") and i.get("counts", True)):
            out["quiet"] += 1
        if not i["read"] and i.get("counts", True):
            out["all"] += 1
            out["alertas_all"] += i["group"] == "alertas"   # el globo de «Alertas» cuenta todas, como antes
            if i.get("portfolio"):
                out["total"] += 1
                out[i["group"]] += 1
    return out


def news_ids_in_notices(keys: List[str]) -> List[int]:
    """Noticias detrás de los avisos «Noticia n/5» (clave «<scope>|att|news|<ticker>|<id>»): leer el aviso es leer la noticia."""
    return [int(k.rsplit("|", 1)[1]) for k in keys if "|att|news|" in k and k.rsplit("|", 1)[1].isdigit()]


def mark_read(db: Session, thesis_repo, notice_repo, keys: List[str], read: bool = True) -> int:
    """Marca como leídas (o no) las notificaciones, cada tipo en su sitio."""
    alert_keys = [k for k in keys if k.startswith("alert|")]
    news_ids = [int(k.split("|", 1)[1]) for k in keys if k.startswith("news|") and k.split("|", 1)[1].isdigit()]
    news_ids += news_ids_in_notices(keys)
    notice_keys = [k for k in keys if not k.startswith(("alert|", "news|"))]
    n = 0
    if alert_keys:
        data = alerts_service.get_alerts_data(db)
        current = set(data.get("read") or [])
        current = current | set(alert_keys) if read else current - set(alert_keys)
        # solo se guardan las de alertas que siguen existiendo (las borradas o limpiadas no hacen falta)
        alive = {"alert|" + alerts_service.build_alert_key(a) for a in data.get("history", [])}
        data["read"] = sorted(current & alive)
        alerts_service.save_alerts_data(db, data)
        n += len(alert_keys)
    if news_ids and read:
        n += thesis_repo.mark_news_seen(news_ids)
    if notice_keys:
        n += notice_repo.set_seen(notice_keys, read)
    return n
