from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.services import alerts_service, notice_service, notification_service
from app.infrastructure.db.database import Base
from app.infrastructure.repositories.sqlalchemy_notice_repository import SqlAlchemyNoticeRepository


class FakeThesisRepo:
    def __init__(self):
        self.news = [{"id": 7, "ticker": "AAA", "title": "Resultados", "materiality": 5, "found_at": "2026-09-29T10:00:00", "seen": False}]

    def list_news(self, limit=60):
        return self.news

    def mark_news_seen(self, ids):
        for n in self.news:
            if n["id"] in ids:
                n["seen"] = True
        return len(ids)


def _setup():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    alerts_service.save_alerts_data(db, {"history": [
        {"type": "VALORACIÓN", "ticker": "AAA", "old_status": "CARA", "new_status": "BARATA", "cagr": 12.0, "date": "2026-09-28 10:00:00"},
        {"type": "CAÍDA", "episode": True, "status": "abierta", "ticker": "BBB", "escalon": 10, "drop_pct": 12.0, "date": "2026-09-01 10:00:00", "silent": True}]})
    notices = SqlAlchemyNoticeRepository(db)
    notice_service.track(notices, "watchlist", [{"prio": 0, "type": "zona", "ticker": "CCC", "text": "En zona", "href": "/ticker/CCC"}],
                         [{"ticker": "AAA", "kind": "nivel", "tone": "up", "text": "Nuevo máximo de 52 semanas", "date": "2026-09-29"}])
    return db, FakeThesisRepo(), notices


def test_panel_collects_alerts_news_and_notices_and_marks_them_read():
    db, theses, notices = _setup()
    items = notification_service.collect(db, theses, notices)
    assert {i["group"] for i in items} == {"alertas", "noticias", "avisos", "monitor"}
    assert all(i["ticker"] != "BBB" for i in items)                      # las heredadas silenciosas no salen
    # nada en cartera: se ven las 3, pero no cuentan en el globo
    assert notification_service.unread_counts(items) == {"total": 0, "alertas": 0, "noticias": 0, "avisos": 0, "monitor": 0, "all": 4, "alertas_all": 1}
    from app.infrastructure.db.models import DBAsset, DBPortfolioItem
    db.add(DBAsset(ticker="AAA", data={})); db.add(DBPortfolioItem(ticker="AAA", shares=10)); db.commit()
    items = notification_service.collect(db, theses, notices)
    assert notification_service.unread_counts(items) == {"total": 3, "alertas": 1, "noticias": 1, "avisos": 0, "monitor": 1, "all": 4, "alertas_all": 1}
    notification_service.mark_read(db, theses, notices, [i["key"] for i in items])
    assert notification_service.unread_counts(notification_service.collect(db, theses, notices))["all"] == 0
    alert_key = next(i["key"] for i in items if i["group"] == "alertas")
    notification_service.mark_read(db, theses, notices, [alert_key], read=False)
    assert notification_service.unread_counts(notification_service.collect(db, theses, notices))["alertas_all"] == 1
