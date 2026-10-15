"""
Cifras clave de la ficha (franja de arriba del Resumen): siete números que se entienden de un vistazo.

Cada una lleva su valor en color (verde = bien, ámbar = normal, rojo = flojo; sin color las que dependen de cada empresa,
como el dividendo), una línea de contexto (su media de 5 años, el PER justo, la tendencia de 10 años…) y, si hay
histórico en las cuentas cargadas, su evolución anual en un minigráfico.
Los valores de hoy, de Yahoo (como antes); el histórico, de las cuentas anuales de la ficha (Finanzas). Sin IA.
"""
from typing import Any, Dict, List, Optional

from app.application.services import financial_trends_service as ft
from app.application.services.financial_labels import _norm
from app.application.services.insider_service import annual_shares
from utils.number_format import es

EQUITY_ROWS = ("total common shareholders' equity", "total common equity", "total shareholders' equity", "total equity")


def _f(v) -> Optional[float]:
    try:
        v = float(v)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _tone(v: Optional[float], good: float, ok: float, lower_is_better: bool = False) -> str:
    if v is None:
        return "none"
    if lower_is_better:
        return "good" if v <= good else "mid" if v <= ok else "bad"
    return "good" if v >= good else "mid" if v >= ok else "bad"


def _full_years(fin: Dict[str, Any]) -> List[int]:
    """Índices de los ejercicios completos (sin el LTM), los últimos 10."""
    years = [str(y) for y in fin.get("years") or []]
    return [i for i, y in enumerate(years) if y.upper() != "LTM"][-10:]


def _year_close(dates: List[str], close: List[float], year: str) -> Optional[float]:
    """Último cierre de ese año (aprox. el cierre del ejercicio: la mayoría acaba en diciembre)."""
    best = None
    for d, c in zip(dates, close):
        if d[:4] == year and c is not None:
            best = c
    return best


def _avg(vals: List[Optional[float]], n: int = 5) -> Optional[float]:
    v = [x for x in vals if x is not None][-n:]
    return sum(v) / len(v) if len(v) >= 3 else None


def build(info: Dict[str, Any], db: Dict[str, Any], ext_stats: Dict[str, Any], fin: Optional[Dict[str, Any]],
          dates: Optional[List[str]] = None, close: Optional[List[float]] = None) -> List[Dict[str, Any]]:
    fin, dates, close = fin or {}, dates or [], close or []
    years = [str(y) for y in fin.get("years") or []]
    idx = _full_years(fin)
    col = lambda vals: [_f(vals[i]) if vals and i < len(vals) else None for i in idx]
    eps, fcf, rev, net = col(fin.get("eps")), col(fin.get("fcf")), col(fin.get("revenue")), col(fin.get("net_income"))
    shares_by_year = dict(annual_shares(fin) or [])
    shares = [shares_by_year.get(years[i]) for i in idx]
    year_close = [_year_close(dates, close, years[i]) for i in idx]
    trends = {t["key"]: t for t in ft.build(fin)}
    out = []

    # PER adelantado: frente a tu PER justo (si lo has fijado) y a su media de 5 años (cierre del año / BPA)
    pe = _f(info.get("forwardPE"))
    pe_hist = [c / e if c and e and e > 0 else None for c, e in zip(year_close, eps)]
    fair = _f((db.get("fair_pe_data") or {}).get("fair_forward_pe"))
    pe_avg = _avg(pe_hist)
    ref = fair or pe_avg
    tone = "none" if pe is None or not ref else ("good" if pe <= ref else "mid" if pe <= ref * 1.2 else "bad")
    ctx = " · ".join(x for x in ((f"justo {es(fair, 1)}x" if fair else None), (f"media 5 a {es(pe_avg, 1)}x" if pe_avg else None)) if x)
    out.append({"key": "pe", "label": "PER adelantado", "value": pe, "fmt": "x", "tone": tone, "context": ctx,
                "spark": pe_hist, "tip": "Precio / BPA estimado de los próximos 12 meses. Verde: por debajo de tu PER justo "
                                        "(o, sin él, de su media de 5 años); el minigráfico, el PER de cada año (cierre del año / BPA)."})

    # FCF yield: hoy y por año (FCF / capitalización al cierre del año)
    fy = _f(db.get("fcf_yield"))
    if fy is None and _f(info.get("freeCashflow")) and _f(info.get("marketCap")):
        fy = info["freeCashflow"] / info["marketCap"]
    fy_hist = [100 * f / (c * s) if f is not None and c and s else None for f, c, s in zip(fcf, year_close, shares)]
    fy_avg = _avg(fy_hist)
    out.append({"key": "fcfy", "label": "FCF yield", "value": fy * 100 if fy is not None else None, "fmt": "%",
                "tone": _tone(fy * 100 if fy is not None else None, 4, 2), "context": f"media 5 a {es(fy_avg, 1)} %" if fy_avg else "",
                "spark": fy_hist, "tip": "Flujo de caja libre / capitalización. Verde desde el 4 %, ámbar desde el 2 %."})

    # ROE (o ROIC si Yahoo lo da): hoy y por año (beneficio neto / patrimonio)
    roic, roe = _f(ext_stats.get("returns", {}).get("ROIC (TTM)")), _f(ext_stats.get("returns", {}).get("ROE (TTM)"))
    bal = fin.get("balance_sheet") or {}
    eq_row = next((bal[k] for k in bal if _norm(k) in EQUITY_ROWS), None)
    equity = col(eq_row)
    roe_hist = [100 * n / e if n is not None and e and e > 0 else None for n, e in zip(net, equity)]
    r, lab = (roic, "ROIC") if roic is not None else (roe, "ROE")
    r_avg = _avg(roe_hist)
    out.append({"key": "roe", "label": lab, "value": r * 100 if r is not None else None, "fmt": "%",
                "tone": _tone(r * 100 if r is not None else None, 15, 8), "context": f"media 5 a {es(r_avg, 0)} %" if r_avg and lab == "ROE" else "",
                "spark": roe_hist, "tip": "Rentabilidad sobre los fondos propios (o sobre el capital invertido, si Yahoo lo da), "
                                         "12 meses. Verde desde el 15 %. Ojo: las recompras inflan el ROE."})

    # Margen operativo: hoy y su serie de las cuentas
    om = _f(info.get("operatingMargins"))
    mt = trends.get("margins") or {}
    oper = next((s["values"] for s in mt.get("series", []) if s["name"] == "Operativo"), [])
    oper = [v for v, y in zip(oper, mt.get("years", [])) if str(y).upper() != "LTM"]
    pp = (mt.get("trend") or {}).get("pp")
    out.append({"key": "margin", "label": "Margen operativo", "value": om * 100 if om is not None else None, "fmt": "%",
                "tone": _tone(om * 100 if om is not None else None, 20, 10),
                "context": f"{es(pp, 1, sign=True)} pp en {mt['trend']['years']} años" if pp is not None else "",
                "spark": oper, "tip": "Beneficio operativo / ventas. Verde desde el 20 %, ámbar desde el 10 %."})

    # Crecimiento de ventas esperado (o el último interanual): con el de los últimos 10 años como contexto
    g = _f(db.get("expected_growth")) if db.get("expected_growth") not in (None, "N/A") else _f(info.get("revenueGrowth"))
    rt = (trends.get("revenue") or {}).get("trend") or {}
    out.append({"key": "growth", "label": "Crec. ventas esperado", "value": g * 100 if g is not None else None, "fmt": "%",
                "tone": _tone(g * 100 if g is not None else None, 10, 3),
                "context": f"{rt['years']} años: {es(rt['pct'], 1, sign=True)} %/año" if rt.get("pct") is not None else "",
                "spark": rev, "tip": "Crecimiento de ventas previsto por los analistas para el próximo año (si no lo hay, el "
                                    "último interanual). Verde desde el 10 %. El minigráfico, las ventas de cada año."})

    # Deuda neta / EBITDA
    td, tc, eb = _f(info.get("totalDebt")), _f(info.get("totalCash")), _f(info.get("ebitda"))
    nd = (td - tc) / eb if td is not None and tc is not None and eb else None
    dt = trends.get("debt") or {}
    debt, cash = (dt.get("series") or [{}, {}])[0].get("values", []), (dt.get("series") or [{}, {}])[1].get("values", [])
    net_debt = [d - c if d is not None and c is not None else None for d, c, y in zip(debt, cash, dt.get("years", [])) if str(y).upper() != "LTM"]
    out.append({"key": "debt", "label": "Deuda neta / EBITDA", "value": nd, "fmt": "x",
                "tone": "good" if nd is not None and nd <= 1 else _tone(nd, 1, 3, lower_is_better=True),
                "context": "más caja que deuda" if nd is not None and nd < 0 else ("años de EBITDA para pagarla" if nd is not None else ""),
                "spark": net_debt, "spark_lower": True,
                "tip": "Años de EBITDA para pagar la deuda neta. Negativo: más caja que deuda. Verde hasta 1, ámbar hasta 3. "
                       "El minigráfico, la deuda neta de cada año (más abajo = mejor)."})

    # Dividendo: sin color (depende de la empresa); el pay-out de contexto
    dy = _f(info.get("dividendYield"))
    dy = dy / 100 if dy and dy > 0.2 else dy
    po = _f(info.get("payoutRatio"))
    out.append({"key": "div", "label": "Rent. dividendo", "value": dy * 100 if dy else None, "fmt": "%", "dec": 2, "tone": "none",
                "context": f"pay-out {es(po * 100, 0)} %" if po else ("no reparte" if not dy else ""), "spark": [],
                "tip": "Dividendo anual / precio, y la parte del beneficio que reparte (pay-out)."})

    for k in out:
        k["spark_pts"] = spark_points(k.get("spark") or [])
    return out


def spark_points(vals: List[Optional[float]], w: float = 100, h: float = 22) -> str:
    """Puntos de un minigráfico SVG (polyline) en un lienzo de w × h; los huecos se saltan."""
    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(pts) < 3:
        return ""
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    span = (hi - lo) or 1
    n = len(vals) - 1 or 1
    return " ".join(f"{w * i / n:.1f},{h - 2 - (h - 4) * (v - lo) / span:.1f}" for i, v in pts)
