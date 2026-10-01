"""Roles de cartera (CORE, GROWTH, QUALITY, OPTIONALITY) y el Portfolio ideal agrupado por rol.

El rol se guarda en la propia empresa (assets.data.portfolio_role). Solo pueden tenerlo las que están en
seguimiento (ACCEPTED), favoritas o en cartera; al descartar una empresa pierde el rol.
"""
import os
from typing import Any, Dict, Iterable, List, Optional

import yaml

ROLE_KEY = "portfolio_role"
CONFIG_PATH = os.path.join("config", "portfolio_roles.yaml")


def load_roles(path: str = CONFIG_PATH) -> List[Dict[str, Any]]:
    """Definiciones de los roles (se lee en cada llamada: editar el YAML no requiere reiniciar)."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["roles"]


def role_keys() -> List[str]:
    return [r["key"] for r in load_roles()]


def eligible(asset_data: Dict[str, Any], is_favorite: bool, held: bool) -> bool:
    return held or is_favorite or (asset_data or {}).get("status") == "ACCEPTED"


def band(weight: float, lo: float, hi: float) -> str:
    return "bajo" if weight < lo else "alto" if weight > hi else "en_rango"


def _closest_first(c: Dict[str, Any]):
    """Candidatas: primero las más cerca de su precio de compra; sin valoración, al final."""
    drop = c.get("drop_pct")
    return (drop is None, drop if drop is not None else 0.0, c["ticker"])


def build(roles: List[Dict[str, Any]], companies: Iterable[Dict[str, Any]], total_value: float) -> Dict[str, Any]:
    """Agrupa por rol las empresas y compara el peso real con el rango objetivo.

    companies: dicts con ticker, role (o None), held, value_eur (0 si no está en cartera) y lo que se quiera mostrar.
    Devuelve los grupos (en cartera ordenadas por peso, candidatas aparte), lo que falta o sobra para estar en
    rango, y las empresas sin rol: las de cartera (descuadran el reparto) y las vigiladas pendientes de clasificar.
    """
    pct = lambda v: round(100 * v / total_value, 1) if total_value > 0 else 0.0
    by_role: Dict[Optional[str], List[Dict[str, Any]]] = {}
    for c in companies:
        c = {**c, "weight": pct(c.get("value_eur") or 0.0) if c.get("held") else None}
        by_role.setdefault(c.get("role"), []).append(c)

    groups = []
    for r in roles:
        rows = by_role.get(r["key"], [])
        held = sorted((c for c in rows if c["held"]), key=lambda c: -(c.get("value_eur") or 0.0))
        value = sum(c.get("value_eur") or 0.0 for c in held)
        weight = pct(value)
        b = band(weight, r["min"], r["max"])
        # hasta el borde más cercano del rango (en % de la cartera y en euros)
        gap = r["min"] - weight if b == "bajo" else r["max"] - weight if b == "alto" else 0.0
        groups.append({**r, "held": held, "candidates": sorted((c for c in rows if not c["held"]), key=_closest_first),
                       "value_eur": value, "weight": weight, "band": b, "gap_pct": round(gap, 1),
                       "gap_eur": gap / 100 * total_value if total_value > 0 else 0.0})

    keys = {r["key"] for r in roles}
    unassigned = [c for k, rows in by_role.items() if k not in keys for c in rows]
    return {
        "groups": groups,
        "unassigned_held": sorted((c for c in unassigned if c["held"]), key=lambda c: -(c.get("value_eur") or 0.0)),
        "unassigned_watch": sorted((c for c in unassigned if not c["held"]), key=lambda c: c["ticker"]),
        "total_value": total_value,
        "assigned_weight": round(sum(g["weight"] for g in groups), 1),
        "target_min": sum(r["min"] for r in roles), "target_max": sum(r["max"] for r in roles),
    }
