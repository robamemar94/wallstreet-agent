"""
El año fiscal nuevo en las cuentas pegadas a mano (fiscal.ai → `full_audit_structure`), solo y gratis, con los datos
anuales de Yahoo. Nunca mezcla cifras que no cuadren:
- el año se empareja por las ventas (las de Yahoo frente a las tuyas, ±0,5 %); si ningún año cuadra, no se toca nada y
  avisa para pegarlas de fiscal.ai;
- cada línea (ventas, beneficio, BPA, acciones, caja, deuda, flujos…) se comprueba por separado en el último año que ya
  tienes: solo se añade si allí cuadra con la tuya (`TOLERANCE`); las demás líneas quedan vacías ese año;
- al añadir el año cerrado, se quita la columna «LTM», que ya se ha quedado vieja.
Cuándo: al arrancar y cada día, cada empresa de la cartera, Tier 1 y Tier 2 se mira como mucho una vez por semana
(Yahoo tiene las cuentas cuando ya se han presentado: 2–3 meses tras el cierre de cada una). Un solo aviso con el
resumen de cada pasada. Guardado en settings «fin_update»: lo comprobado y lo que pasó.
"""
import datetime
import logging
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.infrastructure.db.models import DBAsset, DBSetting

logger = logging.getLogger(__name__)

SETTINGS_KEY = "fin_update"
STRUCT_KEY, CHART_KEY = "full_audit_structure", "financials_hist_manual"
CHECK_EVERY_DAYS = 7
NOTICE_DAYS = 14
LOOP_S = 24 * 3600
REVENUE_TOL = 0.005               # emparejar el año: las ventas, ±0,5 %
TOLERANCE = {"default": 0.02, "net_income": 0.015, "eps": 0.015, "fcf": 0.05}
RATIO_TOL = 0.005                 # márgenes y % de cambio (fracciones): ±0,5 puntos

_lock = threading.Lock()
_started = False


# --- Qué línea de tus cuentas sale de qué dato de Yahoo -----------------------------------------------------------
# (sección, nombres posibles de la línea en minúsculas, cómo calcularla con Yahoo, tolerancia)

def _y(frame: str, *names: str) -> Callable:
    def get(yh, col):
        df = yh.get(frame)
        for n in names:
            if df is not None and n in df.index:
                v = df.loc[n, col]
                if v == v and v is not None:   # no NaN
                    return float(v)
        return None
    return get


def _first(*fs: Callable) -> Callable:
    """El primero que dé valor (p. ej. el beneficio del flujo de caja o, si no viene, el de la cuenta de resultados)."""
    def get(yh, col):
        return next((v for v in (f(yh, col) for f in fs) if v is not None), None)
    return get


def _ratio(num: Callable, den: Callable) -> Callable:
    def get(yh, col):
        a, b = num(yh, col), den(yh, col)
        return a / b if a is not None and b else None
    return get


def _chg(f: Callable) -> Callable:
    def get(yh, col):
        cols = list(yh["income"].columns)
        i = cols.index(col)
        prev = cols[i + 1] if i + 1 < len(cols) else None   # Yahoo: del más reciente al más antiguo
        a, b = f(yh, col), f(yh, prev) if prev is not None else None
        return a / b - 1 if a is not None and b else None
    return get


REV = _y("income", "Total Revenue")
GP = _y("income", "Gross Profit")
OI = _y("income", "Operating Income")
NI = _y("income", "Net Income Common Stockholders", "Net Income")
LINES: List[Tuple[str, Tuple[str, ...], Callable, str]] = [
    ("income_statement", ("total revenues",), REV, "revenue"),
    ("income_statement", ("total revenues %chg", "total revenues % chg."), _chg(REV), "ratio"),
    ("income_statement", ("gross profit",), GP, "default"),
    ("income_statement", ("gross profit margin", "gross margin"), _ratio(GP, REV), "ratio"),
    ("income_statement", ("operating income", "operating profit"), OI, "default"),
    ("income_statement", ("operating margin",), _ratio(OI, REV), "ratio"),
    ("income_statement", ("net income attributable to common shareholders", "net income to common incl extra items", "net income"), NI, "net_income"),
    ("income_statement", ("consolidated net income",), _y("income", "Net Income Including Noncontrolling Interests", "Net Income"), "net_income"),
    ("income_statement", ("diluted eps",), _y("income", "Diluted EPS"), "eps"),
    ("income_statement", ("basic eps",), _y("income", "Basic EPS"), "eps"),
    ("income_statement", ("diluted weighted average shares outstanding", "weighted avg. shares outstanding dil"), _y("income", "Diluted Average Shares"), "default"),
    ("income_statement", ("basic weighted average shares outstanding", "weighted avg. shares outstanding"), _y("income", "Basic Average Shares"), "default"),
    ("income_statement", ("shares outstanding", "total shares outstanding"), _y("balance", "Ordinary Shares Number", "Share Issued"), "default"),
    ("balance_sheet", ("cash and cash equivalents", "cash and equivalents"), _y("balance", "Cash And Cash Equivalents"), "default"),
    ("balance_sheet", ("total cash and cash equivalents", "total cash and short term investments"), _y("balance", "Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"), "default"),
    ("balance_sheet", ("long-term debt",), _y("balance", "Long Term Debt"), "default"),
    ("balance_sheet", ("total common shareholders' equity", "total common equity"), _y("balance", "Common Stock Equity"), "default"),
    ("balance_sheet", ("total shareholders' equity", "total equity"), _y("balance", "Total Equity Gross Minority Interest", "Stockholders Equity"), "default"),
    ("cash_flow", ("net income",), _first(_y("cash", "Net Income From Continuing Operations"), _y("income", "Net Income")), "net_income"),
    # «método directo» (Australia…): Yahoo le pone otro nombre al flujo de operaciones
    ("cash_flow", ("cash from operating activities", "cash from operations"),
     _y("cash", "Operating Cash Flow", "Cash Flow From Continuing Operating Activities", "Cash Flowsfromusedin Operating Activities Direct"), "default"),
    ("cash_flow", ("capital expenditure",), _y("cash", "Capital Expenditure"), "default"),
    ("cash_flow", ("free cash flow",), _y("cash", "Free Cash Flow"), "fcf"),
    ("cash_flow", ("cash from investing activities", "cash from investing"), _y("cash", "Investing Cash Flow"), "default"),
    ("cash_flow", ("cash from financing activities", "cash from financing"), _y("cash", "Financing Cash Flow"), "default"),
    ("cash_flow", ("repurchases of common shares",), _y("cash", "Repurchase Of Capital Stock"), "default"),
    ("cash_flow", ("net change in cash",), _y("cash", "Changes In Cash"), "default"),
]
CHART: Dict[str, Tuple[Callable, str]] = {"revenue": (REV, "revenue"), "net_income": (NI, "net_income"),
                                          "eps": (_y("income", "Diluted EPS"), "eps"), "fcf": (_y("cash", "Free Cash Flow"), "fcf")}


def _matches(a: Optional[float], b: Any, kind: str) -> bool:
    if a is None or not isinstance(b, (int, float)) or isinstance(b, bool):
        return False
    if kind == "ratio":
        return abs(a - b) <= RATIO_TOL
    tol = REVENUE_TOL if kind == "revenue" else TOLERANCE.get(kind, TOLERANCE["default"])
    if b == 0:
        return abs(a) < 1e-9
    return abs(a / b - 1) <= tol and (a >= 0) == (b >= 0)


# --- Los años ------------------------------------------------------------------------------------------------------

MONTH_LABEL = re.compile(r"^[A-Z][a-z]{2} '\d{2}$")   # «Jan '26»: ejercicios que no acaban en diciembre


def _label_for(col, matched_label: str, matched_col) -> Optional[str]:
    """La etiqueta del año nuevo con el mismo formato que las tuyas («2026» o «Jan '27»)."""
    if re.fullmatch(r"\d{4}", matched_label):
        return str(col.year + int(matched_label) - matched_col.year)
    if MONTH_LABEL.match(matched_label):
        return col.strftime("%b '%y")
    return None


def _year_of(label: str) -> Optional[int]:
    if re.fullmatch(r"\d{4}", label):
        return int(label)
    if MONTH_LABEL.match(label):
        return 2000 + int(label[-2:])
    return None


def _revenue_row(struct: Dict[str, Any]) -> Optional[List[Any]]:
    inc = {k.lower(): v for k, v in (struct.get("income_statement") or {}).items()}
    return inc.get("total revenues") or (struct.get("chart_metrics") or {}).get("revenue")


def plan(struct: Dict[str, Any], yh: Dict[str, Any]) -> Dict[str, Any]:
    """Qué hacer con las cuentas de una empresa. status:
    - «up_to_date»: Yahoo no tiene ningún año más reciente que los tuyos;
    - «mismatch»: ningún año de Yahoo cuadra con los tuyos (ventas), no se toca nada;
    - «add»: los años nuevos (new) con sus valores, y las líneas que no cuadraron (skipped)."""
    years = [str(y) for y in struct.get("years") or []]
    rev = _revenue_row(struct) or []
    cols = sorted(yh["income"].columns) if yh.get("income") is not None and not yh["income"].empty else []
    if not cols or not rev:
        return {"status": "mismatch", "reason": "sin ventas para emparejar"}
    matched = {}   # columna de Yahoo → índice de tu año
    for c in cols:
        r = REV(yh, c)
        i = next((i for i, y in enumerate(years) if y.upper() != "LTM" and i < len(rev) and _matches(r, rev[i], "revenue")), None)
        if i is not None:
            matched[c] = i
    if not matched:
        last = next((y for y in reversed(years) if y.upper() != "LTM"), "")
        if _year_of(last) and cols[-1].year <= _year_of(last):   # Yahoo no tiene nada más reciente: no hay nada que hacer
            return {"status": "up_to_date"}
        return {"status": "mismatch", "reason": "las ventas de Yahoo no cuadran con ningún año tuyo"}
    last_col = max(matched)
    ov = matched[last_col]   # el último año que ya tienes y está en Yahoo: aquí se comprueba cada línea
    new_cols = [c for c in cols if c > last_col]
    if not new_cols:
        return {"status": "up_to_date"}
    labels = [_label_for(c, years[ov], last_col) for c in new_cols]
    if any(lab is None for lab in labels):
        return {"status": "mismatch", "reason": f"formato de año no reconocido ({years[ov]})"}
    labels_new = [(c, lab) for c, lab in zip(new_cols, labels) if lab not in years]
    if not labels_new:
        return {"status": "up_to_date"}
    values: Dict[Tuple[str, str], List[Optional[float]]] = {}
    skipped = []
    for section, names, f, kind in LINES:
        rows = {k.lower(): k for k in (struct.get(section) or {})}
        key = next((rows[n] for n in names if n in rows), None)
        if not key:
            continue
        mine = struct[section][key]
        if ov < len(mine) and _matches(f(yh, last_col), mine[ov], kind):
            values[(section, key)] = [f(yh, c) for c, _ in labels_new]
        elif ov < len(mine) and isinstance(mine[ov], (int, float)):
            skipped.append(key)
    chart = {}
    for k, (f, kind) in CHART.items():
        mine = (struct.get("chart_metrics") or {}).get(k) or []
        if ov < len(mine) and _matches(f(yh, last_col), mine[ov], kind):
            chart[k] = [f(yh, c) for c, _ in labels_new]
        elif ov < len(mine) and mine[ov] is not None:
            skipped.append(k)
    return {"status": "add", "new": [lab for _, lab in labels_new], "values": values, "chart": chart,
            "skipped": sorted(set(skipped)), "checked_on": years[ov]}


def refill(struct: Dict[str, Any], yh: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """En los años que ya vinieron de Yahoo, las líneas que quedaron vacías y ahora sí cuadran en el año anterior (p. ej.
    tras reconocer otro nombre de Yahoo). None si no hay nada que rellenar."""
    done = struct.get("updated_from_yahoo") or {}
    years = [str(y) for y in struct.get("years") or []]
    added = [y for y in done.get("years") or [] if y in years]
    if not added or not done.get("skipped"):
        return None
    ov = years.index(added[0]) - 1
    rev = _revenue_row(struct) or []
    cols = sorted(yh["income"].columns) if yh.get("income") is not None and not yh["income"].empty else []
    col_of = {}
    for c in cols:   # cada año (el de comprobación y los añadidos) con su columna de Yahoo, por las ventas
        i = next((i for i in [ov] + [years.index(y) for y in added] if i < len(rev) and _matches(REV(yh, c), rev[i], "revenue")), None)
        if i is not None:
            col_of[i] = c
    if ov < 0 or ov not in col_of:
        return None
    out, filled = {**struct}, []
    for section, names, f, kind in LINES:
        rows = {k.lower(): k for k in (struct.get(section) or {})}
        key = next((rows[n] for n in names if n in rows), None)
        if not key or key not in done["skipped"]:
            continue
        vals = list(struct[section][key])
        if ov < len(vals) and _matches(f(yh, col_of[ov]), vals[ov], kind):
            for y in added:
                i = years.index(y)
                if i < len(vals) and vals[i] is None and i in col_of:
                    vals[i] = f(yh, col_of[i])
            out[section] = {**out.get(section, {}), key: vals}
            filled.append(key)
    if not filled:
        return None
    out["updated_from_yahoo"] = {**done, "skipped": [k for k in done["skipped"] if k not in filled]}
    return out


def apply(struct: Dict[str, Any], p: Dict[str, Any]) -> Dict[str, Any]:
    """Las cuentas con los años nuevos: sin el LTM, cada línea con su valor (o vacía si no cuadró)."""
    years = [str(y) for y in struct.get("years") or []]
    keep = [i for i, y in enumerate(years) if y.upper() != "LTM"]
    n_new = len(p["new"])
    out = {**struct, "years": [years[i] for i in keep] + p["new"]}
    for section in ("income_statement", "balance_sheet", "cash_flow"):
        rows = struct.get(section) or {}
        out[section] = {k: [v[i] if i < len(v) else None for i in keep] + (p["values"].get((section, k)) or [None] * n_new)
                        for k, v in rows.items()}
    cm = struct.get("chart_metrics") or {}
    out["chart_metrics"] = {k: [v[i] if i < len(v) else None for i in keep] + (p["chart"].get(k) or [None] * n_new)
                            for k, v in cm.items()}
    out["updated_from_yahoo"] = {"years": p["new"], "skipped": p["skipped"], "at": datetime.date.today().isoformat()}
    return out


# --- Al pegar de nuevo de fiscal.ai: lo nuevo manda, lo antiguo que ya no trae se conserva ---------------------------

def merge(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Dict[str, Any]:
    """Las cuentas recién pegadas con los años antiguos que ya tenías y no vienen ahora (fiscal.ai da 10 años): en los
    años que trae, manda lo nuevo; el LTM, el nuevo. Si algún año no se entiende («2025», «Jan '26»), lo nuevo tal cual."""
    if not old or not old.get("years") or not new.get("years"):
        return new
    ny = [str(y) for y in new["years"]]
    oy = [str(y) for y in old["years"]]
    extra = [y for y in oy if y.upper() != "LTM" and y not in ny]
    closed = [y for y in ny if y.upper() != "LTM"]
    if not extra or any(_year_of(y) is None for y in extra + closed):
        return new
    years = sorted(extra + closed, key=lambda y: (_year_of(y), y)) + [y for y in ny if y.upper() == "LTM"]

    def section(o: Dict[str, Any], n: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        o_by = {k.lower(): k for k in o}
        for k, vals in n.items():   # las líneas de lo nuevo, en su orden; con los años antiguos de la misma línea
            ok = o_by.get(k.lower())
            out[k] = [vals[ny.index(y)] if y in ny and ny.index(y) < len(vals)
                      else (o[ok][oy.index(y)] if ok and y in oy and oy.index(y) < len(o[ok]) else None) for y in years]
        for k, vals in o.items():   # las que ya no vienen: se quedan con sus años antiguos
            if k.lower() not in {x.lower() for x in n}:
                out[k] = [vals[oy.index(y)] if y in oy and y not in ny and oy.index(y) < len(vals) else None for y in years]
        return out
    out = {**new, "years": years}
    for sec in ("income_statement", "balance_sheet", "cash_flow", "chart_metrics"):
        if isinstance(new.get(sec), dict) or isinstance(old.get(sec), dict):
            out[sec] = section(old.get(sec) or {}, new.get(sec) or {})
    return out


# --- Pasada: las empresas que sigues, como mucho una vez por semana ------------------------------------------------

def _state(db) -> Dict[str, Any]:
    row = db.get(DBSetting, SETTINGS_KEY)
    return dict(row.value) if row and isinstance(row.value, dict) else {"checked": {}, "events": []}


def _save_state(db, state: Dict[str, Any]) -> None:
    row = db.get(DBSetting, SETTINGS_KEY)
    if row:
        row.value = state
    else:
        db.add(DBSetting(key=SETTINGS_KEY, value=state))
    db.commit()


def followed(db) -> List[str]:
    """Cartera, Tier 1 y Tier 2, con cuentas pegadas a mano."""
    from app.application.services.tier_service import tier_of
    from app.infrastructure.db.models import DBPortfolioItem
    held = {p.ticker for p in db.query(DBPortfolioItem).all() if (p.shares or 0) > 0}
    out = []
    for a in db.query(DBAsset).all():
        d = a.data or {}
        if (d.get(STRUCT_KEY) or {}).get("years") and (a.ticker in held or tier_of(bool(a.is_favorite), d) in ("1", "2")):
            out.append(a.ticker)
    return sorted(out)


def yahoo_annual(ticker: str) -> Dict[str, Any]:
    import yfinance as yf
    t = yf.Ticker(ticker)
    return {"income": t.income_stmt, "balance": t.balance_sheet, "cash": t.cashflow}


def update_ticker(db, ticker: str, fetch: Callable[[str], Dict[str, Any]] = yahoo_annual) -> Dict[str, Any]:
    """Comprueba una empresa y, si cuadra, guarda sus años nuevos. Devuelve el plan (sin los valores)."""
    from sqlalchemy.orm.attributes import flag_modified
    a = db.get(DBAsset, ticker)
    struct = ((a.data or {}).get(STRUCT_KEY) if a else None) or {}
    if not struct.get("years"):
        return {"status": "sin cuentas"}
    yh = fetch(ticker)
    p = plan(struct, yh)
    if p["status"] == "up_to_date":
        fixed = refill(struct, yh)
        if fixed:
            a.data = {**(a.data or {}), STRUCT_KEY: fixed}
            flag_modified(a, "data")
            db.commit()
            return {"status": "refilled", "filled": [k for k in (struct.get("updated_from_yahoo") or {}).get("skipped", [])
                                                      if k not in fixed["updated_from_yahoo"]["skipped"]]}
    if p["status"] == "add":
        new = apply(struct, p)
        chart = dict(new["chart_metrics"])
        chart["years"] = new["years"]
        a.data = {**(a.data or {}), STRUCT_KEY: new, CHART_KEY: chart}
        flag_modified(a, "data")
        db.commit()
    return {k: v for k, v in p.items() if k not in ("values", "chart")}


def run(db, now: Optional[datetime.datetime] = None, fetch: Callable[[str], Dict[str, Any]] = yahoo_annual,
        pause_s: float = 1.0) -> Dict[str, Any]:
    """Una pasada: las que toca mirar (más de una semana desde la última vez). Guarda lo que pasó para el aviso; un
    mismo año que no cuadra solo se avisa una vez."""
    now = now or datetime.datetime.now()
    state = _state(db)
    checked, events = dict(state.get("checked") or {}), list(state.get("events") or [])
    known = {(e["ticker"], e.get("year")) for e in events if e["status"] == "mismatch"}
    cutoff = (now - datetime.timedelta(days=CHECK_EVERY_DAYS)).date().isoformat()
    added, mismatched = [], []
    for t in followed(db):
        if checked.get(t, "") > cutoff:
            continue
        try:
            r = update_ticker(db, t, fetch)
        except Exception:
            db.rollback()
            logger.exception("Cuentas anuales: no se pudo comprobar %s", t)
            continue
        checked[t] = now.date().isoformat()
        if r["status"] == "add":
            added.append({"ticker": t, "year": ", ".join(r["new"]), "skipped": r["skipped"], "status": "added"})
        elif r["status"] == "mismatch":
            last = str(((db.get(DBAsset, t).data or {}).get(STRUCT_KEY) or {}).get("years", [""])[-1])
            if (t, last) not in known:   # el mismo caso, una sola vez (hasta que pegues cuentas nuevas)
                mismatched.append({"ticker": t, "year": last, "reason": r.get("reason"), "status": "mismatch"})
        if pause_s:
            time.sleep(pause_s)
    day = now.date().isoformat()
    events += [{**e, "date": day} for e in added + mismatched]
    since = (now - datetime.timedelta(days=90)).date().isoformat()
    _save_state(db, {"checked": checked, "events": [e for e in events if e["date"] >= since], "last_run": now.isoformat(timespec="seconds")})
    if added or mismatched:
        logger.info("Cuentas anuales: %d con año nuevo, %d no cuadran", len(added), len(mismatched))
    return {"added": added, "mismatched": mismatched}


def notices(db, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """Un aviso por pasada con novedades (de los últimos días): a qué empresas se les añadió el año y cuáles no cuadran."""
    now = now or datetime.datetime.now()
    since = (now - datetime.timedelta(days=NOTICE_DAYS)).date().isoformat()
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for e in _state(db).get("events") or []:
        if e["date"] >= since:
            by_day.setdefault(e["date"], []).append(e)
    out = []
    for day, evs in sorted(by_day.items()):
        ok = [e for e in evs if e["status"] == "added"]
        bad = [e for e in evs if e["status"] == "mismatch"]
        parts = []
        if ok:
            parts.append(f"año nuevo en {len(ok)}: " + ", ".join(f"{e['ticker']} ({e['year']})" for e in ok))
        if bad:
            parts.append(f"no cuadran {len(bad)}, pégalas de fiscal.ai: " + ", ".join(e["ticker"] for e in bad))
        first = (bad or ok)[0]["ticker"]
        out.append({"key": f"finanzas|att|fin_update|{day}|", "ticker": None, "kind": "fin_update",
                    "text": "Cuentas anuales · " + " · ".join(parts), "href": f"/ticker/{first}#finanzas"})
    return out


# --- Segundo plano ---------------------------------------------------------------------------------------------------

def start_scheduler() -> None:
    """Al arrancar (tras unos minutos, para no frenar la app) y cada día."""
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(300)
        while True:
            if _lock.acquire(blocking=False):
                from app.infrastructure.db.database import SessionLocal
                db = SessionLocal()
                try:
                    run(db)
                except Exception:
                    logger.exception("Fallo al actualizar las cuentas anuales")
                finally:
                    db.close()
                    _lock.release()
            time.sleep(LOOP_S)
    threading.Thread(target=loop, daemon=True, name="fin-update").start()
