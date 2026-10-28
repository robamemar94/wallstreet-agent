"""
Comisión estimada de una operación apuntada a mano sin comisión (fee = 0). Una sola cuenta para el recálculo de la
cartera, el P&L de las ventas y el formulario de nueva operación (`/api/portfolio/fee_estimate`).

- IBKR, tarifa escalonada (la del usuario):
  - EE. UU.: 0,0035 $/acción, mínimo 0,35 $, máximo 1 % del importe, más las tasas de bolsa y compensación que IBKR
    repercute (≈ 0,0005 $/acción);
  - euros: 0,05 %, mínimo 1,25 €; coronas: 0,05 %, mínimo 40 kr;
  - la conversión de divisa va aparte (se hace cuando se cambian euros, no en cada compra): 0 por defecto.
- DEGIRO: lo fijo de cada mercado más el AutoFX (%) en lo que no es euro. Sus operaciones del CSV traen la comisión real.
Los ajustes guardados (settings['brokers']) mandan; lo que falte sale de DEFAULTS.
"""
from typing import Any, Dict, Optional

DEFAULTS: Dict[str, float] = {
    "degiro_fee_eur": 1.0,
    "degiro_fee_usd": 1.0,
    "degiro_fee_sek": 3.90,
    "degiro_autofx_pct": 0.25,
    "ibkr_fee_usd_per_share": 0.0035,
    "ibkr_fee_usd_min": 0.35,
    "ibkr_fee_usd_max_pct": 1.0,
    "ibkr_fee_usd_extra_per_share": 0.0005,
    "ibkr_autofx_usd_min": 0.0,
    "ibkr_autofx_usd_pct": 0.0,
    "ibkr_fee_sek_min": 40.0,
    "ibkr_fee_sek_pct": 0.05,
    "ibkr_fee_eur_min": 1.25,
    "ibkr_fee_eur_pct": 0.05,
}


def resolve_broker(broker: Optional[str], date: str) -> str:
    """AUTO: IBKR desde 2026, DEGIRO antes."""
    b = (broker or "IBKR").upper()
    if b == "AUTO":
        return "IBKR" if (date or "") >= "2026" else "DEGIRO"
    return b


def estimate_fee(broker: str, currency: str, shares: float, price: float, eur_rate: float,
                 settings: Optional[Dict[str, Any]] = None) -> float:
    """Comisión en la moneda de la operación. `eur_rate` = euros por unidad de esa moneda."""
    s = {**DEFAULTS, **(settings or {})}
    ccy = (currency or "USD").upper()
    value = shares * price
    rate = eur_rate if eur_rate and eur_rate > 0 else 1.0
    if broker == "DEGIRO":
        if ccy == "EUR":
            return s["degiro_fee_eur"]
        fixed_eur = s["degiro_fee_usd"] if ccy == "USD" else s["degiro_fee_sek"]
        return (fixed_eur + s["degiro_autofx_pct"] / 100.0 * value * rate) / rate
    # IBKR
    if ccy == "USD":
        comm = max(s["ibkr_fee_usd_min"], s["ibkr_fee_usd_per_share"] * shares)
        comm = min(comm, s["ibkr_fee_usd_max_pct"] / 100.0 * value)
        fx = max(s["ibkr_autofx_usd_min"], s["ibkr_autofx_usd_pct"] / 100.0 * value) if s["ibkr_autofx_usd_pct"] else 0.0
        return comm + s["ibkr_fee_usd_extra_per_share"] * shares + fx
    if ccy == "SEK":
        return max(s["ibkr_fee_sek_min"], s["ibkr_fee_sek_pct"] / 100.0 * value)
    comm_eur = max(s["ibkr_fee_eur_min"], s["ibkr_fee_eur_pct"] / 100.0 * value * rate)
    return comm_eur / rate
