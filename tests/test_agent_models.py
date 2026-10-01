"""Modelo por agente: los marcados con `model: pro` en agents.yaml usan GEMINI_PRO_MODEL; el resto, GEMINI_MODEL."""
from agents.utils import get_llm, load_agent_config

PRO_AGENTS = {"investment_director", "valuation_agent", "thesis_agent", "thesis_destroyer", "moat_analyst"}


def test_get_llm_resolves_pro_flash_and_explicit(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "flash-x")
    monkeypatch.setenv("GEMINI_PRO_MODEL", "pro-x")
    assert get_llm() == "gemini/flash-x"
    assert get_llm("pro") == "gemini/pro-x"
    assert get_llm("otro-modelo") == "gemini/otro-modelo"


def test_only_synthesis_and_moat_agents_are_pro():
    assert {k for k, v in load_agent_config().items() if v.get("model") == "pro"} == PRO_AGENTS
