"""Panel «Uso de IA»: coste estimado de las llamadas a la IA por día, semana, mes y año, por tipo de tarea, por empresa,
por modelo y por consulta, con un presupuesto mensual y su aviso.

Lee el registro que deja utils.llm_usage (data/llm_usage.jsonl, una línea por consulta rastreada). Las cifras son
estimaciones con los precios publicados: la factura real está en la consola de Google.
"""
import calendar
import json
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from utils.llm_usage import USAGE_LOG_PATH

BUDGET_KEY = "ai_budget"
DEFAULT_BUDGET = {"monthly_usd": 0.0, "alert_pct": 80}   # 0 = sin presupuesto
RUN_GAP_MIN = 45     # partes de un análisis de la misma empresa separadas menos de esto: la misma consulta

# Tipo de tarea: (clave, nombre). El orden fija el color de cada tipo en el gráfico (nunca cambia con el filtro).
CATEGORIES = [
    ("analysis", "Análisis de empresa"),
    ("results", "Revisión de resultados"),
    ("radar", "Radar de noticias"),
    ("summary", "Resumen semanal"),
    ("data", "Extracción de datos"),
    ("other", "Otros"),
]
CATEGORY_NAME = dict(CATEGORIES)
ANALYSIS_PARTS = {
    "audit": "Auditoría forense", "franchise": "Foso", "mgmt_alloc": "Gestión y capital", "verdict": "Veredicto",
    "val": "Valoración", "thesis": "Tesis", "rules": "Normas", "pe_forensic": "PER forense", "technical": "Análisis técnico",
    "thesis_destroyer": "Abogado del diablo", "rules_synthesis": "Normas · síntesis",
}
TASKS = {
    "results-review": ("results", "Revisión de resultados"), "thesis-review": ("results", "Revisión de resultados"),
    "thesis-news": ("radar", "Radar de noticias"), "news-search": ("radar", "Radar de noticias"),
    "news-weekly-summary": ("summary", "Resumen semanal"),
    "thesis-from-pdf": ("data", "Tesis desde PDF"), "next-results": ("data", "Fecha de resultados"),
    "audit/extract": ("data", "Extracción de cuentas"), "fair_pe": ("data", "PER justo"),
    "draft-change": ("other", "Cambio en la tesis"),
}


def parse_label(label: str) -> Dict[str, Any]:
    """«TICKER/tarea», «tarea» o «POST /api/<tarea>/<TICKER>» → empresa, tipo y nombre de la tarea."""
    ticker, task = None, label or ""
    if task.split(" ", 1)[0] in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        parts = [p for p in task.split(" ", 1)[1].split("/") if p and p != "api"]
        ticker, task = (parts[-1], "/".join(parts[:-1])) if len(parts) > 1 else (None, "/".join(parts))
    elif "/" in task:
        ticker, task = task.split("/", 1)
    if task.startswith("rules_pilar"):
        return {"ticker": ticker, "category": "analysis", "task": f"Normas · pilar {task[len('rules_pilar'):]}"}
    if task in ANALYSIS_PARTS:
        return {"ticker": ticker, "category": "analysis", "task": ANALYSIS_PARTS[task]}
    category, name = TASKS.get(task, ("other", task or "Sin etiqueta"))
    return {"ticker": ticker, "category": category, "task": name}


_cache: Dict[str, Any] = {}


def load_rows(path: str = USAGE_LOG_PATH) -> List[Dict[str, Any]]:
    """Registros del log (se relee solo si el fichero ha cambiado: la campana lo consulta en cada página)."""
    try:
        stamp = (os.path.getmtime(path), os.path.getsize(path))
    except OSError:
        return []
    if _cache.get("key") == (path, stamp):
        return _cache["rows"]
    rows = _read_rows(path)
    _cache.update(key=(path, stamp), rows=rows)
    return rows


def _read_rows(path: str) -> List[Dict[str, Any]]:
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    when = datetime.fromisoformat(r["date"])
                except (ValueError, KeyError):
                    continue
                free_calls = r.get("free_calls")
                # registros de antes de marcar la clave gratuita: solo es seguro que fue gratis el resumen semanal hecho
                # con flash-lite (solo lo usa con la clave gratuita; la de pago va con GEMINI_MODEL). El resto, de pago.
                models = r.get("models") or []
                if (free_calls is None and r.get("label") == "news-weekly-summary" and models
                        and all("flash-lite" in m for m in models)):
                    free_calls = r.get("calls", 0)
                free = bool(r.get("calls")) and free_calls == r.get("calls")
                rows.append({**parse_label(r.get("label", "")), "when": when, "label": r.get("label", ""),
                             "cost": 0.0 if free else float(r.get("cost_usd") or 0), "free": free,
                             "calls": r.get("calls", 0), "tokens": r.get("total_tokens", 0),
                             "searches": r.get("search_queries", 0), "models": r.get("models") or [],
                             "unpriced": r.get("unpriced_models") or []})
    except FileNotFoundError:
        pass
    rows.sort(key=lambda r: r["when"])
    return rows


def group_runs(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Consultas: las partes de un análisis de la misma empresa seguidas (menos de RUN_GAP_MIN entre una y otra)
    son una sola consulta («Análisis de KLAC»); el resto, una por registro. De la más reciente a la más antigua."""
    runs: List[Dict[str, Any]] = []
    open_runs: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if r["category"] == "analysis" and r["ticker"]:
            run = open_runs.get(r["ticker"])
            if run and r["when"] - run["end"] <= timedelta(minutes=RUN_GAP_MIN):
                run["parts"].append(r["task"])
                run["end"] = r["when"]
                for k in ("cost", "tokens", "searches", "calls"):
                    run[k] += r[k]
                run["models"] = sorted(set(run["models"]) | set(r["models"]))
                run["free"] = run["free"] and r["free"]
                continue
            run = {"when": r["when"], "end": r["when"], "ticker": r["ticker"], "category": "analysis",
                   "task": "Análisis de empresa", "parts": [r["task"]], **{k: r[k] for k in ("cost", "tokens", "searches", "calls", "models", "free")}}
            open_runs[r["ticker"]] = run
            runs.append(run)
        else:
            runs.append({**r, "end": r["when"], "parts": []})
    for run in runs:
        if run["category"] == "analysis" and len(run["parts"]) == 1:
            run["task"] = run["parts"][0]       # una parte suelta (p. ej. repetir el foso): se llama por su nombre
    runs.sort(key=lambda r: r["when"], reverse=True)
    return runs


def _sum(rows) -> float:
    return round(sum(r["cost"] for r in rows), 6)


def period_start(period: str, today: date) -> Optional[date]:
    return {"semana": today - timedelta(days=today.weekday()), "mes": today.replace(day=1),
            "anio": today.replace(month=1, day=1)}.get(period)


def kpis(rows: List[Dict[str, Any]], today: date) -> Dict[str, Any]:
    """Hoy, esta semana (desde el lunes), este mes (con el anterior y la previsión a fin de mes) y este año."""
    def since(d: date):
        return [r for r in rows if r["when"].date() >= d]
    month_start = today.replace(day=1)
    prev_start = (month_start - timedelta(days=1)).replace(day=1)
    month = _sum(since(month_start))
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    return {
        "today": _sum(since(today)), "week": _sum(since(period_start("semana", today))), "month": month,
        "year": _sum(since(period_start("anio", today))),
        "prev_month": _sum([r for r in rows if prev_start <= r["when"].date() < month_start]), "prev_month_label": prev_start,
        "forecast": round(month / today.day * days_in_month, 6) if month else 0.0,
    }


def budget_status(month_cost: float, forecast: float, budget: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Presupuesto del mes: % gastado, nivel (ok / alerta / superado) y si la previsión lo supera."""
    limit = float(budget.get("monthly_usd") or 0)
    if limit <= 0:
        return None
    alert = float(budget.get("alert_pct") or DEFAULT_BUDGET["alert_pct"])
    pct = month_cost / limit * 100
    level = "over" if pct >= 100 else ("alert" if pct >= alert else "ok")
    return {"limit": limit, "alert_pct": alert, "pct": round(pct, 1), "level": level, "left": round(limit - month_cost, 6),
            "forecast_pct": round(forecast / limit * 100, 1), "forecast_over": forecast > limit}


def chart_series(rows: List[Dict[str, Any]], period: str, today: date) -> Dict[str, Any]:
    """Barras apiladas por tipo de tarea: por día (semana, mes) o por mes (año, todo)."""
    if period in ("semana", "mes"):
        start = period_start(period, today)
        end = today if period == "semana" else today.replace(day=calendar.monthrange(today.year, today.month)[1])
        buckets = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        key = lambda d: d                                                    # noqa: E731
        labels = [d.strftime("%d/%m") for d in buckets]
    else:
        first = period_start("anio", today) if period == "anio" else (rows[0]["when"].date().replace(day=1) if rows else today.replace(day=1))
        buckets, d = [], first.replace(day=1)
        while d <= today:
            buckets.append(d)
            d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        key = lambda d: d.replace(day=1)                                     # noqa: E731
        labels = [f"{MONTHS[d.month - 1]} {d.year % 100:02d}" for d in buckets]
    index = {b: i for i, b in enumerate(buckets)}
    series = {k: [0.0] * len(buckets) for k, _ in CATEGORIES}
    for r in rows:
        i = index.get(key(r["when"].date()))
        if i is not None:
            series[r["category"]][i] += r["cost"]
    return {"labels": labels, "series": [{"key": k, "name": n, "values": [round(v, 4) for v in series[k]]}
                                         for k, n in CATEGORIES if any(series[k])]}


MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def breakdown(runs: List[Dict[str, Any]], field: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Coste por tipo de tarea, empresa o modelo: nº de consultas, total, medio y % del total. Las gratuitas aparte."""
    total = sum(r["cost"] for r in runs) or 1.0
    out: Dict[str, Dict[str, Any]] = {}
    for r in runs:
        keys = r["models"] if field == "model" else [r.get(field)]
        for k in keys or ["—"]:
            if k is None:
                continue
            o = out.setdefault(k, {"key": k, "count": 0, "cost": 0.0, "free": 0})
            o["count"] += 1
            o["free"] += r["free"]
            o["cost"] += r["cost"] / (len(keys) if field == "model" else 1)   # varios modelos en una consulta: a partes
    rows = sorted(out.values(), key=lambda o: o["cost"], reverse=True)
    for o in rows:
        paid = o["count"] - o["free"]
        o.update({"avg": o["cost"] / paid if paid else 0.0, "pct": o["cost"] / total * 100,
                  "name": CATEGORY_NAME.get(o["key"], o["key"]) if field == "category" else o["key"]})
    return rows[:limit] if limit else rows


def dashboard(budget: Dict[str, Any], period: str = "mes", today: Optional[date] = None,
              path: str = USAGE_LOG_PATH) -> Dict[str, Any]:
    today = today or date.today()
    period = period if period in ("semana", "mes", "anio", "todo") else "mes"
    rows = load_rows(path)
    k = kpis(rows, today)
    start = period_start(period, today)
    in_period = [r for r in rows if start is None or r["when"].date() >= start]
    runs = group_runs(in_period)
    return {
        "kpis": k, "budget": {**DEFAULT_BUDGET, **(budget or {})}, "budget_status": budget_status(k["month"], k["forecast"], budget or {}),
        "period": period, "period_total": _sum(in_period), "period_runs": len(runs),
        "chart": chart_series(in_period, period, today),
        "cat_index": {key: i + 1 for i, (key, _) in enumerate(CATEGORIES)}, "cat_name": CATEGORY_NAME,
        "by_category": breakdown(runs, "category"), "by_ticker": breakdown(runs, "ticker", limit=10), "by_model": breakdown(runs, "model"),
        "runs": runs,   # todas: la página las pagina según lo que cabe en la ventana
        "unpriced": sorted({m for r in in_period for m in r["unpriced"]}),
        "since": rows[0]["when"] if rows else None,
    }


def budget_notice(budget: Dict[str, Any], today: Optional[date] = None, path: str = USAGE_LOG_PATH) -> Optional[Dict[str, Any]]:
    """Aviso para la campana cuando el gasto del mes llega al umbral de alerta o supera el presupuesto.
    Una clave por mes y nivel: pasar del aviso al «superado» vuelve a avisar."""
    today = today or date.today()
    rows = load_rows(path)
    k = kpis(rows, today)
    st = budget_status(k["month"], k["forecast"], budget or {})
    if not st or st["level"] == "ok":
        return None
    from utils.number_format import es
    month = today.strftime("%Y-%m")
    if st["level"] == "over":
        text = f"Presupuesto de IA superado: ${es(k['month'], 2)} de ${es(st['limit'], 2)} este mes"
    else:
        text = f"Llevas el {es(st['pct'], 0)}% del presupuesto de IA del mes (${es(k['month'], 2)} de ${es(st['limit'], 2)})"
    return {"key": f"ia|att|budget|{month}|{st['level']}", "ticker": "IA", "kind": "ia_budget", "text": text,
            "href": "/ia"}
