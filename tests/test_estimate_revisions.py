"""Previsiones de los analistas: cómo cambia el BPA esperado y el aviso de recorte (sin internet)."""
import pandas as pd
import pytest

from app.application.services import estimate_revisions_service as est


def _trend(next_year=(15.9, 15.7, 15.5, 12.8, 12.7)):
    cols = ["current", "7daysAgo", "30daysAgo", "60daysAgo", "90daysAgo"]
    return pd.DataFrame([[9.3, 9.3, 9.3, 9.0, 8.9], list(next_year)], index=["0y", "+1y"], columns=cols)


def test_parse_usa_el_ano_que_viene():
    revs = pd.DataFrame([[2, 7, 0, 0]], index=["+1y"], columns=["upLast7days", "upLast30days", "downLast30days", "downLast7Days"])
    out = est.parse(_trend(), revs)
    assert out["period"] == "+1y"
    assert out["chg"]["90d"] == pytest.approx(100 * (15.9 / 12.7 - 1), abs=0.01)
    assert (out["up_30"], out["down_30"]) == (7, 0)
    assert est.signal(out) is None   # las subidas no avisan


def test_recorte_avisa(monkeypatch):
    out = est.parse(_trend((9.0, 9.2, 9.4, 9.6, 10.0)))   # −10 % en 90 días
    assert est.signal(out)["chg"] == pytest.approx(-10.0)
    monkeypatch.setattr(est, "revisions", lambda t, fetch=True: out)
    notes = est.signal_notices(["AAA"])
    assert notes and notes[0]["type"] == "estimate_cut" and "−10,0" in notes[0]["text"].replace("-", "−")


def test_sin_datos():
    assert est.parse(None) is None
    assert est.parse(pd.DataFrame()) is None
