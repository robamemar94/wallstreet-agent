"""Supuestos de la valoración base frente a lo real (sin internet)."""
import pytest

from app.application.services import valuation_reality_service as vr

FIN = {"years": ["2019", "2020", "2021", "2022", "2023", "2024", "LTM"],
       "eps": [1.0, 1.1, 1.21, 1.331, 1.4641, 1.61051, 1.7]}
CLOSES = {"2019": 20, "2020": 22, "2021": 24.2, "2022": 26.62, "2023": 29.282, "2024": 32.2102}   # PER 20x todos los años


def test_history_crecimiento_y_multiplo_reales():
    h = vr.history(FIN, CLOSES)["eps"]
    assert h["cagr5"] == pytest.approx(10.0) and h["cagr3"] == pytest.approx(10.0)
    assert h["mult_avg5"] == pytest.approx(20.0)


def test_peniques_y_crecimientos_absurdos():
    h = vr.history(FIN, {y: c * 100 for y, c in CLOSES.items()}, unit=100)["eps"]   # Londres: cotiza en peniques
    assert h["mult_avg5"] == pytest.approx(20.0)
    jump = {"years": ["2020", "2021", "2022", "2023"], "eps": [0.01, 0.5, 1, 2]}
    assert vr.history(jump, {})["eps"]["cagr3"] is None   # de casi cero: no comparable


@pytest.mark.parametrize("g, mult, tone", [(0.08, 19, "good"), (0.13, 22, "mid"), (0.18, 20, "bad"), (0.08, 26, "bad")])
def test_veredicto(g, mult, tone):
    vd = vr.verdict({"metric": "eps", "growth": g, "exit_multiple": mult}, vr.history(FIN, CLOSES))
    assert vd["tone"] == tone and vd["label"] == vr.LABELS[tone]
    assert (vr.warning(vd) is not None) == (tone == "bad")


def test_sin_datos():
    assert vr.verdict({"metric": "eps", "growth": 0.1, "exit_multiple": 20}, vr.history({}, {})) is None
    assert vr.warning(None) is None


def test_grupos_de_los_avisos():
    from app.application.services.notification_service import filter_of
    assert filter_of({"group": "avisos", "type": "insider_buy"}) == "analistas"
    assert filter_of({"group": "avisos", "type": "estimate_rise"}) == "analistas"
    assert filter_of({"group": "avisos", "type": "zona"}) == filter_of({"group": "avisos", "type": "cerca"}) == "comprar"
    assert filter_of({"group": "monitor", "type": "anormal"}) == "precio"
    assert filter_of({"group": "noticias"}) == "noticias"
    assert filter_of({"group": "alertas", "kind": "valoración", "counts": True}) == "comprar"   # hacia barata o ganga
    assert filter_of({"group": "alertas", "kind": "valoración", "counts": False}) == "otros"
    assert filter_of({"group": "alertas", "kind": "caída", "counts": True}) == "precio"
    assert filter_of({"group": "alertas", "kind": "recuperada", "counts": True}) == "otros"
    assert filter_of({"group": "avisos", "type": "report"}) == filter_of({"group": "avisos", "type": "legacy"}) == "cartera"


def test_agenda_por_semanas():
    import datetime
    from app.interfaces.views.thesis_views import agenda_weeks
    d = datetime.date
    weeks = agenda_weeks([{"ticker": "META", "date": d(2026, 10, 21)}, {"ticker": "ROL", "date": d(2026, 10, 21)},
                          {"ticker": "BKNG", "date": d(2026, 11, 3)}],
                         [{"ticker": "MSFT", "date": d(2026, 10, 30), "kind": "pago", "gross_eur": 13}], today=d(2026, 10, 15))
    assert [w["label"] for w in weeks] == ["La próxima", "En 2 semanas", "En 3 semanas"]
    assert weeks[1]["span"] == "del lun 26 oct al dom 1 nov"
    assert [i["ticker"] for i in weeks[0]["items"]] == ["META", "ROL"] and weeks[1]["items"][0]["ticker"] == "MSFT"
