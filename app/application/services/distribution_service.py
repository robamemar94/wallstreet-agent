"""
Distribución de la cartera (vista «Distribución» de Mi Portfolio): por activo, rol, divisa, sector y país, más unas
cifras de concentración. Lo que pesa menos de MIN_PCT se agrupa en «Otros» para que los roscos se puedan leer.
"""
from typing import Any, Dict, List, Optional

MIN_PCT = 1.0
OTHERS = "Otros"
NO_ROLE = "Sin rol"


# Sectores de yfinance, en español (Distribución e informes)
SECTOR_ES = {
    "Technology": "Tecnología",
    "Healthcare": "Salud",
    "Financial Services": "Servicios Financieros",
    "Consumer Cyclical": "Consumo Cíclico",
    "Industrials": "Industria",
    "Communication Services": "Servicios de Comunicación",
    "Consumer Defensive": "Consumo Defensivo",
    "Energy": "Energía",
    "Utilities": "Servicios Públicos",
    "Real Estate": "Inmobiliario",
    "Basic Materials": "Materiales Básicos",
}


def _slices(totals: Dict[str, float], total: float, min_pct: float = MIN_PCT) -> List[Dict[str, Any]]:
    """[{label, value, pct}] de mayor a menor; lo de menos de min_pct, junto en «Otros» (con quién lo forma)."""
    rows = sorted(({"label": k, "value": v, "pct": 100 * v / total} for k, v in totals.items() if v > 0),
                  key=lambda r: -r["value"])
    big = [r for r in rows if r["pct"] >= min_pct]
    small = [r for r in rows if r["pct"] < min_pct]
    if len(small) == 1:          # agrupar uno solo no ahorra nada
        big += small
    elif small:
        big.append({"label": OTHERS, "value": sum(r["value"] for r in small), "pct": sum(r["pct"] for r in small),
                    "members": [r["label"] for r in small]})
    return big


def _sum_by(positions: List[Dict[str, Any]], key: str, default: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for p in positions:
        k = p.get(key) or default
        out[k] = out.get(k, 0.0) + p["value"]
    return out


def build(positions: List[Dict[str, Any]], roles: List[Dict[str, Any]], cash: float = 0.0) -> Optional[Dict[str, Any]]:
    """positions: [{ticker, name, value (EUR), currency, sector, country, role}]; roles: los de portfolio_roles.yaml.
    cash: liquidez del bróker en euros. Solo entra en la divisa (es exposición real al euro) y en su propio %; los pesos,
    roles, sectores y países siguen siendo de lo invertido."""
    positions = [p for p in positions if (p.get("value") or 0) > 0]
    total = sum(p["value"] for p in positions)
    if not total:
        return None
    weights = sorted((100 * p["value"] / total for p in positions), reverse=True)
    names = {p["ticker"]: p.get("name") or p["ticker"] for p in positions}
    assets = _slices({p["ticker"]: p["value"] for p in positions}, total)
    for a in assets:
        a["ticker"] = a["label"] if a["label"] != OTHERS else None
        a["name"] = names.get(a["label"], a["label"])

    by_role = _sum_by(positions, "role", NO_ROLE)
    role_rows = []
    for r in roles:
        pct = 100 * by_role.get(r["key"], 0.0) / total
        role_rows.append({"key": r["key"], "label": r["name"], "color": r.get("color"), "pct": pct,
                          "value": by_role.get(r["key"], 0.0), "min": r.get("min"), "max": r.get("max"),
                          "band": None if r.get("min") is None else ("bajo" if pct < r["min"] else "alto" if pct > r["max"] else "en_rango")})
    if by_role.get(NO_ROLE):
        role_rows.append({"key": None, "label": NO_ROLE, "color": None, "pct": 100 * by_role[NO_ROLE] / total,
                          "value": by_role[NO_ROLE], "min": None, "max": None, "band": None,
                          "members": sorted(p["ticker"] for p in positions if not p.get("role"))})

    cash = max(cash or 0.0, 0.0)
    by_ccy = _sum_by(positions, "currency", "?")
    if cash:
        by_ccy["EUR"] = by_ccy.get("EUR", 0.0) + cash
    currency = _slices(by_ccy, total + cash)
    return {
        "total": total,
        "concentration": {
            "max_ticker": max(positions, key=lambda p: p["value"])["ticker"], "max_pct": weights[0],
            "top5_pct": sum(weights[:5]), "positions": len(positions),
            # número efectivo de posiciones: 1 / Σw² (con N posiciones iguales sale N)
            "effective_n": 1 / sum((w / 100) ** 2 for w in weights),
            "main_currency": currency[0]["label"], "main_currency_pct": currency[0]["pct"],
        },
        "assets": assets,
        "roles": role_rows,
        "currency": currency,
        "cash": {"value": cash, "pct": 100 * cash / (total + cash)} if cash else None,
        "sector": _slices(_sum_by(positions, "sector", "Desconocido"), total),
        "country": _slices(_sum_by(positions, "country", "Desconocido"), total),
    }
