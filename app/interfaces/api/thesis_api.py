import glob
import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.application.services.thesis_service import (
    AUTO_METRICS, EVENT_TYPES, STATUSES, VERDICTS, classify, evaluate_thesis, fetch_auto_metrics,
    load_thesis_yaml,
)
from app.application.services import thesis_ai_service, thesis_news_runner, thesis_review_service
from app.infrastructure.dependencies import get_thesis_repository
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
def thesis_notices(repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository)):
    """Avisos para la barra lateral: resultados sin revisar y revisiones listas."""
    notices = thesis_review_service.compute_notices(repo)
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
