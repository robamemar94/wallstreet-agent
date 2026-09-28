import glob
import logging
import os
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.application.services.thesis_service import (
    AUTO_METRICS, DECISION_ACTIONS, EVENT_TYPES, STATUSES, VERDICTS, classify, evaluate_thesis, fetch_auto_metrics,
    load_thesis_yaml,
)
from app.application.services import thesis_ai_service, thesis_changes_service, thesis_news_runner, thesis_review_service
from app.infrastructure.dependencies import get_asset_repository, get_portfolio_service, get_thesis_repository
from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Theses"])

STATUS_VALUES = ("green", "amber", "red")


class ThesisUpdate(BaseModel):
    verdict: Optional[str] = None
    status: Optional[str] = None
    summary: Optional[str] = None
    note: Optional[str] = None       # motivo del cambio de veredicto/estado (va al timeline)


class ObservationItem(BaseModel):
    kpi_id: int
    value: Optional[float] = None
    status: Optional[str] = None     # obligatorio para KPIs cualitativos o sin umbral rojo
    note: Optional[str] = None


class ObservationBatch(BaseModel):
    period: str                      # ej: 'Q3 FY27'
    date: str                        # fecha de cierre del periodo (ISO)
    source: Optional[str] = "manual"
    items: List[ObservationItem]
    summary: Optional[str] = None    # si se informa, se añade un evento 'earnings' al timeline


class EventCreate(BaseModel):
    date: str
    type: str = "note"
    title: str
    body: Optional[str] = None
    impact: Optional[str] = None


def _get_or_404(repo: SqlAlchemyThesisRepository, thesis_id: int) -> dict:
    thesis = repo.get_thesis(thesis_id)
    if not thesis:
        raise HTTPException(status_code=404, detail="La tesis no existe")
    return thesis


def _kpis_by_id(thesis: dict) -> dict:
    return {k["id"]: k for p in thesis["pillars"] for k in p["kpis"]}


@router.get("/theses")
def list_theses(repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    result = []
    for t in repo.list_theses():
        ev = evaluate_thesis(t)
        result.append({
            "id": t["id"], "ticker": t["ticker"], "title": t["title"], "verdict": t["verdict"],
            "status": t["status"], "health_score": ev["health_score"], "coverage": ev["coverage"],
            "counts": ev["counts"], "review_recommended": ev["review_recommended"],
        })
    return result


@router.get("/theses/notices")
def thesis_notices(repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository),
                   asset_repo=Depends(get_asset_repository), portfolio_service=Depends(get_portfolio_service)):
    """Avisos para la barra lateral: resultados sin revisar, revisiones listas y posiciones con la tesis deteriorándose."""
    from app.interfaces.views.thesis_views import portfolio_exposure
    weights = {tk: p["weight"] for tk, p in portfolio_exposure(portfolio_service, asset_repo)["positions"].items()}
    notices = thesis_review_service.compute_notices(repo, weights)
    return {"count": len(notices), "notices": notices}


@router.get("/theses/{thesis_id}")
def get_thesis(thesis_id: int, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    thesis = _get_or_404(repo, thesis_id)
    return {**thesis, "evaluation": evaluate_thesis(thesis)}


@router.patch("/theses/{thesis_id}")
def update_thesis(thesis_id: int, payload: ThesisUpdate,
                  repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    thesis = _get_or_404(repo, thesis_id)
    if payload.verdict and payload.verdict not in VERDICTS:
        raise HTTPException(status_code=400, detail=f"Veredicto no válido. Opciones: {', '.join(VERDICTS)}")
    if payload.status and payload.status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"Estado no válido. Opciones: {', '.join(STATUSES)}")

    fields = {k: v for k, v in payload.model_dump().items() if v is not None and k != "note"}
    changes = [f"{label}: {thesis[f]} → {fields[f]}" for f, label in (("verdict", "Veredicto"), ("status", "Estado"))
               if f in fields and fields[f] != thesis[f]]
    updated = repo.update_thesis(thesis_id, fields)
    if changes:
        impact = {"REFORZADA": "positive", "DEBILITADA": "negative", "ROTA": "negative"}.get(fields.get("verdict"), "neutral")
        repo.add_event(thesis_id, date.today().isoformat(), "verdict", " · ".join(changes), payload.note, impact)
    return {"status": "success", "thesis": updated}


@router.post("/theses/{thesis_id}/observations")
def add_observations(thesis_id: int, payload: ObservationBatch,
                     repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    """Registra los KPIs de un periodo (típicamente tras unos resultados). Items vacíos se ignoran."""
    thesis = _get_or_404(repo, thesis_id)
    kpis = _kpis_by_id(thesis)
    if not payload.period.strip():
        raise HTTPException(status_code=400, detail="El periodo es obligatorio")

    saved, errors = [], []
    for item in payload.items:
        kpi = kpis.get(item.kpi_id)
        if not kpi:
            errors.append(f"KPI {item.kpi_id} no pertenece a esta tesis")
            continue
        if item.value is None and not item.status:
            continue
        status = classify(kpi, item.value) or item.status
        if status not in STATUS_VALUES:
            errors.append(f"{kpi['name']}: indica el semáforo (verde/amarillo/rojo)")
            continue
        saved.append(repo.upsert_observation(item.kpi_id, payload.period.strip(), payload.date, item.value,
                                             status, item.note, payload.source))

    if saved and payload.summary:
        repo.add_event(thesis_id, payload.date, "earnings", f"Revisión {payload.period.strip()}", payload.summary)
    return {"status": "success" if not errors else "partial", "saved": len(saved), "errors": errors}


@router.delete("/theses/observations/{observation_id}")
def delete_observation(observation_id: int, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    if not repo.delete_observation(observation_id):
        raise HTTPException(status_code=404, detail="La observación no existe")
    return {"status": "success"}


@router.get("/theses/{thesis_id}/autofill")
def autofill(thesis_id: int, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    """Propone valores del último trimestre desde yfinance. No guarda nada: el usuario revisa y confirma."""
    thesis = _get_or_404(repo, thesis_id)
    kpis = _kpis_by_id(thesis).values()
    try:
        data = fetch_auto_metrics(thesis["ticker"], {k["auto_metric"] for k in kpis if k.get("auto_metric")})
    except Exception as e:
        logger.exception("Autofill yfinance falló para %s", thesis["ticker"])
        raise HTTPException(status_code=502, detail=f"No se pudieron obtener datos de yfinance: {e}")
    suggestions = []
    for kpi in kpis:
        metric = kpi.get("auto_metric")
        value = data["metrics"].get(metric) if metric else None
        if value is not None:
            suggestions.append({"kpi_id": kpi["id"], "value": value, "status": classify(kpi, value),
                                "metric": AUTO_METRICS[metric], "detail": data.get("details", {}).get(metric)})
    return {"period_end": data["period_end"], "suggestions": suggestions}


@router.post("/theses/{thesis_id}/events")
def add_event(thesis_id: int, payload: EventCreate, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    if payload.type not in EVENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Tipo no válido. Opciones: {', '.join(EVENT_TYPES)}")
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="El título es obligatorio")
    event = repo.add_event(thesis_id, payload.date, payload.type, payload.title.strip(), payload.body, payload.impact)
    if not event:
        raise HTTPException(status_code=404, detail="La tesis no existe")
    return {"status": "success", "event": event}


@router.delete("/theses/events/{event_id}")
def delete_event(event_id: int, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    if not repo.delete_event(event_id):
        raise HTTPException(status_code=404, detail="El evento no existe")
    return {"status": "success"}


@router.post("/theses/import")
def import_theses(repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    """Reimporta todas las tesis de config/theses/*.yaml (idempotente, conserva observaciones)."""
    imported, errors = [], []
    for path in sorted(glob.glob("config/theses/*.yaml")):
        try:
            imported.append(repo.import_spec(load_thesis_yaml(path))["ticker"])
        except Exception as e:
            repo.db.rollback()
            errors.append(f"{path}: {e}")
    return {"status": "success" if not errors else "partial", "imported": imported, "errors": errors}


# --- IA: revisión de resultados (bajo demanda) y radar semanal de noticias ---

@router.post("/theses/{thesis_id}/reviews")
def start_results_review(thesis_id: int, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    """Lanza en segundo plano la revisión de resultados con IA (documentos, cifras, call, juicio de la tesis)."""
    _get_or_404(repo, thesis_id)
    review_id = thesis_review_service.start_review(thesis_id)
    if review_id is None:
        raise HTTPException(status_code=409, detail="Ya hay una revisión en curso para esta tesis")
    return {"status": "running", "review_id": review_id}


@router.get("/theses/reviews/{review_id}")
def get_results_review(review_id: int, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    review = repo.get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="La revisión no existe")
    return review


class ReviewStatus(BaseModel):
    status: str   # 'applied' (KPIs guardados) | 'discarded'


@router.post("/theses/reviews/{review_id}/status")
def set_results_review_status(review_id: int, payload: ReviewStatus,
                              repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    if payload.status not in ("applied", "discarded", "ready"):
        raise HTTPException(status_code=400, detail="Estado no válido")
    if not repo.set_review_status(review_id, payload.status):
        raise HTTPException(status_code=404, detail="La revisión no existe o sigue en curso")
    return {"status": "success"}


@router.get("/theses/{thesis_id}/news")
def get_news(thesis_id: int, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    digest = _get_or_404(repo, thesis_id)["extra"].get("news_digest")
    return {"digest": digest, "due": thesis_ai_service.news_due(digest)}


@router.post("/theses/{thesis_id}/news")
def run_news(thesis_id: int, force: bool = False, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    """Radar semanal: si la última consulta tiene menos de 7 días devuelve la guardada, salvo force=true."""
    thesis = _get_or_404(repo, thesis_id)
    digest = thesis["extra"].get("news_digest")
    if not force and not thesis_ai_service.news_due(digest):
        return {"digest": digest, "cached": True}
    try:
        digest = thesis_news_runner.run_news_for_thesis(repo, thesis)
    except Exception as e:
        logger.exception("Radar de noticias falló para %s", thesis["ticker"])
        raise HTTPException(status_code=502, detail=f"El radar de noticias falló: {e}")
    return {"digest": digest, "cached": False}


class NewsSeen(BaseModel):
    ids: Optional[List[int]] = None   # None = marcar todas como leídas


@router.get("/theses/news/unread-count")
def news_unread_count(repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    return {**repo.unread_news_count(), "radar_running": thesis_news_runner.is_running()}


@router.get("/theses/news/all")
def list_all_news(unread: bool = False, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    return repo.list_news(unread_only=unread)


@router.post("/theses/news/seen")
def mark_news_seen(payload: NewsSeen, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    return {"status": "success", "updated": repo.mark_news_seen(payload.ids)}


@router.post("/theses/news/{news_id}/timeline")
def news_to_timeline(news_id: int, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    n = repo.get_news(news_id)
    if not n:
        raise HTTPException(status_code=404, detail="La noticia no existe")
    if not n["in_timeline"]:
        body = "\n".join(x for x in (n["summary"], n["url"]) if x)
        repo.add_event(n["thesis_id"], n["date"] or date.today().isoformat(), "news", n["title"], body, n["impact"])
        repo.mark_news_in_timeline(news_id)
    return {"status": "success"}


# --- Cambios en la tesis a partir de propuestas ---

class ProposalRef(BaseModel):
    review_id: int
    index: int


class ThesisChange(BaseModel):
    action: str                       # 'add_kpi' | 'change_threshold'
    kpi_key: Optional[str] = None
    step_name: Optional[str] = None
    step_question: Optional[str] = None
    kpi: dict
    rationale: str


class WatchItem(BaseModel):
    type: str                         # 'riesgo' | 'catalizador' | 'otro'
    title: str
    rationale: Optional[str] = None


def _proposal(repo, thesis_id: int, ref: ProposalRef) -> dict:
    review = repo.get_review(ref.review_id)
    if not review or review["thesis_id"] != thesis_id:
        raise HTTPException(status_code=404, detail="La revisión no existe")
    proposals = review["report"].get("proposals") or []
    if not 0 <= ref.index < len(proposals):
        raise HTTPException(status_code=404, detail="La propuesta no existe")
    return proposals[ref.index]


@router.post("/theses/{thesis_id}/changes/draft")
def draft_thesis_change(thesis_id: int, ref: ProposalRef, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    """La IA convierte la propuesta en un cambio concreto (umbrales, zonas…) para que el usuario lo revise."""
    thesis = _get_or_404(repo, thesis_id)
    proposal = _proposal(repo, thesis_id, ref)
    try:
        change = thesis_changes_service.draft_change(thesis, proposal)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"No se pudo preparar el cambio: {e}")
    return {"change": change, "rationale": proposal.get("rationale") or "", "title": proposal.get("title")}


@router.post("/theses/{thesis_id}/changes/apply")
def apply_thesis_change(thesis_id: int, payload: ThesisChange, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    """Escribe el cambio en el YAML de la tesis (con copia de la versión anterior), reimporta y lo anota en el timeline."""
    thesis = _get_or_404(repo, thesis_id)
    spec_path = thesis["extra"].get("spec_path")
    if not spec_path or not os.path.exists(spec_path):
        raise HTTPException(status_code=400, detail="No se encuentra el YAML de la tesis; reimpórtala primero")
    if payload.action not in ("add_kpi", "change_threshold"):
        raise HTTPException(status_code=400, detail="Acción no válida")
    keys = {k["key"] for p in thesis["pillars"] for k in p["kpis"]} | {p["key"] for p in thesis["pillars"]}
    try:
        backup = thesis_changes_service.apply_to_yaml(spec_path, payload.model_dump(), keys)
        repo.import_spec(load_thesis_yaml(spec_path))
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    what = (f"Nuevo KPI: {payload.kpi.get('name')}" if payload.action == "add_kpi"
            else f"Umbrales de {payload.kpi_key} actualizados")
    repo.add_event(thesis_id, date.today().isoformat(), "thesis", f"Cambio en la tesis · {what}",
                   f"Motivo: {payload.rationale}\nVersión anterior guardada en {backup}", "neutral")
    return {"status": "success", "backup": backup}


@router.post("/theses/{thesis_id}/watchlist")
def add_watch_item(thesis_id: int, payload: WatchItem, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    thesis = _get_or_404(repo, thesis_id)
    items = list(thesis["extra"].get("watchlist") or [])
    items.append({"type": payload.type, "title": payload.title.strip(), "rationale": payload.rationale,
                  "added": date.today().isoformat()})
    repo.set_extra(thesis_id, "watchlist", items)
    label = {"riesgo": "Nuevo riesgo", "catalizador": "Nuevo catalizador"}.get(payload.type, "Nuevo punto a vigilar")
    repo.add_event(thesis_id, date.today().isoformat(), "thesis", f"{label}: {payload.title.strip()}", payload.rationale,
                   "negative" if payload.type == "riesgo" else "neutral")
    return {"status": "success"}


@router.delete("/theses/{thesis_id}/watchlist/{index}")
def remove_watch_item(thesis_id: int, index: int, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    thesis = _get_or_404(repo, thesis_id)
    items = list(thesis["extra"].get("watchlist") or [])
    if not 0 <= index < len(items):
        raise HTTPException(status_code=404, detail="El punto no existe")
    removed = items.pop(index)
    repo.set_extra(thesis_id, "watchlist", items)
    repo.add_event(thesis_id, date.today().isoformat(), "thesis", f"Deja de vigilarse: {removed['title']}", None, "neutral")
    return {"status": "success"}


# --- Diario de decisiones ---

class DecisionCreate(BaseModel):
    date: str
    action: str
    reason: str
    price: Optional[float] = None
    shares: Optional[float] = None
    transaction_ref: Optional[str] = None


class DecisionReview(BaseModel):
    lesson: str


@router.post("/theses/{thesis_id}/decisions")
def add_decision(thesis_id: int, payload: DecisionCreate, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository),
                 asset_repo=Depends(get_asset_repository), portfolio_service=Depends(get_portfolio_service)):
    """Anota una decisión con la fotografía de la tesis en ese momento (veredicto, health score y peso en cartera)."""
    from app.interfaces.views.thesis_views import portfolio_exposure
    thesis = _get_or_404(repo, thesis_id)
    if payload.action not in DECISION_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Acción no válida. Opciones: {', '.join(DECISION_ACTIONS)}")
    if not payload.reason.strip():
        raise HTTPException(status_code=400, detail="El motivo es obligatorio: es lo que da valor al diario")
    position = portfolio_exposure(portfolio_service, asset_repo)["positions"].get(thesis["ticker"])
    decision = repo.add_decision(thesis_id, {
        **payload.model_dump(), "reason": payload.reason.strip(), "verdict": thesis["verdict"],
        "health_score": evaluate_thesis(thesis)["health_score"], "weight": round(position["weight"], 2) if position else None})
    repo.add_event(thesis_id, payload.date, "decision", f"Decisión: {payload.action}"
                   + (f" a {payload.price:g}" if payload.price else ""), payload.reason.strip(), "neutral")
    return {"status": "success", "decision": decision}


@router.post("/theses/decisions/{decision_id}/review")
def review_decision(decision_id: int, payload: DecisionReview, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    if not payload.lesson.strip():
        raise HTTPException(status_code=400, detail="Escribe qué has aprendido")
    if not repo.review_decision(decision_id, payload.lesson.strip()):
        raise HTTPException(status_code=404, detail="La decisión no existe")
    return {"status": "success"}


@router.delete("/theses/decisions/{decision_id}")
def delete_decision(decision_id: int, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    if not repo.delete_decision(decision_id):
        raise HTTPException(status_code=404, detail="La decisión no existe")
    return {"status": "success"}


# --- Tesis nueva desde un PDF ---

class NewThesisConfirm(BaseModel):
    ticker: str
    yaml_text: str
    md_text: str


def _safe_ticker(ticker: str) -> str:
    import re
    t = (ticker or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", t):
        raise HTTPException(status_code=400, detail="Ticker no válido")
    return t


@router.post("/theses/from-pdf")
async def thesis_from_pdf(ticker: str = Form(...), file: UploadFile = File(...)):
    """Guarda el PDF, lo convierte a Markdown por apartados y la IA propone el YAML. No importa nada todavía."""
    import yaml as _yaml
    from app.application.services.thesis_pdf import convert, draft_thesis_yaml
    from app.application.services.thesis_service import validate_spec
    from starlette.concurrency import run_in_threadpool

    t = _safe_ticker(ticker)
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Sube un PDF")
    os.makedirs("data/theses", exist_ok=True)
    name = os.path.basename(file.filename).replace(" ", "_")
    pdf_path = os.path.join("data", "theses", name)
    with open(pdf_path, "wb") as f:
        f.write(await file.read())
    try:
        md_text = await run_in_threadpool(convert, pdf_path)
        yaml_text = await run_in_threadpool(draft_thesis_yaml, md_text, t, pdf_path, f"config/theses/{t.lower()}.md")
    except Exception as e:
        logger.exception("No se pudo preparar la tesis desde PDF")
        raise HTTPException(status_code=502, detail=f"No se pudo preparar la tesis: {e}")
    problems = None
    try:
        validate_spec(_yaml.safe_load(yaml_text), "Propuesta")
    except Exception as e:
        problems = str(e)
    return {"ticker": t, "yaml_text": yaml_text, "md_text": md_text, "problems": problems,
            "exists": os.path.exists(f"config/theses/{t.lower()}.yaml")}


@router.post("/theses/from-pdf/confirm")
def confirm_thesis_from_pdf(payload: NewThesisConfirm, repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    """Escribe el YAML y el documento revisados por el usuario y los importa (si ya existía, guarda la versión anterior)."""
    import shutil
    import yaml as _yaml
    from datetime import datetime as _dt
    from app.application.services.thesis_service import validate_spec

    t = _safe_ticker(payload.ticker)
    try:
        spec = validate_spec(_yaml.safe_load(payload.yaml_text), "YAML")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"El YAML no es válido: {e}")
    if str(spec.get("ticker", "")).upper() != t:
        raise HTTPException(status_code=400, detail=f"El ticker del YAML ({spec.get('ticker')}) no coincide con {t}")
    yaml_path, md_path = f"config/theses/{t.lower()}.yaml", f"config/theses/{t.lower()}.md"
    if os.path.exists(yaml_path):
        os.makedirs(thesis_changes_service.HISTORY_DIR, exist_ok=True)
        shutil.copy2(yaml_path, os.path.join(thesis_changes_service.HISTORY_DIR, f"{t.lower()}-{_dt.now():%Y%m%d-%H%M%S}.yaml"))
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(payload.md_text)
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(payload.yaml_text)
    try:
        thesis = repo.import_spec(load_thesis_yaml(yaml_path))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"No se pudo importar: {e}")
    return {"status": "success", "thesis_id": thesis["id"]}
