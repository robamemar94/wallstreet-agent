"""Hora de los resultados (pre / after) y búsqueda de fechas en una sola llamada, sin llamadas automáticas a la IA."""
import datetime

import pandas as pd

from app.application.services import agenda_service, results_calendar
from app.application.services.thesis_review_service import release_time

TODAY = datetime.date(2026, 10, 20)


def ts(s):
    return pd.Timestamp(s)


def test_release_time_after_close_and_pre_market():
    assert release_time(ts("2026-10-20 16:00:00-04:00"), "NFLX", TODAY) == {"kind": "after", "madrid": "22:00"}
    assert release_time(ts("2026-10-27 06:00:00-04:00"), "JNJ", TODAY) == {"kind": "pre", "madrid": "11:00"}
    assert release_time(ts("2026-10-21 11:00:00-04:00"), "XYZ", TODAY)["kind"] == "time"


def test_release_time_ignored_when_unreliable():
    assert release_time(ts("2026-10-28 11:00:00-04:00"), "AENA.MC", TODAY) is None      # fuera de EE. UU.
    assert release_time(ts("2027-01-26 07:00:00-05:00"), "JNJ", TODAY) is None          # demasiado lejos
    assert release_time(ts("2026-10-21 00:00:00-04:00"), "XYZ", TODAY) is None          # solo fecha


def test_upcoming_orders_pre_before_after_within_a_day():
    d = TODAY
    res = [{"ticker": "B", "date": d, "time": {"kind": "after", "madrid": "22:00"}},
           {"ticker": "A", "date": d, "time": None},
           {"ticker": "C", "date": d, "time": {"kind": "pre", "madrid": "12:00"}}]
    assert [i["ticker"] for i in agenda_service.upcoming_items(TODAY, 7, res, [])] == ["C", "A", "B"]


def test_next_results_never_calls_ai(tmp_path, monkeypatch):
    monkeypatch.setattr(results_calendar, "CACHE_FILE", str(tmp_path / "c.json"))
    monkeypatch.setattr(results_calendar, "_search_many", lambda items: (_ for _ in ()).throw(AssertionError("llamó a la IA")))
    assert results_calendar.next_results("XYZ", "Xyz", None) is None
    near = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    t = {"kind": "pre", "madrid": "11:00"}
    assert results_calendar.next_results("XYZ", "Xyz", near, t)["time"] == t


def test_refresh_is_one_call_for_all_and_keeps_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(results_calendar, "CACHE_FILE", str(tmp_path / "c.json"))
    calls = []
    soon = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    monkeypatch.setattr(results_calendar, "_search_many", lambda items: calls.append(items) or {"A": {"date": soon, "event": "", "source_url": "", "fetched_at": "x"}})
    assert results_calendar.refresh([("A", "Alfa"), ("B", "Beta"), ("C", "Gamma")]) == 1
    assert len(calls) == 1
    data = results_calendar._load()
    assert data["A"]["date"] == soon and data["B"]["date"] is None and data["C"]["date"] is None


def test_release_time_across_dst_change():
    """Yahoo fija la hora con el horario de hoy: el 3/11 (EE. UU. ya en invierno) da 15:00, que es su 16:00 tras el cierre."""
    assert release_time(ts("2026-11-03 15:00:00-05:00"), "BKNG", datetime.date(2026, 10, 28)) == {"kind": "after", "madrid": "22:00"}
    assert release_time(ts("2026-11-03 16:00:00-05:00"), "BKNG", datetime.date(2026, 11, 2)) == {"kind": "after", "madrid": "22:00"}
