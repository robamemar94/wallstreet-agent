"""
Balance de tus decisiones: cada compra y venta comparada con lo que habría hecho el mismo dinero en el índice.

- Elegir: desde la operación (y a 1, 3, 6 y 12 meses), la acción frente al S&P 500, todo en euros y con
  dividendos (cierres ajustados de yfinance). La acción se mide desde tu precio (en €, ajustado por los splits del ledger)
  y el índice desde el cierre de ese día; si tu precio no cuadra con el cierre (un split sin anotar), desde el cierre.
- Momento: cómo venía el precio el mes antes de comprar (tras caída, tras subida o lateral) y, en las ventas, qué hizo
  la acción después.
- El porqué: el comentario de cada operación (asset.data['tx_notes']) y, desde que existe, la foto de la valoración
  al operar (asset.data['tx_snapshots']: zona respecto al precio de compra de la valoración base y veredicto de la tesis).
Las compras (o ventas) de una misma empresa en un mismo día cuentan como una sola decisión. Las compras seguidas de una
empresa forman una entrada por tramos (hasta 45 días sin comprarla o una venta): ¿compensa frente a comprarlo todo el
primer día?
Sin IA: solo precios y lo que ya hay guardado.
"""
import logging
import time
from typing import Any, Dict, List, Optional

import pandas as pd

TX_NOTES_KEY = "tx_notes"           # {«fecha|tipo|acciones|precio»: comentario}
TX_SNAPS_KEY = "tx_snapshots"       # {«fecha|tipo|acciones|precio»: {zone, drop, buy_price, cagr, verdict}}
HORIZONS = [("1m", 21, "1 mes"), ("3m", 63, "3 meses"), ("6m", 126, "6 meses"), ("12m", 252, "12 meses")]   # sesiones
ENTRY_DAYS = 21                     # «cómo venía»: el mes (21 sesiones) antes de la operación
ENTRY_MOVE_PCT = 5.0                # más de ±5 % en ese mes: tras caída / tras subida; si no, lateral
ENTRY_LABELS = {"caida": "Tras caída", "lateral": "Lateral", "subida": "Tras subida"}
BENCHMARK, BENCHMARK_NAME = "SPY", "S&P 500"
CAMPAIGN_GAP_DAYS = 45              # una entrada por tramos acaba tras 45 días sin comprar esa empresa (o al vender)
TOP_EXAMPLES = 3                    # mejores y peores decisiones de ejemplo
CACHE_TTL = 1800
_cache: Dict[str, tuple] = {}
logger = logging.getLogger(__name__)


def tx_ref(date: str, tx_type: str, shares: float, price: float) -> str:
    """Referencia de una operación: la misma que el diario de decisiones de la tesis y los comentarios."""
    return f"{str(date)[:10]}|{tx_type}|{shares:g}|{price:g}"


def symbols(txs: List[Dict[str, Any]]) -> List[str]:
    """Históricos necesarios: los valores operados, el índice y los cambios a euros (el índice cotiza en USD)."""
    currencies = {_fx_currency(t.get("currency")) for t in txs} | {"USD"}
    return sorted({t["ticker"] for t in txs} | {BENCHMARK} | {f"{c}EUR=X" for c in currencies - {"EUR"}})


def download(syms: List[str], start: str) -> pd.DataFrame:
    """Cierres ajustados desde la primera operación (el histórico común de la app solo cubre 18 meses). Caché 30 min."""
    key = f"{start}|{','.join(sorted(syms))}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    import yfinance as yf
    start_ts = pd.Timestamp(start) - pd.Timedelta(days=45)   # y el mes previo a la primera, para «cómo venía»
    raw = yf.download(sorted(syms), start=start_ts.strftime("%Y-%m-%d"), auto_adjust=True, progress=False)
    df = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) or "Close" in raw.columns else raw
    if isinstance(df, pd.Series):
        df = df.to_frame(syms[0])
    df = df.sort_index()
    _cache[key] = (time.time(), df)
    return df


def _fx_currency(currency: Optional[str]) -> str:
    return "GBP" if currency == "GBp" else (currency or "USD").upper()


def eur_series(df: pd.DataFrame, symbol: str, currency: Optional[str]) -> Optional[pd.Series]:
    """Cierres en euros (para comparar en lo que ganas tú: el cambio de la divisa cuenta)."""
    if symbol not in df.columns:
        return None
    ccy = _fx_currency(currency)
    if ccy == "EUR":
        fx = 1.0
    else:
        col = f"{ccy}EUR=X"
        if col not in df.columns:
            return None
        fx = df[col].ffill()
    s = (df[symbol].ffill() * fx).dropna()
    return s if not s.empty else None


def _fx_at(df: Optional[pd.DataFrame], currency: Optional[str], date: str) -> Optional[float]:
    """Cambio a euros de ese día (las operaciones del CSV del broker no lo traen)."""
    ccy = _fx_currency(currency)
    if ccy == "EUR":
        return 1.0
    col = f"{ccy}EUR=X"
    if df is None or col not in df.columns:
        return None
    s = df[col].dropna()
    pos = s.index.searchsorted(pd.Timestamp(date[:10]))
    return float(s.iloc[min(pos, len(s) - 1)]) if len(s) else None


def _pct(a: float, b: float) -> Optional[float]:
    return 100 * (b / a - 1) if a else None


def entry_context(pre_pct: Optional[float]) -> Optional[str]:
    if pre_pct is None:
        return None
    return "caida" if pre_pct <= -ENTRY_MOVE_PCT else "subida" if pre_pct >= ENTRY_MOVE_PCT else "lateral"


def review_operation(date: str, stock: pd.Series, bench: Optional[pd.Series],
                     entry_eur: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Una decisión: la acción desde tu precio (entry_eur, en € por acción de hoy) y el índice desde el cierre de ese
    día, hasta hoy y a cada plazo; y cómo venía. excess = puntos que la acción saca al índice (en las ventas, lo que
    hizo después de vender)."""
    start = pd.Timestamp(date[:10])
    pos = stock.index.searchsorted(start)
    if pos >= len(stock) or (stock.index[pos] - start).days > 7:   # sin precios de esos días (p. ej. histórico más corto)
        return None
    close, last = stock.iloc[pos], len(stock) - 1
    # tu precio, salvo que no cuadre con el cierre (±25 %: un split sin anotar en el ledger o un precio mal tecleado)
    from_price = bool(entry_eur) and 0.75 < entry_eur / close < 1.33
    p0 = entry_eur if from_price else close
    out = {"since": _pct(p0, stock.iloc[-1]), "excess": None, "horizons": {}, "pre_pct": None, "p0": p0, "from_price": from_price}
    bpos = bench.index.searchsorted(stock.index[pos]) if bench is not None else None
    if bpos is not None and bpos < len(bench):
        out["excess"] = out["since"] - _pct(bench.iloc[bpos], bench.iloc[-1])
    for key, n, _ in HORIZONS:
        if pos + n <= last and bpos is not None and bpos + n < len(bench):
            out["horizons"][key] = _pct(p0, stock.iloc[pos + n]) - _pct(bench.iloc[bpos], bench.iloc[bpos + n])
    if pos >= ENTRY_DAYS:
        out["pre_pct"] = _pct(stock.iloc[pos - ENTRY_DAYS], close)
    out["entry"] = entry_context(out["pre_pct"])
    return out


def _weighted(rows: List[Dict[str, Any]], value) -> Optional[float]:
    pairs = [(value(r), r["amount_eur"] or 0) for r in rows]
    pairs = [(v, w) for v, w in pairs if v is not None]
    total = sum(w for _, w in pairs)
    if not pairs:
        return None
    return sum(v * w for v, w in pairs) / total if total else sum(v for v, _ in pairs) / len(pairs)


def _beat(rows: List[Dict[str, Any]], value) -> Dict[str, Any]:
    vals = [value(r) for r in rows if value(r) is not None]
    return {"n": len(vals), "beat": sum(1 for v in vals if v > 0), "avg": _weighted(rows, value)}


def summarize(ops: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Cifras de resumen. Las medias van ponderadas por el importe de cada decisión (una compra grande pesa más)."""
    buys = [o for o in ops if o["type"] == "BUY" and o["review"]]
    sells = [o for o in ops if o["type"] == "SELL" and o["review"]]
    excess = lambda o: o["review"]["excess"]
    out = {"buys": _beat(buys, excess), "n_buys": len(buys), "n_sells": len(sells), "horizons": []}
    for key, _, label in HORIZONS:
        with_h = [o for o in buys if key in o["review"]["horizons"]]
        if with_h:
            out["horizons"].append({"key": key, "label": label, **_beat(with_h, lambda o, k=key: o["review"]["horizons"][k])})
    # momento de entrada: ¿rinden más las compras tras caídas o las que persiguen subidas?
    out["entry"] = []
    for key, label in ENTRY_LABELS.items():
        group = [o for o in buys if o["review"]["entry"] == key]
        if group:
            out["entry"].append({"key": key, "label": label, **_beat(group, excess)})
    # ventas: si la acción siguió subiendo (y más que el índice), vender salió caro
    after = lambda o: o["review"]["since"]
    out["sells"] = {"n": len(sells), "rose": sum(1 for o in sells if (after(o) or 0) > 0),
                    "avg_after": _weighted(sells, after), "vs": _beat(sells, excess)}
    # compras con foto de la valoración: en zona de compra frente a fuera (solo las operaciones nuevas la tienen)
    zoned = [o for o in buys if (o.get("snapshot") or {}).get("zone") not in (None, "sin")]
    out["zones"] = [{"label": label, **_beat(g, excess)} for label, g in
                    (("En zona de compra", [o for o in zoned if o["snapshot"]["zone"] == "zona"]),
                     ("Fuera de zona", [o for o in zoned if o["snapshot"]["zone"] != "zona"])) if g]
    out["with_reason"] = sum(1 for o in ops if o.get("note"))
    # ejemplos: las decisiones que mejor y peor te han salido frente al índice (en las ventas, bien = la acción cayó más)
    # (una por empresa: tres tramos de la misma compra no son tres ejemplos)
    scored = sorted((o for o in buys + sells if o["review"]["excess"] is not None), key=result_for_you, reverse=True)
    top = min(TOP_EXAMPLES, len({o["ticker"] for o in scored}) // 2)
    out["best"], out["worst"] = _one_per_company(scored, top), _one_per_company(scored[::-1], top)
    return out


def _one_per_company(ops: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for o in ops:
        if o["ticker"] not in seen and len(out) < n:
            seen.add(o["ticker"])
            out.append(o)
    return out


def result_for_you(o: Dict[str, Any]) -> float:
    """Puntos frente al índice desde el punto de vista del inversor: en una venta, que la acción caiga después es acertar."""
    return -o["review"]["excess"] if o["type"] == "SELL" else o["review"]["excess"]


def group_decisions(txs: List[Dict[str, Any]], asset_data: Dict[str, Dict[str, Any]], df: Optional[pd.DataFrame],
                    splits: Optional[Dict[str, List[tuple]]] = None) -> List[Dict[str, Any]]:
    """Agrupa las operaciones de una misma empresa, mismo día y mismo tipo (varias órdenes = una decisión).
    Precio medio ponderado, importe sumado; el porqué y la foto de la valoración, los de cualquiera de sus órdenes."""
    groups: Dict[tuple, Dict[str, Any]] = {}
    for tx in txs:
        key = (tx["ticker"], tx["date"][:10], tx["type"])
        data = asset_data.get(tx["ticker"]) or {}
        ref = tx_ref(tx["date"], tx["type"], tx["shares"], tx["price"])
        fx = tx.get("fx") or _fx_at(df, tx.get("currency"), tx["date"])
        amount = tx["shares"] * tx["price"] * (0.01 if tx.get("currency") == "GBp" else 1) * fx if fx else None
        g = groups.setdefault(key, {"ticker": tx["ticker"], "date": key[1], "type": tx["type"], "currency": tx.get("currency"),
                                    "shares": 0.0, "cost": 0.0, "amount_eur": 0.0, "shares_today": 0.0,
                                    "refs": [], "notes": [], "snapshot": None})
        g["shares"] += tx["shares"]
        g["shares_today"] += tx["shares"] * _split_factor(tx["date"], (splits or {}).get(tx["ticker"], []))
        g["cost"] += tx["shares"] * tx["price"]
        g["amount_eur"] = None if amount is None or g["amount_eur"] is None else g["amount_eur"] + amount
        g["refs"].append(ref)
        note = (data.get(TX_NOTES_KEY) or {}).get(ref)
        if note and note not in g["notes"]:
            g["notes"].append(note)
        g["snapshot"] = g["snapshot"] or (data.get(TX_SNAPS_KEY) or {}).get(ref)
    for g in groups.values():
        g["price"] = g.pop("cost") / g["shares"] if g["shares"] else None
        shares_today = g.pop("shares_today")
        g["entry_eur"] = g["amount_eur"] / shares_today if g["amount_eur"] and shares_today else None   # € por acción de hoy
        g["orders"] = len(g["refs"])
        g["note"] = " · ".join(g.pop("notes")) or None
    return sorted(groups.values(), key=lambda g: g["date"], reverse=True)


def _split_factor(date: str, splits: List[tuple]) -> float:
    """Cuántas acciones de hoy es una acción de ese día (los precios de yfinance vienen ajustados por splits)."""
    factor = 1.0
    for split_date, ratio in splits:
        if split_date > date[:10] and ratio:
            factor *= ratio
    return factor


def build(df: Optional[pd.DataFrame], txs: List[Dict[str, Any]], asset_data: Dict[str, Dict[str, Any]],
          names: Optional[Dict[str, str]] = None, splits: Optional[Dict[str, List[tuple]]] = None) -> Dict[str, Any]:
    """Todas las decisiones (de la más reciente a la más antigua) con su balance y el resumen.
    asset_data: {ticker: asset.data} (comentarios, fotos de la valoración, divisa)."""
    names = names or {}
    bench = eur_series(df, BENCHMARK, "USD") if df is not None else None
    ops, series = [], {}
    for g in group_decisions(txs, asset_data, df, splits):
        data = asset_data.get(g["ticker"]) or {}
        if g["ticker"] not in series:
            series[g["ticker"]] = eur_series(df, g["ticker"], g.get("currency") or data.get("currency")) if df is not None else None
        stock = series[g["ticker"]]
        ops.append({**g, "name": names.get(g["ticker"]) or g["ticker"],
                    "review": review_operation(g["date"], stock, bench, g["entry_eur"]) if stock is not None else None})
    entries = campaigns(ops, series, bench)
    summary = summarize(ops)
    staged = [c for c in entries if c["tranches"] > 1 and c["saving"] is not None]
    summary["tranches"] = {"n": len(staged), "saved": sum(1 for c in staged if c["saving"] > 0),
                           "avg": _weighted(staged, lambda c: c["saving"])}
    return {"ops": ops, "entries": entries, "summary": summary, "benchmark": BENCHMARK_NAME,
            "gap_days": CAMPAIGN_GAP_DAYS, "missing": sorted({o["ticker"] for o in ops if not o["review"]})}


def _close_on(s: pd.Series, date: str) -> Optional[float]:
    pos = s.index.searchsorted(pd.Timestamp(date[:10]))
    return float(s.iloc[pos]) if pos < len(s) and (s.index[pos] - pd.Timestamp(date[:10])).days <= 7 else None


def campaigns(ops: List[Dict[str, Any]], series: Dict[str, Optional[pd.Series]], bench: Optional[pd.Series],
              today: Optional[pd.Timestamp] = None) -> List[Dict[str, Any]]:
    """Entradas por tramos: las compras de una empresa hasta que pasan CAMPAIGN_GAP_DAYS sin comprarla o se vende.
    Por entrada: precio medio (cierres en €, ponderados por importe) frente a haberlo comprado todo el primer día
    (saving > 0: los tramos salieron más baratos), lo que ha hecho desde entonces y el S&P 500 con el mismo dinero
    entrando en las mismas fechas."""
    today = today or pd.Timestamp.today().normalize()
    out: List[Dict[str, Any]] = []
    open_by_ticker: Dict[str, Dict[str, Any]] = {}
    for o in sorted(ops, key=lambda o: o["date"]):
        cur = open_by_ticker.get(o["ticker"])
        if o["type"] == "SELL":
            open_by_ticker.pop(o["ticker"], None)
            continue
        if not cur or (pd.Timestamp(o["date"]) - pd.Timestamp(cur["buys"][-1]["date"])).days > CAMPAIGN_GAP_DAYS:
            cur = {"ticker": o["ticker"], "name": o["name"], "buys": []}
            open_by_ticker[o["ticker"]] = cur
            out.append(cur)
        cur["buys"].append(o)
    still_open = {id(c) for c in open_by_ticker.values()}   # sin venta después: puede seguir sumando tramos
    for c in out:
        buys, stock = c.pop("buys"), series.get(c["ticker"])
        c.update({"start": buys[0]["date"], "end": buys[-1]["date"], "tranches": len(buys),
                  "ongoing": id(c) in still_open and (today - pd.Timestamp(buys[-1]["date"])).days <= CAMPAIGN_GAP_DAYS,
                  "amount_eur": sum(b["amount_eur"] or 0 for b in buys) or None,
                  "refs": [r for b in buys for r in b["refs"]], "saving": None, "since": None, "excess": None,
                  "note": " · ".join(dict.fromkeys(b["note"] for b in buys if b.get("note"))) or None})
        if stock is None or not c["amount_eur"]:
            continue
        # cada tramo a tu precio (el de review_operation: el tuyo o, si no cuadra, el cierre)
        flows = [(b["amount_eur"] or 0, (b["review"] or {}).get("p0"), _close_on(bench, b["date"]) if bench is not None else None) for b in buys]
        if any(p is None for _, p, _ in flows):
            continue
        amount = sum(a for a, _, _ in flows)
        avg = amount / sum(a / p for a, p, _ in flows)
        c["saving"] = 100 * (1 - avg / flows[0][1])
        c["since"] = 100 * (sum(a * stock.iloc[-1] / p for a, p, _ in flows) / amount - 1)
        if bench is not None and all(b for _, _, b in flows):
            c["excess"] = c["since"] - 100 * (sum(a * bench.iloc[-1] / b for a, _, b in flows) / amount - 1)
    return sorted(out, key=lambda c: c["start"], reverse=True)


def snapshot(ticker: str, asset_data: Dict[str, Any], price: float, verdict: Optional[str]) -> Optional[Dict[str, Any]]:
    """Foto de la valoración al operar: zona respecto al precio de compra de la valoración base (como la Watchlist)."""
    from app.application.services import base_valuation_service
    from app.interfaces.views.watchlist_views import proximity
    val = base_valuation_service.live(ticker, asset_data, price)
    zone, drop = proximity(val, price)
    if zone == "sin" and not verdict:
        return None
    return {"zone": zone, "drop": drop, "buy_price": (val or {}).get("buy_price"), "cagr": (val or {}).get("cagr"),
            "verdict": verdict}
