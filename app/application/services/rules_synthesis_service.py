"""Síntesis de las Normas de Calidad: un titular y lo esencial de los 5 pilares, para verlo de un vistazo.

Solo se genera a mano (botón), cuando los 5 pilares tienen informe. No relee los informes completos: resume la nota,
las fortalezas y las debilidades que ya se guardaron de cada pilar (≈3k tokens de entrada, una llamada a Gemini sin
búsqueda: medio céntimo). Guarda una firma de esos datos; si luego se regenera un pilar, la síntesis queda
desactualizada hasta que se vuelva a generar.
"""
import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

KEY = "rules_synthesis"
PILLARS = {
    1: "Foso competitivo, pricing power y riesgo terminal",
    2: "Crecimiento de ventas, FCF total y BPA",
    3: "ROIC, márgenes y eficiencia del capital",
    4: "Solvencia, liquidez y riesgo de refinanciación",
    5: "Asignación de capital, ROIIC, recompras, SBC y gobernanza",
}
MAX_POINTS = 2.0


def _score(raw: Any) -> Optional[float]:
    try:
        return float(str(raw).split("/")[0].strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def pillars(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Lo guardado de cada pilar (solo los que tienen informe)."""
    out = []
    for i, name in PILLARS.items():
        key = f"rules_pilar{i}"
        if not data.get(key):
            continue
        out.append({"n": i, "name": name, "score": _score(data.get(f"{key}_score")),
                    "pros": list(data.get(f"{key}_pros") or []), "cons": list(data.get(f"{key}_cons") or [])})
    return out


def signature(items: List[Dict[str, Any]]) -> str:
    """Firma de lo que entra en la síntesis: cambia si se regenera cualquier pilar."""
    raw = json.dumps([[p["n"], p["score"], p["pros"], p["cons"]] for p in items], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def status(data: Dict[str, Any]) -> Dict[str, Any]:
    """Para pintar la ficha: si se puede generar, qué falta y si la guardada sigue al día."""
    items = pillars(data or {})
    saved = (data or {}).get(KEY) or None
    ready = len(items) == len(PILLARS)
    return {"ready": ready, "missing": [n for n in PILLARS if n not in {p["n"] for p in items}],
            "synthesis": saved, "stale": bool(saved and ready and saved.get("signature") != signature(items))}


def build_prompt(company: str, items: List[Dict[str, Any]]) -> str:
    total = sum(p["score"] or 0 for p in items)
    lines = []
    for p in items:
        lines.append(f"PILAR {p['n']} — {p['name']} · nota {p['score'] if p['score'] is not None else 'N/A'}/{MAX_POINTS}")
        lines += [f"  + {x}" for x in p["pros"]]
        lines += [f"  - {x}" for x in p["cons"]]
    return f"""Eres un analista de inversión. Resume en español, para leerlo de un vistazo, la auditoría de calidad de {company}.
Nota total: {total:.1f}/10 (5 pilares de {MAX_POINTS:.0f} puntos). Datos de cada pilar (+ fortaleza, - debilidad):

{chr(10).join(lines)}

Devuelve SOLO este JSON:
{{"headline": "una frase de 20-30 palabras: qué tipo de negocio es en calidad y su punto más débil",
  "strengths": ["3 fortalezas, máx. 10 palabras cada una, con cifras si las hay"],
  "risks": ["3 riesgos o debilidades, máx. 10 palabras cada una, con cifras si las hay"]}}
No inventes datos que no estén arriba."""


def generate(ticker: str, data: Dict[str, Any], company: str = "") -> Dict[str, Any]:
    """Una llamada a Gemini (sin búsqueda). Lanza ValueError si faltan pilares."""
    from google.genai import types
    from app.application.services import thesis_ai_service as ai
    from utils import llm_usage

    items = pillars(data)
    if len(items) < len(PILLARS):
        raise ValueError("Faltan pilares por ejecutar")
    prompt = build_prompt(company or ticker, items)
    client = ai._client()
    with llm_usage.track_usage(f"{ticker}/rules_synthesis"):   # el consumo queda en Uso de IA
        response = client.models.generate_content(model=ai.GEMINI_MODEL, contents=prompt,
                                                  config=types.GenerateContentConfig(temperature=0.2))
        out = ai.parse_json_or_repair(client, response.text or "", ai.GEMINI_MODEL)
    scores = [p["score"] or 0 for p in items]
    weakest = min(items, key=lambda p: p["score"] if p["score"] is not None else 99)
    return {
        "headline": (out.get("headline") or "").strip(),
        "strengths": [s for s in out.get("strengths") or [] if s][:3],
        "risks": [s for s in out.get("risks") or [] if s][:3],
        "total": round(sum(scores), 2),
        "weakest": {"n": weakest["n"], "score": weakest["score"]},
        "signature": signature(items),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
