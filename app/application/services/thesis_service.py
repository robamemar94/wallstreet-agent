"""
Seguimiento de tesis de inversión: semáforo de KPIs, health score y kill switches.

Reglas (tomadas de las propias tesis):
- Un KPI numérico se clasifica automáticamente con sus umbrales verde/rojo; la zona intermedia es amarillo.
  Si la tesis solo define el umbral verde, el rojo se decide manualmente (nunca se marca rojo automático).
- Un KPI cualitativo recibe el semáforo a mano.
- "Ningún KPI aislado rompe la tesis": un kill switch solo se considera disparado cuando está en rojo
  durante al menos KILL_SWITCH_PERIODS periodos consecutivos. Con un único rojo queda en vigilancia.
"""
import math
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import yaml

STATUS_SCORE = {"green": 100.0, "amber": 50.0, "red": 0.0}
STATUS_RANK = {"green": 2, "amber": 1, "red": 0}
KILL_SWITCH_PERIODS = 2

VERDICTS = ["REFORZADA", "INTACTA", "DEBILITADA", "ROTA"]
STATUSES = ["ACTIVA", "EN_REVISION", "CERRADA"]
EVENT_TYPES = ["earnings", "news", "verdict", "decision", "note", "thesis"]

AUTO_METRICS = {
    "revenue_yoy": "Revenue YoY (%)",
    "gross_margin": "Margen bruto (%)",
    "operating_margin": "Margen operativo (%)",
    "fcf_margin": "Margen FCF (%)",
    "diluted_shares_yoy": "Acciones diluidas YoY (%)",
    "fcf_per_share_yoy": "FCF/acción YoY (%)",
    "capex_to_fcf": "CapEx / FCF (%)",
    "cfo_yoy": "CFO YoY (%)",
    "sbc_to_fcf": "SBC / FCF del trimestre (%)",
    "fcf_per_share_cagr_3y": "FCF/acción CAGR 3 años (%)",
    "hyperscaler_capex_yoy": "Capex MSFT+GOOGL+AMZN+META YoY (%)",
}
HYPERSCALERS = ("MSFT", "GOOGL", "AMZN", "META")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def classify(kpi: Dict[str, Any], value: Optional[float]) -> Optional[str]:
    """Semáforo automático de un KPI numérico. None si no se puede decidir automáticamente."""
    if value is None or kpi.get("kind") != "numeric" or kpi.get("green_threshold") is None:
        return None
    green, red = kpi["green_threshold"], kpi.get("red_threshold")
    if kpi.get("direction", "higher") == "lower":
        if value <= green:
            return "green"
        if red is not None and value > red:
            return "red"
        return "amber"
    if value >= green:
        return "green"
    if red is not None and value < red:
        return "red"
    return "amber"


def _trend(observations: List[Dict[str, Any]]) -> Optional[str]:
    if len(observations) < 2:
        return None
    last, prev = STATUS_RANK[observations[-1]["status"]], STATUS_RANK[observations[-2]["status"]]
    if last > prev:
        return "up"
    if last < prev:
        return "down"
    return "flat"


def _red_streak(observations: List[Dict[str, Any]]) -> int:
    streak = 0
    for obs in reversed(observations):
        if obs["status"] != "red":
            break
        streak += 1
    return streak


def _weighted(items: List[tuple]) -> Optional[float]:
    total_w = sum(w for _, w in items)
    if total_w <= 0:
        return None
    return sum(s * w for s, w in items) / total_w


def evaluate_kpi(kpi: Dict[str, Any]) -> Dict[str, Any]:
    obs = sorted(kpi.get("observations", []), key=lambda o: o["date"])
    last = obs[-1] if obs else None
    red_streak = _red_streak(obs)
    kill_state = None
    if kpi.get("is_kill_switch"):
        if red_streak >= KILL_SWITCH_PERIODS:
            kill_state = "triggered"
        elif red_streak == 1 or (last and last["status"] == "amber"):
            kill_state = "watch"
        else:
            kill_state = "ok"
    return {
        "status": last["status"] if last else None,
        "last": last,
        "trend": _trend(obs),
        "red_streak": red_streak,
        "kill_state": kill_state,
        "score": STATUS_SCORE[last["status"]] if last else None,
    }


def evaluate_thesis(thesis: Dict[str, Any]) -> Dict[str, Any]:
    """Calcula health score, estado por pilar, cobertura y kill switches de una tesis serializada."""
    pillars_out = []
    pillar_scores = []
    counts = {"green": 0, "amber": 0, "red": 0, "none": 0}
    kill_switches = []

    for pillar in thesis["pillars"]:
        kpi_scores = []
        kpis_out = []
        for kpi in pillar["kpis"]:
            ev = evaluate_kpi(kpi)
            kpis_out.append({**kpi, "eval": ev})
            counts[ev["status"] or "none"] += 1
            if ev["score"] is not None:
                kpi_scores.append((ev["score"], kpi.get("weight", 1.0)))
            if kpi.get("is_kill_switch"):
                kill_switches.append({
                    "kpi_id": kpi["id"], "name": kpi["name"], "pillar": pillar["name"],
                    "rule": kpi.get("kill_rule"), "state": ev["kill_state"],
                    "red_streak": ev["red_streak"], "status": ev["status"],
                })
        score = _weighted(kpi_scores)
        weight = pillar.get("weight", 1.0)
        if score is not None and weight > 0:
            pillar_scores.append((score, weight))
        pillars_out.append({**pillar, "kpis": kpis_out, "score": score, "status": score_to_status(score),
                            "measured": len(kpi_scores), "total": len(pillar["kpis"])})

    total = sum(counts.values())
    measured = total - counts["none"]
    health = _weighted(pillar_scores)
    triggered = [k for k in kill_switches if k["state"] == "triggered"]
    return {
        "health_score": round(health) if health is not None else None,
        "health_status": score_to_status(health),
        "pillars": pillars_out,
        "counts": counts,
        "coverage": round(100 * measured / total) if total else 0,
        "kill_switches": kill_switches,
        "kill_triggered": triggered,
        "review_recommended": bool(triggered),
    }


def score_to_status(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    if score >= 75:
        return "green"
    if score >= 45:
        return "amber"
    return "red"


def implied_cagr(current_price: Optional[float], target_price: Optional[float], target_year: Optional[int],
                 today: Optional[datetime] = None) -> Optional[float]:
    """CAGR implícito desde el precio actual hasta el precio objetivo del escenario."""
    if not current_price or not target_price or not target_year or current_price <= 0:
        return None
    today = today or datetime.now()
    years = target_year - (today.year + (today.timetuple().tm_yday - 1) / 365.0)
    if years <= 0:
        return None
    return (target_price / current_price) ** (1 / years) - 1


# --- Métricas automáticas (yfinance) ---

def _row(df, name, idx) -> Optional[float]:
    try:
        v = float(df.loc[name].iloc[idx])
        return None if math.isnan(v) else v
    except Exception:
        return None


def _pct(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or not den:
        return None
    return round(100 * num / den, 2)


def _yoy(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    if cur is None or not prev:
        return None
    return round(100 * (cur / prev - 1), 2)


def _fcf_per_share_cagr(annual_income, annual_cashflow, years: int = 3) -> Optional[float]:
    """CAGR del FCF por acción entre el último ejercicio y el de hace `years` (datos anuales, más recientes primero)."""
    if annual_income is None or annual_cashflow is None:
        return None
    fcf0, fcfn = _row(annual_cashflow, "Free Cash Flow", 0), _row(annual_cashflow, "Free Cash Flow", years)
    sh0, shn = _row(annual_income, "Diluted Average Shares", 0), _row(annual_income, "Diluted Average Shares", years)
    if None in (fcf0, fcfn, sh0, shn) or not sh0 or not shn or fcfn <= 0 or fcf0 <= 0:
        return None
    return round(100 * ((fcf0 / sh0) / (fcfn / shn)) ** (1 / years) - 100, 2)


def compute_auto_metrics(income, cashflow, annual_income=None, annual_cashflow=None) -> Dict[str, Any]:
    """Calcula métricas del último trimestre a partir de los estados trimestrales de yfinance (columnas: más reciente primero)."""
    rev0, rev4 = _row(income, "Total Revenue", 0), _row(income, "Total Revenue", 4)
    sh0, sh4 = _row(income, "Diluted Average Shares", 0), _row(income, "Diluted Average Shares", 4)
    fcf0, fcf4 = _row(cashflow, "Free Cash Flow", 0), _row(cashflow, "Free Cash Flow", 4)
    capex0 = _row(cashflow, "Capital Expenditure", 0)
    cfo0, cfo4 = _row(cashflow, "Operating Cash Flow", 0), _row(cashflow, "Operating Cash Flow", 4)
    sbc0 = _row(cashflow, "Stock Based Compensation", 0)

    fcfps0 = fcf0 / sh0 if fcf0 is not None and sh0 else None
    fcfps4 = fcf4 / sh4 if fcf4 is not None and sh4 else None
    metrics = {
        "revenue_yoy": _yoy(rev0, rev4),
        "gross_margin": _pct(_row(income, "Gross Profit", 0), rev0),
        "operating_margin": _pct(_row(income, "Operating Income", 0), rev0),
        "fcf_margin": _pct(fcf0, rev0),
        "diluted_shares_yoy": _yoy(sh0, sh4),
        "fcf_per_share_yoy": _yoy(fcfps0, fcfps4) if fcfps4 and fcfps4 > 0 else None,
        "capex_to_fcf": _pct(abs(capex0), fcf0) if capex0 is not None and fcf0 and fcf0 > 0 else None,
        "cfo_yoy": _yoy(cfo0, cfo4),
        "sbc_to_fcf": _pct(sbc0, fcf0) if sbc0 is not None and fcf0 and fcf0 > 0 else None,
        "fcf_per_share_cagr_3y": _fcf_per_share_cagr(annual_income, annual_cashflow),
    }
    period_end = None
    try:
        period_end = income.columns[0].date().isoformat()
    except Exception:
        pass
    details = {}
    if metrics["fcf_per_share_cagr_3y"] is not None:
        try:
            details["fcf_per_share_cagr_3y"] = (f"Ejercicios {annual_cashflow.columns[3].date().year}→"
                                                f"{annual_cashflow.columns[0].date().year} (datos anuales)")
        except Exception:
            pass
    return {"period_end": period_end, "metrics": metrics, "details": details}


def aggregate_capex_yoy(capex_by_ticker: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Crecimiento agregado del capex: último trimestre de cada empresa frente al mismo trimestre del año anterior.
    `capex_by_ticker`: {ticker: serie de capex trimestral (más reciente primero, con índice de fechas)}."""
    cur_total, prev_total, parts = 0.0, 0.0, []
    for ticker, series in capex_by_ticker.items():
        try:
            cur, prev = abs(float(series.iloc[0])), abs(float(series.iloc[4]))
            if math.isnan(cur) or math.isnan(prev) or prev == 0:
                continue
        except Exception:
            continue
        cur_total += cur
        prev_total += prev
        parts.append(f"{ticker} {series.index[0].date().isoformat()} {cur / 1e9:.1f}B ({100 * (cur / prev - 1):+.0f}%)")
    if len(parts) < len(capex_by_ticker) or not prev_total:
        return None  # sin datos de alguna empresa: mejor no dar un agregado incompleto
    return {"value": round(100 * (cur_total / prev_total - 1), 2), "detail": "; ".join(parts)}


def fetch_hyperscaler_capex_yoy() -> Optional[Dict[str, Any]]:
    import yfinance as yf
    series = {}
    for t in HYPERSCALERS:
        cf = yf.Ticker(t).quarterly_cashflow
        if cf is not None and "Capital Expenditure" in cf.index:
            series[t] = cf.loc["Capital Expenditure"]
    return aggregate_capex_yoy(series) if len(series) == len(HYPERSCALERS) else None


def fetch_auto_metrics(ticker: str, needed: Optional[set] = None) -> Dict[str, Any]:
    import yfinance as yf
    stock = yf.Ticker(ticker)
    data = compute_auto_metrics(stock.quarterly_income_stmt, stock.quarterly_cashflow, stock.income_stmt, stock.cashflow)
    if needed and "hyperscaler_capex_yoy" in needed:
        agg = fetch_hyperscaler_capex_yoy()
        if agg:
            data["metrics"]["hyperscaler_capex_yoy"] = agg["value"]
            data["details"]["hyperscaler_capex_yoy"] = agg["detail"]
    return data


# --- Importación desde YAML ---

def load_thesis_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    for field in ("ticker", "title", "pillars"):
        if field not in spec:
            raise ValueError(f"{path}: falta el campo obligatorio '{field}'")
    if spec.get("document"):
        if not os.path.exists(spec["document"]):
            raise ValueError(f"{path}: no existe el documento '{spec['document']}'")
        with open(spec["document"], "r", encoding="utf-8") as f:
            spec["document_md"] = f.read()
    for pillar in spec["pillars"]:
        for kpi in pillar.get("kpis", []):
            if kpi.get("kind", "numeric") == "numeric" and kpi.get("green_threshold") is None:
                raise ValueError(f"{path}: KPI numérico '{kpi.get('key')}' sin green_threshold")
            if kpi.get("auto_metric") and kpi["auto_metric"] not in AUTO_METRICS:
                raise ValueError(f"{path}: auto_metric desconocida '{kpi['auto_metric']}'")
    return spec


def render_document(md_text: Optional[str]) -> Dict[str, Any]:
    """Renderiza la tesis en Markdown a HTML con índice de apartados (## = apartado)."""
    if not md_text:
        return {"html": None, "sections": []}
    import markdown
    md = markdown.Markdown(extensions=["tables", "toc", "sane_lists"], extension_configs={"toc": {"toc_depth": "2"}})
    html = md.convert(md_text)
    sections = [{"id": t["id"], "name": t["name"]} for top in md.toc_tokens for t in ([top] if top["level"] == 2 else top["children"])]
    return {"html": html, "sections": sections}
