import datetime
from zoneinfo import ZoneInfo

from app.application.services.price_scheduler import refresh_reason

MAD = ZoneInfo("Europe/Madrid")


def at(y, m, d, hh, mm):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=MAD)


def ts(dt):
    return dt.timestamp()


# miércoles 7-oct-2026: Madrid UTC+2, Nueva York UTC-4 → EE. UU. abre a las 15:30 de Madrid
DAY = (2026, 10, 7)


def test_europe_open_waits_five_minutes():
    last = ts(at(2026, 10, 6, 22, 10))           # última descarga: anoche
    assert refresh_reason(at(*DAY, 9, 2), last) is None
    assert refresh_reason(at(*DAY, 9, 5), last) == "Europa apertura"


def test_open_fires_even_if_someone_refreshed_just_before():
    assert refresh_reason(at(*DAY, 9, 6), ts(at(*DAY, 9, 3))) == "Europa apertura"
    assert refresh_reason(at(*DAY, 9, 7), ts(at(*DAY, 9, 6))) is None


def test_session_every_five_minutes():
    assert refresh_reason(at(*DAY, 11, 0), ts(at(*DAY, 10, 57))) is None
    assert refresh_reason(at(*DAY, 11, 0), ts(at(*DAY, 10, 55))) == "Europa sesión"


def test_us_open_one_minute_after_bell():
    last = ts(at(*DAY, 15, 29))
    assert refresh_reason(at(*DAY, 15, 30), last) is None
    assert refresh_reason(at(*DAY, 15, 31), last) == "EE. UU. apertura"


def test_closes():
    assert refresh_reason(at(*DAY, 17, 40), ts(at(*DAY, 17, 38))) == "Europa cierre"
    assert refresh_reason(at(*DAY, 22, 5), ts(at(*DAY, 22, 1))) == "EE. UU. cierre"
    # después del último cierre, nada hasta mañana
    assert refresh_reason(at(*DAY, 23, 30), ts(at(*DAY, 22, 5))) is None


def test_between_european_close_and_us_session_only_us():
    # 17:50: Europa cerrada (ya guardado su cierre), EE. UU. abierta → cada 5 min por EE. UU.
    assert refresh_reason(at(*DAY, 17, 50), ts(at(*DAY, 17, 44))) == "EE. UU. sesión"


def test_weekend_nothing():
    last = ts(at(2026, 10, 9, 22, 6))             # viernes, tras el cierre
    assert refresh_reason(at(2026, 10, 10, 12, 0), last) is None   # sábado
    assert refresh_reason(at(2026, 10, 11, 18, 0), last) is None   # domingo


def test_us_hours_follow_new_york_dst():
    # 28-oct-2026: Europa ya en horario de invierno (UTC+1), EE. UU. aún en verano (UTC-4) → abre a las 14:30 de Madrid
    last = ts(at(2026, 10, 28, 14, 0))
    assert refresh_reason(at(2026, 10, 28, 14, 31), last) == "EE. UU. apertura"


def test_morning_start_picks_up_yesterdays_us_close():
    # arranque a las 8:00 con la última descarga de ayer a media tarde: falta el cierre de EE. UU.
    assert refresh_reason(at(*DAY, 8, 0), ts(at(2026, 10, 6, 16, 0))) == "EE. UU. cierre"
    assert refresh_reason(at(*DAY, 8, 0), ts(at(2026, 10, 6, 22, 10))) is None


def test_monday_morning_uses_fridays_close():
    assert refresh_reason(at(2026, 10, 12, 8, 0), ts(at(2026, 10, 9, 22, 10))) is None
    assert refresh_reason(at(2026, 10, 12, 8, 0), ts(at(2026, 10, 9, 18, 0))) == "EE. UU. cierre"
