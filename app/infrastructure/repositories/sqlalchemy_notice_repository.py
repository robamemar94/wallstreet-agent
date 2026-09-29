from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.infrastructure.db.models import DBNotice
from app.application.services.thesis_service import now_iso

FIELDS = ("ticker", "kind", "tone", "text", "date", "since")
HISTORY_DAYS = 180   # el histórico se limpia solo: se borran los episodios que llevan ~6 meses sin aparecer


def _dict(n: DBNotice) -> Dict[str, Any]:
    return {"id": n.id, "key": n.key, "scope": n.scope, "category": n.category, "first_seen": n.first_seen,
            "last_seen": n.last_seen, "active": bool(n.active), "seen_at": n.seen_at, **{f: getattr(n, f) for f in FIELDS}}


class SqlAlchemyNoticeRepository:
    def __init__(self, db: Session):
        self.db = db

    def sync(self, scope: str, category: str, items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """items: los avisos de ahora, cada uno con 'key'. Actualiza los episodios activos, abre los nuevos y cierra
        (sin borrar) los que ya no aparecen. Devuelve {key: episodio} de los activos."""
        now = now_iso()
        active = {n.key: n for n in self.db.query(DBNotice).filter_by(scope=scope, category=category, active=True)}
        current = {}
        for it in items:
            n = active.pop(it["key"], None) or current.get(it["key"])
            if n is None:
                n = DBNotice(key=it["key"], scope=scope, category=category, first_seen=now, active=True)
                self.db.add(n)
            for f in FIELDS:
                if it.get(f) is not None or f == "since":
                    setattr(n, f, it.get(f))
            n.text = it.get("text") or n.text or ""
            n.last_seen = now
            current[it["key"]] = n
        for n in active.values():   # ya no aparecen: pasan al histórico
            n.active = False
        cutoff = (datetime.now() - timedelta(days=HISTORY_DAYS)).isoformat(timespec="seconds")
        self.db.query(DBNotice).filter(DBNotice.scope == scope, DBNotice.active.is_(False),
                                       DBNotice.last_seen < cutoff).delete(synchronize_session="fetch")
        self.db.commit()
        return {k: _dict(n) for k, n in current.items()}

    def set_seen(self, keys: List[str], seen: bool = True) -> int:
        rows = self.db.query(DBNotice).filter(DBNotice.key.in_(keys), DBNotice.active.is_(True)).all()
        for n in rows:
            n.seen_at = now_iso() if seen else None
        self.db.commit()
        return len(rows)

    def history(self, scope: str, category: str, limit: int = 200) -> List[Dict[str, Any]]:
        q = (self.db.query(DBNotice).filter_by(scope=scope, category=category, active=False)
             .order_by(DBNotice.last_seen.desc()).limit(limit))
        return [_dict(n) for n in q]

    def delete_inactive(self, notice_id: int) -> bool:
        n = self.db.get(DBNotice, notice_id)
        if not n or n.active:
            return False
        self.db.delete(n)
        self.db.commit()
        return True

    def clear_history(self, scope: str, category: str) -> int:
        deleted = self.db.query(DBNotice).filter_by(scope=scope, category=category, active=False).delete()
        self.db.commit()
        return deleted

