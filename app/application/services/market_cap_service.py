"""Capitalización bursátil en USD y su categoría (mega, large, mid, small, micro, nano cap).

Se calcula con las acciones en circulación (guardadas en el activo, se refrescan cada 30 días) por el último precio
en caché y el tipo de cambio de su moneda: así está al día con cada precio y es comparable entre bolsas (Yahoo da
`marketCap` en la moneda de cotización: el de una suiza viene en francos). Los umbrales son los habituales en USD.
"""
import logging
import threading
import time
from datetime import date, timedelta
from typing import Any, Callable, Dict, Iterable, Optional

from app.application.services.thesis_service import MINOR_UNITS

logger = logging.getLogger(__name__)

# (clave, nombre, desde USD)
TIERS = [
    ("mega", "Mega cap", 200e9),
    ("large", "Large cap", 10e9),
    ("mid", "Mid cap", 2e9),
    ("small", "Small cap", 300e6),
    ("micro", "Micro cap", 50e6),
    ("nano", "Nano cap", 0.0),
]
TIER_RANGES = {"mega": "≥ 200.000 M$", "large": "10.000 – 200.000 M$", "mid": "2.000 – 10.000 M$",
               "small": "300 – 2.000 M$", "micro": "50 – 300 M$", "nano": "< 50 M$"}
SHARES_KEY, SHARES_DATE_KEY = "shares_outstanding", "shares_updated"
REFRESH_DAYS = 30


def tier(cap_usd: Optional[float]) -> Optional[Dict[str, str]]:
    if not cap_usd or cap_usd <= 0:
        return None
    for key, name, floor in TIERS:
        if cap_usd >= floor:
            return {"key": key, "name": name, "range": TIER_RANGES[key]}
    return None


def _main_currency(currency: Optional[str]) -> (str, float):
    """Moneda principal y divisor de la unidad de cotización (peniques de Londres → libras)."""
    cur = currency or "USD"
    main, factor = MINOR_UNITS.get(cur, (cur, 1.0))
    return main, factor


def _eur_rate(currency: str, prices: Dict[str, Any]) -> Optional[float]:
    if currency == "EUR":
        return 1.0
    p = (prices.get(f"{currency}EUR=X") or {}).get("price")
    return float(p) if p else None


def to_usd(amount: Optional[float], currency: Optional[str], prices: Dict[str, Any]) -> Optional[float]:
    """Importe en la unidad de cotización → USD, con los cruces a EUR de la caché de precios."""
    if amount is None:
        return None
    main, factor = _main_currency(currency)
    amount = amount / factor
    if main == "USD":
        return amount
    cur_eur, usd_eur = _eur_rate(main, prices), _eur_rate("USD", prices)
    if not cur_eur or not usd_eur:
        return None
    return amount * cur_eur / usd_eur


def market_cap_usd(shares: Optional[float], price: Optional[float], currency: Optional[str],
                   prices: Dict[str, Any]) -> Optional[float]:
    if not shares or not price:
        return None
    return to_usd(float(shares) * float(price), currency, prices)


def describe(shares, price, currency, prices) -> Optional[Dict[str, Any]]:
    """{usd, label (6,6 B$), tier} o None si falta algún dato."""
    usd = market_cap_usd(shares, price, currency, prices)
    t = tier(usd)
    return {"usd": usd, "label": fmt_usd(usd), "tier": t} if t else None


def fmt_usd(v: Optional[float]) -> str:
    """Importe compacto en dólares, con decimales españoles: 2,85 T$ · 6,6 B$ · 850 M$."""
    from utils.number_format import es
    if not v:
        return "–"
    if v >= 1e12:
        return f"{es(v / 1e12, 2)} T$"
    if v >= 1e9:
        return f"{es(v / 1e9, 1 if v < 100e9 else 0)} B$"
    return f"{es(v / 1e6, 0)} M$"


def shares_from_info(info: Dict[str, Any]) -> Optional[float]:
    """Acciones en circulación de `yf.Ticker.info`. Primero las «implícitas», que suman todas las clases (GOOGL A+C,
    BRK A+B, ASTS A+B+C: con solo `sharesOutstanding` la capitalización sale corta); si no, capitalización / precio
    (Yahoo da `marketCap` en la moneda principal y el precio en la de cotización: peniques en Londres)."""
    for k in ("impliedSharesOutstanding", "sharesOutstanding"):
        v = info.get(k)
        if v and v > 0:
            return float(v)
    cap, price = info.get("marketCap"), info.get("currentPrice") or info.get("regularMarketPrice")
    if cap and price:
        return float(cap) * _main_currency(info.get("currency"))[1] / float(price)
    return None


def needs_refresh(data: Dict[str, Any], today: Optional[date] = None) -> bool:
    if not data.get(SHARES_KEY):
        return True
    try:
        return date.fromisoformat(data.get(SHARES_DATE_KEY) or "") < (today or date.today()) - timedelta(days=REFRESH_DAYS)
    except ValueError:
        return True


def save_shares(save: Callable[[str, str, Any], None], ticker: str, shares: Optional[float]) -> None:
    if shares:
        save(ticker, SHARES_KEY, shares)
        save(ticker, SHARES_DATE_KEY, date.today().isoformat())


_backfill_lock = threading.Lock()
_backfill_running = False


def backfill_in_background(tickers: Iterable[str]) -> bool:
    """Descarga en un hilo las acciones de los valores que no las tienen (o son de hace más de 30 días).
    Una sola pasada a la vez; False si ya hay una en marcha."""
    global _backfill_running
    todo = sorted(set(t for t in tickers if t))
    if not todo:
        return False
    with _backfill_lock:
        if _backfill_running:
            return False
        _backfill_running = True

    def run():
        global _backfill_running
        import yfinance as yf
        from app.infrastructure.db.database import SessionLocal
        from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
        db = SessionLocal()
        try:
            repo, done = SqlAlchemyAssetRepository(db), 0
            for t in todo:
                try:
                    save_shares(repo.save_asset_data, t, shares_from_info(yf.Ticker(t).info or {}))
                    done += 1
                except Exception as e:
                    logger.warning("Acciones en circulación de %s: %s", t, str(e)[:120])
                time.sleep(0.3)   # sin agobiar a Yahoo
            logger.info("Capitalización: acciones en circulación de %d/%d valores", done, len(todo))
        finally:
            db.close()
            with _backfill_lock:
                _backfill_running = False

    threading.Thread(target=run, daemon=True, name="market-cap-backfill").start()
    return True
