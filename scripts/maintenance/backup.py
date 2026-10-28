"""Copias de seguridad desde la terminal (ver app/application/services/backup_service.py).

    uv run python scripts/maintenance/backup.py run                 # hace una copia ahora
    uv run python scripts/maintenance/backup.py list                # las que hay
    uv run python scripts/maintenance/backup.py restore ARCHIVO     # extrae en data/restore-<fecha>/ (no pisa nada)

Restaurar de verdad: parar la app, copiar de la carpeta extraída `db/alpha_flow.db` a `data/alpha_flow.db` y, si hace falta,
`config/`, `data/` y los CSV a su sitio, y volver a arrancar.
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))   # raíz del proyecto
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from app.application.services import backup_service as bs  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("list")
    r = sub.add_parser("restore")
    r.add_argument("archivo")
    r.add_argument("--to", default=None)
    a = ap.parse_args()
    if a.cmd == "run":
        m = bs.create_backup(extra=bs.extra_dir())
        print(f"Copia hecha: {m['file']} ({m['bytes'] / 1e6:.1f} MB, {len(m['files'])} ficheros + BBDD)")
    elif a.cmd == "list":
        for n in reversed(bs.archives()):
            print(f"{n}  {os.path.getsize(os.path.join(bs.BACKUP_DIR, n)) / 1e6:6.1f} MB")
    else:
        path = a.archivo if os.path.exists(a.archivo) else os.path.join(bs.BACKUP_DIR, a.archivo)
        to = a.to or os.path.join("data", f"restore-{datetime.now():%Y%m%d-%H%M%S}")
        names = bs.restore(path, to)
        print(f"Extraído en {to} ({len(names)} ficheros). Para volver a esa copia: para la app, copia {to}/db/alpha_flow.db a data/alpha_flow.db "
              "(y el resto de carpetas/ficheros que necesites) y arranca de nuevo.")


if __name__ == "__main__":
    main()
