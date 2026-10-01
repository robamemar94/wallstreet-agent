from datetime import datetime

from app.application.services import news_summary_service as ns


def test_summary_only_after_the_weekly_radar_not_for_new_news_or_theses():
    now = datetime(2026, 9, 29, 12, 0)
    assert ns.due_after_radar(None, now)                                     # no hay ninguno
    assert not ns.due_after_radar({"created_at": "2026-09-27T10:00:00"}, now)  # radar de una tesis nueva a mitad de semana
    assert ns.due_after_radar({"created_at": "2026-09-22T10:00:00"}, now)     # pasada semanal: ya tiene una semana


def test_prompt_only_uses_what_the_radar_found():
    p = ns.build_prompt([{"ticker": "RMS.PA", "date": "2026-09-28", "materiality": 5, "impact": "negative",
                          "step_name": "Demanda de lujo", "title": "Caen ventas en China", "summary": "-8%"}],
                        [{"ticker": "RMS.PA", "verdict": "INTACTA", "summary": "Semana tranquila"}], "29/09/2026")
    assert "Caen ventas en China" in p and "Semana tranquila" in p and "no inventes" in p
