"""Resumen semanal conjunto de las noticias de todas las tesis.

No vuelve a buscar en internet: resume lo que el radar de cada tesis ya encontró (noticias de la semana del radar,
desde el lunes, y el resumen de cada radar). Una sola llamada a Gemini, sin búsqueda. Solo se genera al terminar la pasada semanal
del radar de los lunes (y si el último es de una semana anterior: el radar de una tesis nueva a mitad de semana no lo lanza),
o a mano con «Regenerar».
"""
import json
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.infrastructure.db.models import DBSetting

logger = logging.getLogger(__name__)

SETTING_KEY = "news_weekly_summary"
MAX_NEWS = 60
RETRY_AFTER_S = 3600   # si falla (sin clave, cuota…) no se reintenta en cada visita
_lock = threading.Lock()
_running = False
_last_failure = 0.0


def load(db) -> Optional[Dict[str, Any]]:
    row = db.get(DBSetting, SETTING_KEY)
    return row.value if row else None


def save(db, summary: Dict[str, Any]) -> None:
    row = db.get(DBSetting, SETTING_KEY)
    if row:
        row.value = summary
    else:
        db.add(DBSetting(key=SETTING_KEY, value=summary))
    db.commit()


def is_running() -> bool:
    return _running


def due_after_radar(summary: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    """¿Toca resumen al terminar una pasada del radar? Solo si no hay o el último es de una semana anterior
    (la del radar, desde el lunes): el radar de una tesis nueva a mitad de semana no lo repite."""
    from app.application.services.thesis_ai_service import radar_week_start
    if not summary or not summary.get("created_at"):
        return True
    return datetime.fromisoformat(summary["created_at"]) < radar_week_start(now)


def week_of(found_at: str) -> str:
    """Semana del radar a la que pertenece una noticia: la fecha (ISO) del lunes de la pasada que la encontró."""
    from app.application.services.thesis_ai_service import radar_week_start
    return radar_week_start(datetime.fromisoformat(found_at)).date().isoformat()


def _window_news(repo, now: datetime) -> List[Dict[str, Any]]:
    """Noticias de esta semana del radar (desde el lunes): las de semanas anteriores ya salieron en su resumen."""
    from app.application.services.thesis_ai_service import radar_week_start
    since = radar_week_start(now).isoformat(timespec="seconds")
    return [n for n in repo.list_news(limit=200) if (n.get("found_at") or "") >= since][:MAX_NEWS]


def build_prompt(news: List[Dict[str, Any]], radars: List[Dict[str, Any]], today: str, week: str = "") -> str:
    news_lines = "\n".join(
        f"- [{n['ticker']}] {n.get('date') or '¿fecha?'} · materialidad {n['materiality']}/5 · impacto {n.get('impact') or 'neutral'}"
        f"{' · paso: ' + n['step_name'] if n.get('step_name') else ''} · {n['title']}: {n.get('summary') or ''}" for n in news) or "(ninguna)"
    radar_lines = "\n".join(f"- [{r['ticker']}] (veredicto de la tesis: {r.get('verdict') or '¿?'}) {r['summary']}" for r in radars) or "(ninguno)"
    return f"""Hoy es {today}. Eres el analista que vigila una cartera de tesis de inversión a largo plazo.
Resume en español, de forma breve y útil para decidir, lo que ha pasado esta semana con las empresas de la cartera.
Usa SOLO la información de abajo (no inventes ni busques nada más).

NOTICIAS RELEVANTES ENCONTRADAS POR EL RADAR SEMANAL{' DEL LUNES ' + week if week else ''} (cubre la semana anterior):
{news_lines}

RESUMEN DEL ÚLTIMO RADAR DE CADA TESIS:
{radar_lines}

Devuelve ÚNICAMENTE un JSON válido con esta estructura:
{{
  "headline": "una frase con lo más importante de la semana",
  "overview": "2-4 frases: qué ha pasado en conjunto y qué tendencia se ve",
  "by_thesis": [{{"ticker": "XXX", "impact": "positive|negative|neutral", "summary": "1-2 frases", "review": true|false}}],
  "to_review": ["acciones concretas: qué tesis revisar y por qué (vacío si nada)"]
}}
Incluye en by_thesis solo las empresas con algo que contar. "review" = true si algo puede cambiar la tesis."""


def generate(db) -> Dict[str, Any]:
    from app.application.services import thesis_ai_service as ai
    from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository
    from utils import llm_usage

    now = datetime.now()
    repo = SqlAlchemyThesisRepository(db)
    news = _window_news(repo, now)
    week = ai.radar_week_start(now)
    radars = [{"ticker": t["ticker"], "verdict": t.get("verdict"), "summary": (t["extra"].get("news_digest") or {}).get("summary")}
              for t in repo.list_theses()]
    radars = [r for r in radars if r["summary"]]
    base = {"created_at": now.isoformat(timespec="seconds"), "week": week.date().isoformat(), "news_count": len(news)}
    if not news and not radars:
        summary = {**base, "headline": "Semana sin noticias relevantes en las tesis.", "overview": "", "by_thesis": [], "to_review": []}
    else:
        from google.genai import types
        prompt = build_prompt(news, radars, now.strftime("%d/%m/%Y"), week.strftime("%d/%m/%Y"))
        def call(client, model):
            with llm_usage.track_usage("news-weekly-summary", background=True):   # el consumo se registra solo
                response = client.models.generate_content(model=model, contents=prompt,
                                                          config=types.GenerateContentConfig(temperature=0.2))
                return ai.parse_json_or_repair(client, response.text or "", model)
        # sin búsqueda: clave gratuita con FREE_TEXT_MODELS en orden (p. ej. si uno está saturado); si fallan, la de pago
        data, free = None, False
        if ai.free_news_key():
            free_client = ai._client(ai.free_news_key())
            for model in ai.FREE_TEXT_MODELS:
                try:
                    data, free = call(free_client, model), True
                    break
                except Exception as e:
                    logger.warning("Resumen semanal con la clave gratuita y %s falló (%s)", model, str(e)[:120])
        if data is None:
            data = call(ai._client(), ai.GEMINI_MODEL)
        summary = {**base, "headline": data.get("headline") or "", "overview": data.get("overview") or "",
                   "by_thesis": [b for b in data.get("by_thesis") or [] if b.get("ticker")],
                   "to_review": [x for x in data.get("to_review") or [] if x], "free_tier": free}
    save(db, summary)
    return summary


def generate_in_background(force: bool = False) -> bool:
    """Genera el resumen en un hilo. False si ya hay uno en marcha (o falló hace poco y no se fuerza)."""
    global _running
    import time
    with _lock:
        if _running or (not force and time.time() - _last_failure < RETRY_AFTER_S):
            return False
        _running = True

    def run():
        global _running, _last_failure
        from app.infrastructure.db.database import SessionLocal
        db = SessionLocal()
        try:
            generate(db)
        except Exception:
            _last_failure = time.time()
            logger.exception("No se pudo generar el resumen semanal de noticias")
        finally:
            db.close()
            with _lock:
                _running = False
    threading.Thread(target=run, daemon=True, name="news-weekly-summary").start()
    return True
