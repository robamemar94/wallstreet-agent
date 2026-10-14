"""
Informes semanales, mensuales, trimestrales y anuales: una foto fija de cada periodo cerrado, para releerla después.

Se generan solos al cerrar el periodo (semanal: el sábado a mediodía, tras el radar de noticias; mensual y trimestral:
el día 1; anual: el 1 de enero) y, la primera vez, hacia atrás desde la fecha de inicio de la cartera (marcados como
«reconstruidos»: la rentabilidad, las operaciones y los dividendos se recalculan bien; lo que no se guardó en su día,
como la agenda de entonces, no está). Los informes en sí, SIN IA: solo datos que la app ya tiene.
IA: en el mensual, solo con el botón. EXCEPCIÓN a la regla de no llamar a la IA sola, decidida por el usuario (13/10/2026)
por lo poco que cuesta: la revisión trimestral y la carta anual se escriben solas al generarse el informe de un periodo
recién cerrado (no en los reconstruidos hacia atrás); una llamada a Gemini Pro sin búsqueda, unos céntimos.

Se guardan en settings con la clave «report:<tipo><periodo>» (report:W2026-10-05, report:M2026-09, report:Y2026).
"""
import calendar
import datetime
import logging
import threading
import time
from typing import Any, Dict, List, Optional

import pandas as pd

from app.infrastructure.db.models import DBSetting

logger = logging.getLogger(__name__)

KEY_PREFIX = "report:"
KINDS = {"W": "Semanal", "M": "Mensual", "Q": "Trimestral", "Y": "Anual"}
AI_KINDS = ("M", "Q", "Y")    # con comentario de IA (a petición): en el semanal bastan los números
WEEKLY_READY_HOUR = 12        # el informe semanal, el sábado desde esta hora (el radar empieza a las 8:00)
RECONSTRUCTED_AFTER_DAYS = 3  # generado más tarde que esto tras el cierre: «reconstruido»
NOTICE_DAYS = 10              # avisos de informe nuevo, durante estos días
CHECK_EVERY_S = 3600
TOP = 3
BENCHMARK, BENCHMARK_NAME = "SPY", "S&P 500"

_lock = threading.Lock()
_running = False
_scheduler_started = False


# --- Periodos -------------------------------------------------------------------------------------------------------

def period(kind: str, start: datetime.date) -> Dict[str, Any]:
    """Un periodo: semana (lunes a viernes), mes natural o año. key, start, end (inclusive), ready (cuándo se cierra)."""
    if kind == "W":
        start = start - datetime.timedelta(days=start.weekday())
        end = start + datetime.timedelta(days=4)
        ready = datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time(WEEKLY_READY_HOUR))
        label = f"Semana del {start:%d/%m} al {end:%d/%m/%Y}"
        key = f"W{start.isoformat()}"
    elif kind == "M":
        start = start.replace(day=1)
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        ready = datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time(0))
        label = f"{['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'][start.month - 1]} {start.year}"
        key = f"M{start:%Y-%m}"
    elif kind == "Q":
        start = start.replace(month=3 * ((start.month - 1) // 3) + 1, day=1)
        last_month = start.month + 2
        end = start.replace(month=last_month, day=calendar.monthrange(start.year, last_month)[1])
        ready = datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time(0))
        label = f"{(start.month - 1) // 3 + 1}.º trimestre {start.year}"
        key = f"Q{start.year}-{(start.month - 1) // 3 + 1}"
    else:
        start = start.replace(month=1, day=1)
        end = start.replace(month=12, day=31)
        ready = datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time(0))
        label = f"Año {start.year}"
        key = f"Y{start.year}"
    return {"kind": kind, "key": key, "start": start, "end": end, "ready": ready, "label": label}


def next_period(p: Dict[str, Any]) -> Dict[str, Any]:
    return period(p["kind"], p["end"] + datetime.timedelta(days=1 if p["kind"] != "W" else 3))


def closed_periods(kind: str, since: datetime.date, now: datetime.datetime) -> List[Dict[str, Any]]:
    """Los periodos cerrados desde «since» (el primero, aunque empiece antes) hasta ahora."""
    out, p = [], period(kind, since)
    while p["ready"] <= now:
        out.append(p)
        p = next_period(p)
    return out


# --- Guardar y leer -------------------------------------------------------------------------------------------------

def load(db, key: str) -> Optional[Dict[str, Any]]:
    row = db.get(DBSetting, KEY_PREFIX + key)
    return row.value if row else None


def save(db, report: Dict[str, Any]) -> None:
    row = db.get(DBSetting, KEY_PREFIX + report["key"])
    if row:
        row.value = report
    else:
        db.add(DBSetting(key=KEY_PREFIX + report["key"], value=report))
    db.commit()


def list_reports(db) -> List[Dict[str, Any]]:
    """Todos los informes (lo justo para el archivo), del más reciente al más antiguo."""
    rows = db.query(DBSetting).filter(DBSetting.key.like(KEY_PREFIX + "%")).all()
    out = [{k: r.value.get(k) for k in ("key", "kind", "label", "start", "end", "created_at", "reconstructed")}
           | {"pct": (r.value.get("performance") or {}).get("pct"), "bench_pct": (r.value.get("performance") or {}).get("bench_pct"),
              "has_ai": bool(r.value.get("ai"))}
           for r in rows if isinstance(r.value, dict)]
    return sorted(out, key=lambda r: (r["end"] or "", r["kind"]), reverse=True)


# --- Cálculo --------------------------------------------------------------------------------------------------------

def _eur_close(df: pd.DataFrame, symbol: str, currency: str, when: pd.Timestamp) -> Optional[float]:
    from app.application.services.price_monitor_service import _fx_series
    if symbol not in df.columns:
        return None
    px = df[symbol].loc[:when].dropna()
    fx = _fx_series(df, currency)
    if not len(px) or fx is None:
        return None
    fxv = fx.loc[:when].dropna()
    return float(px.iloc[-1]) * float(fxv.iloc[-1]) if len(fxv) else None


def performance(df: pd.DataFrame, txs: List[Dict[str, Any]], splits, start: datetime.date, end: datetime.date) -> Dict[str, Any]:
    """Rentabilidad del periodo con el mismo cálculo que el resto de la app (Modified Dietz: las compras y ventas son
    aportaciones, no ganancias), el valor al principio y al final, y el S&P 500 en euros."""
    from app.application.services import price_monitor_service as pm
    end_ts, start_ts = pd.Timestamp(end), pd.Timestamp(start) - pd.Timedelta(days=1)   # desde el cierre anterior
    dfe = df.loc[:end_ts]
    if dfe.empty:
        return {}
    days = max((dfe.index[-1] - start_ts).days, 1)
    ch = pm.portfolio_changes(dfe, txs, splits, rolling_days=(days,))
    per = ch["periods"].get(f"{days}d") or {}
    v0 = pm.portfolio_changes(df.loc[:start_ts], txs, splits)["total_eur"] if not df.loc[:start_ts].empty else 0.0
    b0, b1 = _eur_close(df, BENCHMARK, "USD", start_ts), _eur_close(df, BENCHMARK, "USD", end_ts)
    bench = 100 * (b1 / b0 - 1) if b0 and b1 else None
    positions = [{"ticker": t, "pnl_eur": p["pnl_eur"].get(f"{days}d"), "contribution": p["contribution"].get(f"{days}d"),
                  "value_eur": p["value_eur"], "weight": p["weight"]} for t, p in ch["positions"].items()]
    positions = [p for p in positions if p["pnl_eur"] is not None]
    positions.sort(key=lambda p: p["pnl_eur"], reverse=True)
    return {"pct": per.get("pct"), "pnl_eur": per.get("abs_eur"), "value_start": v0, "value_end": ch["total_eur"],
            "bench_pct": bench, "vs_bench": (per["pct"] - bench) if per.get("pct") is not None and bench is not None else None,
            "best": [p for p in positions[:TOP] if p["pnl_eur"] > 0], "worst": [p for p in positions[::-1][:TOP] if p["pnl_eur"] < 0],
            "fx_eur": per.get("fx_eur"), "incomplete": per.get("incomplete") or []}


def flows(df: pd.DataFrame, txs: List[Dict[str, Any]], start: str, end: str) -> Dict[str, float]:
    """Dinero metido (compras) y sacado (ventas) en el periodo, en euros."""
    from app.application.services.decision_review_service import _fx_at
    buys = sells = 0.0
    for t in txs:
        if start <= t["date"][:10] <= end:
            fx = t.get("fx") or _fx_at(df, t.get("currency"), t["date"]) or 0
            amount = t["shares"] * t["price"] * (0.01 if t.get("currency") == "GBp" else 1) * fx
            if t["type"] == "BUY":
                buys += amount
            else:
                sells += amount
    return {"buys": buys, "sells": sells, "net": buys - sells}


def dividends(db, df: pd.DataFrame, start: str, end: str) -> Dict[str, Any]:
    """Dividendos cobrados en el periodo (del ledger), en euros."""
    from app.application.services.decision_review_service import _fx_at
    from app.domain.models import TransactionType
    from app.infrastructure.db.models import DBAsset, DBLedgerEntry
    rows = db.query(DBLedgerEntry).filter(DBLedgerEntry.type == TransactionType.DIVIDEND,
                                          DBLedgerEntry.date >= start, DBLedgerEntry.date <= end + " 23:59").all()
    ccy = {a.ticker: (a.data or {}).get("currency", "USD") for a in db.query(DBAsset).filter(DBAsset.ticker.in_({r.ticker for r in rows})).all()}
    items = []
    for r in rows:
        fx = _fx_at(df, ccy.get(r.ticker, "USD"), str(r.date)[:10]) or 0
        items.append({"ticker": r.ticker, "date": str(r.date)[:10], "eur": (r.total or 0) * fx * (0.01 if ccy.get(r.ticker) == "GBp" else 1)})
    items.sort(key=lambda x: x["date"])
    return {"total": sum(i["eur"] for i in items), "items": items}


def decisions(df: pd.DataFrame, txs: List[Dict[str, Any]], assets: Dict[str, Dict[str, Any]], splits, start: str, end: str) -> List[Dict[str, Any]]:
    """Las decisiones del periodo (las órdenes del mismo día, juntas) con su porqué."""
    from app.application.services.decision_review_service import group_decisions
    period_txs = [t for t in txs if start <= t["date"][:10] <= end]
    return [{k: g[k] for k in ("ticker", "date", "type", "shares", "price", "amount_eur", "note", "orders")}
            for g in group_decisions(period_txs, assets, df, splits)]


def theses_events(db, start: str, end: str) -> List[Dict[str, Any]]:
    """Lo que pasó en las tesis: cambios de veredicto y resultados presentados."""
    from app.infrastructure.db.models import DBThesis, DBThesisEvent
    q = (db.query(DBThesisEvent, DBThesis.ticker).join(DBThesis, DBThesis.id == DBThesisEvent.thesis_id)
         .filter(DBThesisEvent.date >= start, DBThesisEvent.date <= end + " 23:59", DBThesisEvent.type.in_(("verdict", "earnings"))))
    return sorted(({"ticker": tk, "date": str(e.date)[:10], "type": e.type, "title": e.title, "impact": e.impact} for e, tk in q.all()),
                  key=lambda e: e["date"])


def news(db, start: str, end: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Las noticias más relevantes del radar en el periodo (materialidad 4 o 5)."""
    from app.infrastructure.db.models import DBThesis, DBThesisNews
    q = (db.query(DBThesisNews, DBThesis.ticker).join(DBThesis, DBThesis.id == DBThesisNews.thesis_id)
         .filter(DBThesisNews.found_at >= start, DBThesisNews.found_at <= end + " 23:59", DBThesisNews.materiality >= 4))
    items = [{"ticker": tk, "date": (n.date or n.found_at or "")[:10], "title": n.title, "materiality": n.materiality,
              "impact": getattr(n, "impact", None), "url": n.url} for n, tk in q.all()]
    items.sort(key=lambda n: (n["materiality"] or 0, n["date"]), reverse=True)
    return items[:limit]


def watchlist_zone(db, start: str, end: str) -> List[Dict[str, Any]]:
    """Empresas de la watchlist que entraron en zona de compra en el periodo (avisos guardados desde que existen)."""
    from app.infrastructure.db.models import DBNotice
    q = db.query(DBNotice).filter(DBNotice.scope == "watchlist", DBNotice.kind == "zona",
                                  DBNotice.first_seen >= start, DBNotice.first_seen <= end + "T23:59:59")
    seen, out = set(), []
    for n in sorted(q.all(), key=lambda n: n.first_seen):
        if n.ticker and n.ticker not in seen:
            seen.add(n.ticker)
            out.append({"ticker": n.ticker, "date": n.first_seen[:10], "text": n.text})
    return out


def insider_buys(tickers: List[str], start: str, end: str) -> List[Dict[str, Any]]:
    """Compras en bolsa de directivos en el periodo (cartera y watchlist), de la caché de directivos."""
    from app.application.services import market_cache
    out = []
    for t in sorted(set(tickers)):
        buys = [x for x in market_cache.peek("insider_trades_v1", t) or [] if x["kind"] == "buy" and start <= x["date"] <= end]
        if buys:
            out.append({"ticker": t, "insiders": len({b["insider"] for b in buys}), "value": sum(b["value"] or 0 for b in buys)})
    return sorted(out, key=lambda x: -x["value"])


def agenda(held: List[str], start: datetime.date, end: datetime.date) -> List[Dict[str, Any]]:
    """Lo que viene en el periodo siguiente: resultados de la cartera (fechas en caché)."""
    from app.application.services.thesis_review_service import _earnings_info
    out = []
    for t in held:
        try:
            nxt = (_earnings_info(t) or {}).get("next")
        except Exception:
            nxt = None
        if nxt and start.isoformat() <= str(nxt)[:10] <= end.isoformat():
            out.append({"ticker": t, "date": str(nxt)[:10], "what": "Resultados"})
    return sorted(out, key=lambda e: e["date"])


def build(db, p: Dict[str, Any], portfolio_service, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """El informe de un periodo cerrado."""
    from app.application.services import price_monitor_service as pm
    from app.application.services.decision_review_service import symbols
    from app.infrastructure.db.models import DBAsset, DBWatchlistItem
    now = now or datetime.datetime.now()
    txs, splits = pm.load_transactions(portfolio_service, db)
    start, end = p["start"].isoformat(), p["end"].isoformat()
    df = pm.download_history(symbols(txs)) if txs else pd.DataFrame()
    held = sorted({t["ticker"] for t in txs})
    assets = {a.ticker: a.data or {} for a in db.query(DBAsset).filter(DBAsset.ticker.in_(held)).all()}
    reconstructed = (now - p["ready"]).days > RECONSTRUCTED_AFTER_DAYS
    open_now = [i.ticker for i in portfolio_service.get_active_portfolio() if i.shares > 0]
    watch = [w.ticker for w in db.query(DBWatchlistItem).all()]
    report = {
        "key": p["key"], "kind": p["kind"], "label": p["label"], "start": start, "end": end,
        "created_at": now.isoformat(timespec="seconds"), "reconstructed": reconstructed,
        "performance": performance(df, txs, splits, p["start"], p["end"]) if not df.empty else {},
        "flows": flows(df, txs, start, end), "dividends": dividends(db, df, start, end),
        "decisions": decisions(df, txs, assets, splits, start, end),
        "theses": theses_events(db, start, end), "news": news(db, start, end),
        "watchlist": watchlist_zone(db, start, end), "insiders": insider_buys(open_now + watch, start, end),
        "benchmark": BENCHMARK_NAME, "ai": None,
    }
    if p["kind"] in ("Y", "Q"):   # el año o el trimestre, mes a mes
        report["months"] = []
        for m in range(p["start"].month, p["end"].month + 1):
            mp = period("M", datetime.date(p["start"].year, m, 1))
            if mp["start"] > p["end"] or mp["start"] > now.date():
                break
            perf = performance(df, txs, splits, mp["start"], min(mp["end"], now.date())) if not df.empty else {}
            report["months"].append({"label": mp["label"], "pct": perf.get("pct"), "bench_pct": perf.get("bench_pct")})
    if not reconstructed and p["kind"] in ("W", "M"):   # lo que viene (solo al cerrar el periodo: es la agenda de ese momento)
        nxt = next_period(p)
        report["agenda"] = agenda(open_now, nxt["start"], nxt["end"])
    return report


# --- Generación automática ------------------------------------------------------------------------------------------

def start_date(db) -> datetime.date:
    """Desde cuándo hay informes: la fecha de inicio del Rendimiento (lo de antes eran pruebas) o la primera operación."""
    from app.infrastructure.dependencies import get_settings_from_db
    s = (get_settings_from_db(db).get("performance") or {}).get("start_date")
    return datetime.date.fromisoformat(s) if s else datetime.date(datetime.date.today().year, 1, 1)


def due(db, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """Periodos cerrados sin informe (los más antiguos primero)."""
    now = now or datetime.datetime.now()
    since = start_date(db)
    have = {r.key[len(KEY_PREFIX):] for r in db.query(DBSetting.key).filter(DBSetting.key.like(KEY_PREFIX + "%")).all()}
    out = [p for kind in KINDS for p in closed_periods(kind, since, now) if p["key"] not in have]
    return sorted(out, key=lambda p: p["end"])


def backfill_fx(db, portfolio_service) -> int:
    """Los informes guardados antes de que existiera el efecto divisa: se le añade a su rentabilidad (solo ese dato;
    el resto del informe sigue como se escribió). Una vez hechos, no hace nada."""
    from sqlalchemy.orm.attributes import flag_modified
    from app.application.services import price_monitor_service as pm
    from app.application.services.decision_review_service import symbols
    rows = [r for r in db.query(DBSetting).filter(DBSetting.key.like(KEY_PREFIX + "%")).all()
            if isinstance(r.value, dict) and r.value.get("performance") and "fx_eur" not in r.value["performance"]]
    if not rows:
        return 0
    txs, splits = pm.load_transactions(portfolio_service, db)
    df = pm.download_history(symbols(txs)) if txs else pd.DataFrame()
    if df.empty:
        return 0
    for r in rows:
        perf = performance(df, txs, splits, datetime.date.fromisoformat(r.value["start"]), datetime.date.fromisoformat(r.value["end"]))
        r.value = {**r.value, "performance": {**r.value["performance"], "fx_eur": perf.get("fx_eur")}}
        flag_modified(r, "value")
    db.commit()
    return len(rows)


def generate_due() -> int:
    """Genera los informes pendientes. Devuelve cuántos."""
    from app.application.services.portfolio_service import PortfolioService
    from app.infrastructure.db.database import SessionLocal
    from app.infrastructure.repositories.sqlalchemy_portfolio_repository import SqlAlchemyPortfolioRepository
    db = SessionLocal()
    made = 0
    try:
        ps = PortfolioService(SqlAlchemyPortfolioRepository(db))
        try:
            backfill_fx(db, ps)
        except Exception:
            db.rollback()
            logger.exception("No se pudo añadir el efecto divisa a los informes guardados")
        for p in due(db):
            try:
                report = build(db, p, ps)
                save(db, report)
                made += 1
            except Exception:
                db.rollback()
                logger.exception("No se pudo generar el informe %s", p["key"])
                continue
            auto_ai(db, report)
        if made:
            logger.info("Informes generados: %d", made)
    finally:
        db.close()
    return made


def trigger() -> bool:
    global _running
    with _lock:
        if _running:
            return False
        _running = True

    def run():
        global _running
        try:
            generate_due()
        finally:
            with _lock:
                _running = False
    threading.Thread(target=run, daemon=True, name="reports").start()
    return True


def start_scheduler() -> None:
    """Al arrancar y cada hora: los informes de los periodos que se hayan cerrado (y, la primera vez, los de atrás)."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def loop():
        time.sleep(30)   # que arranque antes la app (precios, caché)
        while True:
            try:
                trigger()
            except Exception:
                logger.exception("Fallo al comprobar los informes")
            time.sleep(CHECK_EVERY_S)
    threading.Thread(target=loop, daemon=True, name="reports-scheduler").start()


# --- Avisos ---------------------------------------------------------------------------------------------------------

def notices(db, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """Avisos «Informe … listo» de los informes nuevos (no reconstruidos) de los últimos días."""
    now = now or datetime.datetime.now()
    since = (now - datetime.timedelta(days=NOTICE_DAYS)).isoformat(timespec="seconds")
    out = []
    for r in list_reports(db):
        if not r["reconstructed"] and (r["created_at"] or "") >= since:
            pct = r.get("pct")
            perf = f": {'+' if pct and pct > 0 else ''}{pct:.1f} %".replace(".", ",") if pct is not None else ""
            what = {"Q": "Revisión trimestral", "Y": "Carta anual"}.get(r["kind"]) if r.get("has_ai") else None
            out.append({"key": f"informes|att|report|{r['key']}|", "ticker": None, "kind": "report",
                        "text": f"{what + ' lista' if what else 'Informe ' + KINDS[r['kind']].lower() + ' listo'} · {r['label']}{perf}",
                        "href": f"/portfolio#informes-{r['key']}"})
    return out


# --- IA (solo a petición) -----------------------------------------------------------------------------------------
# Mensual: un comentario corto. Trimestral: una revisión de la cartera entera tras la temporada de resultados. Anual: una
# carta (mejores y peores decisiones, lecciones, objetivos). El trimestral y el anual, con el modelo Pro: más datos y
# más razonamiento en una sola llamada (céntimos). Ninguno busca en internet: solo los datos de la app.

PRO_KINDS = ("Q", "Y")

PROMPT_HEAD = (
    "Eres el analista que revisa la cartera de inversión a largo plazo de un inversor particular. Datos del informe "
    "{kind} de {label} (importes en euros; rentabilidades en %, frente al {benchmark}):\n{data}\n\n"
    "Escribe en español, claro y útil para decidir, usando SOLO estos datos (no inventes cifras ni busques nada). "
    "No des órdenes de compra o venta: señala qué revisar y por qué.\nDevuelve ÚNICAMENTE un JSON válido:\n")

SCHEMA_M = """{"headline": "una frase con lo esencial del periodo",
  "points": ["3 a 5 puntos: qué explicó el resultado, decisiones a destacar, riesgos o avisos"],
  "watch": ["1 a 3 cosas concretas que vigilar el próximo periodo (vacío si nada)"]}"""

SCHEMA_Q = """{"headline": "una frase con lo esencial del trimestre",
  "attribution": ["2 a 4 puntos: qué explicó el resultado (qué posiciones, mercado frente a tus elecciones)"],
  "theses": [{"ticker": "…", "status": "verde|ámbar|rojo", "note": "una frase: cómo queda la tesis tras sus resultados"}],
  "portfolio": ["2 a 4 puntos sobre la cartera en conjunto: concentración, roles frente a sus bandas, dilución o recompras"],
  "decisions": ["2 o 3 puntos sobre tus decisiones del trimestre y cómo van (si hay porqués anotados, úsalos)"],
  "review": ["2 a 4 cosas concretas que revisar el próximo trimestre"]}"""

SCHEMA_Y = """{"headline": "una frase con lo esencial del año",
  "summary": "un párrafo (4-6 frases) con el balance del año, como la carta anual a uno mismo",
  "best": [{"ticker": "…", "why": "por qué fue de las mejores decisiones (usa el porqué anotado si lo hay)"}],
  "worst": [{"ticker": "…", "why": "por qué fue de las peores y qué enseña"}],
  "lessons": ["3 o 4 lecciones sobre la forma de invertir, sacadas de los patrones de los datos"],
  "theses": ["2 o 3 puntos sobre cómo han evolucionado las tesis durante el año"],
  "goals_check": ["si hay objetivos del año: uno por objetivo, si se cumplió y cómo (vacío si no hay)"],
  "next_year": ["3 o 4 propuestas de enfoque para el año siguiente"]}"""


def _ai_context(db, report: Dict[str, Any]) -> Dict[str, Any]:
    """Lo que la IA necesita además del informe (trimestral y anual): cartera con pesos y roles, estado de las tesis,
    revisiones de resultados del periodo, dilución o recompras y cómo han salido tus decisiones."""
    from app.application.services import decision_review_service as dr, insider_service, portfolio_roles_service as roles_service
    from app.application.services import price_monitor_service as pm
    from app.application.services.portfolio_service import PortfolioService
    from app.infrastructure.db.models import DBAsset, DBThesis, DBThesisReview
    from app.infrastructure.repositories.sqlalchemy_portfolio_repository import SqlAlchemyPortfolioRepository
    ps = PortfolioService(SqlAlchemyPortfolioRepository(db))
    txs, splits = pm.load_transactions(ps, db)
    df = pm.download_history(dr.symbols(txs)) if txs else pd.DataFrame()
    start, end = report["start"], report["end"]
    assets = {a.ticker: a.data or {} for a in db.query(DBAsset).filter(DBAsset.ticker.in_({t["ticker"] for t in txs})).all()}
    # la cartera al final del periodo: pesos y rol
    ch = pm.portfolio_changes(df.loc[:pd.Timestamp(end)], txs, splits) if not df.empty else {"positions": {}}
    roles = {r["key"]: r for r in roles_service.load_roles()}
    positions = [{"ticker": t, "weight_pct": round(p["weight"], 1), "role": (roles.get(assets.get(t, {}).get(roles_service.ROLE_KEY)) or {}).get("name")}
                 for t, p in sorted(ch["positions"].items(), key=lambda x: -x[1]["weight"])]
    by_role: Dict[str, float] = {}
    for p in positions:
        by_role[p["role"] or "sin rol"] = by_role.get(p["role"] or "sin rol", 0) + p["weight_pct"]
    role_bands = [{"role": r["name"], "weight_pct": round(by_role.get(r["name"], 0), 1), "target_min": r["min"], "target_max": r["max"]}
                  for r in roles.values()]
    # tesis: estado de hoy y revisiones de resultados del periodo
    theses = [{"ticker": t.ticker, "verdict": t.verdict} for t in db.query(DBThesis).all() if t.ticker in ch["positions"]]
    reviews = []
    for r, tk in (db.query(DBThesisReview, DBThesis.ticker).join(DBThesis, DBThesis.id == DBThesisReview.thesis_id)
                  .filter(DBThesisReview.created_at >= start, DBThesisReview.created_at <= end + "T23:59:59",
                          DBThesisReview.status.in_(("ready", "applied"))).all()):
        rep = r.report or {}
        reviews.append({"ticker": tk, "period": r.period, "verdict": rep.get("verdict"), "headline": rep.get("headline")})
    shares = {}
    for t in ch["positions"]:
        sh = insider_service.summary(t, fetch=False).get("shares")
        if sh:
            shares[t] = f"{sh['label']} ({sh['pct']:+.1f} %/año)"
    # tus decisiones del periodo y cómo han salido hasta hoy (frente al S&P 500)
    period_txs = [t for t in txs if start <= t["date"][:10] <= end]
    review = dr.build(df, period_txs, assets, splits=splits) if period_txs and not df.empty else {"ops": [], "summary": {}}
    outcomes = [{"ticker": o["ticker"], "date": o["date"], "type": o["type"], "amount_eur": round(o["amount_eur"] or 0),
                 "vs_sp500_pp_to_date": round(o["review"]["excess"], 1) if o["review"] and o["review"]["excess"] is not None else None,
                 "entry": (o["review"] or {}).get("entry"), "why": o.get("note")} for o in review["ops"]]
    sm = review.get("summary") or {}
    patterns = {"buys_beating_sp500": sm.get("buys"), "by_entry": sm.get("entry"), "tranches": sm.get("tranches")}
    return {"positions": positions, "roles": role_bands, "theses_now": theses, "earnings_reviews": reviews,
            "shares_trend": shares, "decisions_outcome": outcomes, "decision_patterns": patterns}


def ai_prompt(report: Dict[str, Any], context: Optional[Dict[str, Any]] = None, previous_goals: Optional[str] = None) -> str:
    import json
    slim = {k: report.get(k) for k in ("label", "performance", "flows", "dividends", "decisions", "theses", "news", "watchlist", "insiders", "months", "agenda")}
    data = json.dumps({**slim, **(context or {})}, ensure_ascii=False, default=str)[:40000]
    head = PROMPT_HEAD.format(kind=KINDS[report["kind"]].lower(), label=report["label"], benchmark=report.get("benchmark"), data=data)
    if report["kind"] == "Q":
        return head + SCHEMA_Q
    if report["kind"] == "Y":
        goals = f"\nObjetivos que el inversor se puso para este año: {previous_goals}\n" if previous_goals else ""
        return head + goals + SCHEMA_Y
    return head + SCHEMA_M


def ai_comment(db, key: str) -> Dict[str, Any]:
    """La IA del informe (una llamada a Gemini, sin búsqueda). Se guarda en el informe."""
    import os
    from google.genai import types
    from app.application.services import thesis_ai_service as ai
    from utils import llm_usage
    report = load(db, key)
    if not report:
        raise ValueError("Informe no encontrado")
    context = _ai_context(db, report) if report["kind"] in PRO_KINDS else None
    previous = load(db, f"Y{int(report['start'][:4]) - 1}") if report["kind"] == "Y" else None
    prompt = ai_prompt(report, context, (previous or {}).get("goals"))

    def call(client, model):
        with llm_usage.track_usage(f"report-{key}"):
            response = client.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(temperature=0.3))
            return ai.parse_json_or_repair(client, response.text or "", model)
    data, model_used = None, None
    if report["kind"] in PRO_KINDS:   # revisión trimestral y carta anual: el modelo Pro (con la clave de pago)
        pro = os.getenv("GEMINI_PRO_MODEL", "gemini-3.1-pro-preview")
        try:
            data, model_used = call(ai._client(), pro), pro
        except Exception as e:
            logger.warning("IA del informe %s con %s falló (%s): se repite con %s", key, pro, str(e)[:120], ai.GEMINI_MODEL)
    elif ai.free_news_key():
        for model in ai.FREE_TEXT_MODELS:
            try:
                data, model_used = call(ai._client(ai.free_news_key()), model), model
                break
            except Exception as e:
                logger.warning("Comentario del informe con la clave gratuita y %s falló (%s)", model, str(e)[:120])
    if data is None:
        data, model_used = call(ai._client(), ai.GEMINI_MODEL), ai.GEMINI_MODEL
    lists = lambda k: [x for x in data.get(k) or [] if x]
    out = {"headline": data.get("headline") or "", "model": model_used, "created_at": datetime.datetime.now().isoformat(timespec="seconds")}
    if report["kind"] == "Q":
        out.update(attribution=lists("attribution"), theses=[t for t in data.get("theses") or [] if isinstance(t, dict) and t.get("ticker")],
                   portfolio=lists("portfolio"), decisions=lists("decisions"), review=lists("review"))
    elif report["kind"] == "Y":
        out.update(summary=data.get("summary") or "", best=[x for x in data.get("best") or [] if isinstance(x, dict) and x.get("ticker")],
                   worst=[x for x in data.get("worst") or [] if isinstance(x, dict) and x.get("ticker")], lessons=lists("lessons"),
                   theses=lists("theses"), goals_check=lists("goals_check"), next_year=lists("next_year"))
    else:
        out.update(points=lists("points"), watch=lists("watch"))
    report["ai"] = out
    save(db, report)
    return out


def auto_ai(db, report: Dict[str, Any]) -> None:
    """La revisión trimestral o la carta anual, sola, al generarse el informe de un periodo recién cerrado (excepción a la
    regla de no llamar a la IA sola, decidida por el usuario). Un solo intento: si falla, queda el botón."""
    if report["kind"] not in PRO_KINDS or report.get("reconstructed"):
        return
    try:
        ai_comment(db, report["key"])
        logger.info("IA del informe %s escrita sola", report["key"])
    except Exception as e:
        db.rollback()
        logger.warning("No se pudo escribir sola la IA del informe %s (%s): queda el botón", report["key"], str(e)[:160])


def save_goals(db, key: str, text: str) -> None:
    """Tus objetivos para el año siguiente (en el informe anual): la carta del año que viene los compara con lo que pasó."""
    report = load(db, key)
    if not report or report["kind"] != "Y":
        raise ValueError("Los objetivos van en el informe anual")
    report["goals"] = (text or "").strip()[:2000] or None
    save(db, report)
