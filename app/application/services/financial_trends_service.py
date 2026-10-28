"""
Finanzas de un vistazo: seis minigráficos de los últimos ejercicios (hasta 10 y el LTM) con su tendencia en color.

Ventas, BPA, flujo de caja libre, márgenes (bruto, operativo y neto), acciones en circulación y deuda (con la caja).
Sale del histórico de la ficha (utils.financial_math.get_financials_history): las filas cambian de nombre según la
fuente (auditoría manual, IA, Alpha Vantage…), así que se buscan por varios nombres. Lo que falta, sale vacío.
Tendencia: crecimiento anual del primer al último ejercicio completo (sin el LTM). En acciones y deuda, bajar es bueno.
"""
from typing import Any, Dict, List, Optional

from app.application.services.insider_service import annual_shares

MAX_YEARS = 10
FLAT_PCT = 2.0          # menos de ±2 %/año (o ±1 punto de margen): estable
FLAT_MARGIN_PP = 1.0

GROSS_MARGIN = ("gross profit margin", "gross margin")
OPERATING_MARGIN = ("operating margin",)
OPERATING_INCOME = ("operating profit", "operating income", "ebit")
GROSS_PROFIT = ("gross profit",)
DEBT_ROWS = (("long-term debt",), ("short-term borrowings", "short-term debt"),
             ("current portion of long-term debt", "current portion of debt"))
TOTAL_DEBT = ("total debt",)
CASH = ("total cash and cash equivalents", "total cash and short term investments", "cash and cash equivalents",
        "cash and equivalents", "cash and short term investments")


def _row(section: Dict[str, Any], names) -> Optional[List[Any]]:
    by_name = {k.lower().strip(): k for k in section or {}}
    key = next((by_name[n] for n in names if n in by_name), None)
    return section[key] if key else None


def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and v == v else None


def _ratio(v) -> Optional[float]:
    """Márgenes: unas fuentes los dan en tanto por uno y otras en %; aquí, en %."""
    v = _num(v)
    return None if v is None else (v * 100 if abs(v) <= 1.5 else v)


def _cagr(first: Optional[float], last: Optional[float], n: int) -> Optional[float]:
    if not first or not last or n <= 0 or first <= 0 or last <= 0:
        return None
    return 100 * ((last / first) ** (1 / n) - 1)


def _span(years: List[str], values: List[Optional[float]]):
    """Primer y último ejercicio completo con dato (el LTM no cuenta para la tendencia)."""
    pts = [(i, v) for i, (y, v) in enumerate(zip(years, values)) if v is not None and str(y).upper() != "LTM"]
    return (pts[0], pts[-1]) if len(pts) >= 2 else (None, None)


def _tone(pct: Optional[float], lower_is_better: bool = False, flat: float = FLAT_PCT) -> str:
    if pct is None:
        return "none"
    if abs(pct) < flat:
        return "flat"
    return "good" if (pct < 0) == lower_is_better else "bad"


def _growth_tile(key: str, title: str, years: List[str], values: List[Optional[float]], unit: str) -> Dict[str, Any]:
    a, b = _span(years, values)
    cagr = _cagr(a[1], b[1], b[0] - a[0]) if a else None
    trend = None
    if cagr is not None:
        trend = {"pct": cagr, "years": b[0] - a[0], "kind": "cagr"}
    elif a and a[1] and b[1] is not None:   # con negativos no hay CAGR: solo si mejora o empeora
        trend = {"pct": None, "years": b[0] - a[0], "kind": "dir", "up": b[1] > a[1]}
    tone = _tone(cagr) if cagr is not None else ("good" if trend and trend["up"] else "bad" if trend else "none")
    last = next((v for v in reversed(values) if v is not None), None)
    return {"key": key, "title": title, "unit": unit, "type": "bar", "years": years, "last": last, "trend": trend, "tone": tone,
            "series": [{"name": title, "values": values}]}


def build(fin: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fin = fin or {}
    years = [str(y) for y in fin.get("years") or []]
    if not years:
        return []
    # los últimos MAX_YEARS ejercicios (y el LTM, si lo hay)
    keep = [i for i, y in enumerate(years) if y.upper() == "LTM"]
    full = [i for i, y in enumerate(years) if y.upper() != "LTM"][-MAX_YEARS:]
    idx = full + keep
    cut = lambda vals: [(_num(vals[i]) if vals and i < len(vals) else None) for i in idx]
    ys = [years[i] for i in idx]
    inc, bal = fin.get("income_statement") or {}, fin.get("balance_sheet") or {}
    revenue, eps, fcf, net = cut(fin.get("revenue")), cut(fin.get("eps")), cut(fin.get("fcf")), cut(fin.get("net_income"))
    tiles = [_growth_tile("revenue", "Ventas", ys, revenue, "money"),
             _growth_tile("eps", "BPA", ys, eps, "price"),
             _growth_tile("fcf", "Flujo de caja libre", ys, fcf, "money")]

    # márgenes: el operativo marca la tendencia (puntos ganados o perdidos)
    def margin(row_names, numerator=None):
        r = _row(inc, row_names)
        if r:
            return [_ratio(v) for v in cut(r)]
        if numerator:
            n = _row(inc, numerator)
            if n:
                return [100 * a / b if a is not None and b else None for a, b in zip(cut(n), revenue)]
        return [None] * len(ys)
    gross = margin(GROSS_MARGIN, GROSS_PROFIT)
    oper = margin(OPERATING_MARGIN, OPERATING_INCOME)
    net_m = [100 * a / b if a is not None and b else None for a, b in zip(net, revenue)]
    lead = oper if any(v is not None for v in oper) else net_m
    a, b = _span(ys, lead)
    pp = (b[1] - a[1]) if a else None
    tiles.append({"key": "margins", "title": "Márgenes", "unit": "pct", "type": "line", "years": ys,
                  "last": next((v for v in reversed(lead) if v is not None), None),
                  "trend": {"pp": pp, "years": b[0] - a[0], "kind": "pp", "of": "operativo" if lead is oper else "neto"} if a else None,
                  "tone": _tone(pp, flat=FLAT_MARGIN_PP),
                  "series": [s for s in ({"name": "Bruto", "values": gross}, {"name": "Operativo", "values": oper},
                                         {"name": "Neto", "values": net_m}) if any(v is not None for v in s["values"])]})

    # acciones en circulación (las diluidas medias de cada ejercicio; ya vienen ajustadas por splits)
    sh = dict((y, v) for y, v in (annual_shares(fin) or []))
    shares = [sh.get(y) for y in ys]
    a, b = _span(ys, shares)
    cagr = _cagr(a[1], b[1], b[0] - a[0]) if a else None
    tiles.append({"key": "shares", "title": "Acciones", "unit": "count", "type": "line", "years": ys,
                  "last": next((v for v in reversed(shares) if v is not None), None),
                  "trend": {"pct": cagr, "years": b[0] - a[0], "kind": "cagr"} if cagr is not None else None,
                  "tone": _tone(cagr, lower_is_better=True, flat=0.5), "series": [{"name": "Acciones", "values": shares}]})

    # deuda financiera (sin arrendamientos) y caja: la tendencia, la de la deuda neta
    total = _row(bal, TOTAL_DEBT)
    if total:
        debt = cut(total)
    else:
        parts = [cut(r) for r in (_row(bal, names) for names in DEBT_ROWS) if r]
        debt = [sum(v for v in col if v is not None) if any(v is not None for v in col) else None for col in zip(*parts)] if parts else [None] * len(ys)
    cash = cut(_row(bal, CASH))
    if not total and any(v is not None for v in debt):   # con balance ese año y la fila de deuda vacía: no tenía deuda
        debt = [0.0 if d is None and c is not None else d for d, c in zip(debt, cash)]
    net_debt = [d - c if d is not None and c is not None else None for d, c in zip(debt, cash)]
    last_nd = next((v for v in reversed(net_debt) if v is not None), None)
    last_fcf = next((v for v in reversed(fcf) if v is not None), None)
    a, b = _span(ys, net_debt)
    tone = "none"
    if a:
        tone = "flat" if abs(b[1] - a[1]) < 0.02 * max(abs(a[1]), abs(b[1]), 1) else ("good" if b[1] < a[1] else "bad")
    if last_nd is not None and last_nd <= 0:
        tone = "good"   # más caja que deuda
    tiles.append({"key": "debt", "title": "Deuda y caja", "unit": "money", "type": "debt", "years": ys,
                  "last": next((v for v in reversed(debt) if v is not None), None), "net_debt": last_nd,
                  "cash": next((v for v in reversed(cash) if v is not None), None),
                  "years_fcf": last_nd / last_fcf if last_nd is not None and last_nd > 0 and last_fcf and last_fcf > 0 else None,
                  "trend": {"kind": "debt", "years": b[0] - a[0]} if a else None, "tone": tone,
                  "series": [{"name": "Deuda", "values": debt}, {"name": "Caja", "values": cash}]})
    for t in tiles:
        t["empty"] = not any(v is not None for s in t["series"] for v in s["values"])
    return tiles


EBITDA = ("ebitda",)


def net_debt_to_ebitda(fin: Optional[Dict[str, Any]], tiles: Optional[List[Dict[str, Any]]] = None) -> Optional[float]:
    """Deuda neta / EBITDA del último ejercicio con deuda, caja y EBITDA a la vez (negativo = más caja que deuda).
    Sin ratio (None) si falta algún dato o el EBITDA no es positivo. `tiles`: el resultado de build(fin), si ya se tiene."""
    fin = fin or {}
    tiles = tiles if tiles is not None else build(fin)
    debt_tile = next((t for t in tiles if t["key"] == "debt"), None)
    row = _row(fin.get("income_statement") or {}, EBITDA)
    if not debt_tile or not row:
        return None
    ebitda = {str(y): _num(v) for y, v in zip(fin.get("years") or [], row)}
    series = {s["name"]: s["values"] for s in debt_tile["series"]}
    for i in range(len(debt_tile["years"]) - 1, -1, -1):
        d, c, e = series["Deuda"][i], series["Caja"][i], ebitda.get(debt_tile["years"][i])
        if d is not None and c is not None and e is not None:
            return (d - c) / e if e > 0 else None
    return None
