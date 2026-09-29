import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.services import notice_service
from app.infrastructure.db.database import Base
from app.infrastructure.repositories.sqlalchemy_notice_repository import SqlAlchemyNoticeRepository


@pytest.fixture
def repo():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return SqlAlchemyNoticeRepository(sessionmaker(bind=engine)())


def _sig(**kw):
    return {"ticker": "AAA", "kind": "nivel", "tone": "up", "text": "Nuevo máximo", "date": "2026-09-29", **kw}


def test_signal_that_disappears_goes_to_history(repo):
    notice_service.track(repo, "resumen", [], [_sig()])
    _, active, history = notice_service.track(repo, "resumen", [], [])
    assert active == [] and len(history) == 1 and history[0]["ticker"] == "AAA"


def test_rolling_window_is_the_same_episode(repo):
    # la ventana de 5 sesiones se desplaza cada día: sigue siendo la misma señal, no una nueva cada día
    notice_service.track(repo, "resumen", [], [_sig(kind="anormal", since="2026-09-22", text="-8%")])
    _, active, history = notice_service.track(repo, "resumen", [], [_sig(kind="anormal", since="2026-09-23", text="-9%")])
    assert len(active) == 1 and active[0]["text"] == "-9%" and history == []


def test_seen_attention_stays_seen_until_it_reappears(repo):
    a = {"prio": 0, "type": "zona", "ticker": "AAA", "text": "Por debajo de su precio de compra"}
    att, _, _ = notice_service.track(repo, "watchlist", [a], [])
    assert att[0]["seen"] is False
    repo.set_seen([att[0]["key"]])
    att, _, _ = notice_service.track(repo, "watchlist", [{**a, "text": "cotiza a 90"}], [])
    assert att[0]["seen"] is True                         # sigue activo: sigue visto aunque cambie el precio
    notice_service.track(repo, "watchlist", [], [])        # sale de la zona
    att, _, _ = notice_service.track(repo, "watchlist", [a], [])
    assert att[0]["seen"] is False                        # vuelve a entrar: aviso nuevo


def test_history_is_purged_after_six_months_and_can_be_cleared(repo):
    from app.infrastructure.db.models import DBNotice
    notice_service.track(repo, "resumen", [], [_sig(), _sig(ticker="BBB")])
    notice_service.track(repo, "resumen", [], [])                       # las dos pasan al histórico
    old = repo.db.query(DBNotice).filter_by(ticker="AAA").one()
    old.last_seen = "2026-01-01T00:00:00"                              # hace más de 6 meses
    repo.db.commit()
    _, _, history = notice_service.track(repo, "resumen", [], [])
    assert [h["ticker"] for h in history] == ["BBB"]
    assert repo.delete_inactive(history[0]["id"]) and repo.history("resumen", "signal") == []
    notice_service.track(repo, "resumen", [], [_sig(ticker="CCC")])
    assert repo.clear_history("resumen", "signal") == 0                 # la activa no es histórico


def test_active_signal_cannot_be_deleted(repo):
    notice_service.track(repo, "resumen", [], [_sig()])
    from app.infrastructure.db.models import DBNotice
    assert repo.delete_inactive(repo.db.query(DBNotice).one().id) is False
