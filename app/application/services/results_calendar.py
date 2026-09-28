"""
Próxima publicación de resultados de cada empresa.

1) yfinance (gratis). 2) Si no da fecha o la que da está muy lejos (>100 días: p.ej. Hermès solo publica las anuales en
yfinance, no la cifra de ventas trimestral), se busca con Gemini + Google Search en segundo plano y se guarda 7 días
en data/next_results_cache.json. Mientras tanto se muestra lo que haya.
"""
import json
import logging
import os
import threading
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CACHE_FILE = os.path.join("data", "next_results_cache.json")
CACHE_DAYS = 7
FAR_DAYS = 100
_lock = threading.Lock()
_pending: set = set()


def _load() -> Dict[str, Any]:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _save(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def needs_fallback(yf_next: Optional[str], today: Optional[date] = None) -> bool:
    today = today or date.today()
    return not yf_next or (date.fromisoformat(yf_next) - today).days > FAR_DAYS


def _search(ticker: str, company: str) -> Optional[Dict[str, Any]]:
    from app.application.services.thesis_ai_service import GEMINI_MODEL, _client, _generate_searched, parse_json_or_repair
    from utils import llm_usage
    today = date.today().isoformat()
    prompt = (f"Hoy es {today}. Busca en Google la PRÓXIMA fecha en la que {company} ({ticker}) publicará resultados o cifras "
              "de ventas/ingresos (trimestrales, semestrales o anuales; lo primero que publique a partir de hoy), en su web de "
              "relación con inversores o su calendario financiero. DEVUELVE ÚNICAMENTE UN JSON: "
              '{"date": "YYYY-MM-DD o null si no está publicada", "event": "qué publica (p.ej. ventas del 3T 2026)", "source_url": "…"}')
    client = _client()
    with llm_usage.track_usage(f"{ticker}/next-results"):
        call = _generate_searched(client, GEMINI_MODEL, prompt, f"{ticker}/next-results")
        data = parse_json_or_repair(client, call["text"])
    d = data.get("date")
    try:
        if not d or date.fromisoformat(d) < date.today():
            return None
    except ValueError:
        return None
    return {"date": d, "event": (data.get("event") or "").strip(), "source_url": data.get("source_url") or "",
            "fetched_at": datetime.now().isoformat(timespec="seconds")}


def _fetch_async(ticker: str, company: str) -> None:
    with _lock:
        if ticker in _pending:
            return
        _pending.add(ticker)

    def work():
        try:
            found = _search(ticker, company)
            with _lock:
                data = _load()
                data[ticker] = found or {"date": None, "fetched_at": datetime.now().isoformat(timespec="seconds")}
                _save(data)
        except Exception as e:
            logger.warning("No se pudo buscar la próxima fecha de resultados de %s: %s", ticker, e)
        finally:
            with _lock:
                _pending.discard(ticker)

    threading.Thread(target=work, name=f"next-results-{ticker}", daemon=True).start()


def next_results(ticker: str, company: str, yf_next: Optional[str]) -> Optional[Dict[str, Any]]:
    """{date, event, source} de la próxima publicación. No bloquea: si hace falta buscar, lo lanza en segundo plano."""
    yf_item = {"date": yf_next, "event": "", "source": "yfinance"} if yf_next else None
    if not needs_fallback(yf_next):
        return yf_item
    cached = _load().get(ticker)
    fresh = cached and datetime.now() - datetime.fromisoformat(cached["fetched_at"]) < timedelta(days=CACHE_DAYS)
    if not fresh:
        _fetch_async(ticker, company)
    if cached and cached.get("date") and cached["date"] >= date.today().isoformat() and (not yf_next or cached["date"] < yf_next):
        return {"date": cached["date"], "event": cached.get("event") or "", "source": "búsqueda", "source_url": cached.get("source_url")}
    return yf_item
