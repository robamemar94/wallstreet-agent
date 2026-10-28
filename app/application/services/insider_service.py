"""
Directivos y acciones en circulación (sin IA, datos de yfinance cacheados 24 h).

- Directivos: solo las compras y ventas en bolsa («Purchase/Sale at price», «Acquisition/Disposition in the public
  market»). Lo demás (acciones que se dan como sueldo, regalos, ejercicio de opciones…) es ruido y se descarta.
  Yahoo solo lo da para EE. UU. y Canadá; en Europa y México no hay datos.
- Acciones en circulación: cambio anual en los últimos 3 años (recompra si bajan, dilución si suben). Sale para todas.
- Señal (aviso en la campana y el Centro de mando, cartera y watchlist): en los últimos 30 días compran 2 o más
  directivos, o uno solo por más de 100.000. Las ventas no avisan: suelen ser planificadas o por impuestos.
- Lectura (`analyze`, la línea y la ventana de la ficha): lo de los últimos 3 meses manda (si no hay nada, se dice y se
  resume el año), con el precio medio frente al de hoy, qué parte de la empresa es, qué parte de lo suyo vende cada
  directivo (participaciones de `insider_roster_holders`) y si sus ventas son periódicas (un plan programado: mismo
  tamaño o mismo intervalo, 3 o más). Conclusión: Compran · Ventas a vigilar (alguien vende el 25 % o más de lo suyo
  fuera de un plan) · Ventas rutinarias (sobre todo periódicas o pequeñas para quien vende) · Venden · Quietos.
"""
import datetime
import logging
import re
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional

from app.application.services import market_cache

logger = logging.getLogger(__name__)

CACHE_TTL_H = 24
TRADES_MONTHS = 12          # operaciones a la vista en la ficha
SUMMARY_DAYS = 182          # compras de los últimos 6 meses (aparte del saldo de 12 meses)
SIGNAL_DAYS = 30            # ventana del aviso
SIGNAL_MIN_INSIDERS = 2     # aviso: al menos 2 directivos distintos comprando…
SIGNAL_MIN_VALUE = 100_000  # …o una compra de más de 100.000 (en la moneda del registro: USD o CAD)
SHARES_YEARS = 5            # historial de acciones en circulación: cambio a 1, 3 y 5 años (la etiqueta, con 3)
SHARE_WINDOWS = (1, 3, 5)
STABLE_PCT = 0.5            # menos de ±0,5 %/año: estable
MAX_PLAUSIBLE_PCT = 25      # más de ±25 %/año: dato roto (p. ej. un split que Yahoo no ha aplicado), no se muestra
TRADES_KEY = "insider_trades_v2"   # v2: con las acciones recibidas como sueldo («award»)
RECENT_DAYS = 90            # la línea de la ficha: lo de los últimos 3 meses
STAKE_WATCH_PCT = 25        # «a vigilar»: un directivo vende esta parte de lo suyo (o más) fuera de un plan
PLAN_MIN_SALES = 3          # ventas periódicas: al menos 3 del mismo directivo…
PLAN_MAX_CV = 0.35          # …de tamaño parecido (coef. de variación de las acciones) o a intervalos regulares
COMP_RATIO = 1.25           # vende lo que cobra en acciones: lo vendido no pasa de 1,25 veces lo recibido en el año

_BUY = re.compile(r"^(purchase at price|acquisition in the public market)", re.I)
_SELL = re.compile(r"^(sale at price|disposition in the public market)", re.I)
# acciones que recibe sin comprarlas en bolsa: sueldo en acciones, ejercicio de opciones (las filas sin texto ni importe
# de Yahoo son, en la práctica, entregas de acciones que se liberan)
_AWARD = re.compile(r"^(stock award|conversion of exercise|acquisition \(non open market\))", re.I)
_PRICE = re.compile(r"at price ([\d.,]+)(?:\s*-\s*([\d.,]+))?", re.I)


def classify(text: str, transaction: str = "") -> Optional[str]:
    """'buy' / 'sell' si es una compra o venta en bolsa; None para el resto."""
    t = (text or "").strip()
    if _BUY.match(t) or (not t and (transaction or "").lower() == "purchase"):
        return "buy"
    if _SELL.match(t) or (not t and (transaction or "").lower() == "sale"):
        return "sell"
    return None


def _price(text: str) -> Optional[float]:
    m = _PRICE.search(text or "")
    if not m:
        return None
    try:
        lo = float(m.group(1).replace(",", ""))
        hi = float(m.group(2).replace(",", "")) if m.group(2) else lo
        return (lo + hi) / 2
    except ValueError:
        return None


def _fetch_trades(ticker: str) -> Optional[List[Dict[str, Any]]]:
    """Operaciones en bolsa de los directivos (None: Yahoo no tiene datos de esta empresa)."""
    import yfinance as yf
    try:
        df = yf.Ticker(ticker).insider_transactions
    except Exception as e:
        logger.warning("Directivos de %s: %s", ticker, e)
        return None
    if df is None or df.empty:
        return None
    out = []
    for r in df.to_dict("records"):
        text = str(r.get("Text") or "").strip()
        kind = classify(text, str(r.get("Transaction") or ""))
        value = r.get("Value")
        if not kind and (_AWARD.match(text) or (not text and not (value and value == value) and (r.get("Shares") or 0) > 0)):
            kind = "award"   # no cuenta como compra: solo para saber si lo que vende es lo que cobra en acciones
        date = r.get("Start Date")
        if not kind or date is None:
            continue
        out.append({"date": str(date)[:10], "insider": str(r.get("Insider") or "").title(), "position": str(r.get("Position") or ""),
                    "kind": kind, "shares": float(r.get("Shares") or 0),
                    "value": float(value) if value and value == value else None, "price": _price(str(r.get("Text") or "")),
                    "own": str(r.get("Ownership") or "")})
    return out


def _fetch_shares(ticker: str) -> Optional[List[List[Any]]]:
    """Acciones en circulación, una cifra por mes (la mediana: la serie de Yahoo trae saltos sueltos)."""
    import yfinance as yf
    try:
        start = (datetime.date.today() - datetime.timedelta(days=365 * SHARES_YEARS + 60)).isoformat()
        s = yf.Ticker(ticker).get_shares_full(start=start)
    except Exception as e:
        logger.warning("Acciones en circulación de %s: %s", ticker, e)
        return None
    if s is None or len(s) == 0:
        return None
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.index = s.index.tz_localize(None) if getattr(s.index, "tz", None) is not None else s.index
    monthly = s.resample("ME").median().dropna()
    return [[d.date().isoformat(), float(v)] for d, v in monthly.items()]


def trades(ticker: str) -> Optional[List[Dict[str, Any]]]:
    return market_cache.cached(TRADES_KEY, ticker, lambda: _fetch_trades(ticker), ttl_hours=CACHE_TTL_H)


def shares_series(ticker: str) -> Optional[List[List[Any]]]:
    return market_cache.cached("shares_history_v2", ticker, lambda: _fetch_shares(ticker), ttl_hours=CACHE_TTL_H)


def _fetch_splits(ticker: str) -> List[List[Any]]:
    """Todos los splits (los de split_service solo miran 3 años y aquí hacen falta 5)."""
    import yfinance as yf
    try:
        sp = yf.Ticker(ticker).splits
        return [[d.date().isoformat(), float(r)] for d, r in sp.items() if r and r != 1] if sp is not None else []
    except Exception:
        return []


def splits_all(ticker: str) -> List[tuple]:
    return [tuple(x) for x in market_cache.cached("splits_all_v1", ticker, lambda: _fetch_splits(ticker), ttl_hours=CACHE_TTL_H) or []]


def split_adjusted(series: Optional[List[List[Any]]], splits) -> List[List[Any]]:
    """Las acciones de antes de cada split, en acciones de hoy (si no, un split 10×1 parece una dilución enorme)."""
    from app.application.services.split_service import factor_since
    return [[d, v * factor_since(splits or [], d)] for d, v in series or []]


def share_change(series: Optional[List[List[Any]]]) -> Optional[Dict[str, Any]]:
    """Cambio anual de las acciones (ya ajustadas por splits): de la mediana de los 3 primeros meses a la de los 3 últimos."""
    if not series or len(series) < 12:
        return None
    first = sorted(v for _, v in series[:3])[1] if len(series) >= 3 else series[0][1]
    last = sorted(v for _, v in series[-3:])[1]
    years = (datetime.date.fromisoformat(series[-2][0]) - datetime.date.fromisoformat(series[1][0])).days / 365.25
    if not first or years < 0.9:
        return None
    pct = 100 * ((last / first) ** (1 / years) - 1)
    if abs(pct) > MAX_PLAUSIBLE_PCT:
        return None
    label = "estable" if abs(pct) < STABLE_PCT else "recompra" if pct < 0 else "diluye"
    return {"pct": pct, "years": round(years, 1), "label": label, "first": first, "last": last}


def share_windows(series: Optional[List[List[Any]]], today: Optional[datetime.date] = None) -> Dict[int, Optional[Dict[str, float]]]:
    """Cambio de las acciones (ajustadas por splits) a 1, 3 y 5 años: total y anual. None si el historial no llega
    o el dato no es creíble. Cada extremo, la mediana de 3 meses (la serie de Yahoo trae saltos sueltos)."""
    out: Dict[int, Optional[Dict[str, float]]] = {n: None for n in SHARE_WINDOWS}
    if not series or len(series) < 6:
        return out
    today = today or datetime.date.fromisoformat(series[-1][0])
    dates = [datetime.date.fromisoformat(d) for d, _ in series]
    last = sorted(v for _, v in series[-3:])[1]
    for n in SHARE_WINDOWS:
        target = today - datetime.timedelta(days=round(365.25 * n))
        if dates[0] > target + datetime.timedelta(days=45):    # el historial no llega tan atrás
            continue
        i = min(range(len(dates)), key=lambda k: abs((dates[k] - target).days))
        around = sorted(v for _, v in series[max(0, i - 1):i + 2])
        start = around[len(around) // 2]
        if not start:
            continue
        annual = 100 * ((last / start) ** (1 / n) - 1)
        if abs(annual) <= MAX_PLAUSIBLE_PCT:
            out[n] = {"total": 100 * (last / start - 1), "annual": annual}
    return out


SHARE_KEYS = ("diluted weighted average shares outstanding", "weighted avg. shares outstanding dil",
              "shares outstanding", "total shares outstanding")


def annual_shares(financials: Optional[Dict[str, Any]]) -> Optional[List[List[Any]]]:
    """Acciones diluidas medias de cada ejercicio de las cuentas cargadas en la ficha (Finanzas y gráficos), sin el LTM:
    [[ejercicio, acciones]]. Vienen ya ajustadas por splits. None si no están."""
    inc = (financials or {}).get("income_statement") or {}
    years = (financials or {}).get("years") or []
    by_name = {k.lower(): k for k in inc}
    key = next((by_name[k] for k in SHARE_KEYS if k in by_name), None)
    if not key:
        return None
    out = [[str(y), float(v)] for y, v in zip(years, inc[key]) if str(y).upper() != "LTM" and isinstance(v, (int, float)) and v > 0]
    return out if len(out) >= 2 else None


def annual_windows(series: Optional[List[List[Any]]]) -> Dict[int, Optional[Dict[str, float]]]:
    """Cambio de las acciones a 1, 3 y 5 ejercicios (el último frente a n ejercicios antes): total y anual."""
    out: Dict[int, Optional[Dict[str, float]]] = {n: None for n in SHARE_WINDOWS}
    if not series:
        return out
    last = series[-1][1]
    for n in SHARE_WINDOWS:
        if len(series) > n and series[-1 - n][1]:
            start = series[-1 - n][1]
            annual = 100 * ((last / start) ** (1 / n) - 1)
            if abs(annual) <= MAX_PLAUSIBLE_PCT:
                out[n] = {"total": 100 * (last / start - 1), "annual": annual}
    return out


def shares_from_financials(financials: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Etiqueta, ventanas y serie de las acciones con las cuentas anuales (la etiqueta, con los últimos 3 ejercicios)."""
    series = annual_shares(financials)
    if not series:
        return None
    windows = annual_windows(series)
    w3 = windows.get(3) or windows.get(1)
    if not w3:
        return None
    pct = w3["annual"]
    label = "estable" if abs(pct) < STABLE_PCT else "recompra" if pct < 0 else "diluye"
    n = 3 if windows.get(3) else 1
    return {"shares": {"pct": pct, "years": n, "label": label, "first": series[-1 - n][1], "last": series[-1][1]},
            "windows": windows, "series": series, "source": f"cuentas anuales ({series[0][0]}–{series[-1][0]})"}


def _window(items: List[Dict[str, Any]], kind: str, since: str) -> Dict[str, Any]:
    sel = [t for t in items if t["kind"] == kind and t["date"] >= since]
    return {"count": len(sel), "insiders": len({t["insider"] for t in sel}), "value": sum(t["value"] or 0 for t in sel)}


def signal(items: Optional[List[Dict[str, Any]]], today: Optional[datetime.date] = None) -> Optional[Dict[str, Any]]:
    """Compras significativas en los últimos SIGNAL_DAYS: 2+ directivos, o una de más de SIGNAL_MIN_VALUE."""
    if not items:
        return None
    today = today or datetime.date.today()
    since = (today - datetime.timedelta(days=SIGNAL_DAYS)).isoformat()
    buys = [t for t in items if t["kind"] == "buy" and t["date"] >= since]
    names = sorted({t["insider"] for t in buys})
    big = [t for t in buys if (t["value"] or 0) >= SIGNAL_MIN_VALUE]
    if len(names) < SIGNAL_MIN_INSIDERS and not big:
        return None
    return {"insiders": names, "value": sum(t["value"] or 0 for t in buys), "count": len(buys),
            "first": min(t["date"] for t in buys), "last": max(t["date"] for t in buys)}


def summary(ticker: str, today: Optional[datetime.date] = None, fetch: bool = True,
            price_now: Optional[float] = None) -> Dict[str, Any]:
    """Todo lo de una empresa. fetch=False: solo lo que ya esté en caché (para no esperar a Yahoo).
    price_now: el de hoy en la moneda de las operaciones, para comparar precios (None: no se compara)."""
    today = today or datetime.date.today()
    if fetch:
        items, series, splits = trades(ticker), shares_series(ticker), splits_all(ticker)
        holders = roster(ticker) if items else None
    else:
        items = market_cache.peek(TRADES_KEY, ticker)
        series = market_cache.peek("shares_history_v2", ticker)
        splits = [tuple(x) for x in market_cache.peek("splits_all_v1", ticker) or []]
        holders = market_cache.peek("insider_roster_v1", ticker)
    series = split_adjusted(series, splits)
    since_12m = (today - datetime.timedelta(days=30 * TRADES_MONTHS)).isoformat()
    since_6m = (today - datetime.timedelta(days=SUMMARY_DAYS)).isoformat()
    recent = sorted((t for t in items or [] if t["date"] >= since_12m and t["kind"] in ("buy", "sell")), key=lambda t: t["date"], reverse=True)
    buys, sells = _window(items or [], "buy", since_12m), _window(items or [], "sell", since_12m)
    return {"available": items is not None, "trades": recent, "buys": buys, "sells": sells,
            "net": buys["value"] - sells["value"],   # > 0: los directivos han comprado más de lo que han vendido
            "buys_6m": _window(items or [], "buy", since_6m),
            "signal": signal(items, today), "series": series or [],
            # la etiqueta (recompra / diluye / estable), con los últimos 3 años; el detalle a 1, 3 y 5 años aparte
            "shares": share_change((series or [])[-37:]), "windows": share_windows(series, today),
            "read": analyze(items, holders, (series or [[None, None]])[-1][1], price_now, splits, today) if items else None}


# --- Lectura: quién vende o compra, cuánto de lo suyo, a qué precio y si es un plan -------------------------------------

_ROLES = [("chief executive", "CEO"), ("ceo", "CEO"), ("chief financial", "CFO"), ("cfo", "CFO"), ("chief operating", "COO"),
          ("chief technology", "CTO"), ("chief accounting", "Contabilidad"), ("chairman", "Presidente del consejo"),
          ("president", "Presidente"), ("general counsel", "Jurídico"), ("beneficial owner", "Accionista >10 %"),
          ("director", "Consejero"), ("officer", "Directivo")]
SENIOR = {"CEO", "CFO", "COO", "Presidente", "Presidente del consejo"}


def role(position: str) -> str:
    """El cargo en corto y en español (CEO, CFO, Consejero…)."""
    p = (position or "").lower()
    for key, label in _ROLES:
        if key in p:
            return label
    return (position or "").strip().title() or "—"


def _fetch_roster(ticker: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """Directivos con lo que tienen hoy (acciones directas e indirectas), por nombre."""
    import yfinance as yf
    try:
        df = yf.Ticker(ticker).insider_roster_holders
    except Exception as e:
        logger.warning("Participaciones de directivos de %s: %s", ticker, e)
        return None
    if df is None or df.empty:
        return None
    num = lambda v: float(v) if v is not None and v == v else None   # noqa: E731 (NaN fuera)
    return {str(r.get("Name") or "").title(): {"position": str(r.get("Position") or ""), "direct": num(r.get("Shares Owned Directly")),
                                                "indirect": num(r.get("Shares Owned Indirectly"))}
            for r in df.to_dict("records") if r.get("Name")}


def roster(ticker: str) -> Optional[Dict[str, Dict[str, Any]]]:
    return market_cache.cached("insider_roster_v1", ticker, lambda: _fetch_roster(ticker), ttl_hours=CACHE_TTL_H)


def _held(roster_map: Optional[Dict[str, Dict[str, Any]]], name: str) -> Optional[float]:
    r = (roster_map or {}).get(name)
    if r is None:   # «Zuckerberg Mark» frente a «Mark Zuckerberg»: mismas palabras en otro orden
        key = sorted(name.lower().split())
        r = next((v for k, v in (roster_map or {}).items() if sorted(k.lower().split()) == key), None)
    if not r or (r.get("direct") is None and r.get("indirect") is None):
        return None
    return (r.get("direct") or 0) + (r.get("indirect") or 0)


def _cv(xs: List[float]) -> float:
    return pstdev(xs) / mean(xs) if len(xs) > 1 and mean(xs) else 9.0


def is_plan(sells: List[Dict[str, Any]]) -> bool:
    """Ventas periódicas (plan programado, 10b5-1): 3 o más del mismo directivo, de tamaño parecido o a intervalos regulares.
    Yahoo no lo marca: se deduce del patrón."""
    if len(sells) < PLAN_MIN_SALES:
        return False
    days = sorted(datetime.date.fromisoformat(t["date"]) for t in sells)
    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    return _cv([t["shares"] for t in sells]) < PLAN_MAX_CV or (_cv(gaps) < PLAN_MAX_CV and mean(gaps) <= 45)


def _avg_price(ts: List[Dict[str, Any]]) -> Optional[float]:
    """Precio medio ponderado por acciones (de las que tienen precio)."""
    priced = [t for t in ts if t.get("price") and t.get("shares")]
    sh = sum(t["shares"] for t in priced)
    return sum(t["price"] * t["shares"] for t in priced) / sh if sh else None


def _value(t: Dict[str, Any]) -> float:
    return t["value"] if t.get("value") else (t["price"] * t["shares"] if t.get("price") else 0.0)


def _side(ts: List[Dict[str, Any]], price_now: Optional[float], shares_out: Optional[float]) -> Dict[str, Any]:
    avg = _avg_price(ts)
    shares = sum(t["shares"] for t in ts)
    return {"count": len(ts), "insiders": sorted({t["insider"] for t in ts}), "value": sum(_value(t) for t in ts), "shares": shares,
            "avg": avg, "vs_now": (100 * (price_now / avg - 1)) if avg and price_now else None,
            "pct_company": (100 * shares / shares_out) if shares_out and shares else None}


def analyze(items: Optional[List[Dict[str, Any]]], roster_map: Optional[Dict[str, Dict[str, Any]]] = None,
            shares_out: Optional[float] = None, price_now: Optional[float] = None, splits=(),
            today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """La lectura de 12 meses de compras y ventas, con lo de los últimos RECENT_DAYS como titular.
    price_now: el precio de hoy en la misma moneda que las operaciones (None: no se compara)."""
    from app.application.services.split_service import factor_since
    today = today or datetime.date.today()
    since_12m = (today - datetime.timedelta(days=30 * TRADES_MONTHS)).isoformat()
    since_recent = (today - datetime.timedelta(days=RECENT_DAYS)).isoformat()
    year = []
    for t in items or []:
        if t["date"] < since_12m:
            continue
        f = factor_since(splits or [], t["date"])   # en acciones y precios de hoy
        year.append({**t, "shares": t["shares"] * f, "price": t["price"] / f if t.get("price") else None})
    awards = [t for t in year if t["kind"] == "award"]
    year = [t for t in year if t["kind"] in ("buy", "sell")]
    recent = [t for t in year if t["date"] >= since_recent]

    people = []
    for name in sorted({t["insider"] for t in year}):
        mine = [t for t in year if t["insider"] == name]
        sells, buys = [t for t in mine if t["kind"] == "sell"], [t for t in mine if t["kind"] == "buy"]
        sold, bought = sum(t["shares"] for t in sells), sum(t["shares"] for t in buys)
        received = sum(t["shares"] for t in awards if t["insider"] == name)
        held = _held(roster_map, name)
        before = (held + sold - bought) if held is not None else None   # lo que tenía al empezar el año (aprox.)
        position = next((t["position"] for t in mine if t.get("position")), "") or ((roster_map or {}).get(name) or {}).get("position", "")
        people.append({
            "name": name, "role": role(position), "senior": role(position) in SENIOR, "ops": len(mine),
            "sold_value": sum(_value(t) for t in sells), "bought_value": sum(_value(t) for t in buys),
            "net": sum(_value(t) for t in buys) - sum(_value(t) for t in sells),
            "sell_avg": _avg_price(sells), "buy_avg": _avg_price(buys), "held": held,
            "stake_sold_pct": (100 * sold / before) if before and before > 0 and sold else None,
            "plan": is_plan(sells), "last": max(t["date"] for t in mine), "received": received,
            "comp": bool(sold and received and sold <= COMP_RATIO * received),   # vende lo que cobra en acciones
            "recent_sell": any(t["date"] >= since_recent for t in sells), "recent_buy": any(t["date"] >= since_recent for t in buys)})
    people.sort(key=lambda p: -abs(p["net"]))

    r_buys = _side([t for t in recent if t["kind"] == "buy"], price_now, shares_out)
    r_sells = _side([t for t in recent if t["kind"] == "sell"], price_now, shares_out)
    sellers = [p for p in people if p["recent_sell"]]
    recent_sold = {p["name"]: sum(_value(t) for t in recent if t["kind"] == "sell" and t["insider"] == p["name"]) for p in sellers}
    total_sold = sum(recent_sold.values())
    routine = lambda n: next(p for p in people if p["name"] == n)  # noqa: E731
    plan_share = (sum(v for n, v in recent_sold.items() if routine(n)["plan"]) / total_sold) if total_sold else None
    comp_share = (sum(v for n, v in recent_sold.items() if routine(n)["comp"] and not routine(n)["plan"]) / total_sold) if total_sold else None
    top = max(sellers, key=lambda p: recent_sold[p["name"]], default=None)
    # a vigilar: vende de lo que ya tenía (no lo que cobra en acciones), fuera de un plan y una parte grande
    watch = [p for p in sellers if not p["plan"] and not p["comp"] and (p["stake_sold_pct"] or 0) >= STAKE_WATCH_PCT]
    buyers = [p for p in people if p["recent_buy"]]

    if r_buys["value"] and (r_buys["value"] >= r_sells["value"] or len(r_buys["insiders"]) >= SIGNAL_MIN_INSIDERS
                           or r_buys["value"] >= SIGNAL_MIN_VALUE):
        verdict = "compran"
    elif watch:
        verdict = "vigilar"
    elif r_sells["count"]:
        small = all(p["stake_sold_pct"] is not None and p["stake_sold_pct"] < 10 for p in sellers)
        verdict = "rutina" if (plan_share or 0) + (comp_share or 0) >= 0.5 or small else "venden"
    elif year:
        verdict = "quietos_reciente"   # nada en 3 meses, pero sí en el año
    else:
        verdict = "quietos"

    months = []
    for i in range(11, -1, -1):   # 12 meses, del más antiguo al actual: compras y ventas de cada uno
        y, m = today.year, today.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        key = f"{y}-{m:02d}"
        months.append({"key": key, "buy": sum(_value(t) for t in year if t["kind"] == "buy" and t["date"][:7] == key),
                       "sell": sum(_value(t) for t in year if t["kind"] == "sell" and t["date"][:7] == key)})
    return {"verdict": verdict, "recent_days": RECENT_DAYS, "buys": r_buys, "sells": r_sells, "people": people,
            "watch": watch, "top_seller": top, "top_share": (recent_sold[top["name"]] / total_sold) if top and total_sold else None,
            "plan_share": plan_share, "comp_share": comp_share, "senior_buyers": [p["role"] for p in buyers if p["senior"]],
            "year_buys": _side([t for t in year if t["kind"] == "buy"], price_now, shares_out),
            "year_sells": _side([t for t in year if t["kind"] == "sell"], price_now, shares_out),
            "months": months, "price_now": price_now,
            "trades": sorted(year, key=lambda t: t["date"], reverse=True)}


def signal_notices(tickers: List[str], today: Optional[datetime.date] = None, prio: int = 2) -> List[Dict[str, Any]]:
    """Avisos de compras de directivos para «Requiere tu atención» (Centro de mando: cartera; Watchlist: el resto),
    solo con lo que ya está en caché (lo descarga la precarga de cada 6 h)."""
    from utils.number_format import es
    out = []
    for t in sorted(set(tickers)):
        sig = signal(market_cache.peek(TRADES_KEY, t), today)
        if not sig:
            continue
        who = sig["insiders"]
        names = ", ".join(who[:2]) + (f" y {len(who) - 2} más" if len(who) > 2 else "")
        value = f" por {es(sig['value'] / 1000, 0)} mil" if sig["value"] else ""
        out.append({"prio": prio, "type": "insider_buy", "ticker": t,   # clave por empresa: sigue abierto mientras dure la racha
                    "text": f"{'Compran ' + str(len(who)) + ' directivos' if len(who) > 1 else 'Compra un directivo'} en bolsa{value} "
                            f"en los últimos {SIGNAL_DAYS} días ({names})",
                    "href": f"/ticker/{t}#directivos"})
    return out
