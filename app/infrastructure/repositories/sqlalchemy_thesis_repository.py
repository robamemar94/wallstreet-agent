from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.domain.repositories.thesis_repository import ThesisRepositoryInterface
from app.infrastructure.db.models import DBThesis, DBThesisPillar, DBThesisKPI, DBKPIObservation, DBThesisEvent, DBThesisNews, DBThesisReview
from app.application.services.thesis_service import classify, now_iso

KPI_FIELDS = ["name", "how_to_measure", "baseline", "kind", "unit", "direction", "green_threshold",
              "red_threshold", "green_text", "amber_text", "red_text", "frequency", "source",
              "auto_metric", "weight", "is_kill_switch", "kill_rule"]
# Datos generados en la app (IA) que no vienen del YAML y deben sobrevivir a una reimportación
RUNTIME_EXTRA_KEYS = ("ai_review", "news_digest")
THESIS_FIELDS = ["title", "summary", "status", "verdict", "version", "reference_price",
                 "reference_date", "source_document"]


def _obs_dict(o: DBKPIObservation) -> Dict[str, Any]:
    return {"id": o.id, "period": o.period, "date": o.date, "value": o.value, "status": o.status,
            "note": o.note, "source": o.source}


def _news_dict(n: DBThesisNews) -> Dict[str, Any]:
    return {"id": n.id, "thesis_id": n.thesis_id, "ticker": n.thesis.ticker if n.thesis else None, "found_at": n.found_at,
            "date": n.date, "title": n.title, "url": n.url, "step": n.step, "step_name": n.step_name, "impact": n.impact,
            "materiality": n.materiality, "summary": n.summary, "seen": bool(n.seen), "in_timeline": bool(n.in_timeline)}


def _norm_title(title: str) -> str:
    return " ".join((title or "").lower().split())


def _kpi_dict(k: DBThesisKPI) -> Dict[str, Any]:
    d = {"id": k.id, "key": k.key, "pillar_id": k.pillar_id, "position": k.position}
    d.update({f: getattr(k, f) for f in KPI_FIELDS})
    d["observations"] = [_obs_dict(o) for o in sorted(k.observations, key=lambda o: o.date)]
    return d


def _thesis_dict(t: DBThesis) -> Dict[str, Any]:
    d = {"id": t.id, "ticker": t.ticker, "created_at": t.created_at, "updated_at": t.updated_at,
         "extra": t.extra or {}}
    d.update({f: getattr(t, f) for f in THESIS_FIELDS})
    d["pillars"] = [{
        "id": p.id, "key": p.key, "name": p.name, "description": p.description, "weight": p.weight,
        "kpis": [_kpi_dict(k) for k in sorted(p.kpis, key=lambda k: k.position)],
    } for p in sorted(t.pillars, key=lambda p: p.position)]
    d["events"] = [{"id": e.id, "date": e.date, "type": e.type, "title": e.title, "body": e.body,
                    "impact": e.impact} for e in sorted(t.events, key=lambda e: (e.date, e.id), reverse=True)]
    return d


class SqlAlchemyThesisRepository(ThesisRepositoryInterface):
    def __init__(self, db: Session):
        self.db = db

    def list_theses(self) -> List[Dict[str, Any]]:
        return [_thesis_dict(t) for t in self.db.query(DBThesis).order_by(DBThesis.ticker).all()]

    def get_thesis(self, thesis_id: int) -> Optional[Dict[str, Any]]:
        t = self.db.get(DBThesis, thesis_id)
        return _thesis_dict(t) if t else None

    def get_kpi(self, kpi_id: int) -> Optional[Dict[str, Any]]:
        k = self.db.get(DBThesisKPI, kpi_id)
        return _kpi_dict(k) if k else None

    def import_spec(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Crea o actualiza una tesis desde su especificación YAML.
        Pilares y KPIs se emparejan por 'key': se actualiza su definición sin perder observaciones.
        Las observaciones y eventos iniciales solo se insertan si no existen ya."""
        ts = now_iso()
        ticker = spec["ticker"].strip().upper()
        thesis = self.db.query(DBThesis).filter(DBThesis.ticker == ticker).first()
        if not thesis:
            thesis = DBThesis(ticker=ticker, created_at=ts, updated_at=ts)
            self.db.add(thesis)
        for f in THESIS_FIELDS:
            if f in spec:
                setattr(thesis, f, spec[f])
        runtime = {k: v for k, v in (thesis.extra or {}).items() if k in RUNTIME_EXTRA_KEYS}
        thesis.extra = {**{k: spec[k] for k in ("ratings", "rules", "valuation", "hypothesis", "hypotheses",
                                                "master_questions", "final_question", "document", "document_md") if k in spec}, **runtime}
        thesis.updated_at = ts
        self.db.flush()

        existing_pillars = {p.key: p for p in thesis.pillars}
        existing_kpis = {k.key: k for k in thesis.kpis}
        seen_pillars, seen_kpis = set(), set()

        for p_pos, p_spec in enumerate(spec["pillars"]):
            pillar = existing_pillars.get(p_spec["key"])
            if not pillar:
                pillar = DBThesisPillar(thesis_id=thesis.id, key=p_spec["key"])
                self.db.add(pillar)
            pillar.name = p_spec["name"]
            pillar.description = p_spec.get("description")
            pillar.weight = p_spec.get("weight", 1.0)
            pillar.position = p_pos
            self.db.flush()
            seen_pillars.add(p_spec["key"])

            for k_pos, k_spec in enumerate(p_spec.get("kpis", [])):
                kpi = existing_kpis.get(k_spec["key"])
                if not kpi:
                    kpi = DBThesisKPI(thesis_id=thesis.id, key=k_spec["key"])
                    self.db.add(kpi)
                kpi.pillar_id = pillar.id
                kpi.position = k_pos
                kpi.kind = k_spec.get("kind", "numeric")
                kpi.direction = k_spec.get("direction", "higher")
                kpi.weight = k_spec.get("weight", 1.0)
                kpi.is_kill_switch = bool(k_spec.get("is_kill_switch", False))
                for f in KPI_FIELDS:
                    if f not in ("kind", "direction", "weight", "is_kill_switch"):
                        setattr(kpi, f, k_spec.get(f))
                self.db.flush()
                seen_kpis.add(k_spec["key"])

                known_periods = {o.period for o in kpi.observations}
                for o in k_spec.get("observations", []):
                    if o["period"] in known_periods:
                        continue
                    status = o.get("status") or classify(_kpi_dict(kpi), o.get("value"))
                    if not status:
                        raise ValueError(f"Observación sin estado en KPI '{kpi.key}' ({o['period']})")
                    self.db.add(DBKPIObservation(kpi_id=kpi.id, period=o["period"], date=str(o["date"]),
                                                 value=o.get("value"), status=status, note=o.get("note"),
                                                 source=o.get("source", "tesis"), created_at=ts))

        # Recargar antes de borrar: los KPIs pueden haberse movido de pilar y las colecciones en memoria estarían obsoletas
        self.db.expire_all()
        for kpi in self.db.query(DBThesisKPI).filter(DBThesisKPI.thesis_id == thesis.id):
            if kpi.key not in seen_kpis:
                self.db.delete(kpi)
        for pillar in self.db.query(DBThesisPillar).filter(DBThesisPillar.thesis_id == thesis.id):
            if pillar.key not in seen_pillars:
                self.db.delete(pillar)
        self.db.flush()

        known_events = {(e.date, e.title) for e in thesis.events}
        for e in spec.get("events", []):
            if (str(e["date"]), e["title"]) not in known_events:
                self.db.add(DBThesisEvent(thesis_id=thesis.id, date=str(e["date"]), type=e.get("type", "note"),
                                          title=e["title"], body=e.get("body"), impact=e.get("impact"),
                                          created_at=ts))
        self.db.commit()
        self.db.refresh(thesis)
        return _thesis_dict(thesis)

    def update_thesis(self, thesis_id: int, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        thesis = self.db.get(DBThesis, thesis_id)
        if not thesis:
            return None
        for f, v in fields.items():
            if f in THESIS_FIELDS:
                setattr(thesis, f, v)
        thesis.updated_at = now_iso()
        self.db.commit()
        self.db.refresh(thesis)
        return _thesis_dict(thesis)

    def set_extra(self, thesis_id: int, key: str, value: Any) -> bool:
        thesis = self.db.get(DBThesis, thesis_id)
        if not thesis:
            return False
        thesis.extra = {**(thesis.extra or {}), key: value}  # reasignar para que SQLAlchemy detecte el cambio en JSON
        self.db.commit()
        return True

    def upsert_observation(self, kpi_id: int, period: str, date: str, value: Optional[float],
                           status: str, note: Optional[str], source: Optional[str]) -> Optional[Dict[str, Any]]:
        kpi = self.db.get(DBThesisKPI, kpi_id)
        if not kpi:
            return None
        obs = next((o for o in kpi.observations if o.period == period), None)
        if not obs:
            obs = DBKPIObservation(kpi_id=kpi_id, period=period, created_at=now_iso())
            self.db.add(obs)
        obs.date, obs.value, obs.status, obs.note, obs.source = date, value, status, note, source
        kpi.thesis.updated_at = now_iso()
        self.db.commit()
        return _obs_dict(obs)

    def delete_observation(self, observation_id: int) -> bool:
        obs = self.db.get(DBKPIObservation, observation_id)
        if not obs:
            return False
        self.db.delete(obs)
        self.db.commit()
        return True

    def add_event(self, thesis_id: int, date: str, type: str, title: str,
                  body: Optional[str] = None, impact: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not self.db.get(DBThesis, thesis_id):
            return None
        e = DBThesisEvent(thesis_id=thesis_id, date=date, type=type, title=title, body=body, impact=impact,
                          created_at=now_iso())
        self.db.add(e)
        self.db.commit()
        return {"id": e.id, "date": e.date, "type": e.type, "title": e.title, "body": e.body, "impact": e.impact}

    def delete_event(self, event_id: int) -> bool:
        e = self.db.get(DBThesisEvent, event_id)
        if not e:
            return False
        self.db.delete(e)
        self.db.commit()
        return True

    # --- Noticias del radar ---

    def add_news(self, thesis_id: int, items: List[Dict[str, Any]], step_names: List[str]) -> int:
        """Guarda las noticias nuevas del radar. Descarta las ya vistas en radares anteriores (misma URL o titular)."""
        existing = self.db.query(DBThesisNews.url, DBThesisNews.title).filter(DBThesisNews.thesis_id == thesis_id).all()
        seen_urls = {u for u, _ in existing if u}
        seen_titles = {_norm_title(t) for _, t in existing}
        ts, added = now_iso(), 0
        for it in items:
            if (it.get("url") and it["url"] in seen_urls) or _norm_title(it["title"]) in seen_titles:
                continue
            step = it.get("step")
            self.db.add(DBThesisNews(
                thesis_id=thesis_id, found_at=ts, date=it.get("date"), title=it["title"], url=it.get("url") or None,
                step=step, step_name=step_names[step - 1] if step and 0 < step <= len(step_names) else None,
                impact=it.get("impact"), materiality=it["materiality"], summary=it.get("summary"), seen=False))
            seen_urls.add(it.get("url"))
            seen_titles.add(_norm_title(it["title"]))
            added += 1
        self.db.commit()
        return added

    def list_news(self, thesis_id: Optional[int] = None, unread_only: bool = False, limit: int = 200) -> List[Dict[str, Any]]:
        q = self.db.query(DBThesisNews)
        if thesis_id:
            q = q.filter(DBThesisNews.thesis_id == thesis_id)
        if unread_only:
            q = q.filter(DBThesisNews.seen.is_(False))
        rows = q.order_by(DBThesisNews.found_at.desc(), DBThesisNews.materiality.desc(), DBThesisNews.date.desc()).limit(limit)
        return [_news_dict(n) for n in rows]

    def unread_news_count(self) -> Dict[str, Any]:
        rows = self.db.query(DBThesisNews).filter(DBThesisNews.seen.is_(False)).all()
        return {"unread": len(rows), "critical": sum(1 for n in rows if n.materiality >= 5)}

    def mark_news_seen(self, ids: Optional[List[int]] = None) -> int:
        q = self.db.query(DBThesisNews).filter(DBThesisNews.seen.is_(False))
        if ids:
            q = q.filter(DBThesisNews.id.in_(ids))
        n = q.update({DBThesisNews.seen: True}, synchronize_session=False)
        self.db.commit()
        return n

    def get_news(self, news_id: int) -> Optional[Dict[str, Any]]:
        n = self.db.get(DBThesisNews, news_id)
        return _news_dict(n) if n else None

    def mark_news_in_timeline(self, news_id: int) -> None:
        n = self.db.get(DBThesisNews, news_id)
        if n:
            n.in_timeline, n.seen = True, True
            self.db.commit()

    # --- Revisiones de resultados con IA ---

    @staticmethod
    def _review_dict(r: DBThesisReview) -> Dict[str, Any]:
        return {"id": r.id, "thesis_id": r.thesis_id, "period": r.period, "status": r.status, "report": r.report or {},
                "error": r.error, "created_at": r.created_at, "finished_at": r.finished_at}

    def create_review(self, thesis_id: int) -> int:
        r = DBThesisReview(thesis_id=thesis_id, status="running", report={}, created_at=now_iso())
        self.db.add(r)
        self.db.commit()
        return r.id

    def finish_review(self, review_id: int, status: str, report: Optional[Dict[str, Any]] = None,
                      error: Optional[str] = None) -> None:
        r = self.db.get(DBThesisReview, review_id)
        if not r:
            return
        r.status, r.error, r.finished_at = status, error, now_iso()
        if report is not None:
            r.report, r.period = report, report.get("period")
        self.db.commit()

    def fail_stale_reviews(self) -> int:
        """Al arrancar la app no puede haber revisiones en curso: si las hay, murieron con el proceso anterior."""
        rows = self.db.query(DBThesisReview).filter(DBThesisReview.status == "running").all()
        for r in rows:
            r.status, r.error, r.finished_at = "error", "Interrumpida: la app se reinició durante la revisión. Vuelve a lanzarla.", now_iso()
        self.db.commit()
        return len(rows)

    def list_reviews(self, thesis_id: int) -> List[Dict[str, Any]]:
        rows = self.db.query(DBThesisReview).filter(DBThesisReview.thesis_id == thesis_id).order_by(DBThesisReview.id.desc())
        return [self._review_dict(r) for r in rows]

    def get_review(self, review_id: int) -> Optional[Dict[str, Any]]:
        r = self.db.get(DBThesisReview, review_id)
        return self._review_dict(r) if r else None

    def set_review_status(self, review_id: int, status: str) -> bool:
        r = self.db.get(DBThesisReview, review_id)
        if not r or r.status in ("running",):
            return False
        r.status = status
        self.db.commit()
        return True
