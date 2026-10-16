"""
Mercado y Cartera: evolución de la cartera y monitor de precios.

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
import os
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd
from utils.number_format import es

logger = logging.getLogger(__name__)

# Periodos que se MUESTRAN: 1 día, 1 semana, 1 mes y 1 año corridos, y lo que va de año (YTD).
# Para detectar movimientos anormales se usa aparte una ventana fija de 5 sesiones.
DISPLAY_PERIODS = ("1d", "1w", "1m", "ytd", "1y")
ABNORMAL_DAY_SIGMA = 2.5
ABNORMAL_WEEK_SIGMA = 2.0
IDIOSYNCRATIC_SIGMA = 2.0
# además, por tamaño absoluto (los valores muy volátiles necesitan mucho para llegar a 2σ): siempre se muestran
BIG_MOVE_DAY_PCT = 5.0
BIG_MOVE_5D_PCT = 7.0
NEW_EXTREME_SESSIONS = 5                        # «nuevo máximo/mínimo» si ocurrió en las últimas 5 sesiones
MIN_52W_SPAN_DAYS = 330                         # y solo si el histórico cubre casi un año (uno corto o roto no es «de 52 semanas»)
SWING_WINDOW = 5
MIN_CAGR_PCT = 6.0                              # por debajo: «cara» (poco retorno implícito)
MAX_WEIGHT_PCT = 15.0                           # por encima: concentración
CACHE_TTL = 30 * 60
_cache: Dict[str, Any] = {}


# --- Datos ---

BACKUP_FILE = os.path.join("data", "price_history_backup.pkl")
_backup_lock = threading.Lock()


def _load_backup() -> Dict[str, pd.Series]:
    try:
        return pd.read_pickle(BACKUP_FILE)
    except Exception:
        return {}


def _save_backup(good: Dict[str, pd.Series]) -> None:
    with _backup_lock:
        data = _load_backup()
        for sym, series in good.items():   # se suma a lo guardado: un histórico cortado no borra el bueno
            data[sym] = series.combine_first(data[sym]) if sym in data else series
        os.makedirs(os.path.dirname(BACKUP_FILE), exist_ok=True)
        pd.to_pickle(data, BACKUP_FILE + ".tmp")
        os.replace(BACKUP_FILE + ".tmp", BACKUP_FILE)


TRUNCATED_DAYS = 45   # un valor que empieza tanto después que el resto viene cortado (o salió a bolsa hace poco)


def _truncated(col: pd.Series, index: pd.DatetimeIndex) -> bool:
    first = col.first_valid_index()
    return first is not None and len(index) > 0 and (first - index[0]).days > TRUNCATED_DAYS


def _single_history(sym: str) -> Optional[pd.Series]:
    """Reintento de un solo valor (la descarga en bloque de Yahoo a veces se deja algunos)."""
    import yfinance as yf
    try:
        h = yf.Ticker(sym).history(period="18mo", auto_adjust=True)["Close"].dropna()
        if h.empty:
            return None
        h.index = pd.DatetimeIndex(h.index).tz_localize(None).normalize()
        return h[~h.index.duplicated(keep="last")]
    except Exception:
        return None


def download_history(symbols: List[str], fill: bool = True) -> pd.DataFrame:
    """Cierres diarios de ~1 año y medio (para 52 semanas y media de 200). Cacheado 30 min.
    fill=False: sin rellenar los días en que ese valor no cotizó (p. ej. EE. UU. antes de abrir, mientras Europa ya cotiza).
    Si Yahoo no devuelve algún valor en la descarga en bloque, se reintenta ese valor solo y, si tampoco, se usa su último
    histórico bueno guardado en disco (así un precio que falla no deja la cartera incompleta)."""
    key = ",".join(sorted(set(symbols)))
    cached = _cache.get(key)
    if not (cached and time.time() - cached[0] < CACHE_TTL):
        import yfinance as yf
        syms = sorted(set(symbols))
        try:
            raw = yf.download(syms, period="18mo", auto_adjust=True, progress=False)
            df = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) or "Close" in raw.columns else raw
            if isinstance(df, pd.Series):
                df = df.to_frame(syms[0])
        except Exception as e:
            logger.warning("Descarga en bloque fallida (%s): se reintenta valor a valor", e)
            df = pd.DataFrame()
        df = df.sort_index()
        # vacíos, o cortados: Yahoo a veces devuelve solo los últimos días de un valor en la descarga en bloque, y ese
        # hueco se tomaría como 0 € al inicio de la semana o del año (ganancias falsas de cientos de %)
        bad = [s for s in syms if s not in df.columns or df[s].dropna().empty or _truncated(df[s], df.index)]
        if bad:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=6) as pool:
                retried = dict(zip(bad, pool.map(_single_history, bad)))
            backup = _load_backup()
            for sym, series in retried.items():
                current = df[sym].dropna() if sym in df.columns else None
                if current is not None and current.empty:
                    current = None
                if current is not None:
                    # cortado: se completa con el reintento o, si no, con el histórico guardado; si tampoco hay más
                    # (salida a bolsa reciente), se deja como está
                    longer = series if series is not None and series.index[0] < current.index[0] else backup.get(sym)
                    if longer is None or longer.index[0] >= current.index[0]:
                        continue
                    logger.warning("Histórico de %s cortado en la descarga en bloque (desde %s): se completa", sym, current.index[0].date())
                    series = current.combine_first(longer)
                if series is None and sym in backup:
                    series = backup[sym]
                    logger.warning("Sin precio de %s en Yahoo: se usa su último histórico guardado (hasta %s)", sym, series.index[-1].date())
                if series is not None:
                    # Yahoo puede haber devuelto la columna vacía (todo NaN): se sustituye
                    df = df.drop(columns=[sym], errors="ignore")
                    df = df.join(series.rename(sym), how="outer") if not df.empty else series.to_frame(sym)
            df = df.sort_index()
        # Yahoo a veces deja vacío el cierre de hoy de bolsas que ya han cotizado hoy (p. ej. Tokio): se completa
        from app.application.services.session_fill import fill_today
        df = fill_today(df)
        good = {s: df[s].dropna() for s in syms if s in df.columns and not df[s].dropna().empty}
        try:
            _save_backup(good)
        except Exception as e:
            logger.warning("No se pudo guardar el respaldo de precios: %s", e)
        cached = _cache[key] = (time.time(), df.ffill(), df)
    return cached[1] if fill else cached[2]


def stale_symbols(df: pd.DataFrame, symbols: List[str], days: int = 4) -> Dict[str, str]:
    """{valor: fecha del último precio} de los que van con datos viejos (respaldo) frente al resto."""
    if df is None or df.empty:
        return {}
    latest = df.index.max()
    out = {}
    for s in symbols:
        if s in df.columns:
            last = df[s].last_valid_index()
            if last is not None and (latest - last).days > days:
                out[s] = last.date().isoformat()
    return out


def period_starts(index: pd.DatetimeIndex) -> Dict[str, pd.Timestamp]:
    """Cierre de referencia de cada periodo: la sesión anterior (1D), el último cierre de hace 7 días (1S), de hace un
    mes (1M) y de hace un año (1A), corridos, y el último cierre antes del 1 de enero (YTD)."""
    now = index[-1]
    starts = {"1d": index[-2] if len(index) > 1 else index[-1]}
    for key, boundary in (("1w", now - pd.Timedelta(days=7)), ("1m", now - pd.DateOffset(months=1)),
                          ("ytd", pd.Timestamp(year=now.year, month=1, day=1) - pd.Timedelta(days=1)),
                          ("1y", now - pd.DateOffset(years=1))):
        before = index[index <= boundary]
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
                      benchmark: Optional[str] = None, rolling_days: Iterable[int] = ()) -> Dict[str, Any]:
    """Rentabilidad de la cartera en los periodos de period_starts (1D, 1S, 1M, YTD, 1A), RESPETANDO LAS FECHAS DE
    COMPRA Y VENTA: las acciones al inicio del periodo se reconstruyen con las operaciones y las compras/ventas dentro del
    periodo son aportaciones/retiradas (Modified Dietz), así una compra no cuenta como ganancia.
    transactions: [{ticker, date, type BUY|SELL, shares, price, currency, fx}] (fx = EUR por unidad, opcional).
    rolling_days: además, periodos corridos («7d», «30d»…): desde el último cierre de hace N días naturales."""
    tickers = sorted({t["ticker"] for t in transactions if t["ticker"] in df.columns})
    if not tickers:
        return {"total_eur": 0.0, "periods": {}, "positions": {}}
    index = df[tickers].dropna(how="all").index
    now, starts = index[-1], period_starts(index)
    for n in rolling_days:
        before = index[index <= now - pd.Timedelta(days=n)]
        starts[f"{n}d"] = before[-1] if len(before) else index[0]
    by_ticker = {t: [x for x in transactions if x["ticker"] == t] for t in tickers}

    def price_fx(t: str, when) -> Optional[tuple]:
        """(precio en su divisa, € por unidad de esa divisa) al cierre de `when`."""
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
        return (price, float(fxv.iloc[-1])) if len(fxv) else None

    def price_eur(t: str, when) -> Optional[float]:
        pf = price_fx(t, when)
        return pf[0] * pf[1] if pf else None

    def shares_at(t: str, when: str) -> float:
        total = 0.0
        for x in by_ticker[t]:
            if x["date"][:10] <= when:
                sh = adjusted_shares(x, splits.get(t, []))
                total += sh if x["type"] == "BUY" else -sh
        return max(total, 0.0)

    def flow_fx(x: Dict[str, Any]) -> Optional[float]:
        """€ por unidad de la divisa de la operación, el día de la operación."""
        cur = x.get("currency") or "USD"
        if cur == "GBp":
            cur = "GBP"
        fx = x.get("fx") or (1.0 if cur == "EUR" else None)
        if fx is None:
            f = _fx_series(df, cur)
            fxv = f.loc[:x["date"][:10]].dropna() if f is not None else []
            fx = float(fxv.iloc[-1]) if len(fxv) else None
        return fx

    def flow_eur(x: Dict[str, Any]) -> Optional[float]:
        price = x["price"] / 100 if x.get("currency") == "GBp" else x["price"]
        fx = flow_fx(x)
        if fx is None:
            return None
        amount = x["shares"] * price * fx
        return amount if x["type"] == "BUY" else -amount

    now_s = now.date().isoformat()
    current = {t: shares_at(t, now_s) for t in tickers}
    values_now = {t: current[t] * (price_eur(t, now) or 0) for t in tickers if current[t] > 0}
    # posiciones sin precio o sin tipo de cambio (Yahoo falló): cuentan 0 € y los pesos no son fiables
    missing = sorted(t for t in values_now if price_eur(t, now) is None)
    total_now = sum(values_now.values())
    periods, contrib, pnl_eur = {}, {t: {} for t in tickers}, {t: {} for t in tickers}
    for key, start in starts.items():
        start_s, span = start.date().isoformat(), max((now - start).days, 1)
        v0_total, pnl_total, weighted_flows, fx_total = 0.0, 0.0, 0.0, 0.0
        pnl, incomplete = {}, []
        for t in tickers:
            sh0 = shares_at(t, start_s)
            p0 = price_eur(t, start)
            if sh0 > 0 and p0 is None:
                # sin precio al inicio del periodo: contarlo a 0 € haría de toda la posición una ganancia
                incomplete.append(t)
                continue
            v0 = sh0 * (p0 or 0)
            v1 = values_now.get(t, 0.0)
            flows = [(x, flow_eur(x)) for x in by_ticker[t] if start_s < x["date"][:10] <= now_s]
            net = sum(f for _, f in flows if f is not None)
            weighted_flows += sum(f * (now - pd.Timestamp(x["date"][:10])).days / span for x, f in flows if f is not None)
            if sh0 or flows:
                pnl[t] = v1 - v0 - net
                # efecto divisa: lo que movió el cambio desde el inicio (o desde cada compra/venta) sobre el precio de hoy;
                # el resto del resultado es lo que hicieron las empresas en su moneda
                pf_now, pf0 = price_fx(t, now), price_fx(t, start)
                if pf_now:
                    if sh0 and pf0:
                        fx_total += sh0 * pf_now[0] * (pf_now[1] - pf0[1])
                    for x, f in flows:
                        fxf = flow_fx(x)
                        if f is not None and fxf:
                            sh = adjusted_shares(x, splits.get(t, []))
                            fx_total += (sh if x["type"] == "BUY" else -sh) * pf_now[0] * (pf_now[1] - fxf)
            v0_total += v0
        pnl_total = sum(pnl.values())
        denom = v0_total + weighted_flows
        periods[key] = {"pct": 100 * pnl_total / denom if denom > 0 else None, "abs_eur": pnl_total,
                        "fx_eur": fx_total, "fx_pct": 100 * fx_total / denom if denom > 0 else None}
        if incomplete:
            periods[key]["incomplete"] = incomplete   # quedan fuera del cálculo de este periodo
        for t in tickers:
            contrib[t][key] = 100 * pnl[t] / denom if t in pnl and denom > 0 else None
            pnl_eur[t][key] = pnl.get(t)   # ganancia o pérdida en euros del periodo (sin contar compras ni ventas)
        if benchmark and benchmark in df.columns:
            bs = df[benchmark].dropna()
            b0 = bs.loc[:start]
            periods[key]["benchmark_pct"] = 100 * (bs.iloc[-1] / b0.iloc[-1] - 1) if len(b0) else None
    pos = {t: {"value_eur": v, "weight": 100 * v / total_now if total_now else 0.0, "contribution": contrib[t], "pnl_eur": pnl_eur[t]}
           for t, v in values_now.items()}
    return {"total_eur": total_now, "as_of": now.date().isoformat(), "periods": periods, "positions": pos, "missing": missing}


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
    full_year = (close.index[-1] - close.index[0]).days >= MIN_52W_SPAN_DAYS
    ma200 = float(close.iloc[-200:].mean()) if len(close) >= 200 else None
    st = {
        "price": float(last), "change": ch, "sigma_d": sigma,
        "z_day": (ch["1d"] / 100) / sigma if sigma and ch["1d"] is not None else None,
        "r5": r5, "z_week": (r5 / 100) / (sigma * math.sqrt(5)) if sigma and r5 is not None else None,
        "high_52w": hi, "low_52w": lo, "from_high_pct": 100 * (last / hi - 1), "from_low_pct": 100 * (last / lo - 1),
        "new_high": full_year and bool(w52.iloc[-NEW_EXTREME_SESSIONS:].max() >= hi),
        "new_low": full_year and bool(w52.iloc[-NEW_EXTREME_SESSIONS:].min() <= lo),
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
    market = (f" · propio: el mercado {es(st['bench_week_pct'], 1, sign=True)}% (beta {es(st['beta'], 1)})" if own
              else (f" · mercado {es(st['bench_week_pct'], 1, sign=True)}%" if st.get("bench_week_pct") is not None else ""))
    sig = lambda z: f", {es(abs(z), 1)}σ para este valor" if z is not None else ""
    if d1 is not None and ((zd is not None and abs(zd) >= ABNORMAL_DAY_SIGMA) or abs(d1) >= BIG_MOVE_DAY_PCT):
        add("anormal", "down" if d1 < 0 else "up", f"{es(d1, 1, sign=True)}% en el día{sig(zd)}", st.get("date"))
    if r5 is not None and ((zw is not None and abs(zw) >= ABNORMAL_WEEK_SIGMA) or abs(r5) >= BIG_MOVE_5D_PCT):
        add("anormal", "down" if r5 < 0 else "up", f"{es(r5, 1, sign=True)}% en 5 sesiones{sig(zw)}{market}", st.get("date"), st.get("r5_from"))
    elif own:
        add("propio", "down" if zo < 0 else "up", f"Movimiento propio: {es(st['own_week_pct'], 1, sign=True)}% en 5 sesiones descontando el mercado ({es(st['bench_week_pct'], 1, sign=True)}%)",
            st.get("date"), st.get("r5_from"))
    if st.get("new_low"):
        add("nivel", "down", f"Nuevo mínimo de 52 semanas ({es(st['low_52w'], 2)})", st.get("low_date"))
    elif st.get("new_high"):
        add("nivel", "up", f"Nuevo máximo de 52 semanas ({es(st['high_52w'], 2)})", st.get("high_date"))
    if live:
        base = next((s for s in live["scenarios"] if str(s["name"]).lower() == "base"), None)
        intact = thesis and thesis.get("verdict") in ("REFORZADA", "INTACTA")
        if base:
            e15 = base["entry_prices"].get(15)
            if intact and e15 and st["price"] <= e15:
                add("oportunidad", "up", f"Bajo precio de entrada ({es(e15, 0)}, al 15%) · tesis {thesis['verdict'].lower()}")
            if base["cagr"] < MIN_CAGR_PCT:
                add("cara", "down", f"Poco retorno a este precio: CAGR implícito {es(base['cagr'], 1)}% (<{es(MIN_CAGR_PCT, 0)}%)")
    if weight is not None and weight > MAX_WEIGHT_PCT:
        add("concentración", "neutral", f"Pesa el {es(weight, 1)}% de la cartera (>{es(MAX_WEIGHT_PCT, 0)}%)")
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


def all_time_high(ticker: str) -> Optional[Dict[str, Any]]:
    """Máximo histórico de cierre (ajustado por splits y dividendos) y su fecha, en la unidad de cotización.
    Una descarga de toda la serie por valor; caché en disco de 24 h."""
    from app.application.services import market_cache

    def download():
        try:
            import yfinance as yf
            close = yf.Ticker(ticker).history(period="max", auto_adjust=True)["Close"].dropna()
            if close.empty:
                return None
            return {"high": float(close.max()), "date": close.idxmax().date().isoformat()}
        except Exception:
            return None
    return market_cache.cached("all_time_high_v1", ticker, download, ttl_hours=24)


def from_all_time_high(ath: Optional[Dict[str, Any]], price: Optional[float]) -> Optional[float]:
    """Caída (en %) desde el máximo histórico; si hoy cotiza por encima, 0."""
    if not ath or not price:
        return None
    high = max(ath["high"], price)
    return 100 * (price / high - 1) if high > 0 else None
