"""Caché en disco de datos de mercado lentos de obtener (FCF/BPA TTM, dividendo, fechas de resultados).

Sobrevive a los reinicios de la app, así la primera carga de Mercado no espera a yfinance.
Cada dato caduca a las `ttl` horas: el BPA/FCF de partida se renueva al menos una vez al día.
"""
import json
import os
import threading
import time
from typing import Any, Callable, Dict

CACHE_FILE = os.path.join("data", "market_cache.json")
_lock = threading.Lock()
_data: Dict[str, Dict[str, list]] = {}   # {tipo: {ticker: [timestamp, valor]}}
_loaded = False


def _ensure_loaded() -> None:
    global _data, _loaded
    if _loaded:
        return
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            _data = json.load(f)
    except (OSError, ValueError):
        _data = {}
    _loaded = True


def _save() -> None:
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_data, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)


def cached(kind: str, ticker: str, fetch: Callable[[], Any], ttl_hours: float = 12, force: bool = False) -> Any:
    """Valor guardado si es reciente; si no, lo pide con fetch() y lo guarda (también un None: no se reintenta hasta que caduque).
    force=True lo vuelve a pedir aunque esté fresco."""
    with _lock:
        _ensure_loaded()
        hit = _data.get(kind, {}).get(ticker)
    if hit and not force and time.time() - hit[0] < ttl_hours * 3600:
        return hit[1]
    value = fetch()
    with _lock:
        _data.setdefault(kind, {})[ticker] = [time.time(), value]
        _save()
    return value


def fetched_at(kind: str, ticker: str) -> float:
    with _lock:
        _ensure_loaded()
        hit = _data.get(kind, {}).get(ticker)
    return hit[0] if hit else 0.0


SNAPSHOT_FILE = os.path.join("data", "market_snapshot.json")


def save_snapshot(obj: Any) -> None:
    """Última foto de precios (para mostrar algo al instante tras un reinicio mientras se actualiza)."""
    with _lock:
        os.makedirs(os.path.dirname(SNAPSHOT_FILE), exist_ok=True)
        tmp = SNAPSHOT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, SNAPSHOT_FILE)


def load_snapshot() -> Any:
    try:
        with open(SNAPSHOT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
