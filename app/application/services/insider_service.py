"""
Directivos y acciones en circulación (sin IA, datos de yfinance cacheados 24 h).

- Directivos: solo las compras y ventas en bolsa («Purchase/Sale at price», «Acquisition/Disposition in the public
  market»). Lo demás (acciones que se dan como sueldo, regalos, ejercicio de opciones…) es ruido y se descarta.
  Yahoo solo lo da para EE. UU. y Canadá; en Europa y México no hay datos.
- Acciones en circulación: cambio anual en los últimos 3 años (recompra si bajan, dilución si suben). Sale para todas.
- Señal (aviso en la campana y el Centro de mando, cartera y watchlist): en los últimos 30 días compran 2 o más
  directivos, o uno solo por más de 100.000. Las ventas no avisan: suelen ser planificadas o por impuestos.
"""
import datetime
import logging
import re
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

_BUY = re.compile(r"^(purchase at price|acquisition in the public market)", re.I)
_SELL = re.compile(r"^(sale at price|disposition in the public market)", re.I)
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
        kind = classify(str(r.get("Text") or ""), str(r.get("Transaction") or ""))
        date = r.get("Start Date")
        if not kind or date is None:
            continue
        value = r.get("Value")
        out.append({"date": str(date)[:10], "insider": str(r.get("Insider") or "").title(), "position": str(r.get("Position") or ""),
                    "kind": kind, "shares": float(r.get("Shares") or 0),
                    "value": float(value) if value and value == value else None, "price": _price(str(r.get("Text") or ""))})
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
    return market_cache.cached("insider_trades_v1", ticker, lambda: _fetch_trades(ticker), ttl_hours=CACHE_TTL_H)


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


def summary(ticker: str, today: Optional[datetime.date] = None, fetch: bool = True) -> Dict[str, Any]:
    """Todo lo de una empresa. fetch=False: solo lo que ya esté en caché (para no esperar a Yahoo)."""
    today = today or datetime.date.today()
    if fetch:
        items, series, splits = trades(ticker), shares_series(ticker), splits_all(ticker)
    else:
        items = market_cache.peek("insider_trades_v1", ticker)
        series = market_cache.peek("shares_history_v2", ticker)
        splits = [tuple(x) for x in market_cache.peek("splits_all_v1", ticker) or []]
    series = split_adjusted(series, splits)
    since_12m = (today - datetime.timedelta(days=30 * TRADES_MONTHS)).isoformat()
    since_6m = (today - datetime.timedelta(days=SUMMARY_DAYS)).isoformat()
    recent = sorted((t for t in items or [] if t["date"] >= since_12m), key=lambda t: t["date"], reverse=True)
    buys, sells = _window(items or [], "buy", since_12m), _window(items or [], "sell", since_12m)
    return {"available": items is not None, "trades": recent, "buys": buys, "sells": sells,
            "net": buys["value"] - sells["value"],   # > 0: los directivos han comprado más de lo que han vendido
            "buys_6m": _window(items or [], "buy", since_6m),
            "signal": signal(items, today), "series": series or [],
            # la etiqueta (recompra / diluye / estable), con los últimos 3 años; el detalle a 1, 3 y 5 años aparte
            "shares": share_change((series or [])[-37:]), "windows": share_windows(series, today)}


def signal_notices(tickers: List[str], today: Optional[datetime.date] = None, prio: int = 2) -> List[Dict[str, Any]]:
    """Avisos de compras de directivos para «Requiere tu atención» (Centro de mando: cartera; Watchlist: el resto),
    solo con lo que ya está en caché (lo descarga la precarga de cada 6 h)."""
    from utils.number_format import es
    out = []
    for t in sorted(set(tickers)):
        sig = signal(market_cache.peek("insider_trades_v1", t), today)
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
