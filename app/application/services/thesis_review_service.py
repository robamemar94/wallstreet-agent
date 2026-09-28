"""
Revisión de resultados con IA: una cadena fija de especialistas (no un agente autónomo), para controlar
qué se busca, qué se lee y cuánto cuesta.

  0. Datos gratis      yfinance: KPIs calculables + resultado frente a consenso (BPA)
  1. Documentos        Gemini localiza comunicado y transcripción; se DESCARGA su texto completo       (Flash)
  2. Analista cifras   completa los KPIs que faltan leyendo el comunicado y la call enteros             (Flash)
  3. Verificador       cada cifra contra el texto fuente y contra yfinance                             (Flash)
  4. Analista call     tono, guía, evasivas, preocupaciones de analistas, promesas y su cumplimiento    (Pro)
  5. Juez de la tesis  hipótesis, abogado del diablo, kill switches, propuestas de cambio, veredicto    (Pro)

Los pasos 4 y 5 reciben las EXPECTATIVAS PREVIAS (consenso, guía y promesas de la revisión anterior, últimos valores y
estado de los kill switches) para juzgar los resultados frente a lo que se esperaba, no en el vacío.

El resultado es un INFORME + una PROPUESTA de KPIs: nada se guarda en la tesis hasta que el usuario confirma.
"""
import json
import logging
import os
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional

from app.application.services import earnings_docs
from app.application.services.thesis_ai_service import (
    GEMINI_MODEL, STATUS_VALUES, _client, _generate, _generate_searched, _kpi_lines, _merge_grounding, extract_json,
    parse_json_or_repair,
)
from app.application.services.thesis_service import classify, evaluate_thesis, fetch_auto_metrics
from utils import llm_usage

logger = logging.getLogger(__name__)

EXTRACT_MODEL = os.getenv("THESIS_REVIEW_MODEL", GEMINI_MODEL)
JUDGE_MODEL = os.getenv("THESIS_JUDGE_MODEL", "gemini-3.1-pro-preview")
VERDICTS = ("REFORZADA", "INTACTA", "VIGILANCIA", "DEBILITADA", "ROTA")
PROMISE_RESULTS = {"cumplida": 1.0, "parcial": 0.5, "incumplida": 0.0}
DOC_CHARS_FOR_EXTRACTION = 120_000
EARNINGS_TTL = 12 * 3600
_earnings_cache: Dict[str, Any] = {}


# --- Paso 0: datos gratuitos ---

def _earnings_info(ticker: str) -> Dict[str, Any]:
    """Última presentación publicada (fecha + BPA real vs consenso) y próxima fecha prevista. Cacheado 12h."""
    cached = _earnings_cache.get(ticker)
    if cached and time.time() - cached[0] < EARNINGS_TTL:
        return cached[1]
    info: Dict[str, Any] = {"last": None, "next": None}
    try:
        import math
        import yfinance as yf
        df = yf.Ticker(ticker).get_earnings_dates(limit=8)
        for ts, row in df.iterrows():  # más reciente primero
            reported = row.get("Reported EPS")
            if reported is None or (isinstance(reported, float) and math.isnan(reported)):
                info["next"] = ts.date().isoformat()        # aún sin publicar: la más cercana queda la última vista
                continue
            est, surprise = row.get("EPS Estimate"), row.get("Surprise(%)")
            info["last"] = {"date": ts.date().isoformat(), "eps_reported": float(reported),
                            "eps_estimate": None if est is None or math.isnan(est) else float(est),
                            "surprise_pct": None if surprise is None or math.isnan(surprise) else float(surprise)}
            break
    except Exception as e:
        logger.warning("Sin calendario de resultados para %s: %s", ticker, e)
    _earnings_cache[ticker] = (time.time(), info)
    return info


def fetch_last_earnings(ticker: str) -> Optional[Dict[str, Any]]:
    return _earnings_info(ticker)["last"]


def in_results_week(ticker: str, today: Optional[date] = None) -> bool:
    """Del día antes al 4.º día después de publicar resultados: la revisión de resultados ya cubre las noticias."""
    today = today or date.today()
    info = _earnings_info(ticker)
    dates = [d for d in ((info.get("last") or {}).get("date"), info.get("next")) if d]
    return any(-1 <= (today - date.fromisoformat(d)).days <= 4 for d in dates)


def company_terms(thesis: Dict[str, Any]) -> List[str]:
    name = (thesis.get("title") or "").split("·")[0].strip()
    return [t for t in {thesis["ticker"], name.split()[0] if name else ""} if t]


# --- Paso 1: documentos ---

def build_locate_prompt(thesis: Dict[str, Any], last: Optional[Dict[str, Any]]) -> str:
    when = f"publicados el {last['date']}" if last else "más recientes"
    return f"""Busca en Google los documentos de los resultados trimestrales {when} de {thesis['ticker']} ({thesis['title']}):
1. El comunicado oficial de resultados (web de relación con inversores, GlobeNewswire, BusinessWire o SEC).
2. La transcripción COMPLETA de la conference call de esos resultados (por ejemplo fool.com/earnings/call-transcripts).

DEVUELVE ÚNICAMENTE UN JSON VÁLIDO:
{{"period": "etiqueta fiscal, p.ej. Q3 FY27", "period_end": "YYYY-MM-DD", "report_date": "YYYY-MM-DD",
  "press_release_urls": ["…"], "transcript_urls": ["…"]}}"""


def locate_documents(client, thesis: Dict[str, Any], last: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    call = _generate_searched(client, EXTRACT_MODEL, build_locate_prompt(thesis, last), f"{thesis['ticker']}/locate")
    try:
        meta = extract_json(call["text"])
    except ValueError:
        meta = {}
    urls = list(meta.get("transcript_urls") or []) + list(meta.get("press_release_urls") or [])
    urls += [s["uri"] for s in call["sources"]]
    docs = earnings_docs.pick_documents(earnings_docs.download_candidates(urls), company_terms(thesis))
    return {"meta": meta, "call": call, "transcript": docs["transcript"], "press_release": docs["press_release"]}


# --- Paso 2 y 3: cifras ---

def _reference_by_kpi(ev: Dict[str, Any], reference: Optional[Dict[str, Any]]) -> Dict[int, float]:
    metrics = (reference or {}).get("metrics", {})
    return {k["id"]: metrics[k["auto_metric"]] for p in ev["pillars"] for k in p["kpis"]
            if k.get("auto_metric") and metrics.get(k["auto_metric"]) is not None}


def build_kpi_prompt(thesis, ev, ref_by_kpi, press_release: str, transcript: str, today: Optional[str] = None) -> str:
    today = today or date.today().isoformat()
    ref_txt = "\n".join(f"- kpi_id={k}: {v}" for k, v in ref_by_kpi.items()) or "(ninguno)"
    return f"""Hoy es {today}. Eres el analista de cifras de una tesis de inversión sobre {thesis['ticker']} ({thesis['title']}).
Rellena el cuadro de mando con el trimestre MÁS RECIENTE a partir de los documentos oficiales de abajo.

CUADRO DE MANDO:
{_kpi_lines(ev)}

YA CALCULADOS con los estados financieros (yfinance). Úsalos salvo que el documento oficial diga otra cosa:
{ref_txt}

COMUNICADO DE RESULTADOS:
{press_release[:DOC_CHARS_FOR_EXTRACTION] or "(no disponible: busca las cifras en fuentes primarias)"}

TRANSCRIPCIÓN DE LA CALL (para cifras que se den en los comentarios de la dirección):
{transcript[:DOC_CHARS_FOR_EXTRACTION] or "(no disponible)"}

REGLAS:
- Cita en "evidence" la frase exacta del documento. Si un dato no aparece en los documentos, puedes buscarlo;
  si no lo encuentras en una fuente fiable, "value": null. NO inventes cifras.
- Valores en la unidad del KPI (12.5 para 12.5%). Crecimientos YoY en %.
- "status" (green|amber|red) según las zonas del KPI; obligatorio en los cualitativos.
- "confidence": high = cifra literal de la compañía; medium = cálculo o lectura razonable; low = inferencia.

DEVUELVE ÚNICAMENTE UN JSON VÁLIDO:
{{"period": "Q3 FY27", "period_end": "YYYY-MM-DD", "report_date": "YYYY-MM-DD",
  "kpis": [{{"kpi_id": 0, "value": null, "status": "green", "evidence": "…", "source_url": "…", "confidence": "high"}}]}}"""


def build_verify_prompt(thesis, ev, draft: Dict[str, Any], ref_by_kpi, press_release: str) -> str:
    return f"""Eres el VERIFICADOR de las cifras de {thesis['ticker']}. Comprueba el borrador contra el comunicado oficial
y contra los datos calculados de yfinance; puedes buscar en internet para confirmar un dato.

CUADRO DE MANDO:
{_kpi_lines(ev)}

DATOS DE YFINANCE (kpi_id: valor): {json.dumps(ref_by_kpi)}

COMUNICADO:
{press_release[:DOC_CHARS_FOR_EXTRACTION] or "(no disponible)"}

BORRADOR:
{json.dumps(draft, ensure_ascii=False)}

PARA CADA KPI: cifra del trimestre correcto (no de la guía ni del trimestre anterior), unidad correcta, evidencia que la
respalda literalmente, coherencia con yfinance (si difiere más de 1 punto, explica o corrige), semáforo coherente con las
zonas. Si no se puede confirmar, "value": null. Rebaja la confianza si la evidencia es genérica.

DEVUELVE ÚNICAMENTE EL MISMO JSON corregido, añadiendo en cada KPI "check": "qué has comprobado o corregido (1 frase)"."""


def parse_kpis(data: Dict[str, Any], valid_kpi_ids: set) -> List[Dict[str, Any]]:
    items = []
    for raw in data.get("kpis", []) or []:
        try:
            kpi_id = int(raw.get("kpi_id"))
        except (TypeError, ValueError):
            continue
        if kpi_id not in valid_kpi_ids:
            continue
        value = raw.get("value")
        try:
            value = float(value) if value is not None and value != "" else None
        except (TypeError, ValueError):
            value = None
        items.append({
            "kpi_id": kpi_id, "value": value,
            "status": raw.get("status") if raw.get("status") in STATUS_VALUES else None,
            "evidence": (raw.get("evidence") or "").strip(), "source_url": (raw.get("source_url") or "").strip(),
            "confidence": raw.get("confidence") if raw.get("confidence") in ("high", "medium", "low") else None,
            "check": (raw.get("check") or "").strip(),
        })
    return items


# --- Expectativas previas (lo que se esperaba antes de publicar) ---

def build_expectations(ev: Dict[str, Any], previous_reports: List[Dict[str, Any]],
                       consensus: Optional[Dict[str, Any]], watchlist: Optional[List[Dict[str, Any]]] = None) -> str:
    """Contexto «previo a resultados» para que el análisis compare lo publicado con lo esperado."""
    lines = [f"- En vigilancia ({w.get('type')}): {w.get('title')}" for w in watchlist or []]
    if consensus and consensus.get("eps_estimate") is not None:
        lines.append(f"- Consenso de BPA para el trimestre: {consensus['eps_estimate']} (real publicado: {consensus.get('eps_reported')}).")
    prev = previous_reports[0] if previous_reports else None
    if prev:
        for g in (prev.get("call") or {}).get("guidance") or []:
            lines.append(f"- Guía dada en la revisión anterior ({prev.get('period') or '?'}): {g.get('metric')}"
                         f"{' (' + g['period'] + ')' if g.get('period') else ''}: {g.get('value')}")
        for p in (prev.get("call") or {}).get("promises") or []:
            lines.append(f"- Promesa de la dirección: {p.get('promise')} (plazo: {p.get('deadline') or '-'})")
        for k in prev.get("kill_switch_watch") or []:
            lines.append(f"- Vigilancia de kill switch: {k.get('kill_switch')}: {k.get('comment')}")
    for p in ev["pillars"]:
        for k in p["kpis"]:
            last = k["eval"]["last"]
            if last and last.get("value") is not None:
                lines.append(f"- Último valor registrado de {k['name']}: {last['value']}{k.get('unit') or ''} ({last['period']}, {last['status']})")
            if k.get("is_kill_switch") and k["eval"]["kill_state"] in ("watch", "triggered"):
                lines.append(f"- Kill switch {k['name']} en estado {k['eval']['kill_state']}")
    return "\n".join(lines) or "(no hay expectativas registradas: primera revisión)"


# --- Paso 4: la call ---

def build_call_prompt(thesis, transcript: str, previous_promises: List[Dict[str, Any]], expectations: str = "") -> str:
    prev = "\n".join(f"- {p.get('promise')} (plazo: {p.get('deadline') or '-'})" for p in previous_promises) or "(primera revisión: no hay)"
    source = f"TRANSCRIPCIÓN COMPLETA:\n{transcript}" if transcript else \
        "No se ha podido descargar la transcripción: BUSCA la conference call más reciente y trabaja con lo que encuentres."
    return f"""Eres un analista buy-side senior. Analiza la conference call de resultados más reciente de {thesis['ticker']}
({thesis['title']}) para un inversor con esta tesis: {thesis.get('summary') or ''}

{source}

PROMESAS DE LA DIRECCIÓN EN LA CALL ANTERIOR (evalúa si se han cumplido):
{prev}

LO QUE SE ESPERABA ANTES DE LOS RESULTADOS (compara la guía nueva con la anterior):
{expectations or "(sin datos)"}

Queremos lo que NO dicen las cifras: sé concreto, cita frases literales cuando sea útil y no te dejes llevar por el tono de la dirección.
DEVUELVE ÚNICAMENTE UN JSON VÁLIDO:
{{"tone": "2-3 frases sobre el tono de la dirección",
  "tone_change": "más optimista|igual|más prudente|sin referencia",
  "guidance": [{{"metric": "…", "period": "periodo al que se refiere la guía (p.ej. Q3 FY27, FY27)", "value": "…", "comment": "vs guía anterior / vs consenso"}}],
  "key_messages": ["mensajes estratégicos importantes"],
  "analyst_concerns": ["qué preocupa a los analistas (temas repetidos en el Q&A)"],
  "evasive_answers": [{{"topic": "…", "detail": "pregunta y cómo la esquivaron"}}],
  "promises": [{{"promise": "compromiso concreto y verificable", "deadline": "cuándo"}}],
  "promises_check": [{{"promise": "…", "result": "cumplida|parcial|incumplida|pendiente", "evidence": "…"}}],
  "credibility_note": "1-2 frases: ¿la dirección hace lo que dice?"}}"""


# --- Paso 5: juez de la tesis ---

def build_judge_prompt(thesis, ev, kpi_items, call: Dict[str, Any], consensus: Optional[Dict[str, Any]],
                       expectations: str = "") -> str:
    kpis_by_id = {k["id"]: k for p in ev["pillars"] for k in p["kpis"]}
    kpi_txt = "\n".join(
        f"- {kpis_by_id[i['kpi_id']]['name']}: {i['value'] if i['value'] is not None else 's/d'}"
        f"{kpis_by_id[i['kpi_id']].get('unit') or ''} → {i['status'] or 's/d'} (confianza {i['confidence'] or '-'})"
        for i in kpi_items)
    extra = thesis.get("extra") or {}
    hyps = "\n".join(f"- {k}: {v}" for k, v in (extra.get("hypotheses") or {}).items()) or "(la tesis no enumera hipótesis)"
    kills = "\n".join(f"- {k['name']}: {k.get('kill_rule')}" for p in ev["pillars"] for k in p["kpis"] if k.get("is_kill_switch")) or "(ninguno)"
    steps = "\n".join(f"{n}. {p['name']}: {p.get('description') or ''}" for n, p in enumerate(ev["pillars"], 1))
    rules = "\n".join(f"- {r}" for r in extra.get("rules") or [])
    masters = "\n".join(f"- {q}" for q in extra.get("master_questions") or []) or "(no hay)"
    return f"""Eres el responsable de la tesis de inversión de {thesis['ticker']}. Integra los resultados del trimestre y decide qué
significan PARA LA TESIS. Sé riguroso: reconoce lo bueno, pero haz también de abogado del diablo.

TESIS: {thesis.get('summary') or ''}
{('HIPÓTESIS FINANCIERA: ' + extra['hypothesis']) if extra.get('hypothesis') else ''}
HIPÓTESIS:
{hyps}
REGLAS DE INTERPRETACIÓN:
{rules}
PASOS DEL CUADRO DE MANDO:
{steps}
KILL SWITCHES:
{kills}

KPIs DEL TRIMESTRE (verificados):
{kpi_txt}

RESULTADO VS CONSENSO (BPA, yfinance): {json.dumps(consensus) if consensus else 'no disponible'}

LO QUE SE ESPERABA ANTES DE LOS RESULTADOS (consenso, guía anterior, promesas, últimos valores, kill switches):
{expectations or "(sin datos)"}

ANÁLISIS DE LA CALL:
{json.dumps(call, ensure_ascii=False)}

PREGUNTAS MAESTRAS:
{masters}

CRONOLOGÍA: distingue los hechos DEL TRIMESTRE analizado de los hechos POSTERIORES (noticias, cambios regulatorios, puntos
en vigilancia surgidos después del cierre). Un buen dato del trimestre NO desmiente ni resuelve un riesgo aparecido después:
en ese caso di explícitamente que el trimestre todavía no lo recoge y márcalo "sin datos" en vs_expectations.

Preguntas maestras: propón una NUEVA (type "pregunta_maestra", title = la pregunta tal cual) cuando aparezca una incógnita
material que haya que seguir trimestre a trimestre y ninguna pregunta actual cubra; propón RETIRAR una (type "retirar_pregunta",
title = el texto EXACTO de la pregunta actual) si ya está resuelta o ha perdido sentido.

Propuestas de cambio: SOLO si hay un motivo económico nuevo (p.ej. la empresa empieza a publicar una métrica clave, cambia el
modelo de negocio, aparece un riesgo material). No propongas KPIs por el mero hecho de que haya datos. Pocas y bien justificadas.

DEVUELVE ÚNICAMENTE UN JSON VÁLIDO:
{{"headline": "1 frase: qué ha pasado este trimestre para la tesis",
  "results_summary": ["5-8 puntos con las cifras clave, cambios frente al trimestre anterior y la guía"],
  "vs_consensus": "1-2 frases",
  "vs_expectations": [{{"expectation": "qué se esperaba", "outcome": "supera|cumple|decepciona|sin datos", "detail": "…"}}],
  "steps": [{{"step": 1, "answer": "respuesta breve a la pregunta del paso"}}],
  "master_answers": [{{"question": "…", "answer": "2-3 frases con datos"}}],
  "hypotheses": [{{"id": "H1", "status": "refuerza|neutral|debilita", "reason": "…"}}],
  "devil_advocate": "3-4 frases: qué de este trimestre favorece la contra-tesis",
  "kill_switch_watch": [{{"kill_switch": "…", "comment": "¿se acerca? por qué"}}],
  "proposals": [{{"type": "nuevo_kpi|umbral|riesgo|catalizador|pregunta_maestra|retirar_pregunta|otro", "title": "…", "rationale": "…"}}],
  "verdict": "REFORZADA|INTACTA|VIGILANCIA|DEBILITADA|ROTA  (= Strengthening / Intact / Watch / Weakening / Broken)",
  "verdict_rationale": "2-4 frases"}}"""


def normalize_judgement(data: Dict[str, Any]) -> Dict[str, Any]:
    def _list(key):
        v = data.get(key)
        return v if isinstance(v, list) else []
    verdict = data.get("verdict")
    return {
        "headline": (data.get("headline") or "").strip(),
        "results_summary": [str(x).strip() for x in _list("results_summary") if str(x).strip()],
        "vs_consensus": (data.get("vs_consensus") or "").strip(),
        "vs_expectations": [e for e in _list("vs_expectations") if isinstance(e, dict) and e.get("expectation")],
        "steps": [{"step": s.get("step"), "answer": (s.get("answer") or "").strip()} for s in _list("steps") if isinstance(s, dict)],
        "master_answers": [{"question": (m.get("question") or "").strip(), "answer": (m.get("answer") or "").strip()}
                           for m in _list("master_answers") if isinstance(m, dict)],
        "hypotheses": [h for h in _list("hypotheses") if isinstance(h, dict) and h.get("status") in ("refuerza", "neutral", "debilita")],
        "devil_advocate": (data.get("devil_advocate") or "").strip(),
        "kill_switch_watch": [k for k in _list("kill_switch_watch") if isinstance(k, dict)],
        "proposals": [p for p in _list("proposals") if isinstance(p, dict) and p.get("title")],
        "verdict": verdict if verdict in VERDICTS else None,
        "verdict_rationale": (data.get("verdict_rationale") or "").strip(),
    }


def credibility_score(reports: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Historial de credibilidad: promesas evaluadas en todas las revisiones (cumplida 1, parcial 0,5, incumplida 0)."""
    scores = [PROMISE_RESULTS[c.get("result")] for r in reports for c in (r.get("call") or {}).get("promises_check", [])
              if c.get("result") in PROMISE_RESULTS]
    if not scores:
        return None
    return {"score": round(100 * sum(scores) / len(scores)), "evaluated": len(scores)}


# --- Orquestación ---

def run_review(thesis: Dict[str, Any], previous_reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    ev = evaluate_thesis(thesis)
    valid = {k["id"] for p in ev["pillars"] for k in p["kpis"]}
    needed = {k["auto_metric"] for p in ev["pillars"] for k in p["kpis"] if k.get("auto_metric")}
    reference = None
    try:
        reference = fetch_auto_metrics(thesis["ticker"], needed)
    except Exception as e:
        logger.warning("Sin datos yfinance para %s: %s", thesis["ticker"], e)
    ref_by_kpi = _reference_by_kpi(ev, reference)
    consensus = fetch_last_earnings(thesis["ticker"])
    prev_promises = next(((r.get("call") or {}).get("promises") or [] for r in previous_reports if r.get("call")), [])
    expectations = build_expectations(ev, previous_reports, consensus, (thesis.get("extra") or {}).get("watchlist"))

    client = _client()
    with llm_usage.track_usage(f"{thesis['ticker']}/results-review") as tracker:
        docs = locate_documents(client, thesis, consensus)
        pr_text = (docs["press_release"] or {}).get("text", "")
        tr_text = (docs["transcript"] or {}).get("text", "")

        draft_call = _generate(client, EXTRACT_MODEL, build_kpi_prompt(thesis, ev, ref_by_kpi, pr_text, tr_text))
        draft = parse_json_or_repair(client, draft_call["text"])
        calls = [docs["call"], draft_call]
        try:
            verify_call = _generate(client, EXTRACT_MODEL, build_verify_prompt(thesis, ev, draft, ref_by_kpi, pr_text))
            final, verified = parse_json_or_repair(client, verify_call["text"]), True
            calls.append(verify_call)
        except Exception as e:
            logger.warning("Verificación de %s falló, se usa el borrador: %s", thesis["ticker"], e)
            final, verified = draft, False
        items = parse_kpis(final, valid)
        kpis = {k["id"]: k for p in ev["pillars"] for k in p["kpis"]}
        for it in items:  # el semáforo de los numéricos lo decide la regla de la tesis, no el modelo
            it["status"] = classify(kpis[it["kpi_id"]], it["value"]) or it["status"]

        call_resp = _generate(client, JUDGE_MODEL, build_call_prompt(thesis, tr_text, prev_promises, expectations))
        calls.append(call_resp)
        call_analysis = parse_json_or_repair(client, call_resp["text"])

        judge_resp = _generate(client, JUDGE_MODEL, build_judge_prompt(thesis, ev, items, call_analysis, consensus, expectations))
        calls.append(judge_resp)
        judgement = normalize_judgement(parse_json_or_repair(client, judge_resp["text"]))

    grounding = _merge_grounding(*calls)
    meta = docs["meta"]
    return {
        "period": (final.get("period") or meta.get("period") or "").strip(),
        "period_end": final.get("period_end") or meta.get("period_end"),
        "report_date": final.get("report_date") or meta.get("report_date") or (consensus or {}).get("date"),
        "items": items, "verified": verified, "consensus": consensus, "call": call_analysis, **judgement,
        "expectations": expectations,
        "docs": {
            "press_release_url": (docs["press_release"] or {}).get("url"),
            "transcript_url": (docs["transcript"] or {}).get("url"),
            "transcript_words": len(tr_text.split()) if tr_text else 0,
        },
        # fiable si buscó en internet o si trabajó sobre documentos oficiales descargados (no de memoria)
        "grounded": bool(grounding["queries"] or pr_text or tr_text), "sources": grounding["sources"][:30],
        "models": {"extract": EXTRACT_MODEL, "judge": JUDGE_MODEL}, "model": f"{EXTRACT_MODEL} + {JUDGE_MODEL}",
        "usage": tracker.result,
    }


# --- Ejecución en segundo plano ---

_running: set = set()
_lock = threading.Lock()


def is_running(thesis_id: int) -> bool:
    return thesis_id in _running


def start_review(thesis_id: int) -> Optional[int]:
    """Crea la revisión en estado 'running' y la ejecuta en un hilo. None si ya hay una en marcha para esa tesis."""
    from app.infrastructure.db.database import SessionLocal
    from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository

    with _lock:
        if thesis_id in _running:
            return None
        _running.add(thesis_id)
    db = SessionLocal()
    try:
        review_id = SqlAlchemyThesisRepository(db).create_review(thesis_id)
    finally:
        db.close()

    def _work():
        db = SessionLocal()
        repo = SqlAlchemyThesisRepository(db)
        try:
            thesis = repo.get_thesis(thesis_id)
            previous = [r["report"] for r in repo.list_reviews(thesis_id) if r["id"] != review_id and r["report"]]
            report = run_review(thesis, previous)
            repo.finish_review(review_id, "ready", report=report)
            logger.info("Revisión de resultados %s lista (%s)", thesis["ticker"], report.get("period"))
        except Exception as e:
            db.rollback()
            logger.exception("Revisión de resultados falló (tesis %s)", thesis_id)
            repo.finish_review(review_id, "error", error=str(e)[:500])
        finally:
            db.close()
            with _lock:
                _running.discard(thesis_id)

    threading.Thread(target=_work, name=f"results-review-{thesis_id}", daemon=True).start()
    return review_id


def compute_notices(repo, weights: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
    """Avisos: resultados publicados sin revisar, revisiones listas para mirar y posiciones relevantes
    cuya tesis se deteriora (si se pasan los pesos de la cartera)."""
    from app.application.services.thesis_service import decision_outcome, exposure_risk, unlogged_transactions
    notices = []
    for t in repo.list_theses():
        risk = exposure_risk(t, evaluate_thesis(t), (weights or {}).get(t["ticker"]))
        if risk:
            notices.append({"thesis_id": t["id"], "ticker": t["ticker"], "type": "portfolio_risk", "text": risk})
        decisions = repo.list_decisions(t["id"])
        for tx in unlogged_transactions(repo.transactions_for(t["ticker"], t["created_at"][:10]), decisions, t["created_at"][:10]):
            verb = "compra" if tx["type"] == "BUY" else "venta"
            notices.append({"thesis_id": t["id"], "ticker": t["ticker"], "type": "decision_missing", "transaction_ref": tx["ref"],
                            "text": f"Anota el motivo de tu {verb} del {tx['date']} ({tx['shares']:g} acc. a {tx['price']:g})"})
        for d in decisions:
            due = decision_outcome(d, None)["review_due"]
            if due:
                notices.append({"thesis_id": t["id"], "ticker": t["ticker"], "type": "decision_review",
                                "text": f"Revisa tu decisión de {d['action']} del {d['date']} ({due // 30} meses después)"})
        reviews = repo.list_reviews(t["id"])
        ready = [r for r in reviews if r["status"] == "ready"]
        for r in ready:
            notices.append({"thesis_id": t["id"], "ticker": t["ticker"], "type": "review_ready", "review_id": r["id"],
                            "text": f"Revisión de resultados {r['report'].get('period') or ''} lista para revisar"})
        if any(r["status"] == "running" for r in reviews) or is_running(t["id"]):
            continue
        last = fetch_last_earnings(t["ticker"])
        if not last:
            continue
        # una revisión cubre los resultados que analizó (report_date) y todo lo publicado antes de lanzarla
        done = [r for r in reviews if r["status"] in ("ready", "applied", "discarded")]
        covered = [r["created_at"][:10] for r in done] + [str(r["report"].get("report_date") or "") for r in done]
        covered.append(str(t.get("reference_date") or ""))
        if len(str(t.get("reference_date") or "")) < 10:
            covered.append(t["created_at"][:10])
        if last["date"] > max(covered):
            notices.append({"thesis_id": t["id"], "ticker": t["ticker"], "type": "new_results",
                            "text": f"Resultados publicados el {last['date']} sin revisar"})
    return notices
