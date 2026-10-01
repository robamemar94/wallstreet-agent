"""Detección de splits (contrasplits incluidos) que afectan a datos que Yahoo no ajusta.

Los precios de Yahoo ya vienen ajustados, pero no lo que guarda la app: la cartera (hasta que se reconstruye),
los valores por acción escritos a mano (BPA fijado, precio de referencia de una tesis) y el precio al añadir a la
watchlist. Una caída del 90 % el día de un split 10:1 no es una caída.
"""
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.application.services import market_cache

LOOKBACK_YEARS = 3


def fetch_splits(ticker: str) -> List[Tuple[str, float]]:
    """[(fecha ISO, ratio)] de los últimos años; ratio 10 = 10 acciones nuevas por cada una. Caché en disco de 24 h."""
    def download():
        try:
            import pandas as pd
            import yfinance as yf
            s = yf.Ticker(ticker).splits
            if s is None or s.empty:
                return []
            cutoff = pd.Timestamp.now(tz=s.index.tz) - pd.DateOffset(years=LOOKBACK_YEARS)
            return [[d.date().isoformat(), float(r)] for d, r in s.items() if d >= cutoff and r and r != 1]
        except Exception:
            return []
    return [tuple(x) for x in market_cache.cached("splits", ticker, download, ttl_hours=24) or []]


def factor_since(splits: Iterable[Tuple[str, float]], since: Optional[str]) -> float:
    """Por cuánto dividir un precio por acción anotado en `since` para compararlo con el de hoy."""
    f = 1.0
    for d, r in splits:
        if since and d > since[:10]:
            f *= r
    return f


def _ratio(r: float) -> str:
    return f"{r:g}:1" if r >= 1 else f"1:{1 / r:g} (contrasplit)"


def portfolio_notices(held: Dict[str, str], ledger_splits: Dict[str, List[Tuple[str, float]]]) -> List[Dict[str, Any]]:
    """held: {ticker: fecha de la primera compra}. Splits de Yahoo posteriores que la cartera aún no refleja."""
    out = []
    for t, first_buy in held.items():
        known = {d[:10] for d, _ in ledger_splits.get(t, [])}
        for d, r in fetch_splits(t):
            if first_buy and d >= first_buy[:10] and d not in known:
                out.append({"prio": 0, "type": "split", "ticker": t, "ref": d, "href": "/portfolio",
                            "text": f"Split {_ratio(r)} el {d[8:10]}/{d[5:7]}/{d[:4]} sin aplicar en tu cartera: acciones y precio "
                                    "medio no cuadran hasta que la reconstruyas (Cartera → Reconstruir)"})
    return out


def manual_value_notices(items: Iterable[Tuple[str, str, Optional[str], str]]) -> List[Dict[str, Any]]:
    """items: (ticker, qué, fecha en que se anotó, href). Aviso si hubo un split después de anotarlo."""
    out = []
    for t, what, when, href in items:
        later = [(d, r) for d, r in fetch_splits(t) if when and d > when[:10]]
        if later:
            d, r = later[-1]
            f = factor_since(later, when)
            out.append({"prio": 1, "type": "split_manual", "ticker": t, "ref": f"{what}|{d}", "href": href,
                        "text": f"{what.capitalize()} anotado antes del split {_ratio(r)} del {d[8:10]}/{d[5:7]}/{d[:4]}: "
                                f"divídelo entre {f:g} para que cuadre con el precio de hoy"})
    return out
