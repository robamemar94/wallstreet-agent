from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.application.services import notification_service
from app.infrastructure.db.database import get_db
from app.infrastructure.dependencies import get_notice_repository, get_thesis_repository
from app.infrastructure.repositories.sqlalchemy_notice_repository import SqlAlchemyNoticeRepository
from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository

router = APIRouter(tags=["Notifications"])


class ReadPayload(BaseModel):
    keys: Optional[List[str]] = None     # sin claves: todas las no leídas (del grupo, si se indica)
    group: Optional[str] = None
    read: bool = True


@router.get("/notifications")
def list_notifications(db: Session = Depends(get_db), thesis_repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository),
                       notice_repo: SqlAlchemyNoticeRepository = Depends(get_notice_repository)):
    items = notification_service.collect(db, thesis_repo, notice_repo)
    return {"items": items, "unread": notification_service.unread_counts(items)}


@router.get("/notifications/count")
def count_notifications(db: Session = Depends(get_db), thesis_repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository),
                        notice_repo: SqlAlchemyNoticeRepository = Depends(get_notice_repository)):
    return notification_service.unread_counts(notification_service.collect(db, thesis_repo, notice_repo))


@router.post("/notifications/read")
def mark_notifications_read(payload: ReadPayload, db: Session = Depends(get_db),
                            thesis_repo: SqlAlchemyThesisRepository = Depends(get_thesis_repository),
                            notice_repo: SqlAlchemyNoticeRepository = Depends(get_notice_repository)):
    keys = payload.keys
    if not keys:
        keys = [i["key"] for i in notification_service.collect(db, thesis_repo, notice_repo)
                if not i["read"] and (not payload.group or i["group"] == payload.group)]
    return {"status": "success", "updated": notification_service.mark_read(db, thesis_repo, notice_repo, keys, payload.read)}
