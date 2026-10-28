"""Panel de notificaciones: alertas, noticias de las tesis y avisos de «Requiere tu atención» en un solo sitio,
con un único estado de leído guardado en la BBDD.

Claves: «alert|<clave de la alerta>», «news|<id>» y la clave del aviso o de la señal del monitor (notices).
"""
import datetime
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
              "upcoming_results": "próximos resultados", "ia_budget": "presupuesto IA", "insider_buy": "directivos compran", "report": "informe", "news_weekly": "resumen semanal",
              "estimate_cut": "previsiones a la baja", "estimate_rise": "previsiones al alza", "cash_deposit": "aportación del mes", "robo_adjust": "roboadvisor",
              "mi_precio": "tu precio de compra", "mi_precio_revisar": "revisar tu precio", "fin_update": "cuentas anuales"}


def _alert_item(a: Dict[str, Any], read: set) -> Dict[str, Any]:
    key = "alert|" + alerts_service.build_alert_key(a)
    if a.get("type") == "CAÍDA":
        if a.get("status") == "cerrada":
            kind, text = "recuperada", f"Recuperada: llegó a −{es(a.get('max_drop'), 1)}% desde el máximo de 52 semanas"
        else:
            text = f"Cae un {es(a.get('drop_pct'), 1)}% desde el máximo de 52 semanas" if a.get("episode") else f"Cae un {es(a.get('drop_pct'), 1)}% desde máximos"
            kind = "caída"
    else:
        kind = "valoración"
        text = f"Pasa de {ZONE_LABEL.get(a.get('old_status'), a.get('old_status'))} a {ZONE_LABEL.get(a.get('new_status'), a.get('new_status'))}"
        if a.get("cagr") is not None:
            text += f" · CAGR estimado {es(a['cagr'], 1)}%"
    return {"key": key, "group": "alertas", "kind": kind, "ticker": a.get("ticker"), "text": text,
            # la fecha en que empezó (o se cerró): la de su última actualización la pondría siempre hoy (se actualiza en cada escaneo)
            "date": a.get("closed_date") or a.get("date") or alerts_service.alert_when(a), "read": key in read,
            "href": f"/ticker/{a.get('ticker')}" + ("#valoracion" if kind == "valoración" else ""),
            # vigente: solo la caída abierta (sigue hasta recuperarse); los cambios de zona son hechos de un día
            "vigente": kind == "caída" and a.get("status") == "abierta", "since": a.get("date"),
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
                      "href": f"/noticias?ticker={n['ticker']}", "url": n.get("url"), "read": n["seen"], "vigente": False,
                      "counts": False})   # las sueltas no avisan: se revisan con el resumen semanal (su propio aviso)
    sync_budget_notice(db, notice_repo)
    from app.application.services import report_service
    notice_repo.sync("informes", "attention", report_service.notices(db))   # «Informe … listo» (los nuevos)
    from app.application.services import cash_service
    notice_repo.sync("liquidez", "attention", cash_service.notices(db))   # día 1: «¿cuánto añades este mes?»
    from app.application.services import robo_service
    notice_repo.sync("roboadvisor", "attention", robo_service.notices(db))   # día 10: «actualiza el roboadvisor»
    notice_repo.sync("noticias", "attention", weekly_news_notice(db))     # el resumen semanal de noticias, un aviso
    from app.application.services import financials_update_service
    notice_repo.sync("finanzas", "attention", financials_update_service.notices(db))   # año fiscal nuevo en tus cuentas
    for a in notice_repo.active_attention():
        if a["kind"] in SKIP_ATTENTION:
            continue
        items.append({"key": a["key"], "group": "avisos", "type": a["kind"], "kind": KIND_LABEL.get(a["kind"], (a["kind"] or "aviso").replace("_", " ")),
                      "ticker": a["ticker"] or ("Watchlist" if a["scope"] == "watchlist" else "Informe" if a["scope"] == "informes" else "Liquidez" if a["scope"] == "liquidez" else "Noticias" if a["scope"] == "noticias" else "Cuentas" if a["scope"] == "finanzas" else ""), "text": a["text"], "date": a["first_seen"],
                      "href": a.get("href") or ("/watchlist" if a["scope"] == "watchlist" else "/mercado#centro"),
                      "read": bool(a["seen_at"]), "vigente": True, "since": a["first_seen"]})   # sigue mientras se dé
    for sig in notice_repo.active_signals():   # monitor de precios: movimientos anormales, propios y máximos/mínimos
        items.append({"key": sig["key"], "group": "monitor", "type": sig["kind"], "kind": f"monitor · {sig['kind']}", "ticker": sig["ticker"],
                      "text": sig["text"], "date": sig["first_seen"], "tone": sig.get("tone"),
                      "href": "/watchlist" if sig["scope"] == "watchlist" else "/mercado#centro", "read": bool(sig["seen_at"]),
                      "vigente": True, "since": sig["first_seen"]})
    # las que saltaron hoy y ya no se cumplen (rebotó, se recuperó): siguen saliendo hoy, marcadas
    today_start = datetime.date.today().isoformat()
    active_keys = {i["key"] for i in items}
    for sig in notice_repo.closed_signals_since(today_start):
        if sig["key"] in active_keys:
            continue
        items.append({"key": sig["key"], "group": "monitor", "type": sig["kind"], "kind": f"monitor · {sig['kind']}", "ticker": sig["ticker"],
                      "text": f"{sig['text']} · ya no se cumple", "date": sig["first_seen"], "tone": sig.get("tone"),
                      "href": "/watchlist" if sig["scope"] == "watchlist" else "/mercado", "read": bool(sig["seen_at"]),
                      "vigente": False, "since": sig["first_seen"]})
    from app.application.services.tier_service import muted_tickers
    muted = muted_tickers(db)   # empresas silenciadas (o vigiladas solo por listas silenciadas): sin avisos
    items = [i for i in items if not (i.get("ticker") and i["ticker"] in muted)]
    for i in items:
        # el presupuesto de IA, los informes nuevos y la aportación del mes siempre cuentan (no son de una empresa)
        i["portfolio"] = i["ticker"] in held or i["kind"] in (KIND_LABEL["ia_budget"], KIND_LABEL["report"], KIND_LABEL["cash_deposit"], KIND_LABEL["robo_adjust"],
                                                              KIND_LABEL["news_weekly"], KIND_LABEL["fin_update"])
    items.sort(key=lambda i: i["date"] or "", reverse=True)
    return items


# Página Avisos: cada notificación en un tipo, con su interruptor (encendido por defecto lo importante). Las noticias ya
# están en Noticias y el Centro, y los cambios de zona que no van hacia compra son ruido: apagados por defecto.
FILTERS = [("comprar", "Comprar", "bi-bullseye", True), ("analistas", "Directivos y analistas", "bi-person-badge", True),
           ("resultados", "Resultados", "bi-calendar-event", True), ("precio", "Precio", "bi-activity", True),
           ("cartera", "Tu cartera", "bi-briefcase", True), ("otros", "Otros", "bi-arrow-left-right", False)]
# (las noticias sueltas no salen en Avisos: no avisan; se revisan con el resumen semanal, que sí es un aviso)
FILTER_OF_TYPE = {"zona": "comprar", "oportunidad": "comprar", "cerca": "comprar", "mi_precio": "comprar", "mi_precio_revisar": "comprar", "insider_buy": "analistas",
                  "estimate_cut": "analistas", "estimate_rise": "analistas",
                  "upcoming_results": "resultados", "new_results": "resultados", "review_ready": "resultados", "news": "noticias",
                  "news_weekly": "cartera",   # el resumen semanal de noticias: lo que toca revisar de tu cartera
                  "portfolio_risk": "cartera", "concentración": "cartera", "cara": "cartera", "decision_missing": "cartera",
                  "decision_review": "cartera", "cash_deposit": "cartera", "robo_adjust": "cartera",
                  "legacy": "cartera", "missing": "cartera", "anchor_expired": "cartera", "split": "cartera", "split_manual": "cartera",
                  "report": "cartera", "ia_budget": "cartera", "fin_update": "cartera"}
QUIET_FILTERS = {"noticias", "otros", "precio"}   # no suman en el globo de «Avisos» del menú
GROUPED_FILTERS = {"comprar", "otros", "precio"}   # en Avisos, una línea por empresa y grupo (la última, «×N»)


# Por qué sigue vigente un aviso (se ve al pasar el ratón por su «vigente»)
VIGENTE_RULE = {"zona": "Sigue mientras cotice por debajo de tu precio de compra",
                "oportunidad": "Sigue mientras cotice por debajo de su precio de entrada",
                "cerca": "Sigue mientras cotice a menos del 15 % de tu precio de compra",
                "mi_precio": "Mientras cotice bajo tu precio de compra",
                "mi_precio_revisar": "Hasta que vuelvas a guardar tu precio",
                "insider_buy": "Sigue mientras la compra esté dentro de los últimos 30 días",
                "estimate_cut": "Sigue mientras la previsión de BPA caiga un 5 % o más en 90 días",
                "estimate_rise": "Sigue mientras la previsión de BPA suba un 5 % o más en 90 días y esté cerca de compra",
                "upcoming_results": "Sigue hasta que presente resultados",
                "new_results": "Sigue hasta que revises la tesis con los resultados nuevos",
                "review_ready": "Sigue hasta que apliques o descartes la revisión",
                "decision_missing": "Sigue hasta que anotes la decisión", "decision_review": "Sigue hasta que la revises",
                "cash_deposit": "Sigue hasta que apuntes la aportación de este mes",
                "robo_adjust": "Sigue hasta que apuntes lo que dice MyInvestor este mes",
                "portfolio_risk": "Sigue mientras la tesis siga débil y la posición pese mucho",
                "concentración": "Sigue mientras el peso siga por encima del límite",
                "legacy": "Sigue hasta que revises la valoración", "missing": "Sigue hasta que hagas la valoración",
                "anchor_expired": "Sigue hasta que revises la corrección", "report": "Sigue hasta que lo abras", "fin_update": "Dos semanas desde la actualización",
                "news_weekly": "Sigue hasta el resumen de la semana que viene"}


def vigente_rule(item: Dict[str, Any]) -> str:
    if item["group"] == "monitor":
        return "Sigue mientras la señal de precio siga activa"
    if item["group"] == "alertas":
        return "Sigue hasta que se recupere a menos de un 5 % del máximo"
    return VIGENTE_RULE.get(item.get("type") or "", "Sigue mientras se dé")


def filter_of(item: Dict[str, Any]) -> str:
    """Grupo de la página Avisos. Cambios de zona: los que van hacia compra, «comprar»; el resto y las recuperaciones de
    una caída, «otros» (ruido, oculto de serie); las caídas, «precio» (con el monitor)."""
    if item["group"] == "monitor":
        return "precio"
    if item["group"] == "noticias":
        return "noticias"
    if item["group"] == "alertas":
        if item["kind"] == "valoración":
            return "comprar" if item.get("counts") else "otros"
        return "otros" if item["kind"] == "recuperada" else "precio"
    return FILTER_OF_TYPE.get(item.get("type") or "", "cartera")


def weekly_news_notice(db: Session) -> List[Dict[str, Any]]:
    """Un aviso por semana con el resumen de noticias del radar, en vez de uno por noticia (las sueltas no avisan): sigue
    mientras sea el de esta semana; abre Noticias."""
    from app.application.services import news_summary_service
    s = news_summary_service.load(db) or {}
    if not s.get("week") or not s.get("headline"):
        return []
    n = s.get("news_count")
    text = f"Resumen semanal de noticias{' · ' + str(n) + ' noticias' if n else ''}: {s['headline']}"
    return [{"key": f"noticias|att|news_weekly|{s['week']}|", "ticker": None, "kind": "news_weekly", "text": text, "href": "/noticias"}]


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
    out = {"total": 0, "alertas": 0, "noticias": 0, "avisos": 0, "monitor": 0, "all": 0, "alertas_all": 0, "quiet": 0, "pendientes": 0}
    for i in items:
        if not i["read"] and i.get("counts", True) and filter_of(i) not in QUIET_FILTERS:
            out["pendientes"] += 1   # globo de «Avisos» en el menú: lo importante sin leer de cualquier empresa
        if not i["read"] and i["group"] != "noticias" and not (i.get("portfolio") and i.get("counts", True)):
            out["quiet"] += 1   # (las noticias sueltas no avisan ni con el punto: van en el resumen semanal)
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
