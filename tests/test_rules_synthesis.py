"""Síntesis de las Normas de Calidad: cuándo se puede generar, cuándo queda desactualizada y qué guarda."""
import json
from types import SimpleNamespace

import pytest

from app.application.services import rules_synthesis_service as rs


def _data(n=5, score="1.5/2.0"):
    d = {}
    for i in range(1, n + 1):
        d[f"rules_pilar{i}"] = f"informe {i}"
        d[f"rules_pilar{i}_score"] = score
        d[f"rules_pilar{i}_pros"] = [f"fortaleza {i}"]
        d[f"rules_pilar{i}_cons"] = [f"debilidad {i}"]
    return d


def test_status_missing_pillars():
    st = rs.status(_data(3))
    assert not st["ready"] and st["missing"] == [4, 5] and st["synthesis"] is None and not st["stale"]


def test_status_stale_after_regenerating_a_pillar():
    data = _data()
    data[rs.KEY] = {"headline": "x", "signature": rs.signature(rs.pillars(data))}
    assert rs.status(data)["ready"] and not rs.status(data)["stale"]
    data["rules_pilar2_score"] = "0.8/2.0"   # se regenera el pilar 2
    assert rs.status(data)["stale"]


def test_prompt_uses_scores_pros_and_cons():
    p = rs.build_prompt("ACME", rs.pillars(_data(score="1.2/2.0")))
    assert "Nota total: 6.0/10" in p and "PILAR 3" in p and "+ fortaleza 3" in p and "- debilidad 5" in p


def test_generate_refuses_without_all_pillars():
    with pytest.raises(ValueError):
        rs.generate("ACME", _data(4))


def test_generate_saves_headline_lists_and_signature(monkeypatch):
    from app.application.services import thesis_ai_service as ai
    reply = {"headline": "Negocio sólido; flojo en capital.", "strengths": ["a", "b", "c", "d"], "risks": ["x", ""]}
    client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text=json.dumps(reply))))
    monkeypatch.setattr(ai, "_client", lambda key=None: client)
    data = _data()
    data["rules_pilar4_score"] = "0.9/2.0"
    out = rs.generate("ACME", data)
    assert out["headline"] == reply["headline"] and out["strengths"] == ["a", "b", "c"] and out["risks"] == ["x"]
    assert out["weakest"] == {"n": 4, "score": 0.9} and out["total"] == 6.9
    assert out["signature"] == rs.signature(rs.pillars(data))
