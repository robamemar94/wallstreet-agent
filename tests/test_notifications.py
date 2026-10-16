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
    # nada en cartera: se ven, pero no cuentan en el globo; la noticia suelta no avisa (va en el resumen semanal)
    assert notification_service.unread_counts(items) == {"total": 0, "alertas": 0, "noticias": 0, "avisos": 0, "monitor": 0, "all": 3, "alertas_all": 1, "quiet": 3, "pendientes": 2}
    from app.infrastructure.db.models import DBAsset, DBPortfolioItem
    db.add(DBAsset(ticker="AAA", data={})); db.add(DBPortfolioItem(ticker="AAA", shares=10)); db.commit()
    items = notification_service.collect(db, theses, notices)
    assert notification_service.unread_counts(items) == {"total": 2, "alertas": 1, "noticias": 0, "avisos": 0, "monitor": 1, "all": 3, "alertas_all": 1, "quiet": 1, "pendientes": 2}
    notification_service.mark_read(db, theses, notices, [i["key"] for i in items])
    assert notification_service.unread_counts(notification_service.collect(db, theses, notices))["all"] == 0
    alert_key = next(i["key"] for i in items if i["group"] == "alertas")
    notification_service.mark_read(db, theses, notices, [alert_key], read=False)
    assert notification_service.unread_counts(notification_service.collect(db, theses, notices))["alertas_all"] == 1


def test_valuation_changes_light_badges_only_when_moving_into_buy_zone():
    db, theses, notices = _setup()
    zone = lambda t, old, new: {"type": "VALORACIÓN", "ticker": t, "old_status": old, "new_status": new, "date": "2026-09-28 10:00:00"}
    alerts_service.save_alerts_data(db, {"history": [
        zone("A1", "PRECIO JUSTO", "BARATA"), zone("A2", "BARATA", "GANGA"),      # cuentan
        zone("A3", "BARATA", "PRECIO JUSTO"), zone("A4", "PRECIO JUSTO", "CARA"),  # se encarecen: no cuentan
        zone("A5", "CARA", "PRECIO JUSTO")]})                                     # se abarata pero no es zona de compra
    items = [i for i in notification_service.collect(db, theses, notices) if i["group"] == "alertas"]
    assert {i["ticker"] for i in items} == {"A1", "A2", "A3", "A4", "A5"}       # todas siguen en la lista
    assert {i["ticker"] for i in items if i["counts"]} == {"A1", "A2"}
    assert notification_service.unread_counts(items)["alertas_all"] == 2
    # aunque estén en cartera, las que no cuentan no van en el número de la campana, solo en su punto («quiet»)
    for i in items:
        i["portfolio"] = True
    c = notification_service.unread_counts(items)
    assert (c["total"], c["quiet"]) == (2, 3)


def test_reading_a_news_notice_reads_the_news():
    from app.application.services.notification_service import news_ids_in_notices
    keys = ["resumen|att|news|EVO.ST|12", "resumen|att|results|EVO.ST|", "resumen|sig|NVDA|anormal|down|1d", "news|7"]
    assert news_ids_in_notices(keys) == [12]
