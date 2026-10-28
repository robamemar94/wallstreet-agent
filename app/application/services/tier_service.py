"""
Niveles de interés de las empresas y temas (posiciones que quieres añadir).

Niveles (de más a menos):
- «cartera»: la tienes (automático).
- Tier 1 «objetivo»: la quieres comprar si llega a precio. Es la estrella de favorita (DBAsset.is_favorite): todo lo que ya
  usaba las favoritas sigue igual.
- Tier 2 «vigilancia»: te gusta, sin prisa (asset.data['watch_tier'] = '2').
- Tier 3: pasó tu filtro de calidad (estado ACCEPTED) sin más (clave interna 'seg').
Avisos: todo lo que sigues avisa igual (zona, caídas, movimientos raros, compras de directivos). El Tier 1, además, cuando
se acerca a su precio de compra; el Tier 3, las caídas solo desde un 20 %.
«Seguimiento» es el grupo de todo lo que sigues: Tier 1 + Tier 2 + Tier 3.
El Tier 1 debería ser corto: a partir de TIER1_SOFT_MAX la app pregunta si de verdad son todas objetivo.

Listas: grupos de empresas parecidas («Industriales», «Semiconductores»). Por defecto NO meten nada en la watchlist: tú
eliges qué listas siguen en ella («Seguir en la watchlist», `watch`). Un objetivo (cuántas posiciones y qué peso quieres
en ella) es opcional; cada lista propone la mejor candidata para entrar.
La watchlist = Tier 1 + las de las listas que sigues (sin silenciar) + las añadidas a mano (el Tier 2 no entra solo).

Silencio: una lista silenciada (p. ej. la cartera de otra persona que solo quieres mirar) no genera avisos de sus
empresas, salvo las que también son Tier 1, de tu cartera, añadidas a mano o de otra lista sin silenciar. Y una empresa
silenciada (asset.data['muted']) no genera ningún aviso (campana, watchlist, monitor, alertas).
Los datos de cada lista se guardan en settings['theme_targets'] = {id de la lista: {n, weight, watch, muted}}.
"""
from typing import Any, Dict, List, Optional

from app.infrastructure.db.models import DBAsset, DBCompanyList, DBSetting

TIER_KEY = "watch_tier"
TIER1_SOFT_MAX = 12
TARGETS_KEY = "theme_targets"   # {id de la lista: {n, weight, watch, muted}}
MUTE_KEY = "muted"
LEVELS = {
    "cartera": {"label": "En cartera", "short": "Cartera", "badge": "secondary"},
    "1": {"label": "Tier 1 · objetivo", "short": "Tier 1", "badge": "success"},
    "2": {"label": "Tier 2 · vigilancia", "short": "Tier 2", "badge": "info"},
    "seg": {"label": "Tier 3", "short": "Tier 3", "badge": "light"},
}


def tier_of(is_favorite: bool, data: Optional[Dict[str, Any]], held: bool = False) -> Optional[str]:
    data = data or {}
    if held:
        return "cartera"
    if is_favorite:
        return "1"
    if str(data.get(TIER_KEY) or "") == "2":
        return "2"
    if data.get("status") == "ACCEPTED":
        return "seg"
    return None


def set_tier(db, ticker: str, tier: Optional[str]) -> Dict[str, Any]:
    """Cambia el nivel: '1', '2', 'seg' o None (ninguno). Tier 1 = la estrella. Tier 1, 2 y 3 cuentan como
    aceptadas (pasan el filtro de calidad). Devuelve el nivel y, si el Tier 1 se pasa de largo, un aviso."""
    from sqlalchemy.orm.attributes import flag_modified
    asset = db.query(DBAsset).filter(DBAsset.ticker == ticker.upper()).first()
    if not asset:
        raise ValueError(f"{ticker} no está en tu base de datos")
    data = dict(asset.data or {})
    asset.is_favorite = tier == "1"
    if tier == "2":
        data[TIER_KEY] = "2"
    else:
        data.pop(TIER_KEY, None)
    if tier in ("1", "2", "seg") and data.get("status") in (None, "", "PENDING", "STANDBY"):
        data["status"] = "ACCEPTED"
    asset.data = data
    flag_modified(asset, "data")
    db.commit()
    n1 = db.query(DBAsset).filter(DBAsset.is_favorite.is_(True)).count()
    warning = (f"Tienes {n1} empresas en Tier 1. Es el nivel de «la compraría si llega a precio»: ¿siguen siéndolo todas? "
               f"Puedes repasarlas desde la Watchlist.") if tier == "1" and n1 > TIER1_SOFT_MAX else None
    return {"tier": tier, "tier1_count": n1, "warning": warning}


RULES_KEYS = [f"rules_pilar{i}" for i in range(1, 6)]


def ready_to_accept(is_favorite: bool, data: Optional[Dict[str, Any]]) -> bool:
    """Analizada y sin decidir: tiene el veredicto y las cinco normas de calidad, y sigue pendiente (ni aceptada, ni en
    dudas, ni descartada, ni Tier 1). Toca aceptarla y darle nivel (pestaña «Por aceptar» de la Base de datos)."""
    d = data or {}
    return (not is_favorite and d.get("status") in (None, "", "None", "PENDING") and bool(d.get("verdict"))
            and all(d.get(k) for k in RULES_KEYS))


def ready_to_accept_count(db) -> int:
    from app.application.services.notification_service import held_tickers
    held = held_tickers(db)
    return sum(1 for a in db.query(DBAsset).all() if a.ticker not in held and ready_to_accept(a.is_favorite, a.data))


# --- Temas ---------------------------------------------------------------------------------------------------------

def targets(db) -> Dict[str, Dict[str, Any]]:
    row = db.get(DBSetting, TARGETS_KEY)
    return dict(row.value) if row and isinstance(row.value, dict) else {}


def _save_meta(db, all_t: Dict[str, Any]) -> None:
    row = db.get(DBSetting, TARGETS_KEY)
    if row:
        row.value = all_t
    else:
        db.add(DBSetting(key=TARGETS_KEY, value=all_t))
    db.commit()


def objective(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Solo el objetivo (posiciones y peso) de los datos de una lista; vacío si no tiene."""
    meta = meta or {}
    return {k: meta[k] for k in ("n", "weight") if meta.get(k)}


def set_target(db, list_id: int, n: Optional[int], weight: Optional[float]) -> Dict[str, Any]:
    """El objetivo de una lista: cuántas posiciones (n) y qué peso de la cartera (%). Vacío: sin objetivo."""
    all_t = targets(db)
    key = str(list_id)
    meta = {**(all_t.get(key) or {}), "n": int(n) if n else None, "weight": float(weight) if weight else None}
    all_t[key] = meta
    _save_meta(db, all_t)
    return objective(meta)


def set_theme_watch(db, list_id: int, watch: bool) -> bool:
    """Sigue (o no) una lista en la watchlist: sus empresas entran en ella y avisan como las de la watchlist."""
    all_t = targets(db)
    key = str(list_id)
    all_t[key] = {**(all_t.get(key) or {}), "watch": bool(watch)}
    _save_meta(db, all_t)
    return bool(watch)


def set_theme_muted(db, list_id: int, muted: bool) -> bool:
    """Silencia (o no) los avisos de las empresas de una lista."""
    all_t = targets(db)
    key = str(list_id)
    all_t[key] = {**(all_t.get(key) or {}), "muted": bool(muted)}
    _save_meta(db, all_t)
    return bool(muted)


def set_muted(db, ticker: str, muted: bool) -> bool:
    """Silencia (o no) todos los avisos de una empresa."""
    from sqlalchemy.orm.attributes import flag_modified
    asset = db.query(DBAsset).filter(DBAsset.ticker == ticker.upper()).first()
    if not asset:
        raise ValueError(f"{ticker} no está en tu base de datos")
    data = dict(asset.data or {})
    if muted:
        data[MUTE_KEY] = True
    else:
        data.pop(MUTE_KEY, None)
    asset.data = data
    flag_modified(asset, "data")
    db.commit()
    return bool(muted)


def muted_tickers(db) -> set:
    """Empresas que no deben avisar: las silenciadas una a una y las que solo están vigiladas por listas silenciadas
    (no Tier 1, no en cartera, no añadidas a mano y sin otro tema con avisos)."""
    from app.infrastructure.db.models import DBPortfolioItem, DBWatchlistItem
    held = {p.ticker for p in db.query(DBPortfolioItem).all() if (p.shares or 0) > 0}
    manual = {w.ticker for w in db.query(DBWatchlistItem).all()}
    by_theme = themes_by_ticker(db)
    out = set()
    for a in db.query(DBAsset).all():
        if (a.data or {}).get(MUTE_KEY):
            out.add(a.ticker)
            continue
        themes = by_theme.get(a.ticker) or []
        if themes and all(th["muted"] for th in themes) and not a.is_favorite and a.ticker not in held and a.ticker not in manual:
            out.add(a.ticker)
    return out


def themes_by_ticker(db) -> Dict[str, List[Dict[str, Any]]]:
    """{ticker: [{id, name, target}]} de todos los temas."""
    t = targets(db)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for lst in db.query(DBCompanyList).all():
        for a in lst.assets:
            meta = t.get(str(lst.id)) or {}
            out.setdefault(a.ticker, []).append({"id": lst.id, "name": lst.name, "target": objective(meta) or None,
                                                 "watch": bool(meta.get("watch")), "muted": bool(meta.get("muted"))})
    return out


def followed_outside_watchlist(db) -> Dict[str, str]:
    """Tier 2 y Tier 3 que no entran en la watchlist (ni Tier 1 ni de una lista que sigues), sin silenciar: {ticker: '2'|'seg'}.
    No salen en la tabla de la watchlist, pero avisan igual (el Tier 3, de caídas solo desde un 20 %)."""
    cands = watch_candidates(db)
    muted = muted_tickers(db)
    out = {}
    for a in db.query(DBAsset).all():
        tier = tier_of(a.is_favorite, a.data)
        if tier in ("2", "seg") and a.ticker not in cands and a.ticker not in muted:
            out[a.ticker] = tier
    return out


def watch_candidates(db) -> Dict[str, Dict[str, Any]]:
    """Empresas que entran solas en la watchlist: Tier 1 y las de las listas que sigues en ella (y sin silenciar).
    {ticker: {tier, themes}}."""
    by_theme = themes_by_ticker(db)
    out: Dict[str, Dict[str, Any]] = {}
    for a in db.query(DBAsset).all():
        tier = tier_of(a.is_favorite, a.data)
        themes = [th["name"] for th in by_theme.get(a.ticker, []) if th["watch"] and not th["muted"]]
        if tier == "1" or themes:
            out[a.ticker] = {"tier": tier, "themes": themes}
    return out


def best_candidate(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """La mejor para entrar ahora en una lista: de las que no tienes y tienen valoración, primero las que están en zona de
    compra (o más cerca), y a igualdad la de más CAGR estimado. rows: {ticker, held, drop_pct, cagr}."""
    pool = [r for r in rows if not r.get("held") and r.get("drop_pct") is not None]
    if not pool:
        return None
    return min(pool, key=lambda r: (max(r["drop_pct"], 0), -(r.get("cagr") or 0)))
