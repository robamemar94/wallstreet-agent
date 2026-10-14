"""
Liquidez del bróker (en euros, una sola cuenta). Sin IA ni internet.

- Se parte de un saldo que el usuario apunta una vez (o cuando no cuadre con el bróker): el «ancla» (saldo al cierre de
  ese día). Desde ahí se lleva sola: las compras restan (importe + comisión), las ventas suman (importe − comisión) y los
  dividendos cobrados suman (bruto menos la retención en origen, que es lo que llega a la cuenta). El usuario solo apunta
  las aportaciones y retiradas.
- Aviso el día 1 de cada mes: «¿Cuánto añades este mes?», hasta que apunta una aportación de ese mes (o lo descarta).
- Es un dato aparte: el valor de la cartera, la rentabilidad y los pesos siguen siendo solo de lo invertido.
Se guarda en settings['cash']: {anchor: {date, balance}, movements: [{id, date, type deposit|withdrawal, amount, note}]}.
"""
import datetime
import uuid
from typing import Any, Dict, List, Optional

SETTINGS_KEY = "cash"
MOVE_TYPES = {"deposit": "Aportación", "withdrawal": "Retirada"}
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# --- Guardado ---------------------------------------------------------------------------------------------------------

def load(db) -> Dict[str, Any]:
    from app.infrastructure.db.models import DBSetting
    row = db.get(DBSetting, SETTINGS_KEY)
    data = dict(row.value) if row and isinstance(row.value, dict) else {}
    data.setdefault("anchor", None)
    data.setdefault("movements", [])
    return data


def save(db, data: Dict[str, Any]) -> None:
    from sqlalchemy.orm.attributes import flag_modified
    from app.infrastructure.db.models import DBSetting
    row = db.get(DBSetting, SETTINGS_KEY)
    if row:
        row.value = data
        flag_modified(row, "value")
    else:
        db.add(DBSetting(key=SETTINGS_KEY, value=data))
    db.commit()


def set_balance(db, amount: float, date: Optional[str] = None) -> Dict[str, Any]:
    """El saldo real del bróker al cierre de `date` (hoy por defecto): desde ahí se lleva solo."""
    data = load(db)
    data["anchor"] = {"date": date or datetime.date.today().isoformat(), "balance": round(float(amount), 2)}
    save(db, data)
    return data


def add_movement(db, kind: str, amount: float, date: Optional[str] = None, note: str = "") -> Dict[str, Any]:
    if kind not in MOVE_TYPES:
        raise ValueError(f"Tipo de movimiento desconocido: {kind}")
    if not amount or amount <= 0:
        raise ValueError("El importe tiene que ser mayor que 0")
    data = load(db)
    move = {"id": uuid.uuid4().hex[:10], "date": date or datetime.date.today().isoformat(), "type": kind,
            "amount": round(float(amount), 2), "note": (note or "").strip()}
    data["movements"].append(move)
    save(db, data)
    return move


def delete_movement(db, move_id: str) -> bool:
    data = load(db)
    before = len(data["movements"])
    data["movements"] = [m for m in data["movements"] if m["id"] != move_id]
    save(db, data)
    return len(data["movements"]) < before


# --- Cálculo ----------------------------------------------------------------------------------------------------------

def _eur(amount: float, currency: Optional[str], fx: Optional[float]) -> float:
    if (currency or "EUR").upper() == "EUR":
        return amount
    if currency == "GBp":
        amount, currency = amount / 100, "GBP"
    if not fx:
        from app.interfaces.views.portfolio_views import get_current_rate
        fx = get_current_rate(currency)
    return amount * (fx or 0.0)


def trade_flows(db) -> List[Dict[str, Any]]:
    """Compras y ventas como movimientos de caja en euros (al cambio del día de la operación, con la comisión)."""
    from app.infrastructure.db.models import DBTransaction
    out = []
    for t in db.query(DBTransaction).all():
        kind = getattr(t.type, "value", t.type)
        if kind not in ("BUY", "SELL"):
            continue
        gross = _eur(t.shares * t.price, t.currency, t.fx_rate_at_purchase)
        fee = t.fee or 0.0
        amount = -(gross + fee) if kind == "BUY" else gross - fee
        out.append({"date": str(t.date)[:10], "type": "buy" if kind == "BUY" else "sell", "ticker": t.ticker, "amount": amount})
    return out


def dividend_flows(db) -> List[Dict[str, Any]]:
    """Dividendos cobrados (del ledger) en euros: el bruto menos la retención en origen, que es lo que llega al bróker."""
    from app.application.services.dividend_forecast import withholding_rate
    from app.domain.models import TransactionType
    from app.infrastructure.db.models import DBAsset, DBLedgerEntry
    rows = db.query(DBLedgerEntry).filter(DBLedgerEntry.type == TransactionType.DIVIDEND).all()
    assets = {a.ticker: a.data or {} for a in db.query(DBAsset).filter(DBAsset.ticker.in_({r.ticker for r in rows})).all()}
    out = []
    for r in rows:
        d = assets.get(r.ticker, {})
        gross = _eur(r.total or 0.0, d.get("currency", "USD"), None)
        out.append({"date": str(r.date)[:10], "type": "dividend", "ticker": r.ticker, "amount": gross * (1 - withholding_rate(d.get("country")))})
    return out


def compute(anchor: Optional[Dict[str, Any]], movements: List[Dict[str, Any]], auto: List[Dict[str, Any]],
            today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """Saldo de hoy = ancla + todo lo posterior a su fecha (aportaciones − retiradas − compras + ventas + dividendos).
    auto: compras, ventas y dividendos [{date, type, ticker, amount (± €)}]. Devuelve el saldo, el desglose desde el
    ancla, el mes en curso (lo aportado) y los últimos movimientos."""
    today = today or datetime.date.today()
    if not anchor:
        return {"configured": False}
    since = anchor["date"]
    manual = [{**m, "amount": m["amount"] if m["type"] == "deposit" else -m["amount"], "manual": True} for m in movements]
    after = [m for m in manual + auto if m["date"] > since and m["date"] <= today.isoformat()]
    parts = {k: sum(m["amount"] for m in after if m["type"] == k) for k in ("deposit", "withdrawal", "buy", "sell", "dividend")}
    month = today.strftime("%Y-%m")
    deposited = sum(m["amount"] for m in manual if m["type"] == "deposit" and m["date"][:7] == month)
    recent = sorted(manual + [m for m in auto if m["date"] > since], key=lambda m: m["date"], reverse=True)
    return {"configured": True, "balance": anchor["balance"] + sum(parts.values()), "anchor": anchor, "since": parts,
            "month": {"key": month, "label": MONTHS[today.month - 1], "deposited": deposited},
            "recent": recent[:12]}


def status(db, today: Optional[datetime.date] = None) -> Dict[str, Any]:
    data = load(db)
    return compute(data["anchor"], data["movements"], trade_flows(db) + dividend_flows(db), today)


def notices(db, today: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
    """Aviso del mes: desde el día 1, «¿cuánto añades este mes?», hasta que se apunta una aportación de ese mes."""
    from utils.number_format import es
    today = today or datetime.date.today()
    st = status(db, today)
    # el mes en que se empieza a llevar no avisa: el saldo de partida ya cuenta lo aportado
    if not st["configured"] or st["month"]["deposited"] > 0 or st["anchor"]["date"][:7] >= st["month"]["key"]:
        return []
    return [{"key": f"liquidez|att|cash_deposit|{st['month']['key']}|", "ticker": None, "kind": "cash_deposit",
             "text": f"Nuevo mes: ¿cuánto añades este {st['month']['label']} a la liquidez del bróker? (ahora {es(st['balance'], 0)} €)",
             "href": "/portfolio#liquidez"}]


def patrimony_series(dates: List[str], values_eur: List[float], nav: List[float], anchor: Optional[Dict[str, Any]],
                     movements: List[Dict[str, Any]], auto: List[Dict[str, Any]]) -> Dict[str, List[Optional[float]]]:
    """La cartera con la liquidez, día a día (mismas fechas que el Rendimiento), desde el saldo de partida (antes: None).
    eur: valor invertido + efectivo. uv: índice por rentabilidad (las aportaciones y retiradas no cuentan, como las
    compras en el de la cartera); el día del ancla vale lo mismo que el de la cartera, para que las curvas salgan juntas.
    cash_pct: % del patrimonio en efectivo."""
    n = len(dates)
    out = {"uv": [None] * n, "eur": [None] * n, "cash_pct": [None] * n}
    if not anchor or not dates:
        return out
    since = anchor["date"]
    manual = [{**m, "amount": m["amount"] if m["type"] == "deposit" else -m["amount"]} for m in movements]
    flows = sorted((m for m in manual + auto if m["date"] > since), key=lambda m: m["date"])
    cash, prev_total, idx, j = anchor["balance"], None, None, 0
    for i, d in enumerate(dates):
        if d < since:
            continue
        external = 0.0
        while j < len(flows) and flows[j]["date"] <= d:   # lo de los días sin sesión cae en la siguiente
            cash += flows[j]["amount"]
            if flows[j]["type"] in MOVE_TYPES:
                external += flows[j]["amount"]
            j += 1
        total = (values_eur[i] or 0.0) + cash
        if idx is None:
            idx = nav[i]
        elif prev_total and prev_total > 0:
            idx *= (total - external) / prev_total
        out["uv"][i], out["eur"][i] = round(idx, 4), round(total, 2)
        out["cash_pct"][i] = round(100 * cash / total, 2) if total > 0 else None
        prev_total = total
    return out


def patrimony(db, dates: List[str], values_eur: List[float], nav: List[float]) -> Dict[str, List[Optional[float]]]:
    data = load(db)
    return patrimony_series(dates, values_eur, nav, data["anchor"], data["movements"], trade_flows(db) + dividend_flows(db))
