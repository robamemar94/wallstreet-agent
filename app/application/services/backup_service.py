"""
Copias de seguridad de todo lo que no se puede volver a descargar: la base de datos (tesis, diario, porqués, informes,
avisos), las tesis en disco (config/ y data/theses), las operaciones en CSV y JSON y los ajustes.

Una copia = un .zip en data/backups/ (alpha-flow-AAAA-MM-DD_HHMM.zip) con:
  db/alpha_flow.db   instantánea coherente (API de copia de SQLite, no una copia del fichero con la app escribiendo) y
                     comprobada con PRAGMA integrity_check antes de guardarla
  <ruta relativa>    el resto de ficheros, con la misma ruta que en el proyecto
  manifest.json      cuándo, qué ficheros, tablas y tamaños

Cuándo: al arrancar y cada hora se comprueba, y si la última copia tiene más de MIN_GAP_H horas se hace otra (así hay una al
día aunque la app esté semanas abierta). Se conservan las últimas KEEP_RECENT y, de las anteriores, la última de cada mes
(KEEP_MONTHS meses). Con BACKUP_EXTRA_DIR (una carpeta sincronizada, un disco externo…) cada copia se duplica allí: es la
única que protege de perder el disco. La copia antigua de solo la BBDD al arrancar (database.make_rolling_backup) se
mantiene como red de seguridad antes de las migraciones.

Recuperar: `uv run python scripts/maintenance/backup.py restore <archivo>` (no pisa nada: extrae a una carpeta).
"""
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional

from app.infrastructure.config import (CLOSED_PORTFOLIO_FILE, CSV_FILE, DIV_CACHE_FILE, LEDGER_FILE, MANUAL_TX_FILE,
                                       PORTFOLIO_FILE, SPLIT_CACHE_FILE)
from app.infrastructure.db.database import DB_PATH, DEFAULT_DB_PATH

logger = logging.getLogger(__name__)

BACKUP_DIR = os.path.join("data", "backups")
PREFIX = "alpha-flow-"
KEEP_RECENT = 14        # las últimas copias, todas
KEEP_MONTHS = 12        # y de las anteriores, la última de cada mes de este último año
MIN_GAP_H = 20          # horas entre copias
CHECK_EVERY_S = 3600

# Lo que se guarda además de la BBDD (rutas relativas a la carpeta del proyecto)
INCLUDE_DIRS = ["config", os.path.join("data", "theses"), os.path.join("data", "comparisons")]
INCLUDE_FILES = [CSV_FILE, "compras.csv", "seguimiento.csv", MANUAL_TX_FILE, LEDGER_FILE, PORTFOLIO_FILE,
                 CLOSED_PORTFOLIO_FILE, DIV_CACHE_FILE, SPLIT_CACHE_FILE, os.path.join("data", "llm_usage.jsonl")]

_lock = threading.Lock()
_started = False


def extra_dir() -> Optional[str]:
    return os.getenv("BACKUP_EXTRA_DIR") or None


def _files() -> Iterator[str]:
    for rel in INCLUDE_FILES:
        if os.path.isfile(rel):
            yield rel
    for d in INCLUDE_DIRS:
        for base, dirs, names in os.walk(d):
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            for n in sorted(names):
                yield os.path.join(base, n)


def _snapshot_db(db_path: str, tmp_dir: str) -> Dict[str, Any]:
    """Instantánea coherente de la BBDD (aunque la app esté escribiendo) y su comprobación de integridad."""
    out = os.path.join(tmp_dir, "alpha_flow.db")
    src, dst = sqlite3.connect(db_path, timeout=30), sqlite3.connect(out)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    chk = sqlite3.connect(out)
    try:
        result = chk.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {t: chk.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                  for (t,) in chk.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        chk.close()
    if result != "ok":
        raise RuntimeError(f"La copia de la BBDD no pasa la comprobación de integridad: {result}")
    return {"path": out, "tables": tables}


def archives(dest: str = BACKUP_DIR) -> List[str]:
    """Nombres de las copias de dest, de la más antigua a la más reciente."""
    if not os.path.isdir(dest):
        return []
    return sorted(f for f in os.listdir(dest) if f.startswith(PREFIX) and f.endswith(".zip"))


def prune(dest: str) -> List[str]:
    """Deja las últimas KEEP_RECENT y la última de cada uno de los KEEP_MONTHS meses anteriores. Devuelve lo borrado."""
    names = archives(dest)
    recent, older = names[-KEEP_RECENT:], names[:-KEEP_RECENT]
    by_month: Dict[str, str] = {}
    for n in older:   # ordenadas: la última de cada mes pisa a las anteriores
        by_month[n[len(PREFIX):len(PREFIX) + 7]] = n
    keep = set(recent) | {by_month[m] for m in sorted(by_month)[-KEEP_MONTHS:]}
    removed = [n for n in names if n not in keep]
    for n in removed:
        os.remove(os.path.join(dest, n))
    return removed


def create_backup(db_path: Optional[str] = None, dest: str = BACKUP_DIR, extra: Optional[str] = None,
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    """Hace una copia ahora. Devuelve el manifiesto (con `file` = ruta del .zip). Lanza si la BBDD no es íntegra."""
    db_path, now = db_path or DB_PATH, now or datetime.now()
    os.makedirs(dest, exist_ok=True)
    final = os.path.join(dest, f"{PREFIX}{now:%Y-%m-%d_%H%M}.zip")
    part = final + ".part"
    with _lock, tempfile.TemporaryDirectory() as tmp:
        snap = _snapshot_db(db_path, tmp)
        files = list(_files())
        manifest = {"created_at": now.isoformat(timespec="seconds"), "tables": snap["tables"], "files": files,
                    "db_bytes": os.path.getsize(snap["path"])}
        try:
            with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                z.write(snap["path"], "db/alpha_flow.db")
                for rel in files:
                    z.write(rel, rel)
                z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            os.replace(part, final)
        finally:
            if os.path.exists(part):
                os.remove(part)
    manifest.update(file=final, bytes=os.path.getsize(final), pruned=prune(dest), extra=None)
    if extra:
        try:
            import shutil
            os.makedirs(extra, exist_ok=True)
            shutil.copy2(final, os.path.join(extra, os.path.basename(final)))
            prune(extra)
            manifest["extra"] = extra
        except Exception as e:   # un disco externo desconectado no debe romper la copia local
            logger.warning("No se pudo duplicar la copia en %s: %s", extra, e)
            manifest["extra_error"] = str(e)
    logger.info("Copia de seguridad: %s (%.1f MB, %d ficheros)", final, manifest["bytes"] / 1e6, len(files) + 1)
    return manifest


def needs_backup(dest: str = BACKUP_DIR, now: Optional[datetime] = None) -> bool:
    names = archives(dest)
    if not names:
        return True
    last = datetime.fromtimestamp(os.path.getmtime(os.path.join(dest, names[-1])))
    return (now or datetime.now()) - last >= timedelta(hours=MIN_GAP_H)


def status(dest: str = BACKUP_DIR) -> Dict[str, Any]:
    """Para Ajustes: última copia, cuántas hay y dónde."""
    names = archives(dest)
    items = []
    for n in reversed(names[-8:]):
        p = os.path.join(dest, n)
        items.append({"name": n, "bytes": os.path.getsize(p), "at": datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="minutes")})
    extra = extra_dir()
    return {"count": len(names), "last": items[0] if items else None, "items": items, "dir": os.path.abspath(dest),
            "extra_dir": extra, "extra_ok": bool(extra and os.path.isdir(extra) and archives(extra)),
            "extra_last": (archives(extra) or [None])[-1] if extra else None, "stale": needs_backup(dest)}


def restore(archive: str, to_dir: str) -> List[str]:
    """Extrae la copia en to_dir (no pisa el proyecto). Devuelve los ficheros extraídos."""
    with zipfile.ZipFile(archive) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f"Copia dañada: {bad}")
        z.extractall(to_dir)
        return z.namelist()


def start_scheduler() -> None:
    """Al arrancar y cada hora: si la última copia es de hace más de MIN_GAP_H horas, otra. No en los tests (BBDD temporal)."""
    global _started
    if _started or DB_PATH != DEFAULT_DB_PATH:
        return
    _started = True

    def loop():
        time.sleep(60)   # que arranque antes la app
        while True:
            try:
                if needs_backup():
                    create_backup(extra=extra_dir())
            except Exception:
                logger.exception("Fallo al hacer la copia de seguridad")
            time.sleep(CHECK_EVERY_S)
    threading.Thread(target=loop, daemon=True, name="backup-scheduler").start()
