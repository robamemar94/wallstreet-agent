"""
Centro de mando: evolución de la cartera y monitor de precios.

Solo MONITORIZA: calcula variaciones, contribuciones y señales de precio para mostrarlas; no genera alertas ni lanza
la IA. Si una señal interesa, el usuario pide él mismo «¿Por qué se mueve?» (radar de noticias de esa tesis).

Señales:
- anormal:     el movimiento es raro para ESA acción (en sigmas de su propia volatilidad diaria de 1 año).
- propio:      se mueve y el mercado (benchmark) no: el residuo tras quitar beta × mercado es anormal.
- nivel:       nuevos máximos/mínimos de 52 semanas (sin señales técnicas menores: solo lo importante).
- oportunidad / cara / concentración: ligadas a la tesis (precio de entrada, CAGR implícito, peso en cartera).
"""
import logging
import math
import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Periodos que se MUESTRAN (de calendario, como pide el inversor): día, semana desde el lunes, mes desde el día 1, año.
# Para detectar movimientos anormales se usa aparte una ventana fija de 5 sesiones.
DISPLAY_PERIODS = ("1d", "wtd", "mtd", "ytd")
ABNORMAL_DAY_SIGMA = 2.5
ABNORMAL_WEEK_SIGMA = 2.0
IDIOSYNCRATIC_SIGMA = 2.0
# además, por tamaño absoluto (los valores muy volátiles necesitan mucho para llegar a 2σ): siempre se muestran
BIG_MOVE_DAY_PCT = 5.0
BIG_MOVE_5D_PCT = 7.0
NEW_EXTREME_SESSIONS = 5                        # «nuevo máximo/mínimo» si ocurrió en las últimas 5 sesiones
SWING_WINDOW = 5
MIN_CAGR_PCT = 6.0                              # por debajo: «cara» (poco retorno implícito)
MAX_WEIGHT_PCT = 15.0                           # por encima: concentración
CACHE_TTL = 30 * 60
_cache: Dict[str, Any] = {}


# --- Datos ---

def download_history(symbols: List[str]) -> pd.DataFrame:
    """Cierres diarios de ~1 año y medio (para 52 semanas y media de 200). Cacheado 30 min."""
    key = ",".join(sorted(set(symbols)))
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]
    import yfinance as yf
    raw = yf.download(sorted(set(symbols)), period="18mo", auto_adjust=True, progress=False)
    df = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) or "Close" in raw.columns else raw
    if isinstance(df, pd.Series):
        df = df.to_frame(symbols[0])
    df = df.sort_index().ffill()
    _cache[key] = (time.time(), df)
    return df


# --- Cartera ---

def period_starts(index: pd.DatetimeIndex) -> Dict[str, pd.Timestamp]:
    """Cierre de referencia de cada periodo: la sesión anterior (día), el último cierre antes del lunes de esta semana
    (semana), antes del día 1 de este mes (mes) y antes del 1 de enero (año)."""
    now = index[-1]
    monday = (now - pd.Timedelta(days=now.weekday())).normalize()
    first_month = pd.Timestamp(year=now.year, month=now.month, day=1)
    first_year = pd.Timestamp(year=now.year, month=1, day=1)
    starts = {"1d": index[-2] if len(index) > 1 else index[-1]}
    for key, boundary in (("wtd", monday), ("mtd", first_month), ("ytd", first_year)):
        before = index[index < boundary]
        starts[key] = before[-1] if len(before) else index[0]
    return starts


def _fx_series(df: pd.DataFrame, currency: str) -> Optional[pd.Series]:
    if currency == "EUR":
        return pd.Series(1.0, index=df.index)
    col = f"{currency}EUR=X"
    return df[col] if col in df.columns else None


def adjusted_shares(tx: Dict[str, Any], splits: List[tuple]) -> float:
    """Acciones de una operación expresadas en acciones de HOY (los precios de yfinance están ajustados por splits)."""
    factor = 1.0
    for split_date, ratio in splits:
        if split_date > tx["date"][:10] and ratio:
            factor *= ratio
    return tx["shares"] * factor


def portfolio_changes(df: pd.DataFrame, transactions: List[Dict[str, Any]], splits: Dict[str, List[tuple]],
                      benchmark: Optional[str] = None) -> Dict[str, Any]:
    """Rentabilidad de la cartera en día / semana desde el lunes / mes desde el día 1 / año, RESPETANDO LAS FECHAS DE
    COMPRA Y VENTA: las acciones al inicio del periodo se reconstruyen con las operaciones y las compras/ventas dentro del
    periodo son aportaciones/retiradas (Modified Dietz), así una compra no cuenta como ganancia.
    transactions: [{ticker, date, type BUY|SELL, shares, price, currency, fx}] (fx = EUR por unidad, opcional)."""
    tickers = sorted({t["ticker"] for t in transactions if t["ticker"] in df.columns})
    if not tickers:
        return {"total_eur": 0.0, "periods": {}, "positions": {}}
    index = df[tickers].dropna(how="all").index
    now, starts = index[-1], period_starts(index)
    by_ticker = {t: [x for x in transactions if x["ticker"] == t] for t in tickers}

    def price_eur(t: str, when) -> Optional[float]:
        cur = next((x.get("currency") for x in by_ticker[t] if x.get("currency")), "USD")
        px = df[t].loc[:when].dropna()
        if not len(px):
            return None
        price = float(px.iloc[-1])
        if cur == "GBp":
            price, cur = price / 100, "GBP"
        fx = _fx_series(df, cur)
        if fx is None:
            return None
        fxv = fx.loc[:when].dropna()
        return price * float(fxv.iloc[-1]) if len(fxv) else None

    def shares_at(t: str, when: str) -> float:
        total = 0.0
        for x in by_ticker[t]:
            if x["date"][:10] <= when:
                sh = adjusted_shares(x, splits.get(t, []))
                total += sh if x["type"] == "BUY" else -sh
        return max(total, 0.0)

    def flow_eur(x: Dict[str, Any]) -> Optional[float]:
        cur, price = x.get("currency") or "USD", x["price"]
        if cur == "GBp":
            price, cur = price / 100, "GBP"
        fx = x.get("fx") or (1.0 if cur == "EUR" else None)
        if fx is None:
            f = _fx_series(df, cur)
            fxv = f.loc[:x["date"][:10]].dropna() if f is not None else []
            fx = float(fxv.iloc[-1]) if len(fxv) else None
        if fx is None:
            return None
        amount = x["shares"] * price * fx
        return amount if x["type"] == "BUY" else -amount

    now_s = now.date().isoformat()
    current = {t: shares_at(t, now_s) for t in tickers}
    values_now = {t: current[t] * (price_eur(t, now) or 0) for t in tickers if current[t] > 0}
    total_now = sum(values_now.values())
    periods, contrib = {}, {t: {} for t in tickers}
    for key, start in starts.items():
        start_s, span = start.date().isoformat(), max((now - start).days, 1)
        v0_total, pnl_total, weighted_flows = 0.0, 0.0, 0.0
        pnl = {}
        for t in tickers:
            sh0 = shares_at(t, start_s)
            v0 = sh0 * (price_eur(t, start) or 0)
            v1 = values_now.get(t, 0.0)
            flows = [(x, flow_eur(x)) for x in by_ticker[t] if start_s < x["date"][:10] <= now_s]
            net = sum(f for _, f in flows if f is not None)
            weighted_flows += sum(f * (now - pd.Timestamp(x["date"][:10])).days / span for x, f in flows if f is not None)
            if sh0 or flows:
                pnl[t] = v1 - v0 - net
            v0_total += v0
        pnl_total = sum(pnl.values())
        denom = v0_total + weighted_flows
        periods[key] = {"pct": 100 * pnl_total / denom if denom > 0 else None, "abs_eur": pnl_total}
        for t in tickers:
            contrib[t][key] = 100 * pnl[t] / denom if t in pnl and denom > 0 else None
        if benchmark and benchmark in df.columns:
            bs = df[benchmark].dropna()
            b0 = bs.loc[:start]
            periods[key]["benchmark_pct"] = 100 * (bs.iloc[-1] / b0.iloc[-1] - 1) if len(b0) else None
    pos = {t: {"value_eur": v, "weight": 100 * v / total_now if total_now else 0.0, "contribution": contrib[t]}
           for t, v in values_now.items()}
    return {"total_eur": total_now, "as_of": now.date().isoformat(), "periods": periods, "positions": pos}


# --- Señales de precio ---

def _swing_lows(s: pd.Series, window: int = SWING_WINDOW) -> pd.Series:
    roll = s.rolling(window * 2 + 1, center=True, min_periods=window + 1).min()
    return s[(s == roll)]


def price_stats(close: pd.Series, bench: Optional[pd.Series] = None) -> Optional[Dict[str, Any]]:
    """Métricas de precio de un valor: variaciones, volatilidad, extremos de 52 semanas, media de 200 y beta vs mercado."""
    close = close.dropna()
    if len(close) < 30:
        return None
    last = close.iloc[-1]
    rets = close.pct_change().dropna()
    year = rets.iloc[-252:]
    sigma = float(year.std()) if len(year) > 20 else None
    ch = {k: 100 * (last / close.loc[start] - 1) for k, start in period_starts(close.index).items()}
    r5 = 100 * (last / close.iloc[-6] - 1) if len(close) > 5 else None     # ventana fija para medir lo anormal
    fmt = lambda ts: ts.date().isoformat()
    w52 = close.iloc[-252:]
    hi, lo = float(w52.max()), float(w52.min())
    ma200 = float(close.iloc[-200:].mean()) if len(close) >= 200 else None
    st = {
        "price": float(last), "change": ch, "sigma_d": sigma,
        "z_day": (ch["1d"] / 100) / sigma if sigma and ch["1d"] is not None else None,
        "r5": r5, "z_week": (r5 / 100) / (sigma * math.sqrt(5)) if sigma and r5 is not None else None,
        "high_52w": hi, "low_52w": lo, "from_high_pct": 100 * (last / hi - 1), "from_low_pct": 100 * (last / lo - 1),
        "new_high": bool(w52.iloc[-NEW_EXTREME_SESSIONS:].max() >= hi), "new_low": bool(w52.iloc[-NEW_EXTREME_SESSIONS:].min() <= lo),
        "date": fmt(close.index[-1]), "r5_from": fmt(close.index[-6]) if len(close) > 5 else None,
        "high_date": fmt(w52.idxmax()), "low_date": fmt(w52.idxmin()),
        "ma200": ma200, "vs_ma200_pct": 100 * (last / ma200 - 1) if ma200 else None, "ma200_cross": None,
        "broken_swing_low": None,
    }
    if ma200 and len(close) >= 206:
        ma = close.rolling(200).mean()
        side_now, side_before = close.iloc[-1] > ma.iloc[-1], close.iloc[-6] > ma.iloc[-6]
        if side_now != side_before:
            st["ma200_cross"] = "arriba" if side_now else "abajo"
    # mínimo relativo reciente perdido: último mínimo local de las 60 sesiones previas a la última semana
    prev = close.iloc[-65:-5]
    lows = _swing_lows(prev) if len(prev) > SWING_WINDOW * 2 else pd.Series(dtype=float)
    if len(lows):
        level = float(lows.iloc[-1])
        if close.iloc[-5:].min() < level <= close.iloc[-6]:
            st["broken_swing_low"] = level
    # componente propio frente al mercado (beta de 1 año)
    if bench is not None and sigma:
        b = bench.dropna().pct_change().dropna()
        joined = pd.concat([rets, b], axis=1, join="inner").dropna().iloc[-252:]
        if len(joined) > 60:
            x, y = joined.iloc[:, 1].values, joined.iloc[:, 0].values
            beta = float(np.cov(y, x)[0, 1] / np.var(x)) if np.var(x) else 1.0
            resid_sigma = float(np.std(y - beta * x))
            b_week = float(bench.dropna().iloc[-1] / bench.dropna().iloc[-6] - 1)
            resid_week = (r5 / 100 if r5 is not None else 0) - beta * b_week
            st.update({"beta": beta, "bench_week_pct": 100 * b_week, "own_week_pct": 100 * resid_week,
                       "z_own_week": resid_week / (resid_sigma * math.sqrt(5)) if resid_sigma else None})
    return st


def price_signals(ticker: str, st: Dict[str, Any], thesis: Optional[Dict[str, Any]] = None,
                  live: Optional[Dict[str, Any]] = None, weight: Optional[float] = None) -> List[Dict[str, Any]]:
    """Señales para MOSTRAR (no alertas). tone: 'down' | 'up' | 'neutral' (para el color)."""
    out = []
    add = lambda kind, tone, text, date=None, since=None: out.append(
        {"ticker": ticker, "kind": kind, "tone": tone, "text": text, "date": date, "since": since})
    zd, zw, zo = st.get("z_day"), st.get("z_week"), st.get("z_own_week")
    d1, r5 = (st.get("change") or {}).get("1d"), st.get("r5")
    own = zo is not None and abs(zo) >= IDIOSYNCRATIC_SIGMA
    market = (f" · propio: el mercado {st['bench_week_pct']:+.1f}% (beta {st['beta']:.1f})" if own
              else (f" · mercado {st['bench_week_pct']:+.1f}%" if st.get("bench_week_pct") is not None else ""))
    sig = lambda z: f", {abs(z):.1f}σ para este valor" if z is not None else ""
    if d1 is not None and ((zd is not None and abs(zd) >= ABNORMAL_DAY_SIGMA) or abs(d1) >= BIG_MOVE_DAY_PCT):
        add("anormal", "down" if d1 < 0 else "up", f"{d1:+.1f}% en el día{sig(zd)}", st.get("date"))
    if r5 is not None and ((zw is not None and abs(zw) >= ABNORMAL_WEEK_SIGMA) or abs(r5) >= BIG_MOVE_5D_PCT):
        add("anormal", "down" if r5 < 0 else "up", f"{r5:+.1f}% en 5 sesiones{sig(zw)}{market}", st.get("date"), st.get("r5_from"))
    elif own:
        add("propio", "down" if zo < 0 else "up", f"Movimiento propio: {st['own_week_pct']:+.1f}% en 5 sesiones descontando el mercado ({st['bench_week_pct']:+.1f}%)",
            st.get("date"), st.get("r5_from"))
    if st.get("new_low"):
        add("nivel", "down", f"Nuevo mínimo de 52 semanas ({st['low_52w']:.2f})", st.get("low_date"))
    elif st.get("new_high"):
        add("nivel", "up", f"Nuevo máximo de 52 semanas ({st['high_52w']:.2f})", st.get("high_date"))
    if live:
        base = next((s for s in live["scenarios"] if str(s["name"]).lower() == "base"), None)
        intact = thesis and thesis.get("verdict") in ("REFORZADA", "INTACTA")
        if base:
            e15 = base["entry_prices"].get(15)
            if intact and e15 and st["price"] <= e15:
                add("oportunidad", "up", f"Por debajo del precio de entrada al 15% del escenario Base ({e15:,.0f}) con la tesis {thesis['verdict']}")
            if base["cagr"] < MIN_CAGR_PCT:
                add("cara", "down", f"CAGR implícito del escenario Base {base['cagr']:.1f}% (<{MIN_CAGR_PCT:.0f}%): poco retorno al precio actual")
    if weight is not None and weight > MAX_WEIGHT_PCT:
        add("concentración", "neutral", f"Pesa el {weight:.1f}% de la cartera (>{MAX_WEIGHT_PCT:.0f}%)")
    return out


def load_transactions(portfolio_service, db) -> tuple:
    """Operaciones de compra/venta (CSV del broker + manuales, como el rendimiento) y splits del ledger."""
    from app.infrastructure.db.models import DBLedgerEntry
    txs = []
    for t in portfolio_service.load_csv_transactions() + portfolio_service.repository.load_manual_transactions():
        kind = getattr(t.type, "value", str(t.type))
        if kind in ("BUY", "SELL") and t.shares:
            txs.append({"ticker": t.ticker.upper(), "date": str(t.date)[:10], "type": kind, "shares": float(t.shares),
                        "price": float(t.price), "currency": t.currency, "fx": t.fx_rate_at_purchase})
    splits: Dict[str, List[tuple]] = {}
    for e in db.query(DBLedgerEntry).filter(DBLedgerEntry.type == "SPLIT").all():
        splits.setdefault(e.ticker.upper(), []).append((str(e.date)[:10], float(e.shares)))
    return txs, splits
