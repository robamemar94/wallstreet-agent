"""
«Mi posición» en la ficha de una empresa: cómo te ha ido con ella, todo en euros.

- Resultado: lo que vale hoy lo que te queda + lo que sacaste al vender + los dividendos cobrados − lo que pusiste.
  En % sobre lo que pusiste (la suma de las compras).
- Frente al S&P 500: el mismo dinero en el índice en las mismas fechas (cada compra compra índice; cada venta vende la
  misma parte del índice que de la acción). Cierres ajustados del índice (con dividendos), en euros.
- Cada operación con su porqué y su balance (lo de Decisiones, solo para esta empresa: decision_review_service.build).
- Mi dinero día a día: lo que vale lo que te queda + lo sacado + los dividendos, frente a lo que pusiste.
- Las compras y ventas sobre el gráfico de precio (precio por acción de hoy: ajustado por los splits del ledger).
Sin IA: solo precios y lo que ya hay guardado.
"""
from typing import Any, Dict, List, Optional

import numpy as np
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


def money_series(txs: List[Dict[str, Any]], dividends: List[Dict[str, Any]], close: Optional[pd.Series], df: Optional[pd.DataFrame],
                 currency: Optional[str], splits: Optional[List[tuple]] = None) -> Dict[str, Any]:
    """Tu dinero en esta empresa cada día desde la primera compra, en euros. close: cierres sin ajustar por dividendos (en
    acciones de hoy, como los da yfinance). -> {dates, total, invested}: total = valor de lo que te queda + lo sacado al
    vender + dividendos cobrados; invested = suma de compras. El último punto da el Resultado de versus_index."""
    if close is None or close.empty or not txs:
        return {}
    close = close.dropna()
    if close.index.tz is not None:
        close.index = close.index.tz_localize(None)
    close.index = close.index.normalize()
    idx = close.index[close.index >= pd.Timestamp(min(t["date"] for t in txs)[:10])]
    if idx.empty:
        return {}
    ccy, pence = dr._fx_currency(currency), currency == "GBp"
    if ccy == "EUR":
        fx_day = pd.Series(1.0, index=idx)
    else:
        col = f"{ccy}EUR=X"
        if df is None or col not in df.columns:
            return {}
        fx_day = df[col].dropna().reindex(idx, method="ffill").bfill()
    n = len(idx)
    shares, invested, sold, divs = (np.zeros(n) for _ in range(4))

    def at(date: str) -> int:
        return min(int(idx.searchsorted(pd.Timestamp(date[:10]))), n - 1)

    for t in txs:
        fx = t.get("fx") or dr._fx_at(df, t.get("currency") or currency, t["date"])
        if not fx:
            return {}
        amount = t["shares"] * t["price"] * (0.01 if (t.get("currency") or currency) == "GBp" else 1) * fx
        today = t["shares"] * dr._split_factor(t["date"], splits or [])
        i = at(t["date"])
        if t["type"] == "BUY":
            invested[i] += amount
            shares[i] += today
        else:
            sold[i] += amount
            shares[i] -= today
    for d in dividends:
        divs[at(d["date"])] += d["eur"] or 0
    value = np.cumsum(shares).clip(min=0) * close.reindex(idx).to_numpy() * fx_day.to_numpy() * (0.01 if pence else 1)
    total = value + np.cumsum(sold) + np.cumsum(divs)
    return {"dates": [d.strftime("%Y-%m-%d") for d in idx], "total": [round(float(v), 2) for v in total],
            "invested": [round(float(v), 2) for v in np.cumsum(invested)]}


def position_stats(ops: List[Dict[str, Any]], txs: List[Dict[str, Any]], active_item: Any = None,
                   today: Optional[pd.Timestamp] = None) -> Dict[str, Any]:
    """Cifras de la tarjeta «Tu posición»: tiempo desde la primera compra, mejor y peor compra y cuántas compras
    superaron al índice a 12 meses. ops: las decisiones de decision_review_service.build."""
    today = today or pd.Timestamp.today().normalize()
    first = pd.Timestamp(min(t["date"] for t in txs)[:10]) if txs else today
    months = max(0, (today.year - first.year) * 12 + today.month - first.month - (today.day < first.day))
    buys = [o for o in ops if o["type"] == "BUY" and (o.get("review") or {}).get("since") is not None]
    h12 = [o["review"]["horizons"]["12m"] for o in buys if "12m" in (o["review"].get("horizons") or {})]
    pick = lambda o: {"date": o["date"][:10], "since": o["review"]["since"]} if o else None
    return {"years": months // 12, "months": months % 12,
            "avg": getattr(active_item, "average_price", None) if active_item else None,
            "best": pick(max(buys, key=lambda o: o["review"]["since"], default=None)),
            "worst": pick(min(buys, key=lambda o: o["review"]["since"], default=None)) if len(buys) > 1 else None,
            "n12": len(h12), "beat12": sum(1 for x in h12 if x > 0)}
