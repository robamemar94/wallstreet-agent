"""
Informes semanales, mensuales, trimestrales y anuales: una foto fija de cada periodo cerrado, para releerla después.

Se generan solos al cerrar el periodo (semanal: el sábado desde mediodía y cuando ha terminado el radar de noticias de esa
semana, que puede ser el lunes si la app no se arranca antes; sus noticias, las que encontró ese radar, como el resumen semanal de noticias; mensual y trimestral:
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
ANNUAL_DAYS = 252             # sesiones al año, para anualizar la volatilidad
MIN_SESSIONS = 20             # con menos sesiones, ni riesgo ni coste de la liquidez
LABEL_GAP = 7                 # separación mínima (% del alto) entre las cifras de la barra del valor
SECTIONS = ("curve", "risk", "cash", "inaction", "gains", "sectors")   # las del trimestral y el anual: si falta alguna, se rehace

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


def performance(df: pd.DataFrame, txs: List[Dict[str, Any]], splits, start: datetime.date, end: datetime.date, top: int = TOP) -> Dict[str, Any]:
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
    positions += [{"ticker": t, "pnl_eur": pnl.get(f"{days}d"), "contribution": None, "value_eur": 0.0, "weight": 0.0, "sold": True}
                  for t, pnl in (ch.get("closed_pnl") or {}).items()]   # las vendidas en el periodo también suman o restan
    positions = [p for p in positions if p["pnl_eur"] is not None]
    positions.sort(key=lambda p: p["pnl_eur"], reverse=True)
    return {"pct": per.get("pct"), "pnl_eur": per.get("abs_eur"), "value_start": v0, "value_end": ch["total_eur"],
            "bench_pct": bench, "vs_bench": (per["pct"] - bench) if per.get("pct") is not None and bench is not None else None,
            "best": [p for p in positions[:top] if p["pnl_eur"] > 0], "worst": [p for p in positions[::-1][:top] if p["pnl_eur"] < 0],
            "n_up": sum(1 for p in positions if p["pnl_eur"] > 0), "n_down": sum(1 for p in positions if p["pnl_eur"] < 0),
            "pnl_by_ticker": {p["ticker"]: round(p["pnl_eur"], 2) for p in positions},
            "fx_eur": per.get("fx_eur"), "incomplete": per.get("incomplete") or []}


def nav_window(portfolio_service, db, start: datetime.date, end: datetime.date) -> Optional[Dict[str, Any]]:
    """El valor por participación del Rendimiento (sin aportaciones) y el S&P 500 en el periodo, desde el cierre anterior.
    Si el Rendimiento empieza dentro del periodo, desde ese día («from»). None si no lo cubre. Si se lleva la liquidez,
    también el índice con ella y el % en efectivo («cash_uv», «cash_pct»)."""
    from app.application.services import cash_service
    from app.application.services.performance_service import PerformanceService
    from app.infrastructure.dependencies import get_settings_from_db
    since = (get_settings_from_db(db).get("performance") or {}).get("start_date")
    data = PerformanceService(portfolio_service).get_performance_data(since)
    dates, nav = data.get("dates") or [], data.get("portfolio_uv") or []
    if not dates or dates[0] > end.isoformat():
        return None
    base = max((i for i, d in enumerate(dates) if d < start.isoformat()), default=0)
    idx = [i for i, d in enumerate(dates) if i >= base and d <= end.isoformat()]
    if len(idx) < 2:
        return None
    bench = (data.get("benchmarks") or {}).get(BENCHMARK_NAME)
    try:
        pat = cash_service.patrimony(db, dates, data.get("portfolio_values_eur") or [], nav)
    except Exception:
        logger.exception("Informe: sin la serie con liquidez")
        pat = {}
    return {"dates": [dates[i] for i in idx], "port": [nav[i] for i in idx],
            "bench": [bench[i] for i in idx] if bench and len(bench) == len(dates) else None,
            "cash_uv": [pat["uv"][i] for i in idx] if pat.get("uv") else None,
            "cash_pct": [pat["cash_pct"][i] for i in idx] if pat.get("cash_pct") else None,
            "from": dates[idx[0]] if dates[0] > start.isoformat() else None}


def _worst_fall(vals: Optional[List[float]]) -> Optional[float]:
    if not vals:
        return None
    peak, dd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        dd = min(dd, 100 * (v / peak - 1) if peak else 0.0)
    return dd


def drawdown(nav: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Peor caída del periodo (máximo drawdown) de la cartera y del S&P 500 en euros."""
    if not nav:
        return None
    return {"pct": _worst_fall(nav["port"]), "bench_pct": _worst_fall(nav["bench"]), "from": nav["from"]}


def curve(nav: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """La gráfica del periodo: cartera y S&P 500 en base 100 al empezar."""
    if not nav:
        return None
    rebase = lambda xs: [round(100 * x / xs[0], 2) for x in xs] if xs and xs[0] else None
    return {"dates": nav["dates"], "port": rebase(nav["port"]), "bench": rebase(nav["bench"])}


def risk(nav: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Riesgo del periodo con el valor por participación (sin aportaciones): rentabilidad de la cartera (TWR),
    volatilidad anualizada y beta frente al S&P 500 en euros, con los rendimientos diarios."""
    if not nav or len(nav["port"]) < MIN_SESSIONS:
        return None
    cols = {"port": nav["port"]} | ({"bench": nav["bench"]} if nav.get("bench") else {})
    rets = pd.DataFrame(cols, dtype=float).pct_change().dropna()
    first = lambda xs: next((x for x in xs if x), None)
    out = {"twr": 100 * (nav["port"][-1] / nav["port"][0] - 1) if nav["port"][0] else None,
           "vol": float(rets["port"].std() * ANNUAL_DAYS ** .5 * 100), "from": nav["from"]}
    if "bench" in rets and rets["bench"].var() > 0:
        b0 = first(nav["bench"])
        out.update(bench_twr=100 * (nav["bench"][-1] / b0 - 1) if b0 else None,
                   bench_vol=float(rets["bench"].std() * ANNUAL_DAYS ** .5 * 100),
                   beta=float(rets["port"].cov(rets["bench"]) / rets["bench"].var()))
    return out


def cash_cost(nav: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Lo que restó (o sumó) el efectivo sin invertir: el patrimonio con liquidez frente a la cartera, en pp, como en
    Rendimiento; y el % medio en efectivo. Desde que se lleva la liquidez («from», si empieza dentro del periodo)."""
    uv = (nav or {}).get("cash_uv")
    if not uv:
        return None
    k = next((i for i, v in enumerate(uv) if v), None)
    if k is None or len(uv) - k < MIN_SESSIONS:   # unos días con liquidez no dicen nada
        return None
    pcts = [v for v in (nav.get("cash_pct") or [])[k:] if v is not None]
    return {"pp": 100 * (uv[-1] / uv[k] - nav["port"][-1] / nav["port"][k]),
            "avg_pct": sum(pcts) / len(pcts) if pcts else None, "from": nav["dates"][k] if k else None}


def inaction(df: pd.DataFrame, txs: List[Dict[str, Any]], splits, start: datetime.date, end: datetime.date) -> Optional[Dict[str, Any]]:
    """La cartera que tenías al empezar, sin tocarla en todo el periodo: su rentabilidad y su resultado. Frente a la
    real, lo que sumaron o restaron tus compras y ventas."""
    from app.application.services import price_monitor_service as pm
    before = [t for t in txs if t["date"][:10] < start.isoformat()]
    if not before:
        return None
    end_ts, start_ts = pd.Timestamp(end), pd.Timestamp(start) - pd.Timedelta(days=1)
    dfe = df.loc[:end_ts]
    if dfe.empty:
        return None
    days = max((dfe.index[-1] - start_ts).days, 1)
    per = pm.portfolio_changes(dfe, before, splits, rolling_days=(days,))["periods"].get(f"{days}d") or {}
    return {"pct": per.get("pct"), "pnl_eur": per.get("abs_eur")} if per.get("pct") is not None else None


def gains(df: pd.DataFrame, txs: List[Dict[str, Any]], splits, start: str, end: str) -> Dict[str, Any]:
    """Ganancias realizadas con las ventas del periodo (sobre el precio medio de compra en euros, sin comisiones) y la
    plusvalía latente de lo que quedaba al cierre."""
    from app.application.services import price_monitor_service as pm
    from app.application.services.decision_review_service import _fx_at
    book: Dict[str, List[float]] = {}   # ticker → [acciones de hoy, coste en €]
    realized: Dict[str, float] = {}
    for t in sorted(txs, key=lambda x: x["date"]):
        day = t["date"][:10]
        if day > end:
            break
        fx = t.get("fx") or _fx_at(df, t.get("currency"), t["date"])
        if not fx:
            continue
        amount = t["shares"] * t["price"] * (0.01 if t.get("currency") == "GBp" else 1) * fx
        sh = pm.adjusted_shares(t, splits.get(t["ticker"], []))
        b = book.setdefault(t["ticker"], [0.0, 0.0])
        if t["type"] == "BUY":
            b[0] += sh
            b[1] += amount
        elif b[0] > 0:
            q = min(sh, b[0])
            cost = b[1] * q / b[0]
            b[0] -= q
            b[1] -= cost
            if day >= start:
                realized[t["ticker"]] = realized.get(t["ticker"], 0.0) + amount * q / sh - cost
    held = pm.portfolio_changes(df.loc[:pd.Timestamp(end)], txs, splits)["positions"] if not df.loc[:pd.Timestamp(end)].empty else {}
    cost = sum(book[t][1] for t in held if t in book)
    value = sum(p["value_eur"] for t, p in held.items() if t in book)
    return {"realized_eur": sum(realized.values()), "n_sales": len(realized),
            "realized": sorted(({"ticker": t, "eur": v} for t, v in realized.items()), key=lambda x: -x["eur"]),
            "latent_eur": value - cost if held else None, "latent_pct": 100 * (value / cost - 1) if cost > 0 else None}


def sectors(pnl_by_ticker: Dict[str, float], assets: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """De dónde vino el resultado por sector (también las vendidas), de más a menos."""
    from app.application.services.distribution_service import SECTOR_ES
    out: Dict[str, Dict[str, Any]] = {}
    for t, pnl in (pnl_by_ticker or {}).items():
        raw = (assets.get(t) or {}).get("sector")
        s = out.setdefault(SECTOR_ES.get(raw, raw or "Sin sector"), {"sector": SECTOR_ES.get(raw, raw or "Sin sector"), "pnl_eur": 0.0, "tickers": []})
        s["pnl_eur"] += pnl
        s["tickers"].append({"ticker": t, "pnl_eur": pnl})
    for s in out.values():
        s["tickers"].sort(key=lambda x: -abs(x["pnl_eur"]))
    return sorted(out.values(), key=lambda s: -s["pnl_eur"])


def portfolio_shift(df: pd.DataFrame, txs: List[Dict[str, Any]], splits, start: datetime.date, end: datetime.date, top: int = 10) -> Dict[str, Any]:
    """Cómo cambió la cartera: empresas que entraron y salieron, las de más peso al final con su peso al empezar y la
    concentración (lo que suman las 5 mayores y el número efectivo de posiciones, 1/Σpeso²) al empezar y al final."""
    from app.application.services import price_monitor_service as pm
    start_ts, end_ts = pd.Timestamp(start) - pd.Timedelta(days=1), pd.Timestamp(end)
    before = df.loc[:start_ts]
    w0 = pm.portfolio_changes(before, txs, splits)["positions"] if not before.empty else {}
    w1 = pm.portfolio_changes(df.loc[:end_ts], txs, splits)["positions"]
    ranked = sorted(w1.items(), key=lambda kv: -kv[1]["weight"])[:top]
    top5 = lambda w: sum(sorted((p["weight"] for p in w.values()), reverse=True)[:5]) if w else None
    effective = lambda w: 1 / sum((p["weight"] / 100) ** 2 for p in w.values()) if w and any(p["weight"] for p in w.values()) else None
    return {"entered": sorted(set(w1) - set(w0)), "exited": sorted(set(w0) - set(w1)),
            "top": [{"ticker": t, "weight": p["weight"], "weight_start": (w0.get(t) or {}).get("weight")} for t, p in ranked],
            "top5_start": top5(w0), "top5_end": top5(w1), "n_start": len(w0), "n_end": len(w1),
            "eff_start": effective(w0), "eff_end": effective(w1)}


def decision_results(df: pd.DataFrame, txs: List[Dict[str, Any]], assets: Dict[str, Dict[str, Any]], splits,
                     start: str, end: str, top: int = 3) -> Dict[str, List[Dict[str, Any]]]:
    """Las decisiones del periodo que mejor y peor han salido desde entonces frente al S&P 500 (como la pestaña
    Decisiones: en una venta, acertar es que la acción caiga después)."""
    from app.application.services import decision_review_service as dr
    ops = dr.build(df, [t for t in txs if start <= t["date"][:10] <= end], assets, None, splits)["ops"]
    scored = sorted((o for o in ops if o.get("review") and o["review"].get("excess") is not None), key=dr.result_for_you, reverse=True)
    pick = lambda o: {"ticker": o["ticker"], "date": o["date"][:10], "type": o["type"], "amount_eur": o.get("amount_eur"),
                      "since": o["review"]["since"], "result": dr.result_for_you(o)}
    good = [pick(o) for o in scored if dr.result_for_you(o) > 0][:top]
    bad = [pick(o) for o in reversed(scored) if dr.result_for_you(o) < 0][:top]
    buys = [o for o in scored if o["type"] == "BUY"]
    return {"best": good, "worst": bad, "n_buys": len(buys), "buys_beat": sum(1 for o in buys if o["review"]["excess"] > 0)}


def theses_close(db, start: str, end: str) -> List[Dict[str, Any]]:
    """Las tesis abiertas al cierre: su veredicto y, si cambió en el periodo, desde cuál."""
    from app.infrastructure.db.models import DBThesis, DBThesisEvent
    changes = {}
    for e in (db.query(DBThesisEvent).filter(DBThesisEvent.type == "verdict", DBThesisEvent.date >= start,
                                             DBThesisEvent.date <= end + " 23:59").order_by(DBThesisEvent.date).all()):
        changes.setdefault(e.thesis_id, []).append(e.title)
    order = {v: i for i, v in enumerate(("ROTA", "DEBILITADA", "VIGILANCIA", "INTACTA", "REFORZADA"))}
    out = [{"ticker": t.ticker, "verdict": t.verdict or "INTACTA", "changes": changes.get(t.id, [])}
           for t in db.query(DBThesis).filter(DBThesis.status != "CERRADA").all()]
    return sorted(out, key=lambda x: (not x["changes"], order.get(x["verdict"], 9), x["ticker"]))


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


def radar_window(p: Dict[str, Any]) -> tuple:
    """Semanal: desde cuándo y hasta cuándo (ISO) encontró sus noticias el radar que repasa esa semana (del sábado
    siguiente a las 8:00 al otro sábado), las mismas que su resumen semanal de noticias."""
    from app.application.services.thesis_ai_service import RADAR_START_HOUR
    sat = datetime.datetime.combine(p["end"] + datetime.timedelta(days=1), datetime.time(RADAR_START_HOUR))
    return sat.isoformat(), (sat + datetime.timedelta(days=7)).isoformat()


def waits_for_radar(p: Dict[str, Any], now: datetime.datetime) -> bool:
    """El semanal de la semana que acaba de cerrar: sus noticias llegan con el radar de esa semana."""
    from app.application.services.thesis_ai_service import radar_week_start
    return p["kind"] == "W" and radar_week_start(now).date() == p["end"] + datetime.timedelta(days=1)


def news(db, p: Dict[str, Any], limit: int = 8) -> List[Dict[str, Any]]:
    """Las noticias más relevantes del radar (materialidad 4 o 5). Semanal: las que encontró el radar que repasa esa
    semana. El resto: las publicadas en el periodo (sin fecha, la del hallazgo)."""
    from sqlalchemy import func
    from app.infrastructure.db.models import DBThesis, DBThesisNews
    q = (db.query(DBThesisNews, DBThesis.ticker).join(DBThesis, DBThesis.id == DBThesisNews.thesis_id)
         .filter(DBThesisNews.materiality >= 4))
    if p["kind"] == "W":
        found_from, found_to = radar_window(p)
        q = q.filter(DBThesisNews.found_at >= found_from, DBThesisNews.found_at < found_to)
    else:
        day = func.coalesce(func.nullif(func.substr(DBThesisNews.date, 1, 10), ""), func.substr(DBThesisNews.found_at, 1, 10))
        q = q.filter(day >= p["start"].isoformat(), day <= p["end"].isoformat())
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
        buys = [x for x in market_cache.peek("insider_trades_v2", t) or [] if x["kind"] == "buy" and start <= x["date"] <= end]
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
    """El informe de un periodo cerrado. Si el Rendimiento empieza dentro del periodo, se estudia desde ese día («since»):
    lo de antes era la cartera vieja y es ruido."""
    from app.application.services import price_monitor_service as pm
    from app.application.services.decision_review_service import symbols
    from app.infrastructure.db.models import DBAsset, DBWatchlistItem
    now = now or datetime.datetime.now()
    since = study_start(db, p)
    q = {**p, "start": since}   # el periodo que se estudia
    txs, splits = pm.load_transactions(portfolio_service, db)
    start, end = since.isoformat(), p["end"].isoformat()
    df = pm.download_history(symbols(txs)) if txs else pd.DataFrame()
    held = sorted({t["ticker"] for t in txs})
    assets = {a.ticker: a.data or {} for a in db.query(DBAsset).filter(DBAsset.ticker.in_(held)).all()}
    reconstructed = (now - p["ready"]).days > RECONSTRUCTED_AFTER_DAYS
    open_now = [i.ticker for i in portfolio_service.get_active_portfolio() if i.shares > 0]
    watch = [w.ticker for w in db.query(DBWatchlistItem).all()]
    report = {
        "key": p["key"], "kind": p["kind"], "label": p["label"], "start": p["start"].isoformat(), "end": end,
        "since": start if since > p["start"] else None,
        "created_at": now.isoformat(timespec="seconds"), "reconstructed": reconstructed,
        "performance": performance(df, txs, splits, since, p["end"], top=5 if p["kind"] in ("Q", "Y") else TOP) if not df.empty else {},
        "flows": flows(df, txs, start, end), "dividends": dividends(db, df, start, end),
        "decisions": decisions(df, txs, assets, splits, start, end),
        "theses": theses_events(db, start, end), "news": news(db, q),
        "watchlist": watchlist_zone(db, start, end), "insiders": insider_buys(open_now + watch, start, end),
        "benchmark": BENCHMARK_NAME, "ai": None,
    }
    if p["kind"] in ("Y", "Q"):   # balance: peor caída y cómo cambió la cartera; en el anual, tus decisiones y tesis
        try:
            nav = nav_window(portfolio_service, db, since, p["end"])
        except Exception:
            logger.exception("Informe %s: sin la serie del Rendimiento", p["key"])
            nav = None
        for k, f in (("drawdown", lambda: drawdown(nav)), ("curve", lambda: curve(nav)),
                     ("risk", lambda: risk(nav)), ("cash", lambda: cash_cost(nav)),
                     ("inaction", lambda: inaction(df, txs, splits, since, p["end"]) if not df.empty else None),
                     ("gains", lambda: gains(df, txs, splits, start, end) if not df.empty else None),
                     ("sectors", lambda: sectors((report["performance"] or {}).get("pnl_by_ticker"), assets)),
                     ("shift", lambda: portfolio_shift(df, txs, splits, since, p["end"]) if not df.empty else None),
                     ("decision_results", lambda: decision_results(df, txs, assets, splits, start, end) if not df.empty else None),
                     ("theses_close", lambda: theses_close(db, start, end) if p["kind"] == "Y" else None)):
            try:
                report[k] = f()
            except Exception:
                logger.exception("Informe %s: no se pudo calcular %s", p["key"], k)
                report[k] = None
    if p["kind"] in ("Y", "Q"):   # el año o el trimestre, mes a mes (desde el que empieza el estudio)
        report["months"] = []
        for m in range(since.month, p["end"].month + 1):
            mp = period("M", datetime.date(p["start"].year, m, 1))
            if mp["start"] > p["end"] or mp["start"] > now.date():
                break
            perf = performance(df, txs, splits, max(mp["start"], since), min(mp["end"], now.date())) if not df.empty else {}
            report["months"].append({"label": mp["label"], "pct": perf.get("pct"), "bench_pct": perf.get("bench_pct")})
    if not reconstructed and p["kind"] in ("W", "M"):   # lo que viene (solo al cerrar el periodo: es la agenda de ese momento)
        nxt = next_period(p)
        report["agenda"] = agenda(open_now, nxt["start"], nxt["end"])
    return report


def study_start(db, p: Dict[str, Any]) -> datetime.date:
    """Desde cuándo se estudia el periodo: su primer día o, si el Rendimiento empieza dentro, ese día."""
    try:
        s = start_date(db)
    except Exception:
        return p["start"]
    return s if p["start"] < s <= p["end"] else p["start"]


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
    if any(waits_for_radar(p, now) for p in out):
        from app.application.services import thesis_news_runner
        if thesis_news_runner.radar_pending():   # cuando acabe el radar (que lo vuelve a pedir)
            out = [p for p in out if not waits_for_radar(p, now)]
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


def backfill_sections(db, portfolio_service) -> int:
    """Los trimestrales y anuales guardados sin alguna de las secciones de balance (`SECTIONS`: curva, riesgo, sectores…) y
    los que empiezan antes del Rendimiento sin estudiarse desde él («since»): se rehacen las cifras y se conserva lo escrito en su día (IA, objetivos, fecha, agenda). Una vez hechos, no hace nada."""
    from sqlalchemy.orm.attributes import flag_modified
    def stale(v: Dict[str, Any]) -> bool:
        p = period(v["kind"], datetime.date.fromisoformat(v["start"]))
        since = study_start(db, p)
        return ((v["kind"] in ("Q", "Y") and any(k not in v for k in SECTIONS))
                or v.get("since") != (since.isoformat() if since > p["start"] else v.get("since")))
    rows = [r for r in db.query(DBSetting).filter(DBSetting.key.like(KEY_PREFIX + "%")).all()
            if isinstance(r.value, dict) and r.value.get("kind") in KINDS and r.value.get("start") and stale(r.value)]
    for r in rows:
        p = period(r.value["kind"], datetime.date.fromisoformat(r.value["start"]))
        fresh = build(db, p, portfolio_service)
        keep = {k: r.value[k] for k in ("created_at", "reconstructed", "ai", "goals", "agenda") if k in r.value}
        r.value = {**fresh, **keep}
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
        try:
            backfill_sections(db, ps)
        except Exception:
            db.rollback()
            logger.exception("No se pudieron añadir las secciones de balance a los informes guardados")
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


# --- Periodos en curso ----------------------------------------------------------------------------------------------
# El mes, el trimestre y el año en curso (del primer día a hoy) se calculan al abrirlos, sin guardarlos ni avisar ni IA:
# el informe de verdad se hace al cerrar el periodo. El semanal no: la semana ya está en la tarjeta 1S y sus noticias
# llegan con el radar del sábado.

LIVE_KINDS = ("M", "Q", "Y")
LIVE_SUFFIX = "-en-curso"
LIVE_TTL_S = 1800   # como el histórico de precios
_live: Dict[str, Dict[str, Any]] = {}


def live_key(kind: str) -> str:
    return kind + LIVE_SUFFIX


def live_report(db, kind: str, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """El informe del periodo en curso hasta hoy (caché de 30 min). En el mensual, «Lo que viene»: lo que queda de mes."""
    now = now or datetime.datetime.now()
    cached = _live.get(kind)
    if cached and time.time() - cached["at"] < LIVE_TTL_S and cached["report"]["end"] == now.date().isoformat():
        return cached["report"]
    from app.application.services.portfolio_service import PortfolioService
    from app.infrastructure.repositories.sqlalchemy_portfolio_repository import SqlAlchemyPortfolioRepository
    p = period(kind, now.date())
    report = build(db, {**p, "key": live_key(kind), "end": now.date()}, PortfolioService(SqlAlchemyPortfolioRepository(db)), now)
    report.update(live=True, reconstructed=False, period_end=p["end"].isoformat(),
                  agenda=[a for a in report.get("agenda") or [] if (a.get("date") or "") > report["end"]])
    _live[kind] = {"at": time.time(), "report": report}
    return report


_warming = threading.Lock()


def warm_live() -> None:
    """Prepara en segundo plano los informes en curso (al entrar en Cartera), para que Informes abra sin esperar."""
    if all(k in _live and time.time() - _live[k]["at"] < LIVE_TTL_S for k in LIVE_KINDS) or not _warming.acquire(blocking=False):
        return

    def run():
        from app.infrastructure.db.database import SessionLocal
        db = SessionLocal()
        try:
            for kind in LIVE_KINDS:
                live_report(db, kind)
        except Exception:
            logger.exception("No se pudieron preparar los informes en curso")
        finally:
            db.close()
            _warming.release()
    threading.Thread(target=run, daemon=True, name="reports-live").start()


def get(db, key: str) -> Optional[Dict[str, Any]]:
    """Un informe guardado o, con la clave de uno en curso («M-en-curso»…), ese; listo para enseñar (display)."""
    kind = key[:-len(LIVE_SUFFIX)] if key.endswith(LIVE_SUFFIX) else None
    report = live_report(db, kind) if kind in LIVE_KINDS else load(db, key)
    if not report:
        return None
    from app.infrastructure.db.models import DBAsset
    from app.interfaces.views.market_views import get_currency_symbol
    tickers = {d["ticker"] for d in report.get("decisions") or []}
    cur = {a.ticker: get_currency_symbol((a.data or {}).get("currency", "USD"), a.ticker)
           for a in db.query(DBAsset).filter(DBAsset.ticker.in_(tickers)).all()} if tickers else {}
    if report.get("kind") == "Y":   # los dividendos del año anterior, para comparar
        prev = load(db, f"Y{int(report['start'][:4]) - 1}")
        report = {**report, "div_prev": ((prev or {}).get("dividends") or {}).get("total")}
    return display(report, cur)


MONTHS = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']


def takeaways(r: Dict[str, Any]) -> List[str]:
    """Lo esencial del periodo en frases cortas, con los datos (sin IA)."""
    from utils.number_format import es
    p, out = r.get("performance") or {}, []
    if p.get("pct") is not None and p.get("bench_pct") is not None:
        out.append(f"{'Por delante' if p['pct'] >= p['bench_pct'] else 'Por detrás'} del {r.get('benchmark', 'S&P 500')}: "
                   f"{es(p['pct'], 1, sign=True)} % frente a {es(p['bench_pct'], 1, sign=True)} %.")
    months = [m for m in r.get("months") or [] if m.get("pct") is not None]
    if len(months) > 1:
        hi, lo = max(months, key=lambda m: m["pct"]), min(months, key=lambda m: m["pct"])
        out.append(f"Mejor mes, {hi['label'].split()[0].lower()} ({es(hi['pct'], 1, sign=True)} %); peor, "
                   f"{lo['label'].split()[0].lower()} ({es(lo['pct'], 1, sign=True)} %).")
    if p.get("best"):
        b = p["best"][0]
        text = f"{b['ticker']} fue la que más aportó ({es(b['pnl_eur'], 0, sign=True)} €)"
        if p.get("worst"):
            w = p["worst"][0]
            text += f"; {w['ticker']}, la que más restó ({es(w['pnl_eur'], 0, sign=True)} €)"
        out.append(text + ".")
    ina = r.get("inaction") or {}
    if ina.get("pct") is not None and p.get("pct") is not None:
        gap = p["pct"] - ina["pct"]
        out.append(f"Tus compras y ventas {'sumaron' if gap >= 0 else 'restaron'} {es(abs(gap), 1)} pp: sin tocar la cartera "
                   f"del principio, {es(ina['pct'], 1, sign=True)} %.")
    secs = [x for x in r.get("sectors") or [] if x["pnl_eur"]]
    if len(secs) > 1 and secs[0]["pnl_eur"] > 0:
        text = f"Por sectores, {secs[0]['sector'].lower()} aportó más ({es(secs[0]['pnl_eur'], 0, sign=True)} €)"
        if secs[-1]["pnl_eur"] < 0:
            text += f"; {secs[-1]['sector'].lower()} restó más ({es(secs[-1]['pnl_eur'], 0, sign=True)} €)"
        out.append(text + ".")
    rk = r.get("risk") or {}
    if rk.get("alpha") is not None:
        out.append(f"Con beta {es(rk['beta'], 2)}, el riesgo asumido daba {es(rk['beta_ret'], 1, sign=True)} %; tu selección "
                   f"{'sumó' if rk['alpha'] >= 0 else 'restó'} {es(abs(rk['alpha']), 1)} pp más.")
    elif rk.get("beta") is not None and rk.get("bench_vol"):
        out.append(f"Más {'movida' if rk['vol'] > rk['bench_vol'] else 'tranquila'} que el índice: volatilidad {es(rk['vol'], 0)} % "
                   f"frente a {es(rk['bench_vol'], 0)} % (beta {es(rk['beta'], 2)}).")
    dr = r.get("decision_results") or {}
    if dr.get("n_buys"):
        out.append(f"{dr['buys_beat']} de {dr['n_buys']} compras van por delante del {r.get('benchmark', 'S&P 500')} desde que las hiciste.")
    return out


def bridge(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Del valor al empezar al del cierre, en una sola columna: aportaciones netas, lo que hicieron las empresas y la
    divisa (posición de cada tramo en % de la columna). «Sin precio y ajustes», lo que no cuadra (posiciones sin precio
    al empezar, redondeos), solo si es más del 0,5 % del valor final."""
    p, f = r.get("performance") or {}, r.get("flows") or {}
    if p.get("value_end") is None or p.get("pnl_eur") is None:
        return None
    fx = p.get("fx_eur") or 0.0
    other = p["value_end"] - (p.get("value_start") or 0) - (f.get("net") or 0) - p["pnl_eur"]
    parts = [("Al empezar", p.get("value_start") or 0, "base"), ("Aportaciones", f.get("net") or 0, "flow"),
             ("Empresas", p["pnl_eur"] - fx, "pnl"), ("Divisa", fx, "fx"),
             ("Sin precio y ajustes", other if abs(other) > 0.005 * p["value_end"] else 0.0, "pnl")]
    steps, acc = [], 0.0
    for label, eur, kind in parts:   # una sola columna: cada tramo, encima del anterior (lo que resta, hacia abajo)
        if eur or kind == "base":
            steps.append({"label": label, "eur": eur, "kind": kind, "lo": min(acc, acc + eur), "hi": max(acc, acc + eur)})
            acc += eur
    top = max([s["hi"] for s in steps] + [p["value_end"], 1.0])
    for x in steps:
        x["bottom"], x["height"] = 100 * x["lo"] / top, max(100 * (x["hi"] - x["lo"]) / top, 0.6)
    floor = 100.0   # la cifra de cada tramo, a su altura; de arriba abajo, sin pisarse (LABEL_GAP en % del alto)
    for x in reversed(steps):
        x["label_pct"] = min(x["bottom"] + x["height"] / 2, floor - LABEL_GAP)
        floor = x["label_pct"]
    for x in steps:   # si se salen por abajo, se suben
        x["label_pct"] = max(x["label_pct"], 0.0)
    return {"steps": steps, "end": p["value_end"], "end_pct": 100 * p["value_end"] / top}


# --- Para enseñar: las listas largas, resumidas -------------------------------------------------------------------------

LONG_LIST = 8     # más líneas que esto: se agrupan (en la práctica, el trimestral y el anual)
TOP_SHORT = 5


def display(report: Dict[str, Any], currencies: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """El informe con las listas largas resumidas (los datos guardados no cambian):
    - decisiones: una línea por empresa y tipo (veces, precio medio e importe), de mayor a menor, con cada una al
      desplegarla, y los totales;
    - dividendos: las 5 empresas que más pagaron;
    - tesis: en el trimestral y el anual, solo los cambios de veredicto (presentaciones de resultados siempre hay);
    - noticias: en el trimestral, las 5 más relevantes; en el anual, ni noticias ni directivos (es un balance);
    - en el trimestral y el anual, siempre agrupadas las decisiones y los dividendos, y sin la watchlist."""
    r = dict(report)
    long_period = r.get("kind") in ("Q", "Y")
    decs = r.get("decisions") or []
    r["dec_totals"] = {t: {"n": sum(1 for d in decs if d["type"] == t), "eur": sum(d.get("amount_eur") or 0 for d in decs if d["type"] == t)}
                       for t in ("BUY", "SELL")}
    if len(decs) > LONG_LIST or (long_period and decs):
        groups: Dict[tuple, Dict[str, Any]] = {}
        for d in decs:
            g = groups.setdefault((d["ticker"], d["type"]), {"ticker": d["ticker"], "type": d["type"], "n": 0, "amount_eur": 0.0,
                                                             "shares": 0.0, "cost": 0.0, "notes": [], "items": [],
                                                             "cur": (currencies or {}).get(d["ticker"], "")})
            g["n"] += 1
            g["amount_eur"] += d.get("amount_eur") or 0
            g["shares"] += d.get("shares") or 0
            g["cost"] += (d.get("shares") or 0) * (d.get("price") or 0)
            g["items"].append(d)
            if d.get("note"):
                g["notes"].append(f"{d['date'][8:10]}/{d['date'][5:7]}: {d['note']}")
        for g in groups.values():
            g["avg_price"] = g.pop("cost") / g["shares"] if g["shares"] else None
            g["items"].sort(key=lambda d: d["date"])
        r["dec_groups"] = sorted(groups.values(), key=lambda g: -g["amount_eur"])
    items = (r.get("dividends") or {}).get("items") or []
    if len(items) > LONG_LIST or (long_period and items):
        by: Dict[str, Dict[str, Any]] = {}
        for i in items:
            b = by.setdefault(i["ticker"], {"ticker": i["ticker"], "n": 0, "eur": 0.0})
            b["n"] += 1
            b["eur"] += i["eur"]
        ranked = sorted(by.values(), key=lambda b: -b["eur"])
        r["div_top"], r["div_rest"] = ranked[:TOP_SHORT], len(ranked) - TOP_SHORT
    if long_period:
        r["theses"] = [e for e in r.get("theses") or [] if e.get("type") == "verdict"]
        r["news"] = (r.get("news") or [])[:TOP_SHORT]
        r["watchlist"] = []   # una señal del momento, no un balance
    if long_period:   # el trimestral y el anual, en pestañas
        r["buy_groups"] = [g for g in r.get("dec_groups") or [] if g["type"] == "BUY"]
        r["sell_groups"] = [g for g in r.get("dec_groups") or [] if g["type"] == "SELL"]
        by_month: Dict[str, float] = {}
        for i in items:
            by_month[i["date"][:7]] = by_month.get(i["date"][:7], 0.0) + i["eur"]
        if r.get("start"):
            first = datetime.date.fromisoformat(r["start"])
            r["div_by_month"] = [{"month": MONTHS[first.month + k - 1][:3], "eur": by_month.get(f"{first.year}-{first.month + k:02d}", 0.0)}
                                 for k in range(12 if r["kind"] == "Y" else 3)]
        comp: Dict[str, Dict[str, Any]] = {}
        for i in items:
            c = comp.setdefault(i["ticker"], {"ticker": i["ticker"], "n": 0, "eur": 0.0})
            c["n"] += 1
            c["eur"] += i["eur"]
        r["div_by_company"] = sorted(comp.values(), key=lambda c: -c["eur"])
        r["bridge"] = bridge(r)
        rk, pf = r.get("risk") or {}, r.get("performance") or {}
        if rk.get("beta") is not None and rk.get("bench_twr") is not None and rk.get("twr") is not None:
            # lo que tocaba solo por ir más (o menos) expuesto al mercado y lo que sumó la selección (sin tipo sin riesgo)
            rk = r["risk"] = {**rk, "beta_ret": rk["beta"] * rk["bench_twr"], "alpha": rk["twr"] - rk["beta"] * rk["bench_twr"]}
        if rk.get("twr") is not None and pf.get("pct") is not None and not rk.get("from"):
            r["timing_pp"] = pf["pct"] - rk["twr"]   # tu rentabilidad (con tus aportaciones) frente a la de la cartera
        r["takeaways"] = takeaways(r)
    if r.get("kind") == "Y":   # el anual es un balance: sin el día a día (noticias, directivos); las tesis, al cierre
        r["news"], r["insiders"] = [], []
        if r.get("theses_close"):
            r["theses"] = []
    return r


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
    "Claves: since = desde cuándo se estudia (antes era la cartera vieja); inaction = la cartera del principio sin tocar "
    "(frente a performance.pct, lo que sumaron tus compras y ventas); risk = rentabilidad sin aportaciones (twr), volatilidad "
    "y beta frente al índice; gains = ganancia realizada y latente; decisions_outcome.entry = cómo venía el precio al "
    "comprar; vs_sp500_pp_to_date = la decisión frente al índice hasta hoy; why y your_notes = lo que el inversor "
    "pensaba entonces (úsalo para juzgar sus razones, no solo los resultados); waiting_entries = precios de compra que "
    "esperaba (Mi precio): si llegó (reached), si compró y cuánto se movió desde entonces (si no llegó y subió, lo que le "
    "costó apurar; si llegó y no compró, por qué pudo ser); notes_moves = cuánto se movió cada empresa desde su primera nota.\n"
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
    start, end = report.get("since") or report["start"], report["end"]   # desde el inicio del Rendimiento, como el informe
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
                 "entry": (o["review"] or {}).get("entry"), "why": o.get("note"),
                 "at_decision": {k: (o.get("snapshot") or {}).get(k) for k in ("zone", "cagr", "verdict")} if o.get("snapshot") else None}
                for o in review["ops"]]
    sm = review.get("summary") or {}
    patterns = {"buys_beating_sp500": sm.get("buys"), "by_entry": sm.get("entry"), "tranches": sm.get("tranches")}
    from app.application.services import company_notes_service
    return {"positions": positions, "roles": role_bands, "theses_now": theses, "earnings_reviews": reviews,
            "shares_trend": shares, "decisions_outcome": outcomes, "decision_patterns": patterns,
            "your_notes": company_notes_service.notes_between(db, start, end), **_waiting(db, txs, start, end)}


def _waiting(db, txs, start: str, end: str) -> Dict[str, Any]:
    try:
        w = waiting_entries(db, txs, start, end)
        return {"waiting_entries": w["entries"], "notes_moves": w["notes_moves"]}
    except Exception:
        logger.exception("Informe: sin las entradas en espera")
        return {}


def waiting_entries(db, txs: List[Dict[str, Any]], start: str, end: str, limit: int = 40) -> Dict[str, Any]:
    """Para la IA: lo que esperabas y qué pasó, con datos reales.
    - entries: cada «Mi precio» que tuviste puesto en el periodo (del historial `my_buy_price_log`): si llegó a tocarlo,
      si compraste, el mínimo que hizo y cuánto se movió desde que lo pusiste (si no llegó y subió, lo que costó apurar);
    - notes_moves: de cada empresa con notas en el periodo, cuánto se movió desde la primera nota."""
    from app.application.services import company_notes_service, split_service
    from app.application.services import price_monitor_service as pm
    from app.infrastructure.db.models import DBAsset
    from app.interfaces.views.watchlist_views import MY_PRICE_KEY, MY_PRICE_LOG_KEY
    spans = []
    for a in db.query(DBAsset).all():
        d = a.data or {}
        log = list(d.get(MY_PRICE_LOG_KEY) or [])
        if not log and (d.get(MY_PRICE_KEY) or {}).get("price"):
            log = [{"date": (d[MY_PRICE_KEY].get("set_at") or start)[:10], "price": d[MY_PRICE_KEY]["price"]}]
        for i, e in enumerate(log):
            until = log[i + 1]["date"] if i + 1 < len(log) else end
            if e.get("price") and e["date"] <= end and until >= start:
                spans.append({"ticker": a.ticker, "target": e["price"], "set_at": e["date"], "until": min(until, end),
                              "removed": i + 1 < len(log)})
    spans = sorted(spans, key=lambda x: x["set_at"], reverse=True)[:limit]
    notes = company_notes_service.notes_between(db, start, end)
    first_note = {}
    for n in notes:
        first_note[n["ticker"]] = min(first_note.get(n["ticker"], n["date"]), n["date"])
    tickers = sorted({x["ticker"] for x in spans} | set(first_note))
    if not tickers:
        return {"entries": [], "notes_moves": []}
    df = pm.download_history(tickers)
    close = lambda t, a, b: df[t].loc[pd.Timestamp(a):pd.Timestamp(b)].dropna() if t in df.columns else pd.Series(dtype=float)
    entries = []
    for x in spans:
        px = close(x["ticker"], x["set_at"], x["until"])
        if len(px) < 2:
            continue
        try:
            target = x["target"] / split_service.factor_since(split_service.fetch_splits(x["ticker"]), x["set_at"])
        except Exception:
            target = x["target"]
        bought = any(t["ticker"] == x["ticker"] and t["type"] == "BUY" and x["set_at"] <= t["date"][:10] <= x["until"] for t in txs)
        entries.append({**x, "target": round(target, 2), "price_at_set": round(float(px.iloc[0]), 2), "low": round(float(px.min()), 2),
                        "reached": bool(px.min() <= target), "bought": bought, "price_end": round(float(px.iloc[-1]), 2),
                        "move_since_set_pct": round(100 * (px.iloc[-1] / px.iloc[0] - 1), 1)})
    moves = []
    for t, d0 in first_note.items():
        px = close(t, d0, end)
        if len(px) >= 2:
            moves.append({"ticker": t, "first_note": d0, "move_since_note_pct": round(100 * (px.iloc[-1] / px.iloc[0] - 1), 1),
                          "bought_after": any(x["ticker"] == t and x["type"] == "BUY" and d0 <= x["date"][:10] <= end for x in txs)})
    return {"entries": entries, "notes_moves": moves}


def ai_prompt(report: Dict[str, Any], context: Optional[Dict[str, Any]] = None, previous_goals: Optional[str] = None) -> str:
    import json
    slim = {k: report.get(k) for k in ("label", "since", "performance", "flows", "dividends", "decisions", "theses", "news", "watchlist",
                                       "insiders", "months", "agenda", "drawdown", "risk", "cash", "inaction", "gains", "sectors",
                                       "shift", "decision_results", "theses_close") if report.get(k) is not None}
    if slim.get("performance"):   # el detalle por empresa ya va en «sectors»
        slim["performance"] = {k: v for k, v in slim["performance"].items() if k != "pnl_by_ticker"}
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
