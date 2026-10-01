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
    assert att[0]["seen"] is True                         # vuelve a entrar enseguida: el mismo episodio, ya visto
    notice_service.track(repo, "watchlist", [], [])
    _age_closed(repo, days=8)
    att, _, _ = notice_service.track(repo, "watchlist", [a], [])
    assert att[0]["seen"] is False                        # vuelve a entrar pasada una semana: aviso nuevo


def _age_closed(repo, days):
    from datetime import datetime, timedelta
    from app.infrastructure.db.models import DBNotice
    old = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    for n in repo.db.query(DBNotice).filter_by(active=False):
        n.last_seen = old
    repo.db.commit()


def test_signal_flickering_around_the_threshold_is_not_notified_again(repo):
    # ROL: −7,5 % (1,9σ) → desaparece → −8,0 % (2,1σ): la misma señal, no una notificación nueva
    notice_service.track(repo, "resumen", [], [_sig(kind="anormal", since="2026-09-22", text="-7.5%")])
    key = repo.active_signals()[0]["key"]
    repo.set_seen([key])
    notice_service.track(repo, "resumen", [], [])
    _, active, history = notice_service.track(repo, "resumen", [], [_sig(kind="anormal", since="2026-09-23", text="-8.0%")])
    assert len(active) == 1 and active[0]["seen"] and active[0]["text"] == "-8.0%" and history == []


def test_same_session_signal_is_never_notified_twice(repo):
    # META: el mismo máximo de 52 semanas del 24/09 no vuelve a notificarse aunque haya pasado más de una semana
    notice_service.track(repo, "resumen", [], [_sig(date="2026-09-24")])
    repo.set_seen([repo.active_signals()[0]["key"]])
    notice_service.track(repo, "resumen", [], [])
    _age_closed(repo, days=30)
    _, active, _ = notice_service.track(repo, "resumen", [], [_sig(date="2026-09-24")])
    assert active[0]["seen"]
    notice_service.track(repo, "resumen", [], [])
    _age_closed(repo, days=30)
    _, active, _ = notice_service.track(repo, "resumen", [], [_sig(date="2026-11-02", text="Nuevo máximo (800)")])
    assert not active[0]["seen"]                          # un máximo nuevo de otra sesión, pasado el plazo, sí avisa


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


def test_signals_are_kept_when_yahoo_returns_no_data(repo):
    notice_service.track(repo, "resumen", [], [_sig()])
    _, active, history = notice_service.track(repo, "resumen", [], [], no_data={"AAA"})   # sin precios de AAA
    assert [s["ticker"] for s in active] == ["AAA"] and history == []
    _, active, history = notice_service.track(repo, "resumen", [], [])                    # con datos: ya no está
    assert active == [] and len(history) == 1


def test_dismissed_notice_is_hidden_while_it_lasts(repo):
    a = {"prio": 0, "type": "zona", "ticker": "AAA", "text": "Por debajo de su precio de compra"}
    att, sig, _ = notice_service.track(repo, "watchlist", [a], [_sig()])
    assert repo.set_dismissed([att[0]["key"], sig[0]["key"]]) == 2
    att, sig, _ = notice_service.track(repo, "watchlist", [{**a, "text": "cotiza a 90"}], [_sig()])
    assert att == [] and sig == []                                   # sigue dándose pero no vuelve a salir
    assert repo.active_attention() == [] and repo.active_signals() == []   # tampoco en la campana
    assert len(repo.dismissed_keys("watchlist")) == 2


def test_dismissed_notice_stays_dismissed_if_it_flickers(repo):
    a = {"prio": 0, "type": "zona", "ticker": "AAA", "text": "Por debajo de su precio de compra"}
    att, _, _ = notice_service.track(repo, "watchlist", [a], [])
    repo.set_dismissed([att[0]["key"]])
    notice_service.track(repo, "watchlist", [], [])                 # sale de la zona…
    att, _, _ = notice_service.track(repo, "watchlist", [a], [])    # …y vuelve al rato: el mismo episodio
    assert att == []


def test_dismiss_can_be_undone(repo):
    a = {"prio": 0, "type": "zona", "ticker": "AAA", "text": "x"}
    att, _, _ = notice_service.track(repo, "watchlist", [a], [])
    repo.set_dismissed([att[0]["key"]])
    repo.set_dismissed([att[0]["key"]], False)
    att, _, _ = notice_service.track(repo, "watchlist", [a], [])
    assert len(att) == 1 and att[0]["seen"] is True                  # vuelve, ya como leído
