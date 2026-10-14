"""
Cambios en las previsiones de los analistas (sin IA, datos de yfinance cacheados 24 h).

- El BPA que esperan para el año fiscal siguiente (o el en curso si no hay) hoy y hace 7, 30 y 90 días, y cuántos
  analistas lo han subido o bajado en los últimos 30 días. Suele moverse antes que la cotización y que la tesis.
- Aviso (campana, Centro de mando para la cartera, Watchlist para el Tier 1): la previsión cae un CUT_PCT % o más
  en 90 días. Las subidas no avisan: se ven en Seguimiento y en la ficha.
"""
import logging
from typing import Any, Dict, List, Optional

from app.application.services import market_cache

logger = logging.getLogger(__name__)

CACHE_KIND = "eps_trend_v1"
CACHE_TTL_H = 24
CUT_PCT = 5.0          # aviso: la previsión de BPA cae al menos esto en 90 días
WINDOWS = {"7d": "7daysAgo", "30d": "30daysAgo", "90d": "90daysAgo"}
PERIOD_LABEL = {"+1y": "el año que viene", "0y": "este año"}


def _num(x) -> Optional[float]:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if x == x else None   # NaN → None


def parse(trend, revisions=None) -> Optional[Dict[str, Any]]:
    """De las tablas eps_trend y eps_revisions de yfinance a {period, current, chg: {7d, 30d, 90d} (%), up_30, down_30}."""
    if trend is None or getattr(trend, "empty", True):
        return None
    for period in ("+1y", "0y"):
        if period not in trend.index:
            continue
        row = trend.loc[period]
        current = _num(row.get("current"))
        if not current:
            continue
        chg = {}
        for key, col in WINDOWS.items():
            before = _num(row.get(col))
            chg[key] = round(100 * (current / before - 1), 2) if before and before > 0 and current > 0 else None
        up = down = None
        if revisions is not None and not getattr(revisions, "empty", True) and period in revisions.index:
            r = revisions.loc[period]
            up, down = _num(r.get("upLast30days")), _num(r.get("downLast30days"))
        return {"period": period, "current": current, "chg": chg,
                "up_30": int(up) if up is not None else None, "down_30": int(down) if down is not None else None}
    return None


def _fetch(ticker: str) -> Optional[Dict[str, Any]]:
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        return parse(stock.eps_trend, stock.eps_revisions)
    except Exception:
        logger.debug("Sin previsiones de analistas para %s", ticker, exc_info=True)
        return None


def revisions(ticker: str, fetch: bool = True) -> Optional[Dict[str, Any]]:
    """Las previsiones de una empresa; fetch=False solo mira la caché (para no frenar una página)."""
    if not fetch:
        return market_cache.peek(CACHE_KIND, ticker)
    return market_cache.cached(CACHE_KIND, ticker, lambda: _fetch(ticker), ttl_hours=CACHE_TTL_H)


def signal(rev: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Recorte de previsiones: la de 90 días cae CUT_PCT % o más."""
    chg = ((rev or {}).get("chg") or {}).get("90d")
    return {"chg": chg, "period": rev["period"]} if chg is not None and chg <= -CUT_PCT else None


def signal_notices(tickers: List[str], prio: int = 2) -> List[Dict[str, Any]]:
    """Avisos de recorte de previsiones, solo con lo que ya está en caché (lo descarga la precarga de cada 6 h)."""
    from utils.number_format import es
    out = []
    for t in sorted(set(tickers)):
        rev = revisions(t, fetch=False)
        sig = signal(rev)
        if not sig:
            continue
        n = rev.get("down_30")
        votes = (f" · {n} analista{'s la bajan' if n > 1 else ' la baja'}") if n else ""
        out.append({"prio": prio, "type": "estimate_cut", "ticker": t,   # clave por empresa: sigue abierto mientras dure
                    "text": f"Previsión de BPA {es(sig['chg'], 1, sign=True)}% en 90 días ({PERIOD_LABEL[sig['period']]}){votes}",
                    "href": f"/ticker/{t}#directivos"})
    return out
