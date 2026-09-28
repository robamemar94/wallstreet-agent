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
from datetime import date as date_cls, datetime
from typing import Any, Dict, List, Optional

import yaml

STATUS_SCORE = {"green": 100.0, "amber": 50.0, "red": 0.0}
STATUS_RANK = {"green": 2, "amber": 1, "red": 0}
KILL_SWITCH_PERIODS = 2

# Estados del framework: Strengthening / Intact / Watch / Weakening / Broken
VERDICTS = ["REFORZADA", "INTACTA", "VIGILANCIA", "DEBILITADA", "ROTA"]
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


# --- Valoración viva (se recalcula con el FCF/acción TTM y el precio del día) ---

REQUIRED_RETURNS = (10, 15)   # rentabilidades anuales para el DCF inverso y el precio de entrada
DEFAULT_HORIZON_YEARS = 10


def _multiple(value) -> Optional[float]:
    try:
        return float(str(value).lower().replace("x", "").replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def live_valuation(valuation: Dict[str, Any], fcf_ps: Optional[float], price: Optional[float],
                   today: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Recalcula los escenarios de la tesis (FCF/share CAGR + múltiplo terminal) con datos actuales.
    Devuelve por escenario el precio al final del horizonte y el CAGR implícito desde hoy, más el DCF inverso."""
    if not valuation or not fcf_ps or fcf_ps <= 0 or not price or price <= 0:
        return None
    today = today or datetime.now()
    now_year = today.year + (today.timetuple().tm_yday - 1) / 365.0
    if valuation.get("target_year"):
        target_year = valuation["target_year"]
        years = target_year - now_year
    else:  # la tesis no fija año: horizonte de DEFAULT_HORIZON_YEARS desde hoy
        years = float(DEFAULT_HORIZON_YEARS)
        target_year = int(round(now_year + years))
    if years <= 0:
        return None
    rows = []
    for sc in valuation.get("scenarios", []):
        g, m = sc.get("fcf_ps_cagr"), _multiple(sc.get("multiple"))
        if g is None or not m:
            continue
        future_price = fcf_ps * (1 + g / 100) ** years * m
        rows.append({"name": sc.get("name"), "fcf_ps_cagr": g, "multiple": m, "price_target": round(future_price, 2),
                     "cagr": round(100 * ((future_price / price) ** (1 / years) - 1), 2),
                     "entry_prices": {r: round(future_price / (1 + r / 100) ** years, 2) for r in REQUIRED_RETURNS}})
    if not rows:
        return None
    base = next((r for r in rows if str(r["name"]).lower() == "base"), rows[len(rows) // 2])
    implied = {r: round(100 * ((price * (1 + r / 100) ** years / (fcf_ps * base["multiple"])) ** (1 / years) - 1), 2)
               for r in REQUIRED_RETURNS}
    return {"fcf_ps": round(fcf_ps, 4), "price": price, "target_year": target_year, "years": round(years, 2),
            "fcf_yield": round(100 * fcf_ps / price, 2), "multiple_now": round(price / fcf_ps, 1),
            "scenarios": rows, "base_name": base["name"], "base_multiple": base["multiple"], "implied_growth": implied}


def ttm_fcf_per_share(quarterly_cashflow, quarterly_income, annual_cashflow=None, annual_income=None) -> Optional[float]:
    """FCF por acción de los últimos 12 meses (4 trimestres seguidos); si hay huecos, el del último ejercicio."""
    shares = _row(quarterly_income, "Diluted Average Shares", 0)
    if quarterly_cashflow is not None and consecutive_quarters(list(quarterly_cashflow.columns)):
        fcfs = [_row(quarterly_cashflow, "Free Cash Flow", i) for i in range(4)]
        if all(f is not None for f in fcfs) and shares:
            return sum(fcfs) / shares
    fcf_y = _row(annual_cashflow, "Free Cash Flow", 0) if annual_cashflow is not None else None
    shares_y = _row(annual_income, "Diluted Average Shares", 0) if annual_income is not None else None
    if fcf_y is not None and (shares or shares_y):
        return fcf_y / (shares or shares_y)
    return None


_TTM_CACHE: Dict[str, Any] = {}


def fetch_ttm_fcf_per_share(ticker: str) -> Optional[float]:
    import time
    cached = _TTM_CACHE.get(ticker)
    if cached and time.time() - cached[0] < 12 * 3600:
        return cached[1]
    value = None
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        value = ttm_fcf_per_share(stock.quarterly_cashflow, stock.quarterly_income_stmt, stock.cashflow, stock.income_stmt)
        info = stock.info or {}
        fin, trade = info.get("financialCurrency"), info.get("currency")
        if value is not None and fin and trade and fin != trade:
            # p.ej. Evolution reporta en EUR y cotiza en SEK: el FCF/acción debe ir en la divisa del precio
            fx = yf.Ticker(f"{fin}{trade}=X").fast_info["last_price"]
            value = value * float(fx)
    except Exception:
        value = None
    _TTM_CACHE[ticker] = (time.time(), value)
    return value


# --- Exposición en cartera ---

WEIGHT_ALERT_PCT = 5.0   # a partir de este peso, una tesis que se deteriora genera aviso


def compute_exposure(positions: List[Dict[str, Any]], prices: Dict[str, float], fx_to_eur: Dict[str, float]) -> Dict[str, Any]:
    """positions: [{ticker, shares, average_price, currency, cost_eur}] -> valor en EUR, peso y P&L por ticker."""
    rows, total = {}, 0.0
    for p in positions:
        price = prices.get(p["ticker"])
        cur = p.get("currency") or "USD"
        if price is None or not p.get("shares"):
            continue
        if cur == "GBp":            # cotiza en peniques
            price, cur = price / 100, "GBP"
        fx = 1.0 if cur == "EUR" else fx_to_eur.get(cur)
        if fx is None:
            continue
        value = p["shares"] * price * fx
        total += value
        cost = p.get("cost_eur") or 0.0
        rows[p["ticker"]] = {"shares": p["shares"], "value_eur": value, "cost_eur": cost,
                             "pnl_pct": (100 * (value / cost - 1)) if cost else None}
    for r in rows.values():
        r["weight"] = 100 * r["value_eur"] / total if total else 0.0
    return {"positions": rows, "total_eur": total}


def exposure_risk(thesis: Dict[str, Any], ev: Dict[str, Any], weight: Optional[float]) -> Optional[str]:
    """Motivo del aviso si una posición relevante tiene la tesis deteriorándose; None si no hay aviso."""
    if weight is None or weight < WEIGHT_ALERT_PCT:
        return None
    reasons = []
    if thesis.get("verdict") in ("DEBILITADA", "ROTA"):
        reasons.append(f"tesis {thesis['verdict']}")
    if ev.get("kill_triggered"):
        reasons.append("kill switch disparado: " + ", ".join(k["name"] for k in ev["kill_triggered"]))
    if ev.get("health_status") == "red":
        reasons.append(f"health score {ev.get('health_score')}")
    if not reasons:
        return None
    return f"Pesa el {weight:.1f}% de tu cartera y " + "; ".join(reasons)


# --- Diario de decisiones ---

DECISION_ACTIONS = ("comprar", "aumentar", "mantener", "reducir", "vender")
DECISION_REVIEW_DAYS = (180, 365)


def decision_outcome(decision: Dict[str, Any], current_price: Optional[float], today: Optional[date_cls] = None) -> Dict[str, Any]:
    """Qué ha pasado desde la decisión: días, variación del precio y si toca revisarla (6 y 12 meses)."""
    today = today or date_cls.today()
    days = (today - date_cls.fromisoformat(decision["date"][:10])).days
    change = None
    if current_price and decision.get("price"):
        change = 100 * (current_price / decision["price"] - 1)
    # para una venta o reducción, que el precio caiga después es acertar
    good = None if change is None else (change >= 0 if decision["action"] in ("comprar", "aumentar", "mantener") else change <= 0)
    reviewed_days = None
    if decision.get("reviewed_at"):
        reviewed_days = (date_cls.fromisoformat(decision["reviewed_at"][:10]) - date_cls.fromisoformat(decision["date"][:10])).days
    due = next((m for m in DECISION_REVIEW_DAYS if days >= m and (reviewed_days is None or reviewed_days < m)), None)
    return {"days": days, "price_change": round(change, 1) if change is not None else None, "good": good, "review_due": due}


def unlogged_transactions(transactions: List[Dict[str, Any]], decisions: List[Dict[str, Any]], since: str) -> List[Dict[str, Any]]:
    """Compras/ventas posteriores a la creación de la tesis que no tienen una decisión anotada."""
    linked = {d.get("transaction_ref") for d in decisions if d.get("transaction_ref")}
    return [t for t in transactions if t["date"] >= since and t["ref"] not in linked and t["type"] in ("BUY", "SELL")]


# --- Métricas automáticas (yfinance) ---

def _row(df, name, idx) -> Optional[float]:
    try:
        v = float(df.loc[name].iloc[idx])
        return None if math.isnan(v) else v
    except Exception:
        return None


def year_ago_index(dates) -> Optional[int]:
    """Posición de la columna de hace ~1 año respecto a la primera (yfinance a veces se salta trimestres,
    así que no vale suponer que es la quinta)."""
    try:
        first = dates[0]
        for i, d in enumerate(dates[1:], 1):
            if 330 <= (first - d).days <= 400:
                return i
    except Exception:
        pass
    return None


def consecutive_quarters(dates, n: int = 4) -> bool:
    """True si las n primeras columnas son trimestres seguidos (sin huecos)."""
    try:
        return len(dates) >= n and all(60 <= (dates[i] - dates[i + 1]).days <= 120 for i in range(n - 1))
    except Exception:
        return False


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
    yi = year_ago_index(list(income.columns)) if income is not None else None
    yc = year_ago_index(list(cashflow.columns)) if cashflow is not None else None
    rev0, rev4 = _row(income, "Total Revenue", 0), (_row(income, "Total Revenue", yi) if yi else None)
    sh0, sh4 = _row(income, "Diluted Average Shares", 0), (_row(income, "Diluted Average Shares", yi) if yi else None)
    fcf0, fcf4 = _row(cashflow, "Free Cash Flow", 0), (_row(cashflow, "Free Cash Flow", yc) if yc else None)
    capex0 = _row(cashflow, "Capital Expenditure", 0)
    cfo0, cfo4 = _row(cashflow, "Operating Cash Flow", 0), (_row(cashflow, "Operating Cash Flow", yc) if yc else None)
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
            idx = year_ago_index(list(series.index))
            if idx is None:
                continue
            cur, prev = abs(float(series.iloc[0])), abs(float(series.iloc[idx]))
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

def validate_spec(spec: Dict[str, Any], origin: str = "YAML") -> Dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError(f"{origin}: no es un documento YAML de tesis")
    for field in ("ticker", "title", "pillars"):
        if field not in spec:
            raise ValueError(f"{origin}: falta el campo obligatorio '{field}'")
    for pillar in spec["pillars"]:
        for field in ("key", "name"):
            if field not in pillar:
                raise ValueError(f"{origin}: un paso no tiene '{field}'")
        for kpi in pillar.get("kpis", []):
            if "key" not in kpi or "name" not in kpi:
                raise ValueError(f"{origin}: un KPI no tiene 'key' o 'name'")
            if kpi.get("kind", "numeric") == "numeric" and kpi.get("green_threshold") is None:
                raise ValueError(f"{origin}: KPI numérico '{kpi.get('key')}' sin green_threshold")
            if kpi.get("auto_metric") and kpi["auto_metric"] not in AUTO_METRICS:
                raise ValueError(f"{origin}: auto_metric desconocida '{kpi['auto_metric']}'")
    return spec


def load_thesis_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    validate_spec(spec, path)
    spec["spec_path"] = path[:-4] + ".yaml" if path.endswith(".yaml.tmp") else path
    if spec.get("document"):
        if not os.path.exists(spec["document"]):
            raise ValueError(f"{path}: no existe el documento '{spec['document']}'")
        with open(spec["document"], "r", encoding="utf-8") as f:
            spec["document_md"] = f.read()
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
