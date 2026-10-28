"""
Diario de una empresa (ficha → botón «Diario», panel lateral): tus notas libres con fecha y, en la misma línea de tiempo,
lo que ya guarda la app: tus operaciones con su porqué (asset.data['tx_notes'], el mismo de Decisiones), los cambios de
veredicto de la tesis, sus notas y las presentaciones de resultados.
Las notas sirven para cualquier empresa (en cartera, en la watchlist o ninguna). La carta anual recibe las del año
(`notes_between`), para juzgar tus razones y no solo los resultados.
"""
import datetime
from typing import Any, Dict, List, Optional

from app.infrastructure.db.models import DBCompanyNote, DBThesis, DBThesisEvent

MAX_LEN = 2000          # caracteres de una nota
AI_NOTE_LEN = 300       # en la carta anual, cada nota recortada a esto
AI_MAX_NOTES = 80


def _row(n: DBCompanyNote) -> Dict[str, Any]:
    return {"id": n.id, "ticker": n.ticker, "date": n.date, "text": n.text, "created_at": n.created_at, "updated_at": n.updated_at}


def _clean(text: str) -> str:
    text = (text or "").strip()[:MAX_LEN]
    if not text:
        raise ValueError("La nota está vacía")
    return text


def _day(date: Optional[str]) -> str:
    if not date:
        return datetime.date.today().isoformat()
    return datetime.date.fromisoformat(str(date)[:10]).isoformat()   # ValueError si no es una fecha


def add(db, ticker: str, text: str, date: Optional[str] = None) -> Dict[str, Any]:
    n = DBCompanyNote(ticker=ticker.upper(), date=_day(date), text=_clean(text),
                      created_at=datetime.datetime.now().isoformat(timespec="seconds"))
    db.add(n)
    db.commit()
    return _row(n)


def update(db, note_id: int, text: str, date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    n = db.get(DBCompanyNote, note_id)
    if not n:
        return None
    n.text, n.date = _clean(text), _day(date) if date else n.date
    n.updated_at = datetime.datetime.now().isoformat(timespec="seconds")
    db.commit()
    return _row(n)


def delete(db, note_id: int) -> bool:
    n = db.get(DBCompanyNote, note_id)
    if not n:
        return False
    db.delete(n)
    db.commit()
    return True


def notes(db, ticker: str) -> List[Dict[str, Any]]:
    q = db.query(DBCompanyNote).filter(DBCompanyNote.ticker == ticker.upper())
    return [_row(n) for n in q.order_by(DBCompanyNote.date.desc(), DBCompanyNote.id.desc()).all()]


def all_notes(db, ticker: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
    """Las notas de todas las empresas (o de una), de la más reciente a la más antigua: la pantalla Diario."""
    q = db.query(DBCompanyNote)
    if ticker:
        q = q.filter(DBCompanyNote.ticker == ticker.upper())
    return [_row(n) for n in q.order_by(DBCompanyNote.date.desc(), DBCompanyNote.id.desc()).limit(limit).all()]


def written(db, txs: List[Dict[str, Any]], asset_data: Dict[str, Dict[str, Any]], ticker: Optional[str] = None,
            splits=None) -> List[Dict[str, Any]]:
    """Todo lo que has escrito, de lo más reciente a lo más antiguo: tus notas («note») y el porqué de tus operaciones
    («op», las órdenes del mismo día juntas). La pantalla Diario."""
    from app.application.services.decision_review_service import group_decisions
    t = ticker.upper() if ticker else None
    items = [{"kind": "note", **n} for n in all_notes(db, t)]
    for g in group_decisions([x for x in txs if not t or x["ticker"] == t], asset_data, None, splits):
        if g["note"]:
            items.append({"kind": "op", "ticker": g["ticker"], "date": g["date"], "type": g["type"], "shares": g["shares"],
                          "price": g["price"], "orders": g["orders"], "refs": g["refs"], "text": g["note"]})
    items.sort(key=lambda i: (i["date"], i["kind"] == "note", i.get("id") or 0), reverse=True)
    return items


def noted_tickers(db) -> List[str]:
    return sorted(t for (t,) in db.query(DBCompanyNote.ticker).distinct().all())


def missing_reasons(txs: List[Dict[str, Any]], asset_data: Dict[str, Dict[str, Any]], since: str, splits=None) -> List[Dict[str, Any]]:
    """Tus operaciones desde «since» sin el porqué anotado (las órdenes del mismo día, juntas), de la más reciente."""
    from app.application.services.decision_review_service import group_decisions
    groups = group_decisions([x for x in txs if x["date"][:10] >= since], asset_data, None, splits)
    return [{k: g[k] for k in ("ticker", "date", "type", "shares", "price", "currency", "orders", "refs")} for g in groups if not g["note"]]


def count(db, ticker: str) -> int:
    return db.query(DBCompanyNote).filter(DBCompanyNote.ticker == ticker.upper()).count()


def timeline(db, ticker: str, txs: List[Dict[str, Any]], asset_data: Dict[str, Any], splits=None) -> List[Dict[str, Any]]:
    """Todo junto, de lo más reciente a lo más antiguo:
    - note: tus notas;
    - op: tus compras y ventas (las órdenes del mismo día, juntas) con su porqué;
    - verdict / earnings / thesis_note: de la tesis (cambios de veredicto, resultados y sus notas)."""
    from app.application.services.decision_review_service import group_decisions
    t = ticker.upper()
    items = [{"kind": "note", **n} for n in notes(db, t)]
    for g in group_decisions([x for x in txs if x["ticker"] == t], {t: asset_data or {}}, None, {t: splits or []}):
        items.append({"kind": "op", "date": g["date"], "type": g["type"], "shares": g["shares"], "price": g["price"],
                      "currency": g.get("currency"), "orders": g["orders"], "refs": g["refs"], "text": g["note"]})
    thesis = db.query(DBThesis).filter(DBThesis.ticker == t).first()
    if thesis:
        kinds = {"verdict": "verdict", "earnings": "earnings", "note": "thesis_note"}
        for e in db.query(DBThesisEvent).filter(DBThesisEvent.thesis_id == thesis.id, DBThesisEvent.type.in_(tuple(kinds))).all():
            items.append({"kind": kinds[e.type], "date": str(e.date)[:10], "title": e.title, "text": e.body, "impact": e.impact})
    order = {"note": 0, "op": 1, "thesis_note": 2, "verdict": 3, "earnings": 4}   # el mismo día, lo tuyo primero
    items.sort(key=lambda i: (i["date"], -order[i["kind"]], i.get("id") or 0), reverse=True)
    return items


def notes_between(db, start: str, end: str, limit: int = AI_MAX_NOTES) -> List[Dict[str, Any]]:
    """Tus notas del periodo (todas las empresas), recortadas, para la IA de los informes."""
    q = (db.query(DBCompanyNote).filter(DBCompanyNote.date >= start, DBCompanyNote.date <= end)
         .order_by(DBCompanyNote.date.desc()).limit(limit))
    return [{"ticker": n.ticker, "date": n.date, "text": n.text[:AI_NOTE_LEN]} for n in q.all()]
