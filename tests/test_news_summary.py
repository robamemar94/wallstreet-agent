from datetime import datetime

from app.application.services import news_summary_service as ns


def test_summary_only_after_the_weekly_radar_not_for_new_news_or_theses():
    now = datetime(2026, 10, 10, 9, 0)                                       # sábado, pasada semanal
    assert ns.due_after_radar(None, now)                                     # no hay ninguno
    assert ns.due_after_radar({"created_at": "2026-10-05T16:00:00"}, now)     # el de la semana pasada
    wed = datetime(2026, 10, 14, 12, 0)
    assert not ns.due_after_radar({"created_at": "2026-10-10T08:10:00"}, wed)  # radar de una tesis nueva a mitad de semana


def test_prompt_only_uses_what_the_radar_found():
    p = ns.build_prompt([{"ticker": "RMS.PA", "date": "2026-09-28", "materiality": 5, "impact": "negative",
                          "step_name": "Demanda de lujo", "title": "Caen ventas en China", "summary": "-8%"}],
                        [{"ticker": "RMS.PA", "verdict": "INTACTA", "summary": "Semana tranquila"}], "29/09/2026")
    assert "Caen ventas en China" in p and "Semana tranquila" in p and "no inventes" in p


def test_news_grouped_by_radar_week():
    from app.interfaces.views.thesis_views import news_weeks
    assert ns.week_of("2026-10-10T10:02:43") == "2026-10-10"     # pasada del sábado
    assert ns.week_of("2026-10-08T11:00:00") == "2026-10-03"     # forzado el jueves: semana del radar anterior
    assert ns.week_of("2026-10-10T07:30:00") == "2026-10-03"     # antes de las 8:00 del sábado
    assert ns.week_of("2026-10-13T07:00:00") == "2026-10-10"     # las pasadas de los lunes (antes del cambio): su sábado
    items = [{"found_at": "2026-10-10T10:02:43"}, {"found_at": "2026-10-07T15:06:20"}, {"found_at": "2026-10-03T12:35:06"},
             {"found_at": "2026-08-29T10:00:00"}, {"found_at": "2026-09-05T10:00:00"}]
    weeks = news_weeks(items, datetime(2026, 10, 10, 12, 0))
    assert [w["start"].isoformat() for w in weeks] == ["2026-10-10", "2026-10-03", "2026-09-05", "2026-08-29"]
    assert weeks[0]["current"] and not weeks[1]["current"] and len(weeks[1]["news"]) == 2
    assert [d.isoformat() for d in weeks[0]["days"]] == ["2026-10-05", "2026-10-09"]   # repasa de lunes a viernes
    old = news_weeks(items + [{"found_at": "2026-08-22T10:00:00"}], datetime(2026, 10, 10, 12, 0))
    assert [w["folded"] for w in old] == [False, False, False, False, True]


def test_summary_waits_until_no_thesis_is_pending(monkeypatch):
    from app.application.services import thesis_news_runner as runner
    import app.application.services.news_summary_service as nsmod
    theses = [{"ticker": "AAA", "extra": {}}, {"ticker": "BBB", "extra": {}}]
    pending = {"AAA", "BBB"}

    class Repo:
        def __init__(self, db): pass
        def list_theses(self): return theses
    monkeypatch.setattr(runner, "SessionLocal", lambda: type("DB", (), {"close": lambda s: None, "rollback": lambda s: None})())
    monkeypatch.setattr(runner, "SqlAlchemyThesisRepository", Repo)
    monkeypatch.setattr(runner, "held_tickers", lambda db: {"AAA", "BBB", "CCC"})
    monkeypatch.setattr(runner, "radar_due", lambda t, held: t["ticker"] in pending)
    monkeypatch.setattr(nsmod, "load", lambda db: None)
    started = []
    monkeypatch.setattr(nsmod, "generate_in_background", lambda: started.append(1))

    def fetch(t):
        if t["ticker"] == "AAA":
            return {}, {"new_items": 0}
        raise RuntimeError("cuota")                       # BBB falla: no bloquea el resumen
    monkeypatch.setattr(runner, "fetch_digest", fetch)
    monkeypatch.setattr(runner, "save_digest", lambda repo, t, ev, d: pending.discard(t["ticker"]) or d)
    runner._run_due()
    assert started == [1]
    assert runner.status()["radar_last"]["ran"] == 1 and runner.status()["radar_last"]["failed"] == ["BBB"]

    # CCC sigue pendiente sin haber fallado aquí (la está haciendo otra pasada): el resumen espera
    pending.update({"AAA", "CCC"})
    monkeypatch.setattr(runner, "fetch_digest", lambda t: ({}, {"new_items": 0}))
    theses[:] = [{"ticker": "AAA", "extra": {}}]
    monkeypatch.setattr(Repo, "list_theses", lambda self: theses if "AAA" in pending else theses + [{"ticker": "CCC", "extra": {}}])
    runner._run_due()
    assert started == [1]
