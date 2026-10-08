"""
Precio de hoy cuando Yahoo no lo da en la serie diaria.

Con algunas bolsas (p. ej. Tokio) Yahoo deja vacía la vela diaria de hoy aunque la sesión ya se haya celebrado: la
app lo tomaba como «su mercado no ha abierto hoy». Si, según su horario, la bolsa ya ha abierto hoy (hoy = el día en
España) y falta el cierre de hoy, se completa con el último precio intradía de hoy (una descarga para todos).
"""
import datetime
import logging
from typing import Iterable, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

HOME = ZoneInfo("Europe/Madrid")
OPEN_MARGIN_MIN = 20   # Yahoo tarda unos minutos en dar el primer precio de la sesión

# sufijo del ticker → (zona horaria de la bolsa, hora de apertura local)
_EUROPE = ("Europe/Madrid", datetime.time(9, 0))
EXCHANGES = {
    "": ("America/New_York", datetime.time(9, 30)),
    ".TO": ("America/Toronto", datetime.time(9, 30)), ".V": ("America/Toronto", datetime.time(9, 30)),
    ".MX": ("America/Mexico_City", datetime.time(8, 30)), ".SA": ("America/Sao_Paulo", datetime.time(10, 0)),
    ".L": ("Europe/London", datetime.time(8, 0)), ".IR": ("Europe/Dublin", datetime.time(8, 0)),
    **{s: _EUROPE for s in (".MC", ".PA", ".AS", ".DE", ".F", ".MI", ".BR", ".LS", ".VI", ".SW", ".ST", ".OL", ".CO", ".HE")},
    ".T": ("Asia/Tokyo", datetime.time(9, 0)), ".HK": ("Asia/Hong_Kong", datetime.time(9, 30)),
    ".SS": ("Asia/Shanghai", datetime.time(9, 30)), ".SZ": ("Asia/Shanghai", datetime.time(9, 30)),
    ".KS": ("Asia/Seoul", datetime.time(9, 0)), ".TW": ("Asia/Taipei", datetime.time(9, 0)),
    ".SI": ("Asia/Singapore", datetime.time(9, 0)), ".NS": ("Asia/Kolkata", datetime.time(9, 15)),
    ".AX": ("Australia/Sydney", datetime.time(10, 0)), ".NZ": ("Pacific/Auckland", datetime.time(10, 0)),
}


def exchange_of(ticker: str):
    """(zona, apertura) de la bolsa del ticker; None para índices (^), divisas (=X) y cripto (-USD), que no tienen."""
    if ticker.startswith("^") or ticker.endswith("=X") or "-" in ticker and ticker.split("-")[-1] in ("USD", "EUR"):
        return None
    suffix = "." + ticker.rsplit(".", 1)[1] if "." in ticker else ""
    tz, opens = EXCHANGES.get(suffix, EXCHANGES[""] if not suffix else (None, None))
    return (ZoneInfo(tz), opens) if tz else None


def opened_today(ticker: str, now: Optional[datetime.datetime] = None) -> bool:
    """¿Ha empezado ya la sesión de hoy (la fecha de hoy en España) en la bolsa de este valor? Tokio abre a las 2:00
    hora española, así que a las 23:30 su sesión de hoy ya se celebró y cuenta como «hoy»."""
    ex = exchange_of(ticker)
    if not ex:
        return False
    tz, opens = ex
    now = now or datetime.datetime.now(HOME)
    day = now.astimezone(HOME).date()          # «hoy» es nuestro día: la sesión de esa fecha en su bolsa
    if day.weekday() >= 5:
        return False
    start = datetime.datetime.combine(day, opens, tz) + datetime.timedelta(minutes=OPEN_MARGIN_MIN)
    return now >= start


def missing_today(closes: pd.DataFrame, symbols: Iterable[str], now: Optional[datetime.datetime] = None) -> List[str]:
    """Valores sin cierre de hoy cuya bolsa ya ha abierto hoy."""
    today = pd.Timestamp((now or datetime.datetime.now(HOME)).astimezone(HOME).date())
    out = []
    for s in symbols:
        if s not in closes.columns or not opened_today(s, now):
            continue
        last = closes[s].last_valid_index()
        if last is not None and pd.Timestamp(last).normalize() < today:
            out.append(s)
    return out


def fill_today(closes: pd.DataFrame, symbols: Optional[Iterable[str]] = None) -> pd.DataFrame:
    """Completa el cierre de hoy de los valores a los que Yahoo se lo ha dejado vacío (con el último precio intradía)."""
    if closes is None or closes.empty:
        return closes
    syms = missing_today(closes, symbols if symbols is not None else closes.columns)
    if not syms:
        return closes
    try:
        import yfinance as yf
        intra = yf.download(syms, period="1d", interval="5m", progress=False, auto_adjust=True)["Close"]
        if isinstance(intra, pd.Series):
            intra = intra.to_frame(syms[0])
    except Exception as e:
        logger.warning("Precio intradía de hoy no disponible (%s): %s", ", ".join(syms), e)
        return closes
    today = pd.Timestamp(datetime.datetime.now(HOME).date())
    closes = closes.copy()
    filled = []
    for s in syms:
        col = intra[s].dropna() if s in intra.columns else pd.Series(dtype=float)
        if col.empty:
            continue
        ts = col.index[-1]
        local_day = (ts.tz_convert(exchange_of(s)[0]) if ts.tzinfo else ts).date()
        if local_day != today.date():
            continue   # el último intradía es de otro día: de verdad no ha cotizado hoy
        if today not in closes.index:
            closes.loc[today] = float("nan")
            closes = closes.sort_index()
        closes.loc[today, s] = float(col.iloc[-1])
        filled.append(s)
    if filled:
        logger.info("Cierre de hoy completado con el intradía (Yahoo lo dejó vacío): %s", ", ".join(filled))
    return closes
