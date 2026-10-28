"""«Frente a la revisión anterior»: qué cambia entre dos revisiones de resultados."""
from app.application.services.thesis_review_service import review_changes

KPIS = {84: {"name": "Organic revenue growth", "unit": "%"}, 85: {"name": "Leather Goods", "unit": "%"}, 87: {"name": "Margen", "unit": "%"}}


def test_changes_between_reviews():
    prev = {"period": "H1 2026", "verdict": "VIGILANCIA",
            "hypotheses": [{"id": "H2", "status": "neutral"}, {"id": "H3", "status": "refuerza"}],
            "items": [{"kpi_id": 84, "value": 6.1, "status": "amber"}, {"kpi_id": 85, "value": 10.0, "status": "green"},
                      {"kpi_id": 87, "value": 41.0, "status": "green"}]}
    now = {"verdict": "VIGILANCIA",
           "hypotheses": [{"id": "H2", "status": "debilita", "reason": "solo marroquinería"}, {"id": "H3", "status": "refuerza"},
                          {"id": "H8", "status": "debilita"}],
           "items": [{"kpi_id": 84, "value": 4.3, "status": "red"}, {"kpi_id": 85, "value": 10.0, "status": "green"}]}
    ch = review_changes(prev, now, KPIS)
    assert ch["prev_period"] == "H1 2026" and ch["prev_verdict"] == ch["verdict"] == "VIGILANCIA"
    assert [(h["id"], h["prev"], h["now"]) for h in ch["hypotheses"]] == [("H2", "neutral", "debilita")]   # H8 no estaba antes
    assert [(k["name"], k["prev_value"], k["value"]) for k in ch["kpis"]] == [("Organic revenue growth", 6.1, 4.3)]   # sin cambio y sin dato: fuera
    assert review_changes(None, now, KPIS) is None
