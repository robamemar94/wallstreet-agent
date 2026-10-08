"""Previsión de dividendos de los próximos 12 meses para las posiciones abiertas.

Por empresa: lo ya anunciado (yfinance: próxima fecha ex y de pago) y, para el resto del año, el mismo calendario que
los últimos 12 meses (cada fecha ex desplazada un año). El importe estimado es el último pagado si reparte 4 o más veces
al año (las subidas se recogen al momento) y, si reparte menos, el de ese mismo pago del año pasado (a cuenta y
complementario suelen ser distintos). Las acciones son las de hoy. Todo en euros al cambio actual.

Neto: retención en origen del país de la empresa y, encima, el 19 % del IRPF menos la deducción por doble imposición
(lo retenido fuera, con el tope del convenio, normalmente el 15 %)."""
import datetime
import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

HORIZON_DAYS = 365
SAME_SLOT_DAYS = 45            # un pago anunciado o ya cobrado a menos de esto de una fecha prevista la sustituye
FREQUENT_PAYMENTS = 4          # con 4+ pagos al año, todos se estiman con el último importe
SPAIN_RATE = 0.19              # IRPF del ahorro, primer tramo
TREATY_CAP = 0.15              # deducción máxima por doble imposición (convenios habituales)

# Retención en origen que suele aplicar el bróker a un residente en España (W-8BEN en EE. UU.)
WITHHOLDING = {
    "Spain": 0.19, "United States": 0.15, "United Kingdom": 0.0, "Ireland": 0.25, "France": 0.25, "Germany": 0.26375,
    "Netherlands": 0.15, "Switzerland": 0.35, "Denmark": 0.27, "Sweden": 0.30, "Norway": 0.25, "Finland": 0.35,
    "Italy": 0.26, "Belgium": 0.30, "Portugal": 0.25, "Austria": 0.275, "Luxembourg": 0.15, "Poland": 0.19,
    "Greece": 0.05, "Canada": 0.25, "Mexico": 0.10, "Japan": 0.15315, "China": 0.10, "Hong Kong": 0.0,
    "Cayman Islands": 0.0, "Australia": 0.0, "Uruguay": 0.07, "Brazil": 0.0, "Singapore": 0.0, "Israel": 0.25,
}
DEFAULT_WITHHOLDING = 0.15

FREQUENCY = {12: "Mensual", 4: "Trimestral", 3: "Cuatrimestral", 2: "Semestral", 1: "Anual"}


def withholding_rate(country: Optional[str]) -> float:
    return WITHHOLDING.get(country or "", DEFAULT_WITHHOLDING)


def net_factor(country: Optional[str]) -> float:
    """Parte del dividendo bruto que queda tras la retención en origen y el IRPF español."""
    if country == "Spain":
        return 1 - SPAIN_RATE
    wht = withholding_rate(country)
    return 1 - wht - max(0.0, SPAIN_RATE - min(wht, TREATY_CAP))


def _d(v) -> Optional[datetime.date]:
    if not v:
        return None
    return v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v)[:10])


def _plus_year(d: datetime.date) -> datetime.date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:          # 29 de febrero
        return d.replace(year=d.year + 1, day=28)


def forecast_ticker(history: Dict[str, float], today: datetime.date,
                    announced: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Pagos por acción previstos en los próximos 12 meses: [{ex_date, pay_date, amount, estimated}].
    history: {fecha ex: importe por acción} (yfinance, ajustado por splits).
    announced: {ex_date, pay_date, amount} del próximo dividendo, si yfinance lo publica (fecha ex futura, o ya pasada
    con el pago pendiente)."""
    past = sorted((_d(k), float(v)) for k, v in history.items() if v and v == v and float(v) > 0)
    end = today + datetime.timedelta(days=HORIZON_DAYS)
    out: List[Dict[str, Any]] = []
    ann_ex = _d((announced or {}).get("ex_date"))
    ann_pay = _d((announced or {}).get("pay_date"))
    if announced and (announced.get("amount") or 0) > 0 and ((ann_ex and ann_ex >= today) or (ann_pay and ann_pay >= today)):
        out.append({"ex_date": ann_ex or ann_pay, "pay_date": ann_pay, "amount": float(announced["amount"]), "estimated": False})
    last_year = [(d, a) for d, a in past if today - datetime.timedelta(days=HORIZON_DAYS) < d <= today]
    latest = past[-1][1] if past else None
    frequent = len(last_year) >= FREQUENT_PAYMENTS
    for d, amount in last_year:
        when = _plus_year(d)
        if not (today < when <= end):
            continue
        # ya anunciado, o ya cobrado este año antes de lo habitual
        if ann_ex and abs((when - ann_ex).days) <= SAME_SLOT_DAYS:
            continue
        if any(d < h and abs((when - h).days) <= SAME_SLOT_DAYS for h, _ in past):
            continue
        out.append({"ex_date": when, "pay_date": None, "amount": latest if frequent else amount, "estimated": True})
    return sorted(out, key=lambda e: e["ex_date"])


def frequency_label(payments_per_year: int) -> str:
    return FREQUENCY.get(payments_per_year, f"{payments_per_year} al año" if payments_per_year else "—")


def build_forecast(positions: List[Dict[str, Any]], histories: Dict[str, Dict[str, float]],
                   announced: Dict[str, Optional[Dict[str, Any]]], today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """positions: [{ticker, name, shares, shares_at(date)->float, currency, fx (EUR por unidad), country, cost_eur,
    value_eur}]. Las cotizaciones en peniques (GBp) se pasan a libras.
    Devuelve los pagos previstos en euros, la suma por mes (anunciado / estimado), totales y el resumen por empresa."""
    today = today or datetime.date.today()
    events, by_ticker = [], []
    for p in positions:
        if not p.get("fx"):
            continue
        unit = 0.01 if p.get("currency") == "GBp" else 1.0
        net = net_factor(p.get("country"))
        rows = forecast_ticker(histories.get(p["ticker"]) or {}, today, announced.get(p["ticker"]))
        total = 0.0
        for e in rows:
            # un pago anunciado con la fecha ex ya pasada solo es nuestro si teníamos las acciones ese día
            shares = p["shares_at"](e["ex_date"]) if e["ex_date"] < today else p["shares"]
            if shares <= 0.0001:
                continue
            gross = e["amount"] * unit * shares * p["fx"]
            total += gross
            events.append({**e, "ticker": p["ticker"], "name": p.get("name") or p["ticker"], "shares": shares,
                           "amount_local": e["amount"] * shares, "currency": p.get("currency"),
                           "gross_eur": gross, "net_eur": gross * net,
                           "date": e["pay_date"] or e["ex_date"]})   # día de cobro si se conoce; si no, la fecha ex
        if total:
            mine = [e for e in events if e["ticker"] == p["ticker"]]
            by_ticker.append({
                "ticker": p["ticker"], "name": p.get("name") or p["ticker"], "frequency": frequency_label(len(mine)),
                "next": min(mine, key=lambda e: e["date"]), "gross_eur": total, "net_eur": total * net,
                "withholding": withholding_rate(p.get("country")) if p.get("country") != "Spain" else SPAIN_RATE,
                "country": p.get("country"),
                "yield_on_cost": 100 * total / p["cost_eur"] if p.get("cost_eur") else None,
                "yield_now": 100 * total / p["value_eur"] if p.get("value_eur") else None,
                "estimated_only": all(e["estimated"] for e in mine),
            })
    events.sort(key=lambda e: (e["date"], e["ticker"]))
    months = []
    first = today.replace(day=1)
    for i in range(13):
        y, m = first.year + (first.month - 1 + i) // 12, (first.month - 1 + i) % 12 + 1
        key = f"{y}-{m:02d}"
        evs = [e for e in events if e["date"].strftime("%Y-%m") == key]
        if i == 12 and not evs:
            break
        months.append({"month": key, "announced_eur": sum(e["gross_eur"] for e in evs if not e["estimated"]),
                       "estimated_eur": sum(e["gross_eur"] for e in evs if e["estimated"]), "events": len(evs),
                       # desglose por empresa para el detalle al pulsar la barra
                       "items": [{"ticker": e["ticker"], "name": e["name"], "date": e["date"].isoformat(),
                                  "kind": "pago" if e["pay_date"] else "ex", "estimated": e["estimated"],
                                  "gross_eur": e["gross_eur"], "net_eur": e["net_eur"]} for e in evs]})
    by_ticker.sort(key=lambda r: -r["gross_eur"])
    gross = sum(r["gross_eur"] for r in by_ticker)
    cost = sum(p.get("cost_eur") or 0 for p in positions)
    value = sum(p.get("value_eur") or 0 for p in positions)
    return {
        "events": events, "months": months, "by_ticker": by_ticker,
        "total_gross": gross, "total_net": sum(r["net_eur"] for r in by_ticker),
        "announced_gross": sum(e["gross_eur"] for e in events if not e["estimated"]),
        "yield_on_cost": 100 * gross / cost if cost else None, "yield_now": 100 * gross / value if value else None,
    }


def shares_held_at(ledger_entries, as_of: datetime.date) -> float:
    """Acciones en cartera a fecha `as_of`, reconstruidas con el ledger (compras, ventas y splits)."""
    balance = 0.0
    for entry in sorted(ledger_entries, key=lambda e: e.date):
        if _d(entry.date) > as_of:
            break
        kind = getattr(entry.type, "value", entry.type)
        if kind == "SPLIT":
            balance *= entry.shares
        elif kind == "BUY":
            balance += entry.shares
        elif kind == "SELL":
            balance -= entry.shares
    return balance


def load_histories() -> Dict[str, Dict[str, float]]:
    """Histórico de dividendos por acción que guarda la reconstrucción de la cartera."""
    from app.infrastructure.config import DIV_CACHE_FILE
    if not os.path.exists(DIV_CACHE_FILE):
        return {}
    try:
        with open(DIV_CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def portfolio_forecast(portfolio_service, asset_repo, prices: Dict[str, Dict[str, Any]],
                       raw: Callable[[str], Dict[str, Any]], today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """Previsión de la cartera abierta. prices: la caché de cotizaciones (ticker y «XXXEUR=X» → {price}).
    raw(ticker): datos de yfinance con el próximo dividendo anunciado (ex_div, pay_date, div_value)."""
    today = today or datetime.date.today()
    positions, announced = [], {}
    for item in portfolio_service.get_active_portfolio():
        if item.shares <= 0.0001:
            continue
        data = asset_repo.get_asset_data(item.ticker) or {}
        currency = data.get("currency") or item.currency_code or "USD"
        ccy = "GBP" if currency == "GBp" else currency
        fx = 1.0 if ccy == "EUR" else (prices.get(f"{ccy}EUR=X") or {}).get("price")
        price = (prices.get(item.ticker) or {}).get("price")
        ledger = portfolio_service.get_ledger(item.ticker)
        positions.append({
            "ticker": item.ticker, "name": data.get("company_name") or item.ticker, "shares": item.shares,
            "shares_at": lambda d, ledger=ledger: shares_held_at(ledger, d), "currency": currency, "fx": fx,
            "country": data.get("country"), "cost_eur": item.total_cost_eur,
            "value_eur": item.shares * price * (0.01 if currency == "GBp" else 1.0) * fx if price and fx else None,
        })
    def fetch(ticker):
        try:
            return ticker, raw(ticker) or {}
        except Exception:
            return ticker, {}
    with ThreadPoolExecutor(max_workers=8) as pool:   # caché en disco de 12 h; en frío, una llamada lenta por empresa
        for ticker, r in pool.map(fetch, [p["ticker"] for p in positions]):
            announced[ticker] = {"ex_date": r.get("ex_div"), "pay_date": r.get("pay_date"), "amount": r.get("div_value")}
    return build_forecast(positions, load_histories(), announced, today)
