"""Calendario propio de resultados: lo ya publicado no se pierde al buscar la siguiente fecha (sin internet ni IA)."""
import json
from datetime import date

from app.application.services import results_calendar as rc


def test_published_includes_today_and_keeps_past(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "CACHE_FILE", str(tmp_path / "cal.json"))
    (tmp_path / "cal.json").write_text(json.dumps({"RMS.PA": {"date": "2026-10-22", "event": "Ventas del 3T", "fetched_at": "2026-10-05T10:00:00"}}))
    assert rc.published("RMS.PA", date(2026, 10, 21)) == []
    assert rc.published("RMS.PA", date(2026, 10, 22)) == [{"date": "2026-10-22", "event": "Ventas del 3T"}]

    # al buscar la siguiente, la del 22/10 pasa a «past» y sigue contando como publicada
    monkeypatch.setattr(rc, "_search_many", lambda items: {"RMS.PA": {"date": "2027-02-12", "event": "Anuales", "fetched_at": "x"}})

    class Day(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 23)
    monkeypatch.setattr(rc, "date", Day)
    rc.refresh([("RMS.PA", "Hermès")])
    assert rc.published("RMS.PA", date(2026, 10, 23)) == [{"date": "2026-10-22", "event": "Ventas del 3T"}]
    assert rc.next_results("RMS.PA", "Hermès", "2027-02-12")["date"] == "2027-02-12"
