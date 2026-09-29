"""Valoración base de una empresa: un solo escenario sobre BPA o FCF por acción.

Métrica(año N) = métrica₀ · (1+g)^N; precio(año N) = métrica(N) · múltiplo de salida.
Los dividendos se proyectan desde el dividendo por acción actual (importe fijo, no rentabilidad),
así el precio de compra no depende de la cotización. La rentabilidad estimada es la TIR de pagar
el precio de hoy, cobrar los dividendos y vender en el año N; el precio de compra es el que da
exactamente la rentabilidad objetivo.
"""
from datetime import datetime
from typing import Any, Dict, Optional

from app.application.services import market_cache

ASSET_KEY = "base_valuation"
METRICS = {"eps": {"label": "BPA", "multiple": "PER"}, "fcf": {"label": "FCF/acción", "multiple": "P/FCF"}}
TARGET_RETURN_PCT = 15.0
DEFAULT_YEARS = 5
# Zonas por rentabilidad estimada (mismos cortes que el color del CAGR en el Centro de mando)
ZONES = ((TARGET_RETURN_PCT, "ganga"), (10.0, "barata"), (6.0, "justo"))


def present_value(v: Dict[str, Any], metric0: float, rate_pct: float) -> float:
    """Lo que valen hoy, a la tasa dada, los dividendos y el precio de venta del año N."""
    n, r = int(v["years"]), rate_pct / 100
    dps, gd = v.get("dps") or 0.0, v.get("div_growth") or 0.0
    pv = sum(dps * (1 + gd) ** t / (1 + r) ** t for t in range(1, n + 1))
    return pv + metric0 * (1 + v["growth"]) ** n * v["exit_multiple"] / (1 + r) ** n


def _irr(v: Dict[str, Any], metric0: float, price: float) -> Optional[float]:
    lo, hi = -99.0, 1000.0
    if present_value(v, metric0, lo) < price or present_value(v, metric0, hi) > price:
        return None
    for _ in range(100):   # el valor presente baja al subir la tasa: bisección
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if present_value(v, metric0, mid) > price else (lo, mid)
    return (lo + hi) / 2


def zone(cagr: Optional[float]) -> Optional[str]:
    if cagr is None:
        return None
    return next((name for cut, name in ZONES if cagr >= cut), "cara")


def evaluate(v: Optional[Dict[str, Any]], price: Optional[float], metric0: Optional[float]) -> Optional[Dict[str, Any]]:
    """v: valoración guardada; metric0: valor de partida de la métrica (manual o TTM)."""
    if not v or not price or price <= 0 or not metric0 or metric0 <= 0 or not v.get("exit_multiple"):
        return None
    n = int(v.get("years") or DEFAULT_YEARS)
    v = {**v, "years": n, "growth": v.get("growth") or 0.0}
    metric_n = metric0 * (1 + v["growth"]) ** n
    dps, gd = v.get("dps") or 0.0, v.get("div_growth") or 0.0
    cagr = _irr(v, metric0, price)
    buy = present_value(v, metric0, TARGET_RETURN_PCT)
    return {
        "metric": v.get("metric", "eps"), "metric0": round(metric0, 4), "years": n,
        "metric_n": round(metric_n, 4), "price_n": round(metric_n * v["exit_multiple"], 2),
        "dividends": round(sum(dps * (1 + gd) ** t for t in range(1, n + 1)), 2),
        "multiple_now": round(price / metric0, 1),
        "cagr": round(cagr, 2) if cagr is not None else None,
        "buy_price": round(buy, 2), "target": TARGET_RETURN_PCT,
        "margin_pct": round(100 * (buy / price - 1), 1),   # >0: cotiza por debajo del precio de compra
        "zone": zone(cagr),
    }


def from_legacy(proj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convierte el escenario Base de la antigua proyección de 3 escenarios."""
    base = (proj or {}).get("base") or {}
    if not proj or not base.get("exit_pe"):
        return None
    # metric_value None: parte del BPA TTM, que se actualiza solo (el antiguo BPA fijo se quedaba viejo)
    return {"metric": "eps", "metric_value": None, "growth": base.get("eps_growth") or 0.0,
            "exit_multiple": base["exit_pe"], "dps": None,   # la antigua guardaba rentabilidad: se usa el dividendo actual
            "div_growth": base.get("div_growth") or 0.0, "years": DEFAULT_YEARS,
            "rationale": proj.get("rationale") or "", "is_manual": bool(proj.get("is_manual")),
            "last_updated": proj.get("last_updated"),
            # la antigua se autoguardaba con valores por defecto: sin revisar no genera avisos
            "legacy": True}


def stored(asset_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return asset_data.get(ASSET_KEY) or from_legacy(asset_data.get("projection_scenarios"))


def normalize(data: Dict[str, Any], is_manual: bool = True) -> Dict[str, Any]:
    f = lambda k, d=0.0: float(data[k]) if data.get(k) not in (None, "") else d
    metric = data.get("metric") if data.get("metric") in METRICS else "eps"
    return {"metric": metric, "metric_value": f("metric_value", None),   # None → TTM automático
            "growth": f("growth"), "exit_multiple": f("exit_multiple"), "dps": f("dps"),
            "div_growth": f("div_growth"), "years": int(f("years", DEFAULT_YEARS)) or DEFAULT_YEARS,
            "rationale": data.get("rationale") or "", "is_manual": is_manual,
            "last_updated": datetime.now().strftime("%Y-%m-%d")}


def _market_info(ticker: str) -> Dict[str, Optional[float]]:
    """BPA de los últimos 12 meses y dividendo anual por acción (una llamada a yfinance; caché en disco de 12 h)."""
    def download():
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info or {}
            return {k: float(info[k]) if info.get(k) else None for k in ("trailingEps", "dividendRate")}
        except Exception:
            return {"trailingEps": None, "dividendRate": None}
    return market_cache.cached("market_info", ticker, download)


def fetch_ttm_eps(ticker: str) -> Optional[float]:
    return _market_info(ticker)["trailingEps"]


def ttm_metric(ticker: str, metric: str) -> Optional[float]:
    if metric == "fcf":
        from app.application.services.thesis_service import fetch_ttm_fcf_per_share
        return fetch_ttm_fcf_per_share(ticker)
    return fetch_ttm_eps(ticker)


def live(ticker: str, asset_data: Dict[str, Any], price: Optional[float]) -> Optional[Dict[str, Any]]:
    """Valoración guardada evaluada al precio de hoy (con la métrica TTM si no se fijó a mano)."""
    v = stored(asset_data)
    if not v:
        return None
    if v.get("dps") is None:
        v = {**v, "dps": _market_info(ticker)["dividendRate"] or 0.0}
    metric0 = v.get("metric_value") or ttm_metric(ticker, v.get("metric", "eps"))
    out = evaluate(v, price, metric0)
    return {**out, "auto_metric": not v.get("metric_value"), "inputs": v} if out else None
