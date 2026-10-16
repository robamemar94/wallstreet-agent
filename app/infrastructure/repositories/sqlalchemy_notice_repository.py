from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.infrastructure.db.models import DBNotice
from app.application.services.thesis_service import now_iso

FIELDS = ("ticker", "kind", "tone", "text", "date", "since", "href")
HISTORY_DAYS = 180   # el histórico se limpia solo: se borran los episodios que llevan ~6 meses sin aparecer
# Un aviso que vuelve a salir dentro de este plazo es el mismo episodio (el precio baila alrededor del umbral):
# se reabre con su estado de visto en vez de notificar otra vez. La misma señal de la misma sesión, siempre.
REOPEN_DAYS = 7


def _dict(n: DBNotice) -> Dict[str, Any]:
    return {"id": n.id, "key": n.key, "scope": n.scope, "category": n.category, "first_seen": n.first_seen,
            "last_seen": n.last_seen, "active": bool(n.active), "seen_at": n.seen_at, "dismissed": bool(n.dismissed_at), **{f: getattr(n, f) for f in FIELDS}}


class SqlAlchemyNoticeRepository:
    def __init__(self, db: Session):
        self.db = db

    def sync(self, scope: str, category: str, items: List[Dict[str, Any]],
             keep_tickers: Optional[set] = None) -> Dict[str, Dict[str, Any]]:
        """items: los avisos de ahora, cada uno con 'key'. Actualiza los episodios activos, abre los nuevos y cierra
        (sin borrar) los que ya no aparecen. keep_tickers: empresas sin datos en esta carga (p. ej. Yahoo falló):
        sus episodios activos no se cierran. Devuelve {key: episodio} de los activos."""
        now = now_iso()
        active = {n.key: n for n in self.db.query(DBNotice).filter_by(scope=scope, category=category, active=True)}
        current = {}
        for it in items:
            n = active.pop(it["key"], None) or current.get(it["key"]) or self._recent(scope, category, it)
            if n is not None:
                n.active = True
            else:
                n = DBNotice(key=it["key"], scope=scope, category=category, first_seen=now, active=True)
                self.db.add(n)
            for f in FIELDS:
                if it.get(f) is not None or f == "since":
                    setattr(n, f, it.get(f))
            n.text = it.get("text") or n.text or ""
            n.last_seen = now
            current[it["key"]] = n
        kept = {}
        for k, n in active.items():   # ya no aparecen: pasan al histórico (salvo si faltan los datos de esa empresa)
            if keep_tickers and n.ticker in keep_tickers:
                kept[k] = n
                continue
            n.active = False
        cutoff = (datetime.now() - timedelta(days=HISTORY_DAYS)).isoformat(timespec="seconds")
        self.db.query(DBNotice).filter(DBNotice.scope == scope, DBNotice.active.is_(False),
                                       DBNotice.last_seen < cutoff).delete(synchronize_session="fetch")
        self.db.commit()
        return {k: _dict(n) for k, n in {**kept, **current}.items()}

    def _recent(self, scope: str, category: str, it: Dict[str, Any]) -> Optional[DBNotice]:
        """Último episodio cerrado del mismo aviso si es de hace menos de REOPEN_DAYS o de la misma sesión."""
        n = (self.db.query(DBNotice).filter_by(scope=scope, category=category, key=it["key"], active=False)
             .order_by(DBNotice.last_seen.desc()).first())
        if n is None:
            return None
        cutoff = (datetime.now() - timedelta(days=REOPEN_DAYS)).isoformat(timespec="seconds")
        same_session = bool(it.get("date")) and it.get("date") == n.date
        return n if n.last_seen >= cutoff or same_session else None

    def set_seen(self, keys: List[str], seen: bool = True) -> int:
        rows = self.db.query(DBNotice).filter(DBNotice.key.in_(keys), DBNotice.active.is_(True)).all()
        for n in rows:
            n.seen_at = now_iso() if seen else None
        self.db.commit()
        return len(rows)

    def set_dismissed(self, keys: List[str], dismissed: bool = True) -> int:
        """Descarta (borrado lógico) los avisos: dejan de mostrarse mientras dure el episodio, también si vuelve
        dentro de REOPEN_DAYS (se reabre el mismo). Si desaparece más tiempo y vuelve, es un aviso nuevo."""
        rows = self.db.query(DBNotice).filter(DBNotice.key.in_(keys), DBNotice.active.is_(True)).all()
        for n in rows:
            n.dismissed_at = now_iso() if dismissed else None
            if dismissed and not n.seen_at:
                n.seen_at = n.dismissed_at
        self.db.commit()
        return len(rows)

    def dismissed_keys(self, scope: str) -> List[str]:
        q = self.db.query(DBNotice.key).filter(DBNotice.scope == scope, DBNotice.active.is_(True), DBNotice.dismissed_at.isnot(None))
        return [k for (k,) in q]

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

    def active_attention(self) -> List[Dict[str, Any]]:
        """Avisos de «Requiere tu atención» vigentes y no descartados (según la última carga del Centro y de la watchlist)."""
        q = self.db.query(DBNotice).filter_by(category="attention", active=True).filter(DBNotice.dismissed_at.is_(None)).order_by(DBNotice.first_seen.desc())
        return [_dict(n) for n in q]

    def closed_signals_since(self, since_iso: str) -> List[Dict[str, Any]]:
        """Señales del monitor que saltaron desde since_iso (hoy) y ya se han cerrado (p. ej. una caída que rebota): lo que
        pasó hoy sigue en la Bandeja de hoy y en Avisos aunque deje de cumplirse. Sin las descartadas."""
        q = (self.db.query(DBNotice).filter_by(category="signal", active=False).filter(DBNotice.dismissed_at.is_(None))
             .filter(DBNotice.first_seen >= since_iso).order_by(DBNotice.first_seen.desc()))
        return [_dict(n) for n in q]

    def active_signals(self) -> List[Dict[str, Any]]:
        """Señales del monitor de precios vigentes y no descartadas (Resumen y watchlist)."""
        q = self.db.query(DBNotice).filter_by(category="signal", active=True).filter(DBNotice.dismissed_at.is_(None)).order_by(DBNotice.first_seen.desc())
        return [_dict(n) for n in q]

