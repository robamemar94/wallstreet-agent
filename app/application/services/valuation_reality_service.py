"""
Supuestos de la valoración base frente a lo que ha hecho de verdad la empresa.

- Crecimiento (g) de la métrica (BPA o FCF por acción) frente a su crecimiento anual real a 3, 5 y 10 años
  (cuentas anuales de la ficha, sin el LTM). Referencia: 5 años (si no hay, 3; si no, 10).
- Múltiplo de salida (PER o P/FCF) frente a su múltiplo real: cierre de cada año / métrica de ese año; media de 5 años
  y mediana de 10. Referencia: la media de 5 años (si no hay, la mediana de 10).
Veredicto de cada uno: conservador (pides lo mismo o menos que la realidad), ajustado (hasta 5 puntos de crecimiento
o un 20 % de múltiplo por encima) u optimista (más). El global, el peor de los dos. Solo contrasta: no cambia nada.
Sin IA: cuentas guardadas y precios (cierres de fin de año, caché de 24 h).
"""
import statistics
from typing import Any, Dict, List, Optional

from app.application.services import market_cache
from app.application.services.insider_service import annual_shares

GROWTH_MARGIN_PP = 5.0     # hasta 5 puntos por encima del real: ajustado; más: optimista
MULTIPLE_MARGIN = 1.20     # hasta un 20 % por encima del múltiplo real: ajustado; más: optimista
MAX_PLAUSIBLE_PCT = 60.0   # un crecimiento anual de más de ±60 % (parte de casi cero) no es comparable
LABELS = {"good": "conservador", "mid": "ajustado", "bad": "optimista"}
RANK = {"good": 0, "mid": 1, "bad": 2}


def stored_financials(data: Dict[str, Any]) -> Dict[str, Any]:
    """Las cuentas guardadas de la ficha (sin descargar nada), con la misma prioridad que get_financials_history."""
    full = data.get("full_audit_structure")
    if full and full.get("years"):
        out = dict(full)
        for k, v in (full.get("chart_metrics") or {}).items():
            out.setdefault(k, v)
        return out
    for key in ("financials_hist_manual", "financials_hist_15y", "financials_hist_10y"):
        if (data.get(key) or {}).get("years"):
            return data[key]
    return {}


def _num(v) -> Optional[float]:
    try:
        v = float(v)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def per_share(fin: Dict[str, Any], metric: str) -> Dict[str, float]:
    """{ejercicio: métrica por acción} de los ejercicios completos (BPA tal cual; FCF / acciones diluidas medias)."""
    years = [str(y) for y in fin.get("years") or []]
    if metric == "eps":
        vals = fin.get("eps") or []
        return {y: v for y, v in ((y, _num(vals[i]) if i < len(vals) else None) for i, y in enumerate(years))
                if y.upper() != "LTM" and v is not None}
    shares = dict(annual_shares(fin) or [])
    fcf = fin.get("fcf") or []
    out = {}
    for i, y in enumerate(years):
        f, s = (_num(fcf[i]) if i < len(fcf) else None), shares.get(y)
        if y.upper() != "LTM" and f is not None and s:
            out[y] = f / s
    return out


def _cagr(series: Dict[str, float], n: int) -> Optional[float]:
    ys = sorted(series)
    if len(ys) <= n:
        return None
    a, b = series[ys[-1 - n]], series[ys[-1]]
    if a <= 0 or b <= 0:
        return None
    g = 100 * ((b / a) ** (1 / n) - 1)
    return g if abs(g) <= MAX_PLAUSIBLE_PCT else None


def yearly_closes_from(dates: List[str], close: List[float]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for d, c in zip(dates or [], close or []):
        if c is not None:
            out[str(d)[:4]] = float(c)   # el último cierre de cada año
    return out


def yearly_closes(ticker: str) -> Dict[str, float]:
    """Último cierre de cada año (10 años, mensual). Caché de 24 h."""
    def download():
        try:
            import yfinance as yf
            h = yf.Ticker(ticker).history(period="10y", interval="1mo", auto_adjust=False)["Close"].dropna()
            return yearly_closes_from([d.strftime("%Y-%m-%d") for d in h.index], list(h))
        except Exception:
            return None
    return market_cache.cached("yearly_closes_v1", ticker, download, ttl_hours=24) or {}


def history(fin: Dict[str, Any], closes: Dict[str, float], unit: float = 1.0) -> Dict[str, Any]:
    """Lo real, por métrica: crecimiento anual a 3, 5 y 10 años y múltiplo (media de 5 años, mediana de 10).
    unit: de la moneda de las cuentas a la de cotización (100 si cotiza en peniques: Londres)."""
    out = {}
    for metric in ("eps", "fcf"):
        s = per_share(fin, metric)
        mult = [closes[y] / (v * unit) for y, v in sorted(s.items()) if v > 0 and closes.get(y)]
        last5, last10 = mult[-5:], mult[-10:]
        out[metric] = {"cagr3": _cagr(s, 3), "cagr5": _cagr(s, 5), "cagr10": _cagr(s, 9 if len(s) == 10 else 10),
                       "mult_avg5": sum(last5) / len(last5) if len(last5) >= 3 else None,
                       "mult_med10": statistics.median(last10) if len(last10) >= 5 else None,
                       "years": len(s)}
    return out


def verdict(v: Optional[Dict[str, Any]], hist: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Veredicto de los supuestos de la valoración guardada v frente a lo real (None si no hay con qué comparar)."""
    if not v or not hist:
        return None
    h = hist.get(v.get("metric", "eps")) or {}
    g = 100 * (v.get("growth") or 0.0)
    ref_g = next((h[k] for k in ("cagr5", "cagr3", "cagr10") if h.get(k) is not None), None)
    growth = None
    if ref_g is not None:
        d = g - ref_g
        growth = {"tone": "good" if d <= 0 else "mid" if d <= GROWTH_MARGIN_PP else "bad", "assumed": g, "real": ref_g, "diff_pp": d}
    m = v.get("exit_multiple")
    ref_m = h.get("mult_avg5") or h.get("mult_med10")
    multiple = None
    if m and ref_m:
        r = m / ref_m
        multiple = {"tone": "good" if r <= 1 else "mid" if r <= MULTIPLE_MARGIN else "bad", "assumed": m, "real": ref_m, "diff_pct": 100 * (r - 1)}
    parts = [p for p in (growth, multiple) if p]
    if not parts:
        return None
    tone = max((p["tone"] for p in parts), key=RANK.get)
    return {"tone": tone, "label": LABELS[tone], "growth": growth, "multiple": multiple}


def warning(vd: Optional[Dict[str, Any]], unit: str = "PER") -> Optional[str]:
    """Texto corto para los avisos: solo si los supuestos son optimistas."""
    from utils.number_format import es
    if not vd or vd["tone"] != "bad":
        return None
    why = []
    if vd["growth"] and vd["growth"]["tone"] == "bad":
        why.append(f"crecimiento {es(vd['growth']['assumed'], 1)}% frente a {es(vd['growth']['real'], 1)}% real")
    if vd["multiple"] and vd["multiple"]["tone"] == "bad":
        why.append(f"{unit} de salida {es(vd['multiple']['assumed'], 1)}x frente a {es(vd['multiple']['real'], 1)}x real")
    return "supuestos optimistas (" + "; ".join(why) + ")"


def for_ticker(ticker: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Veredicto con las cuentas guardadas y los cierres de fin de año en caché (para la Watchlist)."""
    from app.application.services import base_valuation_service as bv
    from app.application.services.thesis_service import quote_unit_factor
    v = bv.stored(data)
    fin = stored_financials(data)
    if not v or not fin:
        return None
    return verdict(v, history(fin, yearly_closes(ticker), quote_unit_factor(data.get("currency"))))
