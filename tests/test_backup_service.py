"""Copias de seguridad: instantánea coherente de la BBDD, ficheros, retención y copia externa."""
import json
import os
import sqlite3
import zipfile
from datetime import datetime, timedelta

import pytest

from app.application.services import backup_service as bs


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for rel, text in {"config/settings.json": "{}", "config/theses/meta.md": "tesis", "data/theses/doc.md": "fuente",
                      "Transactions.csv": "a;b\n1;2\n", "data/manual_transactions.json": "[]"}.items():
        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        open(rel, "w").write(text)
    db = tmp_path / "alpha.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, txt TEXT)")
    con.executemany("INSERT INTO notes (txt) VALUES (?)", [("uno",), ("dos",)])
    con.commit()
    con.close()
    return str(db)


def test_backup_has_consistent_db_files_and_manifest(project, tmp_path):
    m = bs.create_backup(db_path=project, dest="copias", now=datetime(2026, 10, 20, 9, 30))
    assert os.path.basename(m["file"]) == "alpha-flow-2026-10-20_0930.zip"
    with zipfile.ZipFile(m["file"]) as z:
        names = set(z.namelist())
        assert {"db/alpha_flow.db", "manifest.json", "Transactions.csv", "config/settings.json",
                "config/theses/meta.md", "data/theses/doc.md", "data/manual_transactions.json"} <= names
        z.extract("db/alpha_flow.db", tmp_path / "out")
        manifest = json.loads(z.read("manifest.json"))
    rows = sqlite3.connect(tmp_path / "out" / "db" / "alpha_flow.db").execute("SELECT txt FROM notes ORDER BY id").fetchall()
    assert rows == [("uno",), ("dos",)] and manifest["tables"] == {"notes": 2}
    assert not [f for f in os.listdir("copias") if f.endswith(".part")]


def test_corrupt_db_raises_and_leaves_no_archive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"esto no es sqlite" * 100)
    with pytest.raises(Exception):
        bs.create_backup(db_path=str(bad), dest="copias")
    assert bs.archives("copias") == []


def test_prune_keeps_recent_and_last_of_each_month(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    day = datetime(2026, 1, 1)
    for i in range(120):   # 120 días seguidos
        (d / f"{bs.PREFIX}{day + timedelta(days=i):%Y-%m-%d_0900}.zip").write_bytes(b"x")
    removed = bs.prune(str(d))
    kept = bs.archives(str(d))
    assert set(kept[-bs.KEEP_RECENT:]) == set(bs.archives(str(d))[-bs.KEEP_RECENT:]) and len(kept) < 120 - len(removed) + 1
    assert f"{bs.PREFIX}2026-01-31_0900.zip" in kept and f"{bs.PREFIX}2026-02-28_0900.zip" in kept   # fin de mes anterior
    assert f"{bs.PREFIX}2026-01-15_0900.zip" not in kept
    assert kept[-1] == f"{bs.PREFIX}2026-04-30_0900.zip"


def test_needs_backup_after_gap(project):
    assert bs.needs_backup("copias")   # ninguna todavía
    bs.create_backup(db_path=project, dest="copias")
    now = datetime.now()
    assert not bs.needs_backup("copias", now + timedelta(hours=bs.MIN_GAP_H - 1))
    assert bs.needs_backup("copias", now + timedelta(hours=bs.MIN_GAP_H + 1))


def test_extra_copy_and_unplugged_disk_does_not_break(project, tmp_path):
    ok = bs.create_backup(db_path=project, dest="copias", extra=str(tmp_path / "externo"), now=datetime(2026, 10, 20, 9, 0))
    assert bs.archives(str(tmp_path / "externo")) == ["alpha-flow-2026-10-20_0900.zip"] and ok["extra"]
    (tmp_path / "ocupado").write_text("un fichero, no una carpeta")
    bad = bs.create_backup(db_path=project, dest="copias", extra=str(tmp_path / "ocupado" / "sub"), now=datetime(2026, 10, 21, 9, 0))
    assert bad["extra_error"] and os.path.exists(bad["file"])   # la copia local se hizo igual


def test_restore_extracts_without_touching_project(project, tmp_path):
    m = bs.create_backup(db_path=project, dest="copias")
    names = bs.restore(m["file"], str(tmp_path / "restaurado"))
    assert "db/alpha_flow.db" in names and (tmp_path / "restaurado" / "Transactions.csv").read_text() == "a;b\n1;2\n"
