"""
Utilidades de IA para las tesis (llamadas a Gemini con Google Search y control de si buscó de verdad) y el
radar semanal de noticias: búsqueda + clasificación por materialidad (solo ≥4/5), cubriendo desde el último radar.
La revisión de resultados está en thesis_review_service.
"""
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from utils import llm_usage

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
NEWS_INTERVAL_DAYS = 7
NEWS_MAX_WINDOW_DAYS = 31  # si hace mucho del último radar, no buscar más atrás de esto
NEWS_MIN_MATERIALITY = 4   # solo noticias muy relevantes para la tesis
STATUS_VALUES = ("green", "amber", "red")
IMPACT_VALUES = ("positive", "negative", "neutral")


def extract_json(text: str) -> Dict[str, Any]:
    """Extrae el primer objeto JSON de la respuesta (el modelo a veces lo envuelve en ```json o añade texto)."""
    if not text:
        raise ValueError("Respuesta vacía del modelo")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("La respuesta del modelo no contiene JSON")
    return json.loads(text[start:end + 1])


def _client():
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY no configurada")
    return genai.Client(api_key=api_key)


def _generate(client, model: str, prompt: str) -> Dict[str, Any]:
    """Llamada con Google Search. Devuelve el texto y lo que Gemini buscó de verdad (grounding metadata)."""
    from google.genai import types

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(tools=[{"google_search": {}}], temperature=0.1),
    )
    queries, sources = [], []
    for cand in response.candidates or []:
        gm = getattr(cand, "grounding_metadata", None)
        if gm is None:
            continue
        queries += list(getattr(gm, "web_search_queries", None) or [])
        for chunk in getattr(gm, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            if web and getattr(web, "uri", None):
                sources.append({"title": getattr(web, "title", "") or "", "uri": web.uri})
    return {"text": response.text or "", "queries": queries, "sources": sources}


def _generate_searched(client, model: str, prompt: str, label: str) -> Dict[str, Any]:
    """Como _generate, pero si Gemini respondió sin usar la búsqueda (de memoria) reintenta una vez exigiéndola."""
    call = _generate(client, model, prompt)
    if not call["queries"]:
        logger.warning("%s: la respuesta no usó la búsqueda; reintentando", label)
        call = _generate(client, model, prompt + "\n\nIMPORTANTE: tu respuesta anterior no usó la búsqueda de Google. "
                                                  "Busca ahora en internet antes de responder; no respondas de memoria.")
    return call


def _merge_grounding(*calls: Dict[str, Any]) -> Dict[str, Any]:
    queries, sources, seen = [], [], set()
    for c in calls:
        queries += c["queries"]
        for src in c["sources"]:
            if src["uri"] not in seen:
                seen.add(src["uri"])
                sources.append(src)
    return {"queries": queries, "sources": sources}


def _kpi_lines(ev: Dict[str, Any]) -> str:
    lines = []
    for n, p in enumerate(ev["pillars"], 1):
        lines.append(f"\nPASO {n}. {p['name']} — {p.get('description') or ''}")
        for k in p["kpis"]:
            last = k["eval"]["last"]
            last_txt = f"último registrado: {last['period']} = {last['value']}" if last and last["value"] is not None else "sin dato previo"
            kind = "NUMÉRICO (da el valor)" if k["kind"] == "numeric" else "CUALITATIVO (da semáforo y, si aplica, un valor de apoyo)"
            lines.append(
                f"  - kpi_id={k['id']} | {k['name']} | {kind} | unidad: {k.get('unit') or '-'} | "
                f"cómo medir: {k.get('how_to_measure') or '-'} | verde: {k.get('green_text') or '-'} | "
                f"amarillo: {k.get('amber_text') or '-'} | rojo: {k.get('red_text') or '-'} | {last_txt}"
            )
    return "\n".join(lines)


# --- Radar semanal de noticias ---

def news_window_days(last_digest: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> int:
    """Días a cubrir: desde el último radar (para no dejar huecos), mínimo 7 y máximo NEWS_MAX_WINDOW_DAYS."""
    if not last_digest or not last_digest.get("created_at"):
        return NEWS_INTERVAL_DAYS
    now = now or datetime.now()
    since_last = (now - datetime.fromisoformat(last_digest["created_at"])).days + 1
    return max(NEWS_INTERVAL_DAYS, min(since_last, NEWS_MAX_WINDOW_DAYS))


def build_news_search_prompt(thesis: Dict[str, Any], days: int = NEWS_INTERVAL_DAYS, today: Optional[str] = None) -> str:
    today = today or datetime.now().strftime("%Y-%m-%d")
    since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    return f"""Busca en Google noticias publicadas entre el {since} y el {today} sobre {thesis['ticker']} ({thesis['title']}):
resultados o guía, adquisiciones, clientes, directivos, regulación, y movimientos de sus principales competidores.
Lista cada noticia con fecha, titular, medio, URL y 2 frases de contenido. Si no encuentras ninguna en ese periodo, dilo."""


def build_news_prompt(thesis: Dict[str, Any], ev: Dict[str, Any], found: str = "", days: int = NEWS_INTERVAL_DAYS,
                      today: Optional[str] = None) -> str:
    today = today or datetime.now().strftime("%Y-%m-%d")
    steps = "\n".join(f"{n}. {p['name']}: {p.get('description') or ''}" for n, p in enumerate(ev["pillars"], 1))
    return f"""Hoy es {today}. Clasifica las noticias recogidas abajo sobre {thesis['ticker']} ({thesis['title']}),
sus competidores y su sector según su impacto en esta tesis de inversión.

TESIS: {thesis.get('summary') or ''}

PASOS DEL CHECKLIST QUE VIGILAMOS:
{steps}

NOTICIAS ENCONTRADAS (búsqueda de los últimos {days} días; descarta las anteriores a esa ventana):
{found or "(ninguna)"}

Usa solo estas noticias (puedes buscar para confirmar un dato). SOLO QUEREMOS NOTICIAS MUY RELEVANTES. Escala de "materiality" (sé estricto):
  5 = puede cambiar la tesis: afecta a un kill switch o a una hipótesis maestra (p.ej. un cliente clave migra a otra plataforma,
      una adquisición grande, un cambio regulatorio que altera el negocio, un profit warning, una salida del CEO/CFO).
  4 = cambia un KPI del checklist de forma concreta y medible (guía revisada, pérdida/ganancia de un cliente relevante,
      movimiento competitivo con datos, cambio de política de capital).
  3 o menos = lanzamientos de producto rutinarios, partnerships menores, notas de prensa comerciales, opiniones de analistas,
      movimientos de precio, refritos. NO LOS INCLUYAS.
- Máximo 5 noticias. Lo normal es que en una semana haya 0, 1 o 2. Si no hay nada de materialidad 4 o 5, devuelve "items": [].

DEVUELVE ÚNICAMENTE UN JSON VÁLIDO, sin markdown:
{{
  "items": [{{"date": "YYYY-MM-DD", "title": "…", "url": "…", "step": 1, "impact": "positive|negative|neutral",
              "materiality": 4, "summary": "1-2 frases: qué pasa y por qué importa para el paso"}}],
  "summary": "1-2 frases con la lectura global de la semana"
}}"""


def parse_news(data: Dict[str, Any], n_steps: int) -> Dict[str, Any]:
    items = []
    for raw in data.get("items", []) or []:
        try:
            materiality = int(raw.get("materiality", 0))
            step = int(raw.get("step")) if raw.get("step") is not None else None
        except (TypeError, ValueError):
            continue
        if materiality < NEWS_MIN_MATERIALITY or not raw.get("title"):
            continue
        items.append({
            "date": raw.get("date") or "", "title": raw["title"].strip(), "url": (raw.get("url") or "").strip(),
            "step": step if step and 1 <= step <= n_steps else None,
            "impact": raw.get("impact") if raw.get("impact") in IMPACT_VALUES else "neutral",
            "materiality": min(materiality, 5), "summary": (raw.get("summary") or "").strip(),
        })
    items.sort(key=lambda i: i["date"], reverse=True)
    items.sort(key=lambda i: i["materiality"], reverse=True)
    return {"items": items, "summary": (data.get("summary") or "").strip()}


def news_due(last_digest: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    """El radar solo se vuelve a consultar si el último tiene NEWS_INTERVAL_DAYS o más."""
    if not last_digest or not last_digest.get("created_at"):
        return True
    now = now or datetime.now()
    return now - datetime.fromisoformat(last_digest["created_at"]) >= timedelta(days=NEWS_INTERVAL_DAYS)


def run_news_digest(thesis: Dict[str, Any], ev: Dict[str, Any]) -> Dict[str, Any]:
    days = news_window_days(thesis["extra"].get("news_digest"))
    client = _client()
    with llm_usage.track_usage(f"{thesis['ticker']}/thesis-news") as tracker:
        # 1) búsqueda con un prompt sencillo (el modelo tiende a no buscar si el prompt es largo y exigente)
        found = _generate_searched(client, GEMINI_MODEL, build_news_search_prompt(thesis, days), f"{thesis['ticker']}/news-search")
        # 2) clasificación por materialidad frente al checklist
        call = _generate(client, GEMINI_MODEL, build_news_prompt(thesis, ev, found["text"], days))
    digest = parse_news(extract_json(call["text"]), len(ev["pillars"]))
    digest.update({"usage": tracker.result, "grounded": bool(found["queries"] or call["queries"]), "window_days": days,
                   "created_at": datetime.now().isoformat(timespec="seconds")})
    return digest
