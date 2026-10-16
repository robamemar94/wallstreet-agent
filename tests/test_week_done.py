import datetime

from app.application.services.agenda_service import week_done

FRI = datetime.date(2026, 10, 16)


def test_week_done_only_this_week_before_today():
    reported = [{"ticker": "ASML", "date": "2026-10-14", "surprise_pct": 9.9},
                {"ticker": "TSM", "date": "2026-10-15", "surprise_pct": 1.7},
                {"ticker": "OLD", "date": "2026-10-09"},    # semana pasada
                {"ticker": "HOY", "date": "2026-10-16"}]    # hoy: aún va en lo que viene
    collected = [{"ticker": "MSFT", "date": "2026-10-13", "gross_eur": 13.0}]
    out = week_done(FRI, reported, collected)
    assert [(i["ticker"], i["kind"]) for i in out] == [("MSFT", "pay"), ("ASML", "results"), ("TSM", "results")]


def test_week_done_empty_on_monday():
    assert week_done(datetime.date(2026, 10, 12), [{"ticker": "X", "date": "2026-10-09"}]) == []
