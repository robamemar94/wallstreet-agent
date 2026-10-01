"""Valoración base de una empresa: un solo escenario sobre BPA o FCF por acción.

Métrica(año N) = métrica₀ · (1+g)^N; precio(año N) = métrica(N) · múltiplo de salida.
Los dividendos se proyectan desde el dividendo por acción actual (importe fijo, no rentabilidad),
así el precio de compra no depende de la cotización. La rentabilidad estimada es la TIR de pagar
el precio de hoy, cobrar los dividendos y vender en el año N; el precio de compra es el que da
exactamente la rentabilidad objetivo.

El TTM va con su fecha real (cierre del último trimestre publicado), no con el año en curso: el valor de un año
natural Y es TTM · (1+g)^(años desde el cierre del TTM hasta el 31/12 de Y). Así el año en curso incluye el
crecimiento de los trimestres que faltan (con un TTM a 30/06, medio año).

Horizonte fijo de 5 años. Se puede corregir a mano el valor de un año concreto (anclaje): desde ese año la
métrica sigue con el crecimiento g, así que el año N = corrección · (1+g)^(N−k). El anclaje va con su año
natural: al pasar el año se reproyecta solo, y caduca cuando el TTM ya cubre ese año (sin fecha del TTM, al
pasar el año).

El año en curso puede partir de los analistas (por defecto, solo BPA): su estimación del año fiscal llevada al
31/12 del año natural con g; o de la media entre analistas y TTM; o solo del TTM. Sin estimaciones, con FCF o con
la métrica fijada a mano, siempre del TTM.
"""
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.application.services import market_cache
from app.application.services.thesis_service import quote_unit_factor

ASSET_KEY = "base_valuation"
METRICS = {"eps": {"label": "BPA", "multiple": "PER"}, "fcf": {"label": "FCF/acción", "multiple": "P/FCF"}}
TARGET_RETURN_PCT = 18.0   # precio de compra = el que da esta rentabilidad = corte de «ganga»
DEFAULT_YEARS = 5
# Zonas por rentabilidad estimada (mismos cortes que el color del CAGR en el Centro de mando y la Watchlist)
BARATA_PCT, JUSTO_PCT = 13.0, 8.0
ZONES = ((TARGET_RETURN_PCT, "ganga"), (BARATA_PCT, "barata"), (JUSTO_PCT, "justo"))
# Anticuada: dos presentaciones de resultados desde la última revisión; sin calendario de resultados, 7 meses
STALE_RESULTS, STALE_DAYS = 2, 213


def present_value(v: Dict[str, Any], metric_n: float, rate_pct: float) -> float:
    """Lo que valen hoy, a la tasa dada, los dividendos y el precio de venta del año N (métrica del año N: metric_n)."""
    n, r = int(v["years"]), rate_pct / 100
    dps, gd = v.get("dps") or 0.0, v.get("div_growth") or 0.0
    pv = sum(dps * (1 + gd) ** t / (1 + r) ** t for t in range(1, n + 1))
    return pv + metric_n * v["exit_multiple"] / (1 + r) ** n


def _irr(v: Dict[str, Any], metric_n: float, price: float) -> Optional[float]:
    lo, hi = -99.0, 1000.0
    if present_value(v, metric_n, lo) < price or present_value(v, metric_n, hi) > price:
        return None
    for _ in range(100):   # el valor presente baja al subir la tasa: bisección
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if present_value(v, metric_n, mid) > price else (lo, mid)
    return (lo + hi) / 2


YEAR0_SOURCES = ("analysts", "mean", "ttm")   # de dónde sale el año en curso (por defecto, analistas)
COVERED_DAYS = 46   # un TTM que cierra hasta mes y medio antes de fin de año ya cuenta como ese año


def ttm_offset(ttm_end: Optional[date], year0: int) -> float:
    """Años de crecimiento entre el cierre del TTM y el 31/12 de year0 (0 sin fecha: el TTM cuenta como year0)."""
    if not ttm_end:
        return 0.0
    return min(2.0, max(0.0, (date(year0, 12, 31) - ttm_end).days / 365.25))


def anchor_state(v: Dict[str, Any], year0: int, ttm_end: Optional[date] = None) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """("active" | "expired" | None, anclaje). Solo cuenta si es de la métrica elegida; caduca cuando el TTM ya
    cubre su año (sin fecha del TTM, cuando el año ha pasado)."""
    a = v.get("anchor") or {}
    if not a.get("year") or not a.get("value") or a.get("metric", "eps") != v.get("metric", "eps"):
        return None, None
    year = int(a["year"])
    covered = (date(year, 12, 31) - ttm_end).days <= COVERED_DAYS if ttm_end else year < year0
    return ("expired" if covered else "active"), a


def calendar_estimates(estimates: Optional[Dict[str, Any]], growth: float) -> Dict[int, float]:
    """Estimaciones de los analistas por año natural: cada año fiscal llevado a su 31/12 con g
    (un año fiscal que cierra en junio o antes cuenta como el año natural anterior)."""
    out = {}
    for e in (estimates or {}).get("estimates", []):
        fy = date.fromisoformat(e["fy_end"])
        year = fy.year if fy.month >= 7 else fy.year - 1
        out[year] = e["value"] * (1 + growth) ** ((date(year, 12, 31) - fy).days / 365.25)
    return out


def year0_start(v: Dict[str, Any], metric0: float, year0: int, ttm_end: Optional[date] = None,
                estimates: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Valor del año en curso sin corrección manual: {value, source, ttm, analysts}. ttm = el TTM llevado al 31/12."""
    ttm = metric0 * (1 + v["growth"]) ** ttm_offset(ttm_end, year0)
    est = calendar_estimates(estimates, v["growth"]).get(year0) if v.get("metric", "eps") == "eps" else None
    source = v.get("year0_source") if v.get("year0_source") in YEAR0_SOURCES else "analysts"
    if not est or source == "ttm":
        return {"value": ttm, "source": "ttm", "ttm": ttm, "analysts": est}
    return {"value": est if source == "analysts" else (est + ttm) / 2, "source": source, "ttm": ttm, "analysts": est}


def metric_at(v: Dict[str, Any], metric0: float, t: int, year0: int, ttm_end: Optional[date] = None,
              estimates: Optional[Dict[str, Any]] = None) -> float:
    """Métrica del año year0+t: desde el año en curso (analistas, media o TTM) con g o, desde el año corregido,
    desde la corrección con g."""
    state, a = anchor_state(v, year0, ttm_end)
    if state == "active":
        return a["value"] * (1 + v["growth"]) ** (t - (int(a["year"]) - year0))
    return year0_start(v, metric0, year0, ttm_end, estimates)["value"] * (1 + v["growth"]) ** t


def zone(cagr: Optional[float]) -> Optional[str]:
    if cagr is None:
        return None
    return next((name for cut, name in ZONES if cagr >= cut), "cara")


def evaluate(v: Optional[Dict[str, Any]], price: Optional[float], metric0: Optional[float],
             year0: Optional[int] = None, ttm_end: Optional[date] = None,
             estimates: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """v: valoración guardada; metric0: valor de partida de la métrica (manual o TTM); year0: año en curso;
    ttm_end: cierre del TTM (None si la métrica es manual: cuenta como la del año en curso);
    estimates: estimaciones de los analistas (analyst_eps_estimates) o None."""
    if not v or not price or price <= 0 or not metric0 or metric0 <= 0 or not v.get("exit_multiple"):
        return None
    n, year0 = DEFAULT_YEARS, year0 or date.today().year
    v = {**v, "years": n, "growth": v.get("growth") or 0.0}
    start = year0_start(v, metric0, year0, ttm_end, estimates)
    metric_n = metric_at(v, metric0, n, year0, ttm_end, estimates)
    dps, gd = v.get("dps") or 0.0, v.get("div_growth") or 0.0
    cagr = _irr(v, metric_n, price)
    buy = present_value(v, metric_n, TARGET_RETURN_PCT)
    state, a = anchor_state(v, year0, ttm_end)
    anchor = None
    if state:
        # lo que saldría sin corregir; si el TTM ya cubre el año, el dato real más cercano: el TTM
        auto = start["value"] * (1 + v["growth"]) ** (int(a["year"]) - year0) if state == "active" else metric0
        anchor = {"state": state, "year": int(a["year"]), "value": a["value"], "auto": round(auto, 4),
                  "diff_pct": round(100 * (a["value"] / auto - 1), 1)}
        if state == "active":
            base_cagr = _irr(v, start["value"] * (1 + v["growth"]) ** n, price)
            anchor["cagr_auto"] = round(base_cagr, 2) if base_cagr is not None else None
    return {
        "metric": v.get("metric", "eps"), "metric0": round(metric0, 4), "years": n,
        "metric_n": round(metric_n, 4), "price_n": round(metric_n * v["exit_multiple"], 2),
        "dividends": round(sum(dps * (1 + gd) ** t for t in range(1, n + 1)), 2),
        "multiple_now": round(price / metric0, 1),
        "cagr": round(cagr, 2) if cagr is not None else None,
        "buy_price": round(buy, 2), "target": TARGET_RETURN_PCT,
        "margin_pct": round(100 * (buy / price - 1), 1),   # >0: cotiza por debajo del precio de compra
        "zone": zone(cagr), "year_n": year0 + n,
        "ttm_end": ttm_end.isoformat() if ttm_end else None,
        "metric_year0": round(metric_at(v, metric0, 0, year0, ttm_end, estimates), 4),   # el año en curso
        "year0": {k: round(x, 4) if isinstance(x, float) else x for k, x in start.items()},
        "anchor": anchor,   # corrección manual de un año (activa o caducada), o None
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


def _anchor(a: Any, metric: str) -> Optional[Dict[str, Any]]:
    try:
        year, value = int(a["year"]), float(a["value"])
    except (TypeError, KeyError, ValueError):
        return None
    if value <= 0:
        return None
    return {"year": year, "value": value, "metric": a.get("metric") if a.get("metric") in METRICS else metric,
            "set_on": a.get("set_on") or datetime.now().strftime("%Y-%m-%d")}


def normalize(data: Dict[str, Any], is_manual: bool = True) -> Dict[str, Any]:
    f = lambda k, d=0.0: float(data[k]) if data.get(k) not in (None, "") else d
    metric = data.get("metric") if data.get("metric") in METRICS else "eps"
    return {"metric": metric, "metric_value": f("metric_value", None),   # None → TTM automático
            "growth": f("growth"), "exit_multiple": f("exit_multiple"), "dps": f("dps"),
            "div_growth": f("div_growth"), "years": DEFAULT_YEARS,
            "anchor": _anchor(data.get("anchor"), metric),
            "year0_source": data.get("year0_source") if data.get("year0_source") in YEAR0_SOURCES else "analysts",
            "rationale": data.get("rationale") or "", "is_manual": is_manual,
            "last_updated": datetime.now().strftime("%Y-%m-%d")}


def reviewed_on(v: Dict[str, Any]) -> Optional[str]:
    """Última revisión: al guardar la valoración o al marcarla «revisada, sin cambios» (fechas ISO)."""
    return max((d for d in (v.get("last_updated"), v.get("reviewed_at")) if d), default=None)


def staleness(v: Optional[Dict[str, Any]], reported: List[str], today: Optional[date] = None) -> Optional[Dict[str, Any]]:
    """¿Toca revisar la valoración? reported: fechas de resultados ya publicados. Las antiguas sin revisar ya tienen su aviso."""
    if not v or v.get("legacy") or not reviewed_on(v):
        return None
    today, reviewed = today or date.today(), reviewed_on(v)
    since = sorted(d for d in reported if reviewed < d <= today.isoformat())
    days = (today - date.fromisoformat(reviewed)).days
    stale = len(since) >= STALE_RESULTS if reported else days > STALE_DAYS
    return {"stale": stale, "reviewed": reviewed, "days": days, "results_since": since, "by_calendar": bool(reported)}


def _market_info(ticker: str) -> Dict[str, Any]:
    """BPA de los últimos 12 meses, dividendo anual por acción (en la unidad de cotización) y cierre del último
    trimestre publicado, que es la fecha del TTM (una llamada a yfinance; caché en disco de 12 h)."""
    def download():
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info or {}
            factor = quote_unit_factor(info.get("currency"))   # Londres: BPA y dividendo en libras, precio en peniques
            out = {k: float(info[k]) * factor if info.get(k) else None for k in ("trailingEps", "dividendRate")}
            mrq = info.get("mostRecentQuarter")
            out["ttm_end"] = datetime.fromtimestamp(mrq, timezone.utc).date().isoformat() if mrq else None
            return out
        except Exception:
            return {"trailingEps": None, "dividendRate": None, "ttm_end": None}
    return market_cache.cached("market_info_v3", ticker, download)


def ttm_end_date(ticker: str) -> Optional[date]:
    end = _market_info(ticker).get("ttm_end")
    return date.fromisoformat(end) if end else None


BASIS_GAP_PCT = 10.0   # a partir de esta diferencia entre el BPA del último año de los analistas y el contable se avisa


def analyst_eps_estimates(ticker: str) -> Optional[Dict[str, Any]]:
    """BPA estimado por los analistas para el año fiscal en curso y el siguiente, en la unidad de cotización.

    Cada estimación lleva el cierre de su año fiscal (MSFT cierra en junio, NVDA en enero). basis_gap_pct compara
    el BPA del último año cerrado según los analistas con el contable: si difiere mucho, estiman BPA ajustado y no
    es comparable con el TTM. Caché en disco de 12 h."""
    def download():
        try:
            import yfinance as yf
            from app.application.services.thesis_service import MINOR_UNITS
            stock = yf.Ticker(ticker)
            info = stock.info or {}
            est = stock.earnings_estimate
            next_fy = info.get("nextFiscalYearEnd")
            if est is None or est.empty or "0y" not in est.index or not next_fy:
                return None
            trade, fin = info.get("currency"), info.get("financialCurrency")
            major, factor = MINOR_UNITS.get(trade or "", (trade, 1.0))
            if fin and major and fin != major:   # Evolution estima en euros y cotiza en coronas
                factor *= float(yf.Ticker(f"{fin}{major}=X").fast_info["last_price"])
            num = lambda x: float(x) if x is not None and x == x else None   # NaN → None
            fy0 = datetime.fromtimestamp(next_fy, timezone.utc).date()
            rows = []
            fy1 = fy0.replace(year=fy0.year + 1, day=28) if (fy0.month, fy0.day) == (2, 29) else fy0.replace(year=fy0.year + 1)
            for key, fy_end in (("0y", fy0), ("+1y", fy1)):
                avg = num(est.loc[key, "avg"]) if key in est.index else None
                if avg:
                    rows.append({"period": key, "fy_end": fy_end.isoformat(), "value": round(avg * factor, 4),
                                 "analysts": int(num(est.loc[key, "numberOfAnalysts"]) or 0)})
            if not rows:
                return None
            year_ago = num(est.loc["0y", "yearAgoEps"]) if "yearAgoEps" in est.columns else None
            try:
                gaap = num(stock.income_stmt.loc["Diluted EPS"].dropna().iloc[0])
            except Exception:
                gaap = None
            gap = round(100 * (year_ago / gaap - 1), 1) if year_ago and gaap and gaap > 0 else None
            return {"estimates": rows, "basis_gap_pct": gap,
                    "year_ago": round(year_ago * factor, 4) if year_ago else None,
                    "gaap_last_fy": round(gaap * factor, 4) if gaap else None}
        except Exception:
            return None
    return market_cache.cached("eps_estimates_v1", ticker, download)


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
    # la métrica fijada a mano cuenta como la del año en curso; la automática va con la fecha del TTM y, con BPA,
    # el año en curso puede salir de los analistas
    auto = not v.get("metric_value")
    use_estimates = auto and v.get("metric", "eps") == "eps" and v.get("year0_source") != "ttm"
    out = evaluate(v, price, metric0, ttm_end=ttm_end_date(ticker) if auto else None,
                   estimates=analyst_eps_estimates(ticker) if use_estimates else None)
    return {**out, "auto_metric": not v.get("metric_value"), "inputs": v} if out else None
