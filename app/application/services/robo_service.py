"""
Roboadvisor (MyInvestor) como una cesta: no se sabe cómo reparte cada aportación, así que se lleva el valor total.

- El usuario ajusta de vez en cuando con lo que dice MyInvestor (valor de mercado y capital invertido; o pega el resumen
  y de ahí salen también los pesos de cada fondo). Eso es la «foto» real.
- Entre fotos, el valor se estima: el de la última foto por lo que han hecho sus fondos desde entonces (liquidativo
  diario de Yahoo, ponderado por su peso; el efectivo no se mueve) más cada aportación del plan desde su fecha.
- Aportación mensual (importe y día) con historial por fecha: cambiarla no reescribe los meses anteriores.
- Aviso el día 10 de cada mes («actualiza el roboadvisor») hasta que hay una foto posterior a la aportación del mes.
- Es información aparte: suma al patrimonio del Resumen, no a las cifras de la cartera de acciones.
- Historia: las aportaciones registradas (reales; las anteriores a nov-2025, estimadas a partir de la tabla de
  rentabilidades de MyInvestor) y esa tabla (ganancia y % de cada mes, total e índice de cada año). Desde la última
  aportación registrada, las del plan. Valor de cada fin de mes = invertido + ganancia acumulada.
Se guarda en settings['robo']: {funds: [{isin, name, yahoo, currency, keys, weight}], plan: [{since 'AAAA-MM', amount, day}],
snapshots: [{date, value, invested, cash, est, returns}], contributions: [{date, amount, source real|estimado}],
monthly: {AAAA-MM: {gain, pct}}, annual: {AAAA: {gain, pct, index}}}.
"""
import datetime
import re
import time
from typing import Any, Dict, List, Optional

SETTINGS_KEY = "robo"
ADJUST_DAY = 10          # día del mes en que avisa de actualizar (la aportación ya está invertida)
NAV_TTL = 6 * 3600       # caché de los liquidativos

# Los fondos de la cartera de MyInvestor; keys: palabras para reconocerlos en el resumen pegado (y las que lo excluyen)
FUNDS = [
    {"isin": "IE000N4ZYX28", "name": "iShares US Index S", "yahoo": "0P0001XF42.F", "currency": "EUR", "keys": ["us equity", "us index"], "not": ["vanguard"]},
    {"isin": "IE000N51F726", "name": "iShares Developed World D", "yahoo": "0P0001XF41.F", "currency": "EUR", "keys": ["developed world"]},
    {"isin": "IE000QAZP7L2", "name": "iShares Emerging Markets S", "yahoo": "0P0001XF3Z.F", "currency": "EUR", "keys": ["emerging"]},
    {"isin": "IE00BYX5N771", "name": "Fidelity MSCI Japan P", "yahoo": "0P0001CLDI.F", "currency": "EUR", "keys": ["japan"], "not": ["ex-japan", "ex japan"]},
    {"isin": "IE00BYX5MD61", "name": "Fidelity MSCI Europe P", "yahoo": "0P0001CJGN.F", "currency": "EUR", "keys": ["europe"]},
    {"isin": "IE00B1G3DH73", "name": "Vanguard US 500 € Hedged", "yahoo": "0P00006TV8.F", "currency": "EUR", "keys": ["vanguard"]},
    {"isin": "IE00BDZVHT63", "name": "Fidelity MSCI Pacific ex-Japan P", "yahoo": "0P0001COSJ", "currency": "USD", "keys": ["pacific"]},
]


# Exposición aproximada por zonas de cada fondo (el MSCI World: ~72 % EE. UU., ~15 % Europa, ~6 % Japón, ~7 % resto)
ZONES = ["EE. UU.", "Europa", "Japón", "Emergentes", "Pacífico y otros"]
FUND_ZONES = {
    "IE000N4ZYX28": {"EE. UU.": 100},
    "IE000N51F726": {"EE. UU.": 72, "Europa": 15, "Japón": 6, "Pacífico y otros": 7},
    "IE000QAZP7L2": {"Emergentes": 100},
    "IE00BYX5N771": {"Japón": 100},
    "IE00BYX5MD61": {"Europa": 100},
    "IE00B1G3DH73": {"EE. UU.": 100},
    "IE00BDZVHT63": {"Pacífico y otros": 100},
}
INDEX_PROXY = "IWDA.AS"   # MSCI World en euros (iShares Core MSCI World, Ámsterdam): la curva del índice
INDEX_TTL = 12 * 3600
DAYS_ES = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


# --- Guardado ---------------------------------------------------------------------------------------------------------

def load(db) -> Dict[str, Any]:
    from app.infrastructure.db.models import DBSetting
    row = db.get(DBSetting, SETTINGS_KEY)
    data = dict(row.value) if row and isinstance(row.value, dict) else {}
    data.setdefault("funds", [dict(f, weight=None) for f in FUNDS])
    data.setdefault("plan", [])
    data.setdefault("snapshots", [])
    data.setdefault("contributions", [])
    data.setdefault("monthly", {})
    data.setdefault("annual", {})
    return data


def save(db, data: Dict[str, Any]) -> None:
    from sqlalchemy.orm.attributes import flag_modified
    from app.infrastructure.db.models import DBSetting
    row = db.get(DBSetting, SETTINGS_KEY)
    if row:
        row.value = data
        flag_modified(row, "value")
    else:
        db.add(DBSetting(key=SETTINGS_KEY, value=data))
    db.commit()


def set_plan(db, amount: float, day: int = 3, since: Optional[str] = None) -> Dict[str, Any]:
    """La aportación mensual desde el mes `since` (AAAA-MM; este mes por defecto). Los meses anteriores no cambian."""
    if amount < 0 or not 1 <= int(day) <= 28:
        raise ValueError("Importe ≥ 0 y día entre 1 y 28")
    data = load(db)
    since = (since or datetime.date.today().strftime("%Y-%m"))[:7]
    data["plan"] = sorted([p for p in data["plan"] if p["since"] != since] + [{"since": since, "amount": round(float(amount), 2), "day": int(day)}],
                          key=lambda p: p["since"])
    save(db, data)
    return data


def add_snapshot(db, value: float, invested: float, cash: Optional[float] = None, date: Optional[str] = None,
                 fund_values: Optional[Dict[str, float]] = None, returns: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Foto real con lo que dice MyInvestor. Con el valor de cada fondo, se rehacen los pesos de la cesta.
    Guarda también lo que se estimaba ese día, para saber cuánto se desviaba la estimación."""
    if not value or value <= 0 or invested is None or invested <= 0:
        raise ValueError("Valor de mercado y capital invertido tienen que ser mayores que 0")
    data = load(db)
    date = (date or datetime.date.today().isoformat())[:10]
    before = estimate(data, datetime.date.fromisoformat(date)) if data["snapshots"] else None
    if fund_values:
        total = float(value)
        for f in data["funds"]:
            if f["isin"] in fund_values:
                f["weight"] = round(fund_values[f["isin"]] / total, 6)
    snap = {"date": date, "value": round(float(value), 2), "invested": round(float(invested), 2),
            "cash": round(float(cash), 2) if cash is not None else None,
            "est": round(before["value"], 2) if before else None}
    if returns:
        snap["returns"] = returns
    data["snapshots"] = sorted([s for s in data["snapshots"] if s["date"] != date] + [snap], key=lambda s: s["date"])
    save(db, data)
    return snap


def delete_snapshot(db, date: str) -> bool:
    data = load(db)
    before = len(data["snapshots"])
    data["snapshots"] = [s for s in data["snapshots"] if s["date"] != date]
    save(db, data)
    return len(data["snapshots"]) < before


# --- Resumen pegado de MyInvestor -------------------------------------------------------------------------------------

def _num(s: str) -> float:
    return float(s.replace(".", "").replace(",", ".").replace("+", "").strip())


def parse_summary(text: str) -> Dict[str, Any]:
    """Saca del resumen pegado: valor de mercado, capital invertido, efectivo, el valor de cada fondo y la rentabilidad
    de cada año. Lo que no encuentra, no lo devuelve."""
    out: Dict[str, Any] = {}
    t = " ".join((text or "").split())
    for key, label in (("value", r"valor de mercado"), ("invested", r"capital invertido"), ("cash", r"efectivo")):
        m = re.search(label + r"\s*:?\s*([\d.]+,\d{2})\s*€", t, re.I)
        if m:
            out[key] = _num(m.group(1))
    years = {y: _num(p) for y, p in re.findall(r"total\s+(\d{4})\s*:?\s*[+-]?[\d.]+,\d{2}\s*€\s*\(\s*([+-]?[\d.]+,\d{1,2})\s*%\s*\)", t, re.I)}
    if years:
        out["returns"] = years
    funds: Dict[str, float] = {}
    prev = 0
    for m in re.finditer(r"valor\s*:\s*([\d.]+,\d{2})\s*€", t, re.I):
        name = t[prev:m.start()].lower()[-120:]   # el nombre del fondo va justo antes de su «Valor:»
        prev = m.end()
        for f in FUNDS:
            if f["isin"] not in funds and any(k in name for k in f["keys"]) and not any(k in name for k in f.get("not", [])):
                funds[f["isin"]] = _num(m.group(1))
                break
    if funds:
        out["funds"] = funds
    return out


# --- Liquidativos -----------------------------------------------------------------------------------------------------

_nav_cache: Dict[str, Any] = {"at": 0.0, "since": None, "series": {}}


def _nav_series(funds: List[Dict[str, Any]], since: datetime.date) -> Dict[str, Any]:
    """Liquidativo diario en euros de cada fondo desde `since` ({yahoo: pandas.Series}). Sin internet: {}."""
    if (_nav_cache["series"] and time.time() - _nav_cache["at"] < NAV_TTL and _nav_cache["since"] and _nav_cache["since"] <= since):
        return _nav_cache["series"]
    import concurrent.futures
    import yfinance as yf
    start = (since - datetime.timedelta(days=10)).isoformat()

    def one(sym: str):
        try:
            s = yf.Ticker(sym).history(start=start)["Close"]
            s.index = s.index.tz_localize(None).normalize()
            return sym, s
        except Exception:
            return sym, None

    syms = [f["yahoo"] for f in funds] + (["USDEUR=X"] if any(f["currency"] == "USD" for f in funds) else [])
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        got = dict(ex.map(one, syms))
    usd = got.pop("USDEUR=X", None)
    series = {}
    for f in funds:
        s = got.get(f["yahoo"])
        if s is None or s.empty:
            continue
        if f["currency"] == "USD":
            if usd is None or usd.empty:
                continue
            s = s * usd.reindex(s.index, method="ffill")
        series[f["yahoo"]] = s.dropna()
    if series:
        _nav_cache.update(at=time.time(), since=since, series=series)
    return series


def _nav_on(s, day: datetime.date) -> Optional[float]:
    """El último liquidativo publicado hasta ese día."""
    import pandas as pd
    s = s[s.index <= pd.Timestamp(day)]
    return float(s.iloc[-1]) if len(s) else None


# --- Cálculo ----------------------------------------------------------------------------------------------------------

def plan_on(plan: List[Dict[str, Any]], month: str) -> Optional[Dict[str, Any]]:
    """La aportación vigente en ese mes (AAAA-MM)."""
    valid = [p for p in plan if p["since"] <= month]
    return valid[-1] if valid else None


def contributions(plan: List[Dict[str, Any]], after: datetime.date, until: datetime.date) -> List[Dict[str, Any]]:
    """Las aportaciones del plan con fecha en (after, until]."""
    out = []
    y, m = after.year, after.month
    while (y, m) <= (until.year, until.month):
        p = plan_on(plan, f"{y:04d}-{m:02d}")
        if p and p["amount"] > 0:
            d = datetime.date(y, m, p["day"])
            if after < d <= until:
                out.append({"date": d.isoformat(), "amount": p["amount"]})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def all_contributions(data: Dict[str, Any], until: datetime.date) -> List[Dict[str, Any]]:
    """Las registradas y, desde la última, las del plan (source «plan»), hasta `until`."""
    rec = sorted((c for c in data.get("contributions", []) if c["date"] <= until.isoformat()), key=lambda c: c["date"])
    last = datetime.date.fromisoformat(rec[-1]["date"]) if rec else None
    if last is None and data["snapshots"]:
        last = datetime.date.fromisoformat(data["snapshots"][0]["date"])
    planned = [dict(c, source="plan") for c in contributions(data["plan"], last, until)] if last else []
    return rec + planned


def estimate(data: Dict[str, Any], today: Optional[datetime.date] = None, navs: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Valor de hoy desde la última foto: lo que han hecho sus fondos y las aportaciones posteriores."""
    today = today or datetime.date.today()
    snaps = [s for s in data["snapshots"] if s["date"] <= today.isoformat()]
    if not snaps:
        return None
    snap = snaps[-1]
    d0 = datetime.date.fromisoformat(snap["date"])
    funds = [f for f in data["funds"] if f.get("weight")]
    if navs is None:
        navs = _nav_series(funds, d0) if funds and today > d0 else {}
    cash_w = max(0.0, 1.0 - sum(f["weight"] for f in funds))

    def growth(frm: datetime.date) -> Optional[float]:
        """Lo que ha hecho la cesta desde `frm` hasta hoy (1 = nada). None si falta algún fondo."""
        g = cash_w
        for f in funds:
            s = navs.get(f["yahoo"])
            a, b = (_nav_on(s, frm), _nav_on(s, today)) if s is not None else (None, None)
            if not a or not b:
                return None
            g += f["weight"] * b / a
        return g

    adds = [c for c in all_contributions(data, today) if c["date"] > snap["date"]]
    g0 = growth(d0) if today > d0 else 1.0
    complete = g0 is not None
    value = snap["value"] * (g0 or 1.0)
    for c in adds:
        gc = growth(datetime.date.fromisoformat(c["date"]))
        complete = complete and gc is not None
        value += c["amount"] * (gc or 1.0)
    invested = snap["invested"] + sum(c["amount"] for c in adds)
    last_nav = min((s.index[-1].date() for s in navs.values() if len(s)), default=None)   # el día que tienen todos
    rows = []
    for f in data["funds"]:
        s = navs.get(f["yahoo"])
        a, b = (_nav_on(s, d0), _nav_on(s, today)) if s is not None else (None, None)
        rows.append({"isin": f["isin"], "name": f["name"], "weight": f.get("weight"),
                     "chg_pct": 100 * (b / a - 1) if a and b else None})
    return {"value": value, "invested": invested, "gain": value - invested,
            "pct": 100 * (value / invested - 1) if invested else None,
            "estimated": today > d0 and (bool(adds) or (g0 is not None and abs(g0 - 1) > 1e-9)),
            "complete": complete, "nav_date": last_nav.isoformat() if last_nav else None,
            "snapshot": snap, "since_snapshot": adds, "funds": rows}


def _month_end(month: str) -> datetime.date:
    y, m = int(month[:4]), int(month[5:])
    return (datetime.date(y + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1))


def series(data: Dict[str, Any], est: Dict[str, Any], today: datetime.date) -> List[Dict[str, Any]]:
    """Mes a mes desde la primera aportación: aportado, invertido, ganancia, % y valor a fin de mes.
    Los meses de la tabla de MyInvestor usan su ganancia; los demás, el valor estimado a fin de mes (el actual, el de hoy)."""
    contribs = all_contributions(data, today)
    if not contribs:
        return []
    out, invested, value_prev, cum_gain = [], 0.0, 0.0, 0.0
    month, last = contribs[0]["date"][:7], today.strftime("%Y-%m")
    while month <= last:
        mc = [c for c in contribs if c["date"][:7] == month]
        added = sum(c["amount"] for c in mc)
        invested += added
        row = data["monthly"].get(month)
        if row:
            gain, pct = row["gain"], row.get("pct")
        else:
            end = min(_month_end(month), today)
            e = est if end == today else estimate(data, end)
            gain = (e["value"] - value_prev - added) if e else 0.0
            base = value_prev + added
            pct = 100 * gain / base if e and base else None
        cum_gain += gain
        value = invested + cum_gain
        out.append({"month": month, "added": added, "est_added": sum(c["amount"] for c in mc if c.get("source") == "estimado"), "source": "estimado" if any(c.get("source") == "estimado" for c in mc) else
                    ("plan" if mc and all(c.get("source") == "plan" for c in mc) else "real"),
                    "invested": round(invested, 2), "gain": round(gain, 2), "pct": pct, "value": round(value, 2),
                    "from_table": bool(row)})
        value_prev = value
        y, m = int(month[:4]), int(month[5:])
        month = f"{y + (m == 12):04d}-{m % 12 + 1:02d}"
    return out


def year_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Rentabilidad por año y mes, como la tabla de MyInvestor (años en columnas, de más reciente a más antiguo)."""
    years = sorted({r["month"][:4] for r in rows}, reverse=True)
    cells = {r["month"]: r for r in rows}
    out = []
    for y in years:
        ann = data["annual"].get(y) or {}
        ms = [cells.get(f"{y}-{m:02d}") for m in range(1, 13)]
        live = any(r and not r["from_table"] for r in ms)   # año con meses que no están en la tabla: se suma en vivo
        gain = sum(r["gain"] for r in ms if r) if live or "gain" not in ann else ann["gain"]
        pct = ann.get("pct")
        if live:   # el % del año, encadenando los meses (aproximado: MyInvestor lo calcula a su manera)
            prod = 1.0
            for r in ms:
                prod *= 1 + (r["pct"] or 0) / 100 if r else 1
            pct = 100 * (prod - 1)
        out.append({"year": y, "gain": gain, "pct": pct, "live": live, "index": ann.get("index"),
                    "months": [{"gain": r["gain"], "pct": r["pct"], "from_table": r["from_table"]} if r else None for r in ms]})
    return {"years": out}


def status(db, today: Optional[datetime.date] = None) -> Dict[str, Any]:
    data = load(db)
    today = today or datetime.date.today()
    est = estimate(data, today)
    if not est:
        return {"configured": False, "plan": plan_on(data["plan"], today.strftime("%Y-%m"))}
    snaps = data["snapshots"]
    history = [{"date": s["date"], "value": s["value"], "est": s.get("est"),
                "dev_pct": 100 * (s["value"] / s["est"] - 1) if s.get("est") else None} for s in reversed(snaps)][:6]
    returns = next((s["returns"] for s in reversed(snaps) if s.get("returns")), None)
    from utils.financial_math import xirr
    rows = series(data, est, today)
    contribs = all_contributions(data, today)
    irr = xirr([(c["date"], -c["amount"]) for c in contribs] + [(today, est["value"])]) if contribs else None
    return {"configured": True, **est, "plan": plan_on(data["plan"], today.strftime("%Y-%m")), "plans": data["plan"],
            "history": history, "returns": returns, "adjust_due": adjust_due(data, today),
            "series": rows, "table": year_table(data, rows), "irr": irr,
            "contributions": list(reversed(contribs)), "annual": data["annual"],
            "first": contribs[0]["date"] if contribs else None}


def adjust_due(data: Dict[str, Any], today: datetime.date) -> bool:
    """Desde el día 10, si no hay foto posterior a la aportación de este mes."""
    p = plan_on(data["plan"], today.strftime("%Y-%m"))
    if not p or not p["amount"] or today.day < ADJUST_DAY or not data["snapshots"]:
        return False
    return data["snapshots"][-1]["date"] < datetime.date(today.year, today.month, p["day"]).isoformat()


def notices(db, today: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
    """Aviso del mes: «actualiza el roboadvisor» desde el día 10 hasta que se apunta lo que dice MyInvestor."""
    today = today or datetime.date.today()
    if not adjust_due(load(db), today):
        return []
    return [{"key": f"roboadvisor|att|robo_adjust|{today.strftime('%Y-%m')}|", "ticker": None, "kind": "robo_adjust",
             "text": "Actualiza el roboadvisor con lo que dice MyInvestor (valor e invertido) para corregir la estimación",
             "href": "/roboadvisor#ajustar"}]


# --- Gráficas de la pantalla ------------------------------------------------------------------------------------------

_index_cache: Dict[str, Any] = {"at": 0.0, "closes": {}}


def _index_monthly() -> Dict[str, float]:
    """Cierre de cada mes del MSCI World en euros ({AAAA-MM: cierre}; el mes en curso, el último). Sin internet: {}."""
    if _index_cache["closes"] and time.time() - _index_cache["at"] < INDEX_TTL:
        return _index_cache["closes"]
    try:
        import yfinance as yf
        h = yf.Ticker(INDEX_PROXY).history(start="2022-12-01", interval="1mo")["Close"].dropna()
        closes = {d.strftime("%Y-%m"): float(v) for d, v in h.items()}
    except Exception:
        closes = {}
    if closes:
        _index_cache.update(at=time.time(), closes=closes)
    return closes


def compare(rows: List[Dict[str, Any]], annual: Optional[Dict[str, Any]] = None, perf_dates: Optional[List[str]] = None,
            perf_uv: Optional[List[float]] = None) -> Dict[str, Any]:
    """Curvas en % sin el efecto de las aportaciones, a fin de cada mes (la pantalla las rebasa al inicio del tramo):
    el robo (encadenando el % de cada mes, desde 100 el mes anterior al primero), el MSCI World en euros y la cartera de
    acciones (su valor por participación del Rendimiento, desde que empieza)."""
    if not rows:
        return {"months": [], "robo": [], "index": [], "port": []}
    y, m = int(rows[0]["month"][:4]), int(rows[0]["month"][5:])
    months = [f"{y - (m == 1):04d}-{(m - 2) % 12 + 1:02d}"] + [r["month"] for r in rows]
    # cada año completo de la tabla se calibra para que acabe en el % anual de MyInvestor (sus meses no encadenan exacto)
    import math
    factor = {}
    for y_ in {r["month"][:4] for r in rows}:
        ms = [r for r in rows if r["month"][:4] == y_ and r["from_table"]]   # el % anual cubre los meses de su tabla
        target = ((annual or {}).get(y_) or {}).get("pct")
        logs = sum(math.log(1 + (r["pct"] or 0) / 100) for r in ms)
        if target is not None and logs:
            factor[y_] = math.log(1 + target / 100) / logs
    robo, idx = [100.0], 100.0
    for r in rows:
        idx *= math.exp(math.log(1 + (r["pct"] or 0) / 100) * (factor.get(r["month"][:4], 1.0) if r["from_table"] else 1.0))
        robo.append(round(idx, 3))
    closes = _index_monthly()
    index = [closes.get(mo) for mo in months]
    port: List[Optional[float]] = [None] * len(months)
    if perf_dates and perf_uv:
        for i, mo in enumerate(months):
            end = _month_end(mo).isoformat()
            vals = [v for d, v in zip(perf_dates, perf_uv) if d <= end and v is not None]
            if vals and perf_dates[0] <= end:
                port[i] = vals[-1]
    return {"months": months, "robo": robo, "index": index, "port": port}


def fund_details(data: Dict[str, Any], value: float, today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """Cada fondo: peso, valor aproximado en €, variación del liquidativo (1M, 3M, YTD, 1A) y su curva de un año
    (semanal); y la exposición de la cesta por zonas."""
    today = today or datetime.date.today()
    funds = [f for f in data["funds"] if f.get("weight")]
    navs = _nav_series(funds, today - datetime.timedelta(days=5 * 366)) if funds else {}   # 5 años para la ficha
    out = []
    for f in sorted(funds, key=lambda f: -f["weight"]):
        s = navs.get(f["yahoo"])
        last = _nav_on(s, today) if s is not None else None

        def chg(since: datetime.date) -> Optional[float]:
            a = _nav_on(s, since) if s is not None else None
            return 100 * (last / a - 1) if a and last else None

        spark, hist, hi, lo, nav_date = [], {"d": [], "v": []}, None, None, None
        if s is not None and len(s):
            import pandas as pd
            year = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
            spark = [round(float(v), 4) for v in year.resample("W").last().dropna().values]
            hist = {"d": [d.strftime("%Y-%m-%d") for d in s.index], "v": [round(float(v), 4) for v in s.values]}
            hi, lo, nav_date = float(year.max()), float(year.min()), s.index[-1].strftime("%Y-%m-%d")
        out.append({"isin": f["isin"], "name": f["name"], "currency": f["currency"], "yahoo": f["yahoo"], "weight": f["weight"],
                    "value": value * f["weight"], "nav": last, "nav_date": nav_date,
                    "chg": {"1S": chg(today - datetime.timedelta(days=7)), "1M": chg(today - datetime.timedelta(days=30)),
                            "3M": chg(today - datetime.timedelta(days=91)),
                            "YTD": chg(datetime.date(today.year - 1, 12, 31)), "1A": chg(today - datetime.timedelta(days=365)),
                            "3A": chg(today - datetime.timedelta(days=3 * 365)), "5A": chg(today - datetime.timedelta(days=5 * 365))},
                    "since": s.index[0].strftime("%Y-%m-%d") if s is not None and len(s) else None,
                    "hi52": hi, "lo52": lo, "from_hi": 100 * (last / hi - 1) if last and hi else None,
                    "spark": spark, "hist": hist, "zones": FUND_ZONES.get(f["isin"], {})})
    total_w = sum(f["weight"] for f in funds) or 1
    zones = {z: 0.0 for z in ZONES}
    for f in funds:
        for z, pct in FUND_ZONES.get(f["isin"], {}).items():
            zones[z] += f["weight"] / total_w * pct
    return {"funds": out, "zones": [{"zone": z, "pct": round(v, 1)} for z, v in zones.items() if v > 0]}


def periods(data: Dict[str, Any], rows: List[Dict[str, Any]], value: float,
            today: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
    """La banda del Resumen: lo ganado (€ y %) el último día, este mes, 3 meses, este año y 12 meses.
    El último día: el último liquidativo que tienen todos los fondos frente al anterior, ponderado por su peso (estimado);
    `stale` si ese día no es la última sesión. El resto, de la serie mensual (el % encadenando los meses)."""
    import pandas as pd
    today = today or datetime.date.today()
    out = []
    funds = [f for f in data["funds"] if f.get("weight")]
    navs = _nav_series(funds, today - datetime.timedelta(days=10)) if funds else {}
    series_ = [navs.get(f["yahoo"]) for f in funds]
    if funds and all(s is not None and len(s) for s in series_):
        lasts = [s.index[-1].date() for s in series_]
        day, newest = min(lasts), max(lasts)   # el mismo día para todos: no mezclar el viernes de uno con el lunes de otro
        g = 0.0
        for f, s in zip(funds, series_):
            s = s[s.index <= pd.Timestamp(day)]
            if len(s) < 2:
                g = None
                break
            g += f["weight"] * (float(s.iloc[-1]) / float(s.iloc[-2]) - 1)
        if g is not None:
            expected = today - datetime.timedelta(days=1)
            while expected.weekday() >= 5:
                expected -= datetime.timedelta(days=1)
            behind = [f"{f['name']} ({d.strftime('%d/%m')})" for f, d in zip(funds, lasts) if d < newest]
            note = f"Liquidativo del {day.strftime('%d/%m')}"
            if day < expected:
                note += f" · aún sin el del {expected.strftime('%d/%m')}"
            if behind:
                note += " · van por detrás: " + ", ".join(behind)
            out.append({"key": "1d", "label": f"{DAYS_ES[day.weekday()]} {day.strftime('%d/%m')}", "gain": value * g / (1 + g),
                        "pct": 100 * g, "note": note, "stale": day < expected})

    def span(rs: List[Dict[str, Any]], key: str, label: str, note: str = "") -> None:
        if not rs:
            return
        prod = 1.0
        for r in rs:
            prod *= 1 + (r["pct"] or 0) / 100
        out.append({"key": key, "label": label, "gain": sum(r["gain"] for r in rs), "pct": 100 * (prod - 1), "note": note})

    month = today.strftime("%Y-%m")
    span([r for r in rows if r["month"] == month], "1m", "Este mes")
    span(rows[-3:], "3m", "3 meses")
    span([r for r in rows if r["month"][:4] == str(today.year)], "ytd", "Este año")
    span(rows[-12:], "1y", "12 meses")
    return out
