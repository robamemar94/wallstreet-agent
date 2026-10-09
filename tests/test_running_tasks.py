"""Informes en curso: al recargar la ficha se siguen esperando; los que se cortaron por un reinicio pasan a error."""
from datetime import datetime

from app.infrastructure.db.database import SessionLocal
from app.infrastructure.db.models import DBAsset, DBTask
from app.interfaces.api import analysis_api


def _task(db, task_id, report_type, status="processing"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.add(DBTask(task_id=task_id, ticker="ZZTEST", report_type=report_type, status=status, created_at=now, updated_at=now))
    db.commit()


def test_running_tasks_resume_and_orphans():
    db = SessionLocal()
    try:
        if not db.get(DBAsset, "ZZTEST"):
            db.add(DBAsset(ticker="ZZTEST"))
            db.commit()
        _task(db, "ZZTEST_audit_vivo", "audit")
        _task(db, "ZZTEST_franchise_huerf", "franchise")
        _task(db, "ZZTEST_verdict_ok", "verdict", status="success")
        analysis_api.RUNNING_TASKS.add("ZZTEST_audit_vivo")

        assert analysis_api.running_tasks(db, "zztest") == {"audit": "ZZTEST_audit_vivo"}
        orphan = db.get(DBTask, "ZZTEST_franchise_huerf")
        db.refresh(orphan)
        assert orphan.status == "error" and "reinici" in orphan.error_message
    finally:
        analysis_api.RUNNING_TASKS.discard("ZZTEST_audit_vivo")
        db.query(DBTask).filter(DBTask.ticker == "ZZTEST").delete()
        db.query(DBAsset).filter(DBAsset.ticker == "ZZTEST").delete()
        db.commit()
        db.close()
