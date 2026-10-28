"""
Ratios forenses calculados en Python a partir de las cuentas pegadas (`full_audit_structure`), para que los agentes IA
no tengan que hacer cuentas (las hacen mal): una tabla markdown por ejercicio (los últimos MAX_YEARS y el LTM).

Calidad del beneficio (CFO y FCF frente al beneficio, accruals), circulante (DSO/DIO/DPO), inversión (capex frente a
amortizaciones y ventas), dilución (SBC, acciones), deuda (deuda neta/EBITDA, cobertura de intereses), ROIC y Altman
Z'' (versión con patrimonio contable, válida para no industriales). Lo que no se puede calcular sale «N/A».
Las filas cambian de nombre según la fuente, así que se buscan por varios nombres (como en financial_trends_service).
"""
from typing import Any, Callable, Dict, List, Optional

from app.application.services.financial_trends_service import _row, _num

MAX_YEARS = 10

REVENUE = ("total revenues", "operating revenues", "revenue", "total revenue")
COST_OF_SALES = ("cost of sales", "cost of revenue", "cost of goods sold", "cost of revenues")
GROSS_PROFIT = ("gross profit",)
OPERATING_INCOME = ("operating profit", "operating income", "ebit")
NET_INCOME_IS = ("net income attributable to common shareholders", "net income to common incl extra items",
                 "consolidated net income", "net income")
EBITDA = ("ebitda",)
INTEREST = ("interest expense", "interest expense, total", "net interest expenses")
TAX_RATE = ("effective tax rate",)
SHARES = ("diluted weighted average shares outstanding", "weighted avg. shares outstanding dil",
          "shares outstanding", "total shares outstanding")

CASH = ("total cash and cash equivalents", "total cash and short term investments", "cash and cash equivalents",
        "cash and equivalents")
RECEIVABLES = ("total trade receivables", "accounts receivable", "accounts receivable, total", "total receivables")
INVENTORY = ("inventories", "inventory")
PAYABLES = ("accounts payable", "accounts payable, total")
CURRENT_ASSETS = ("total current assets",)
CURRENT_LIAB = ("total current liabilities",)
TOTAL_ASSETS = ("total assets",)
TOTAL_LIAB = ("total liabilities",)
RETAINED = ("retained earnings",)
EQUITY = ("total shareholders' equity", "total equity", "total common shareholders' equity", "total common equity")
GOODWILL = ("goodwill",)
INTANGIBLES = ("net intangible assets", "other intangibles, total", "intangible assets")
TOTAL_DEBT = ("total debt",)
DEBT_PARTS = (("long-term debt",), ("short-term debt", "short-term borrowings"),
              ("current portion of long-term debt", "current portion of debt"))

NET_INCOME_CF = ("net income",)
CFO = ("cash from operating activities", "cash from operations")
CAPEX = ("capital expenditure", "capital expenditures")
DA = ("depreciation & amortization", "depreciation & amortization, total", "depreciation & amortization expenses")
SBC = ("share-based compensation expense", "stock-based compensation")
FCF = ("free cash flow",)
NOPAT = ("nopat",)


def _div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return a / b if a is not None and b else None


class _Fin:
    """Acceso por ejercicio (índice) a las filas de las tres cuentas, buscando por varios nombres."""

    def __init__(self, struct: Dict[str, Any]):
        self.years = [str(y) for y in struct.get("years") or []]
        self.sections = [struct.get(s) or {} for s in ("income_statement", "balance_sheet", "cash_flow")]

    def get(self, names, i: int, section: Optional[int] = None) -> Optional[float]:
        for sec in ([self.sections[section]] if section is not None else self.sections):
            row = _row(sec, names)
            if row is not None and i < len(row):
                v = _num(row[i])
                if v is not None:
                    return v
        return None

    def ratio_row(self, names, i: int) -> Optional[float]:
        """Márgenes y tipos: unas fuentes en tanto por uno y otras en %; aquí, en tanto por uno."""
        v = self.get(names, i)
        return None if v is None else (v / 100 if abs(v) > 1.5 else v)

    def revenue(self, i):
        return self.get(REVENUE, i, 0)

    def cogs(self, i):
        c = self.get(COST_OF_SALES, i, 0)
        if c is not None:
            return abs(c)
        r, g = self.revenue(i), self.get(GROSS_PROFIT, i, 0)
        return r - g if r is not None and g is not None else None

    def net_income(self, i):
        v = self.get(NET_INCOME_IS, i, 0)
        return v if v is not None else self.get(NET_INCOME_CF, i, 2)

    def debt(self, i):
        total = self.get(TOTAL_DEBT, i, 1)
        if total is not None:
            return total
        parts = [self.get(n, i, 1) for n in DEBT_PARTS]
        return sum(p for p in parts if p is not None) if any(p is not None for p in parts) else None

    def capex(self, i):
        v = self.get(CAPEX, i, 2)
        return abs(v) if v is not None else None

    def fcf(self, i):
        v = self.get(FCF, i, 2)
        if v is not None:
            return v
        cfo, capex = self.get(CFO, i, 2), self.capex(i)
        return cfo - capex if cfo is not None and capex is not None else None

    def ebit(self, i):
        v = self.get(OPERATING_INCOME, i, 0)
        if v is not None:
            return v
        r, m = self.revenue(i), self.ratio_row(("operating margin",), i)
        return r * m if r is not None and m is not None else None


def _growth(f: _Fin, fn: Callable, i: int) -> Optional[float]:
    if i == 0:
        return None
    a, b = fn(i), fn(i - 1)
    return a / b - 1 if a is not None and b and b > 0 else None


def _roic(f: _Fin, i: int) -> Optional[float]:
    nopat = f.get(NOPAT, i, 2)
    if nopat is None:
        ebit, tax = f.ebit(i), f.ratio_row(TAX_RATE, i)
        if ebit is None:
            return None
        tax = min(max(tax if tax is not None else 0.25, 0.0), 0.5)
        nopat = ebit * (1 - tax)
    debt, eq, cash = f.debt(i), f.get(EQUITY, i, 1), f.get(CASH, i, 1)
    if eq is None:
        return None
    invested = (debt or 0) + eq - (cash or 0)
    return nopat / invested if invested > 0 else None


def _altman_z2(f: _Fin, i: int) -> Optional[float]:
    """Altman Z'' (no industriales, con patrimonio contable): >2,6 sano; 1,1-2,6 zona gris; <1,1 riesgo."""
    ta, ca, cl = f.get(TOTAL_ASSETS, i, 1), f.get(CURRENT_ASSETS, i, 1), f.get(CURRENT_LIAB, i, 1)
    re_, ebit, eq = f.get(RETAINED, i, 1), f.ebit(i), f.get(EQUITY, i, 1)
    tl = f.get(TOTAL_LIAB, i, 1)
    if tl is None and ta is not None and eq is not None:
        tl = ta - eq
    if None in (ta, ca, cl, re_, ebit, eq, tl) or not ta or not tl:
        return None
    return 6.56 * (ca - cl) / ta + 3.26 * re_ / ta + 6.72 * ebit / ta + 1.05 * eq / tl


def _metrics(f: _Fin) -> List[tuple]:
    """(nombre, formato, función por índice de ejercicio)."""
    def days(num_fn, den_fn):
        return lambda i: (lambda n, d: n / d * 365 if n is not None and d else None)(num_fn(i), den_fn(i))

    return [
        ("Crec. ventas", "pct", lambda i: _growth(f, f.revenue, i)),
        ("Margen bruto", "pct", lambda i: _div(f.revenue(i) - f.cogs(i), f.revenue(i))
            if f.revenue(i) is not None and f.cogs(i) is not None else f.ratio_row(("gross profit margin",), i)),
        ("Margen operativo", "pct", lambda i: _div(f.ebit(i), f.revenue(i))),
        ("Margen neto", "pct", lambda i: _div(f.net_income(i), f.revenue(i))),
        ("CFO / Beneficio neto", "x", lambda i: _div(f.get(CFO, i, 2), f.net_income(i))
            if (f.net_income(i) or 0) > 0 else None),
        ("FCF / Beneficio neto", "x", lambda i: _div(f.fcf(i), f.net_income(i)) if (f.net_income(i) or 0) > 0 else None),
        ("Accruals (BN−CFO)/Activo", "pct", lambda i: _div(
            f.net_income(i) - f.get(CFO, i, 2), f.get(TOTAL_ASSETS, i, 1))
            if f.net_income(i) is not None and f.get(CFO, i, 2) is not None else None),
        ("DSO (días)", "days", days(lambda i: f.get(RECEIVABLES, i, 1), f.revenue)),
        ("DIO (días)", "days", days(lambda i: f.get(INVENTORY, i, 1), f.cogs)),
        ("DPO (días)", "days", days(lambda i: f.get(PAYABLES, i, 1), f.cogs)),
        ("Capex / Amortizaciones", "x", lambda i: _div(f.capex(i), abs(f.get(DA, i) or 0) or None)),
        ("Capex / Ventas", "pct", lambda i: _div(f.capex(i), f.revenue(i))),
        ("SBC / FCF", "pct", lambda i: _div(abs(f.get(SBC, i, 2)), f.fcf(i))
            if f.get(SBC, i, 2) is not None and (f.fcf(i) or 0) > 0 else None),
        ("Var. acciones", "pct", lambda i: _growth(f, lambda j: f.get(SHARES, j, 0), i)),
        ("Deuda neta / EBITDA", "x", lambda i: _div((f.debt(i) or 0) - (f.get(CASH, i, 1) or 0), f.get(EBITDA, i, 0))
            if (f.get(EBITDA, i, 0) or 0) > 0 and (f.debt(i) is not None or f.get(CASH, i, 1) is not None) else None),
        ("Cobertura intereses (EBIT/int.)", "x", lambda i: _div(f.ebit(i), abs(f.get(INTEREST, i, 0) or 0) or None)),
        ("Goodwill+intangibles / Patrimonio", "pct", lambda i: _div(
            (f.get(GOODWILL, i, 1) or 0) + (f.get(INTANGIBLES, i, 1) or 0), f.get(EQUITY, i, 1))
            if (f.get(EQUITY, i, 1) or 0) > 0 and (f.get(GOODWILL, i, 1) is not None or f.get(INTANGIBLES, i, 1) is not None)
            else None),
        ("ROIC aprox.", "pct", lambda i: _roic(f, i)),
        ("Altman Z''", "x", lambda i: _altman_z2(f, i)),
    ]


def _fmt(v: Optional[float], kind: str) -> str:
    if v is None:
        return "N/A"
    if kind == "pct":
        return f"{v * 100:.1f}%"
    if kind == "days":
        return f"{v:.0f}"
    return f"{v:.2f}"


def compute(struct: Optional[Dict[str, Any]]) -> Dict[str, List[Optional[float]]]:
    """{métrica: [valor por ejercicio]} (todos los ejercicios de la estructura)."""
    if not struct or not struct.get("years"):
        return {}
    f = _Fin(struct)
    out = {}
    for name, _kind, fn in _metrics(f):
        vals = []
        for i in range(len(f.years)):
            try:
                vals.append(fn(i))
            except (TypeError, ZeroDivisionError):
                vals.append(None)
        out[name] = vals
    return out


def ratios_markdown(struct: Optional[Dict[str, Any]], max_years: int = MAX_YEARS) -> str:
    """Tabla markdown de ratios (últimos `max_years` ejercicios + LTM). Vacía si no hay cuentas."""
    if not struct or not struct.get("years"):
        return ""
    f = _Fin(struct)
    years = f.years
    keep = [i for i, y in enumerate(years) if y.upper() != "LTM"][-max_years:] + \
           [i for i, y in enumerate(years) if y.upper() == "LTM"]
    kinds = {name: kind for name, kind, _ in _metrics(f)}
    data = compute(struct)
    if all(v is None for vals in data.values() for v in vals):
        return ""
    lines = ["| Ratio | " + " | ".join(years[i] for i in keep) + " |", "| --- |" + " --- |" * len(keep)]
    for name, vals in data.items():
        if all(vals[i] is None for i in keep):
            continue
        lines.append(f"| {name} | " + " | ".join(_fmt(vals[i], kinds[name]) for i in keep) + " |")
    return ("### Ratios forenses (calculados en Python a partir de las cuentas; úsalos, no los recalcules)\n"
            + "\n".join(lines)
            + "\n_Lectura: CFO/BN < 0,8 de forma sostenida o accruals > 10% = beneficio de baja calidad; DSO/DIO "
              "creciendo más que las ventas = posible forzado de ventas; Capex/Amortizaciones < 1 sostenido = "
              "infra-inversión; Altman Z'' < 1,1 = riesgo de insolvencia, > 2,6 = sano._")


# --- Métricas de los 5 pilares de las Normas de Calidad ----------------------------------------------------------
# Lo que las normas piden calcular (CAGR, medias a 10 años, ROIIC…), hecho aquí para que el agente solo lo interprete.
# Solo ejercicios completos (el LTM no cuenta para CAGR ni medias).

LEASES = ("leases", "long-term leases", "current portion of leases")


def _full_idx(f: _Fin) -> List[int]:
    return [i for i, y in enumerate(f.years) if y.upper() != "LTM"]


def _cagr(series: List[Optional[float]], idx: List[int], n: int) -> Optional[float]:
    """CAGR a n años terminando en el último ejercicio completo; None si falta o hay valores ≤ 0 en los extremos."""
    if len(idx) <= n:
        return None
    a, b = series[idx[-1 - n]], series[idx[-1]]
    return (b / a) ** (1 / n) - 1 if a and b and a > 0 and b > 0 else None


def _avg(vals: List[Optional[float]]) -> Optional[float]:
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


def _invested(f: _Fin, i: int, tangible: bool = False) -> Optional[float]:
    eq = f.get(EQUITY, i, 1)
    if eq is None:
        return None
    ic = (f.debt(i) or 0) + eq - (f.get(CASH, i, 1) or 0)
    if tangible:
        ic -= (f.get(GOODWILL, i, 1) or 0) + (f.get(INTANGIBLES, i, 1) or 0)
    return ic


def _nopat(f: _Fin, i: int) -> Optional[float]:
    v = f.get(NOPAT, i, 2)
    if v is not None:
        return v
    ebit, tax = f.ebit(i), f.ratio_row(TAX_RATE, i)
    return None if ebit is None else ebit * (1 - min(max(tax if tax is not None else 0.25, 0.0), 0.5))


MIN_REINVESTMENT = 0.10   # ROIIC solo si el capital invertido creció al menos un 10%: con menos, el cociente es ruido


def _roiic(f: _Fin, idx: List[int], n: int) -> Optional[float]:
    """ΔNOPAT / Δcapital invertido en n años. Sin reinversión relevante (Δcapital < 10% del inicial, o negativo) no
    hay ROIIC: None (no es malo; suele ser una empresa que devuelve el capital)."""
    if len(idx) <= n:
        return None
    a, b = idx[-1 - n], idx[-1]
    dn = (lambda x, y: y - x if x is not None and y is not None else None)(_nopat(f, a), _nopat(f, b))
    ic_a, ic_b = _invested(f, a), _invested(f, b)
    if dn is None or ic_a is None or ic_b is None:
        return None
    di = ic_b - ic_a
    return dn / di if di > 0 and di >= MIN_REINVESTMENT * abs(ic_a) else None


def _cagr_avg3(series: List[Optional[float]], idx: List[int]) -> Optional[float]:
    """CAGR entre la media de los 3 primeros ejercicios y la de los 3 últimos (menos sensible a un año extremo)."""
    if len(idx) < 6:
        return None
    first = [series[i] for i in idx[:3]]
    last = [series[i] for i in idx[-3:]]
    if any(v is None for v in first + last):
        return None
    a, b = sum(first) / 3, sum(last) / 3
    years = (len(idx) - 1) - 2          # de centro a centro de cada trío
    return (b / a) ** (1 / years) - 1 if a > 0 and b > 0 and years > 0 else None


def pillar_metrics_markdown(struct: Optional[Dict[str, Any]]) -> str:
    """Tabla de métricas de los pilares (CAGR 3/5/10 años, medias a 10 años, ROIIC…). Vacía si no hay cuentas."""
    if not struct or not struct.get("years"):
        return ""
    f = _Fin(struct)
    idx = _full_idx(f)[-11:]          # hasta 10 años de recorrido (11 ejercicios)
    if len(idx) < 2:
        return ""
    n_all = len(f.years)
    rev = [f.revenue(i) for i in range(n_all)]
    fcf = [f.fcf(i) for i in range(n_all)]
    eps = [f.get(("diluted eps", "basic eps", "eps"), i, 0) for i in range(n_all)]
    shares = [f.get(SHARES, i, 0) for i in range(n_all)]
    fcf_ps = [fc / s if fc is not None and s else None for fc, s in zip(fcf, shares)]
    span = len(idx) - 1

    long_n = min(10, span)            # «10 años» o lo máximo que haya
    # Un salto de más del 50% en las acciones en un año (salida a bolsa, split mal ajustado, dato erróneo) invalida
    # los CAGR por acción que lo cruzan
    jumps = [k for k in range(1, len(idx)) if shares[idx[k]] and shares[idx[k - 1]]
             and abs(shares[idx[k]] / shares[idx[k - 1]] - 1) > 0.5]

    def cagr_row(name, series, per_share=False):
        cells = []
        for n in (3, 5, long_n):
            crosses = per_share and any(k > len(idx) - 1 - n for k in jumps)
            cells.append("N/A*" if crosses else _fmt(_cagr(series, idx, n), "pct"))
        return f"| {name} | " + " | ".join(cells) + " |"

    last10 = idx[-10:]
    m = compute(struct)
    gross = [m["Margen bruto"][i] for i in last10]
    ebit_m = [m["Margen operativo"][i] for i in last10]
    fcf_m = [_div(fcf[i], rev[i]) for i in last10]
    roic = [_div(_nopat(f, i), _invested(f, i)) if (_invested(f, i) or 0) > 0 else None for i in last10]
    # ROIC tangible: sin sentido si el capital tangible es casi nulo (< 10% del total): N/A
    roic_t = [_div(_nopat(f, i), _invested(f, i, True))
              if (_invested(f, i, True) or 0) > 0.1 * (_invested(f, i) or 0) > 0 else None for i in last10]
    sbc_fcf = [m["SBC / FCF"][i] for i in last10]
    roe = [_div(f.net_income(i), f.get(EQUITY, i, 1)) if (f.get(EQUITY, i, 1) or 0) > 0 else None for i in last10]
    last = idx[-1]
    debt_econ = (f.debt(last) or 0) + sum(f.get(n, last, 1) or 0 for n in (("leases", "long-term leases"),
                                                                             ("current portion of leases",)))
    net_debt = debt_econ - (f.get(CASH, last, 1) or 0)
    quick = _div((f.get(CASH, last, 1) or 0) + (f.get(RECEIVABLES, last, 1) or 0), f.get(CURRENT_LIAB, last, 1))
    fcf_ebitda = _avg([_div(fcf[i], f.get(EBITDA, i, 0)) if (f.get(EBITDA, i, 0) or 0) > 0 else None for i in last10])

    lines = [
        f"### Métricas de los pilares (calculadas en Python; ejercicios completos {f.years[idx[0]]}–{f.years[last]})",
        f"| Crecimiento (CAGR) | 3 años | 5 años | {long_n} años |", "| --- | --- | --- | --- |",
        cagr_row("Ventas", rev), cagr_row("FCF total", fcf), cagr_row("BPA diluido", eps, True),
        cagr_row("FCF por acción", fcf_ps, True), cagr_row("Acciones en circulación", shares, True),
        f"| Ventas (CAGR de medias de 3 años: primeras frente a últimas) | | | {_fmt(_cagr_avg3(rev, idx), 'pct')} |",
        f"| FCF total (CAGR de medias de 3 años) | | | {_fmt(_cagr_avg3(fcf, idx), 'pct')} |",
        *([f"_* Salto anómalo de acciones en {', '.join(f.years[idx[k]] for k in jumps)} (salida a bolsa, split o "
           "dato erróneo): las cifras por acción de antes no son comparables._"] if jumps else []),
        "", "| Medias y niveles | Valor |", "| --- | --- |",
        f"| Margen bruto: media / mín. / máx. ({len(last10)} años) | {_fmt(_avg(gross), 'pct')} / "
        f"{_fmt(min((g for g in gross if g is not None), default=None), 'pct')} / "
        f"{_fmt(max((g for g in gross if g is not None), default=None), 'pct')} |",
        f"| Margen operativo medio | {_fmt(_avg(ebit_m), 'pct')} |",
        f"| Margen FCF medio | {_fmt(_avg(fcf_m), 'pct')} |",
        f"| ROIC medio (con fondo de comercio) | {_fmt(_avg(roic), 'pct')} |",
        f"| ROIC tangible medio (sin fondo de comercio ni intangibles; N/A = capital tangible casi nulo) | {_fmt(_avg(roic_t), 'pct')} |",
        f"| Años con ROIC > 10% | {sum(1 for r in roic if r is not None and r > 0.10)} de {sum(1 for r in roic if r is not None)} |",
        f"| ROE medio (beneficio neto / patrimonio; la referencia en bancos y aseguradoras) | {_fmt(_avg(roe), 'pct')} |",
        f"| ROIIC 3 / 5 / {long_n} años (ΔNOPAT / Δcapital invertido; N/A = sin reinversión relevante, < 10%) | "
        f"{_fmt(_roiic(f, idx, 3), 'pct')} / {_fmt(_roiic(f, idx, 5), 'pct')} / {_fmt(_roiic(f, idx, long_n), 'pct')} |",
        f"| SBC / FCF medio | {_fmt(_avg(sbc_fcf), 'pct')} |",
        f"| Deuda económica (deuda + arrendamientos) / FCF, último año | {_fmt(_div(debt_econ, fcf[last]) if (fcf[last] or 0) > 0 else None, 'x')} |",
        f"| Deuda neta (con arrendamientos) / EBITDA, último año | {_fmt(_div(net_debt, f.get(EBITDA, last, 0)) if (f.get(EBITDA, last, 0) or 0) > 0 else None, 'x')} |",
        f"| Quick ratio (caja + cobros) / pasivo corriente, último año | {_fmt(quick, 'x')} |",
        f"| FCF / EBITDA medio | {_fmt(fcf_ebitda, 'x')} |",
    ]
    return "\n".join(lines)
