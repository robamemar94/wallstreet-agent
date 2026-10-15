"""
«Mi posición» en la ficha de una empresa: cómo te ha ido con ella, todo en euros.

- Resultado: lo que vale hoy lo que te queda + lo que sacaste al vender + los dividendos cobrados − lo que pusiste.
  En % sobre lo que pusiste (la suma de las compras).
- Frente al S&P 500: el mismo dinero en el índice en las mismas fechas (cada compra compra índice; cada venta vende la
  misma parte del índice que de la acción). Cierres ajustados del índice (con dividendos), en euros.
- Cada operación con su porqué y su balance (lo de Decisiones, solo para esta empresa: decision_review_service.build).
- Las compras y ventas sobre el gráfico de precio (precio por acción de hoy: ajustado por los splits del ledger).
Sin IA: solo precios y lo que ya hay guardado.
"""
from typing import Any, Dict, List, Optional

import pandas as pd

from app.application.services import decision_review_service as dr


def _close_on(s: Optional[pd.Series], date: str) -> Optional[float]:
    if s is None or s.empty:
        return None
    pos = min(s.index.searchsorted(pd.Timestamp(date[:10])), len(s) - 1)
    return float(s.iloc[pos])


def versus_index(txs: List[Dict[str, Any]], dividends: List[Dict[str, Any]], df: Optional[pd.DataFrame],
                 currency: Optional[str], splits: Optional[List[tuple]] = None) -> Dict[str, Any]:
    """txs: compras y ventas de UNA empresa ({date, type, shares, price, currency, fx}); dividends: [{date, eur}].
    -> {invested, value, sold, dividends, result, result_pct, index_result, index_pct, excess, shares, first}"""
    splits = splits or []
    stock = dr.eur_series(df, txs[0]["ticker"], currency) if df is not None and txs else None
    bench = dr.eur_series(df, dr.BENCHMARK, "USD") if df is not None else None
    invested = sold = units = idx_sold = shares = 0.0
    idx_ok = bench is not None
    for t in sorted(txs, key=lambda t: t["date"]):
        fx = t.get("fx") or dr._fx_at(df, t.get("currency") or currency, t["date"])
        if not fx:
            return {}
        amount = t["shares"] * t["price"] * (0.01 if (t.get("currency") or currency) == "GBp" else 1) * fx
        today_shares = t["shares"] * dr._split_factor(t["date"], splits)
        b = _close_on(bench, t["date"]) if idx_ok else None
        if t["type"] == "BUY":
            invested += amount
            shares += today_shares
            if b:
                units += amount / b
        else:
            frac = min(1.0, today_shares / shares) if shares else 1.0
            sold += amount
            shares = max(0.0, shares - today_shares)
            if b:
                idx_sold += frac * units * b
                units *= 1 - frac
        idx_ok = idx_ok and bool(b)
    divs = sum(d["eur"] or 0 for d in dividends)
    price_eur = float(stock.iloc[-1]) if stock is not None else None
    value = shares * price_eur if price_eur is not None and shares > 1e-9 else 0.0
    if price_eur is None and shares > 1e-9:
        return {}
    result = value + sold + divs - invested
    out = {"invested": invested, "value": value, "sold": sold, "dividends": divs, "result": result,
           "result_pct": 100 * result / invested if invested else None, "shares": shares,
           "first": min(t["date"] for t in txs)[:10] if txs else None,
           "index_result": None, "index_pct": None, "excess": None}
    if idx_ok and invested:
        idx_result = units * float(bench.iloc[-1]) + idx_sold - invested
        out.update(index_result=idx_result, index_pct=100 * idx_result / invested,
                   excess=out["result_pct"] - 100 * idx_result / invested)
    return out


def markers(txs: List[Dict[str, Any]], splits: Optional[List[tuple]] = None) -> List[Dict[str, Any]]:
    """Compras y ventas para el gráfico: una por día y tipo, al precio medio de ese día en acciones de hoy."""
    groups: Dict[tuple, Dict[str, float]] = {}
    for t in txs:
        f = dr._split_factor(t["date"], splits or [])
        g = groups.setdefault((t["date"][:10], t["type"]), {"shares": 0.0, "cost": 0.0, "orig": 0.0})
        g["shares"] += t["shares"] * f
        g["orig"] += t["shares"]
        g["cost"] += t["shares"] * t["price"]
    return sorted(({"date": d, "type": k, "shares": g["orig"], "price": g["cost"] / g["shares"]}
                   for (d, k), g in groups.items() if g["shares"]), key=lambda m: m["date"])


def dividends_by_year(dividends: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    years: Dict[str, Dict[str, Any]] = {}
    for d in dividends:
        y = years.setdefault(d["date"][:4], {"year": d["date"][:4], "eur": 0.0, "n": 0})
        y["eur"] += d["eur"] or 0
        y["n"] += 1
    return sorted(years.values(), key=lambda y: y["year"], reverse=True)
