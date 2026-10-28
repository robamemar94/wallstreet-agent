"""Cambio histórico de una divisa a euros (Yahoo, `XXXEUR=X`)."""
import datetime
from typing import Dict, Optional, Tuple

_cache: Dict[Tuple[str, str], Optional[float]] = {}


def fx_rate_on(currency: str, date_str: str) -> Optional[float]:
    """Euros por unidad de `currency` al cierre de `date_str` (o del primer día hábil siguiente). Sin dato: None.
    Recuerda también los fallos, para no repetir la consulta en cada recálculo de la cartera."""
    ccy = (currency or "").upper()
    if ccy == "EUR":
        return 1.0
    key = (ccy, (date_str or "")[:10])
    if key in _cache:
        return _cache[key]
    rate = None
    try:
        import yfinance as yf
        d = datetime.date.fromisoformat(key[1])
        hist = yf.Ticker(f"{ccy}EUR=X").history(start=d.isoformat(), end=(d + datetime.timedelta(days=5)).isoformat())
        if not hist.empty:
            rate = float(hist["Close"].iloc[0])
    except Exception:
        rate = None
    _cache[key] = rate
    return rate
