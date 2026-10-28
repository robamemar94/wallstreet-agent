"""
Próxima publicación de resultados de cada empresa.

1) yfinance (gratis). 2) Si no da fecha o la que da está muy lejos (>100 días: p.ej. Hermès solo publica las anuales en
yfinance, no la cifra de ventas trimestral), se busca con Gemini + Google Search y se guarda en
data/next_results_cache.json. Solo cuando el usuario lo pide (botón de la Agenda) y en UNA llamada para todas las que
faltan; el botón solo sale si hay MIN_BATCH o más «cerca» (su ciclo trimestral ya toca). Una fecha encontrada vale hasta
que pasa; si no se encontró, no se reintenta hasta pasados CACHE_DAYS días. Mientras tanto se muestra lo que haya.
"""
import json
import logging
import os
import threading
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CACHE_FILE = os.path.join("data", "next_results_cache.json")
CACHE_DAYS = 45   # reintento cuando la búsqueda no encontró fecha
MIN_BATCH = 3     # pendientes cerca para ofrecer la búsqueda
CYCLE_DAYS = 75   # desde su última presentación, a partir de aquí «toca» la siguiente (trimestral)
FAR_DAYS = 100
_lock = threading.Lock()


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


def _search_many(items: List[Tuple[str, str]]) -> Dict[str, Dict[str, Any]]:
    """Una sola llamada con búsqueda para todas: items [(ticker, empresa)] → {ticker: {date, event, source_url}}.
    Sin reintento: si Gemini no buscó de verdad (respondió de memoria), se descarta."""
    from app.application.services import thesis_ai_service as ai
    from utils import llm_usage
    today = date.today().isoformat()
    lines = "\n".join(f"- {t}: {c}" for t, c in items)
    prompt = (f"Hoy es {today}. Para cada empresa, busca en Google la PRÓXIMA fecha en la que publicará resultados o cifras "
              "de ventas/ingresos (trimestrales, semestrales o anuales; lo primero que publique a partir de hoy), en su web de "
              f"relación con inversores o su calendario financiero:\n{lines}\n"
              "DEVUELVE ÚNICAMENTE UN JSON: "
              '{"results": [{"ticker": "…", "date": "YYYY-MM-DD o null si no está publicada", "event": "qué publica (p.ej. ventas del 3T 2026)", "source_url": "…"}]}')
    clients = []
    if ai.free_news_key() and ai.free_search_enabled():
        clients.append((ai._client(ai.free_news_key()), ai.FREE_NEWS_MODEL))
    clients.append((ai._client(), ai.GEMINI_MODEL))
    call = None
    for client, model in clients:
        try:
            with llm_usage.track_usage("next-results"):
                call = ai._generate(client, model, prompt)
            break
        except Exception as e:   # p. ej. 429 de la clave gratuita: se pasa a la de pago
            logger.warning("Búsqueda de fechas con %s: %s", model, e)
    if not call or not call["queries"]:
        return {}
    data = ai.parse_json_or_repair(clients[-1][0], call["text"])
    now, wanted, out = datetime.now().isoformat(timespec="seconds"), {t for t, _ in items}, {}
    for r in data.get("results") or []:
        t, d = r.get("ticker"), r.get("date")
        try:
            if t in wanted and d and date.fromisoformat(d) >= date.today():
                out[t] = {"date": d, "event": (r.get("event") or "").strip(), "source_url": r.get("source_url") or "", "fetched_at": now}
        except ValueError:
            continue
    return out


def pending(db, held) -> List[Tuple[str, str]]:
    """Empresas de la cartera y favoritas a las que les falta fecha (o Yahoo da una lejana), sin búsqueda vigente y con su
    ciclo trimestral ya «cerca». Solo con lo ya guardado: no descarga nada."""
    from app.application.services import market_cache
    from app.infrastructure.db.models import DBAsset
    cache, today, out = _load(), date.today(), []
    for a in db.query(DBAsset).all():
        if a.ticker not in held and not a.is_favorite:
            continue
        info = market_cache.peek("earnings_v4", a.ticker)
        if info is None or not needs_fallback(info.get("next")) or is_fresh(cache.get(a.ticker)):
            continue
        last = (info.get("last") or {}).get("date")
        if last and (today - date.fromisoformat(last)).days < CYCLE_DAYS:
            continue
        out.append((a.ticker, (a.data or {}).get("company_name") or a.ticker))
    return out


def refresh(items: List[Tuple[str, str]]) -> int:
    """Busca de una vez las fechas de items y las guarda (las no encontradas, con date null). Devuelve cuántas halló."""
    if not items:
        return 0
    found = _search_many(items)
    with _lock:
        data = _load()
        now = datetime.now().isoformat(timespec="seconds")
        today = date.today().isoformat()
        for t, _ in items:
            old, new = data.get(t) or {}, dict(found.get(t) or {"date": None, "fetched_at": now})
            # la fecha que ya pasó no se pierde: es la única constancia de lo publicado si Yahoo no lo registra
            past = list(old.get("past") or [])
            if old.get("date") and old["date"] < today and old["date"] not in {p["date"] for p in past}:
                past.append({"date": old["date"], "event": old.get("event") or ""})
            if past:
                new["past"] = sorted(past, key=lambda p: p["date"])[-8:]
            data[t] = new
        _save(data)
    return len(found)


def published(ticker: str, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """Publicaciones de la búsqueda propia cuya fecha ya ha llegado ([{date, event}], la más reciente al final). Es lo que
    hay de las empresas que Yahoo no registra: p. ej. Hermès, que en abril y octubre publica solo ventas (sin BPA)."""
    today_iso = (today or date.today()).isoformat()
    c = _load().get(ticker) or {}
    items = list(c.get("past") or []) + ([{"date": c["date"], "event": c.get("event") or ""}] if c.get("date") else [])
    return sorted((i for i in items if i["date"] <= today_iso), key=lambda i: i["date"])


def is_fresh(cached: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    """¿Sirve lo guardado? Una fecha encontrada, hasta que pase; sin fecha, CACHE_DAYS días desde la búsqueda."""
    if not cached:
        return False
    now = now or datetime.now()
    if cached.get("date"):
        return cached["date"] >= now.date().isoformat()
    return now - datetime.fromisoformat(cached["fetched_at"]) < timedelta(days=CACHE_DAYS)


def next_results(ticker: str, company: str, yf_next: Optional[str], yf_time: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """{date, event, source, time} de la próxima publicación, con lo ya guardado (nunca llama a la IA).
    time: {kind: pre|after|time, madrid: HH:MM} si Yahoo da una hora fiable."""
    yf_item = {"date": yf_next, "event": "", "source": "yfinance", "time": yf_time} if yf_next else None
    if not needs_fallback(yf_next):
        return yf_item
    cached = _load().get(ticker)
    if cached and cached.get("date") and cached["date"] >= date.today().isoformat() and (not yf_next or cached["date"] < yf_next):
        return {"date": cached["date"], "event": cached.get("event") or "", "source": "búsqueda", "source_url": cached.get("source_url"), "time": None}
    return yf_item
