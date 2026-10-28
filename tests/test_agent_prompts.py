"""Prompts de los agentes: cada contexto entra una sola vez, ninguna plantilla se queda sin rellenar, y las piezas
nuevas (ratios forenses, contexto condensado, normas desde los pilares, unión de los crews) hacen lo que deben."""
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agents import utils
from app.application.services.forensic_ratios_service import compute, ratios_markdown
from app.interfaces.api import analysis_api

MARK = {k: f"<<{k.upper()}>>" for k in (
    "audit_context", "franchise_context", "mgmt_alloc_context", "rules_context", "verdict_context",
    "valuation_context", "thesis_context", "destroyer_context")}
LEFTOVER = re.compile(r"\{[a-z_]+\}")


def _capture_tasks():
    """Sustituye Task por un registro de descripciones (sin LLM)."""
    seen = []

    def fake_task(description, expected_output, agent):
        seen.append(description + "\n" + expected_output)
        return SimpleNamespace(description=description)
    return seen, fake_task


@pytest.mark.parametrize("runner, kwargs, once", [
    ("run_investment_director", dict(franchise_context=MARK["franchise_context"], mgmt_alloc_context=MARK["mgmt_alloc_context"],
                                     audit_context=MARK["audit_context"], rules_context=MARK["rules_context"],
                                     destroyer_context=MARK["destroyer_context"]),
     ["franchise_context", "mgmt_alloc_context", "audit_context", "rules_context", "destroyer_context"]),
    ("run_pe_forensic", dict(audit_context=MARK["audit_context"]), ["audit_context"]),
    ("run_valuation_agent", dict(verdict_context=MARK["verdict_context"], audit_context=MARK["audit_context"],
                                 destroyer_context=MARK["destroyer_context"]),
     ["verdict_context", "audit_context", "destroyer_context"]),
    ("run_thesis_agent", dict(verdict_context=MARK["verdict_context"], valuation_context=MARK["valuation_context"],
                              franchise_context=MARK["franchise_context"], mgmt_alloc_context=MARK["mgmt_alloc_context"],
                              audit_context=MARK["audit_context"], destroyer_context=MARK["destroyer_context"]),
     ["verdict_context", "valuation_context", "franchise_context", "destroyer_context"]),
    ("run_thesis_destroyer", dict(verdict_context=MARK["verdict_context"], thesis_context=MARK["thesis_context"],
                                  franchise_context=MARK["franchise_context"], audit_context=MARK["audit_context"],
                                  rules_context=MARK["rules_context"], valuation_context=MARK["valuation_context"]),
     ["thesis_context", "verdict_context", "valuation_context", "rules_context", "audit_context"]),
])
def test_single_agent_prompts_inject_each_context_once(runner, kwargs, once):
    import agents
    seen, fake_task = _capture_tasks()
    with patch.object(utils, "Task", side_effect=fake_task), patch.object(utils, "get_agent", return_value=MagicMock()), \
            patch.object(utils, "execute_crew", return_value="ok"):
        getattr(agents, runner)("AAPL", "Apple", **kwargs)
    prompt = seen[0]
    for key in once:
        assert prompt.count(MARK[key]) == 1, f"{runner}: {key} aparece {prompt.count(MARK[key])} veces"
    assert not LEFTOVER.findall(prompt), LEFTOVER.findall(prompt)


@pytest.mark.parametrize("rule_index", [None, 0, 1, 2, 3, 4])
def test_rules_prompts_inject_audit_context_once(rule_index):
    from agents.rules_auditor import run_rules_auditor
    seen, fake_task = _capture_tasks()
    with patch.object(utils, "Task", side_effect=fake_task), patch.object(utils, "get_agent", return_value=MagicMock()), \
            patch.object(utils, "execute_crew", return_value="ok"):
        run_rules_auditor("AAPL", "Apple", "", MARK["audit_context"], rule_index=rule_index)
    assert seen[0].count(MARK["audit_context"]) == 1
    assert "11 Normas" not in seen[0]


def test_thesis_destroyer_attacks_the_thesis_not_the_rules():
    from agents.thesis_destroyer import run_thesis_destroyer
    seen, fake_task = _capture_tasks()
    with patch.object(utils, "Task", side_effect=fake_task), patch.object(utils, "get_agent", return_value=MagicMock()), \
            patch.object(utils, "execute_crew", return_value="ok"):
        run_thesis_destroyer("AAPL", "Apple", thesis_context=MARK["thesis_context"], rules_context=MARK["rules_context"])
    line = next(ln for ln in seen[0].splitlines() if "Tesis de Inversión Alcista" in ln)
    assert MARK["thesis_context"] in line and MARK["rules_context"] not in line


def _fake_crew_result(*raws):
    return SimpleNamespace(tasks_output=[SimpleNamespace(raw=r) for r in raws], __str__=lambda self: raws[-1])


@pytest.mark.parametrize("module, runner, first_task", [
    ("agents.franchise_crew", "run_franchise_crew", "business_model_task"),
    ("agents.management_crew", "run_management_crew", "evaluate_management_task"),
])
def test_crews_keep_both_outputs_and_inject_context_once(module, runner, first_task):
    import importlib
    mod = importlib.import_module(module)
    seen, fake_task = _capture_tasks()
    crew = MagicMock()
    crew.return_value.kickoff.return_value = _fake_crew_result(
        "## 🗺️ 1. PRIMERA SECCIÓN\ntexto uno", "<resumen><score>8</score></resumen>\n# TÍTULO\n> fecha\n\n## 2. SEGUNDA\ntexto dos")
    with patch.object(utils, "Task", side_effect=fake_task), patch.object(mod, "get_agent", return_value=MagicMock()), \
            patch.object(mod, "Crew", crew):
        out = getattr(mod, runner)("AAPL", "Apple", "", MARK["audit_context"])
    assert out.index("# TÍTULO") < out.index("PRIMERA SECCIÓN") < out.index("## 2. SEGUNDA")
    assert out.count("<resumen>") == 1
    # el contexto de auditoría, una vez en total por tarea; la primera tarea del crew no pide <resumen>
    assert all(p.count(MARK["audit_context"]) <= 1 for p in seen)
    assert "<resumen>" not in seen[0]
    assert not any(LEFTOVER.findall(p) for p in seen)


def test_forensic_auditor_cross_statement_context_and_usage_tracking():
    """Cada analista ve los otros dos estados; la síntesis ve las cuentas y los ratios; los hilos heredan el contador."""
    import json
    from agents import forensic_auditor as fa
    from utils import llm_usage

    seen, fake_task = _capture_tasks()
    trackers_seen = []

    def fake_execute(agent, task):
        trackers_seen.append(llm_usage._current.get())
        return "### informe\n**Nota de Resultados:** 7/10"

    raw = json.dumps({"income": "<<INCOME>>", "balance": "<<BALANCE>>", "cashflow": "<<CASH>>"})
    with patch.object(utils, "Task", side_effect=fake_task), patch.object(fa, "get_agent", return_value=MagicMock()), \
            patch.object(fa, "execute_crew", side_effect=fake_execute):
        with llm_usage.track_usage("T/audit") as tracker:
            out = fa.run_forensic_auditor("AAPL", "Apple", raw, "", "<<RATIOS>>")
    income, balance, cash, synthesis = seen
    for prompt, own in ((income, "<<INCOME>>"), (balance, "<<BALANCE>>"), (cash, "<<CASH>>")):
        assert prompt.count(own) == 1
        assert all(x in prompt for x in ("<<INCOME>>", "<<BALANCE>>", "<<CASH>>", "<<RATIOS>>"))
    assert all(x in synthesis for x in ("<<INCOME>>", "<<BALANCE>>", "<<CASH>>", "<<RATIOS>>", "CRUCES ENTRE ESTADOS"))
    assert trackers_seen == [tracker] * 4
    assert "---AUDIT_SYNTHESIS---" in out
    assert not any(LEFTOVER.findall(p) for p in seen)


# --- Ratios forenses ---------------------------------------------------------------------------------------------

STRUCT = {
    "years": ["2023", "2024", "LTM"],
    "income_statement": {
        "Total Revenues": [1000.0, 1200.0, 1300.0], "Cost of Sales": [-400.0, -480.0, -520.0],
        "Operating Profit": [200.0, 300.0, 325.0], "Net Income Attributable to Common Shareholders": [150.0, 220.0, 240.0],
        "EBITDA": [250.0, 360.0, 390.0], "Interest Expense": [-10.0, -10.0, -10.0],
        "Diluted Weighted Average Shares Outstanding": [100.0, 98.0, 97.0], "Effective Tax Rate": [0.2, 0.2, 0.2],
    },
    "balance_sheet": {
        "Total Cash and Cash Equivalents": [100.0, 150.0, 160.0], "Accounts Receivable": [120.0, 180.0, None],
        "Inventories": [80.0, 96.0, 100.0], "Accounts Payable": [40.0, 48.0, 52.0], "Long-Term Debt": [300.0, 250.0, 240.0],
        "Total Shareholders' Equity": [500.0, 600.0, 650.0], "Goodwill": [100.0, 100.0, 100.0],
        "Total Assets": [1000.0, 1100.0, 1150.0], "Total Current Assets": [400.0, 500.0, 520.0],
        "Total Current Liabilities": [200.0, 220.0, 230.0], "Retained Earnings": [300.0, 400.0, 450.0],
    },
    "cash_flow": {
        "Cash from Operating Activities": [180.0, 200.0, 260.0], "Capital Expenditure": [-50.0, -60.0, -65.0],
        "Depreciation & Amortization": [50.0, 60.0, 65.0], "Share-Based Compensation Expense": [13.0, 14.0, 15.0],
        "Free Cash Flow": [130.0, 140.0, 195.0],
    },
}


def test_ratios_known_values():
    r = compute(STRUCT)
    assert r["Crec. ventas"][0] is None and r["Crec. ventas"][1] == pytest.approx(0.2)
    assert r["Margen bruto"][0] == pytest.approx(0.6)
    assert r["CFO / Beneficio neto"][0] == pytest.approx(1.2)
    assert r["DSO (días)"][0] == pytest.approx(120 / 1000 * 365)
    assert r["DSO (días)"][2] is None                           # sin cuentas por cobrar ese año
    assert r["DIO (días)"][0] == pytest.approx(80 / 400 * 365)  # coste de ventas negativo en la fuente: se usa en valor absoluto
    assert r["Capex / Amortizaciones"][0] == pytest.approx(1.0)
    assert r["SBC / FCF"][0] == pytest.approx(0.1)
    assert r["Deuda neta / EBITDA"][0] == pytest.approx(200 / 250)
    assert r["Cobertura intereses (EBIT/int.)"][0] == pytest.approx(20)
    assert r["ROIC aprox."][0] == pytest.approx(200 * 0.8 / (300 + 500 - 100))
    assert r["Var. acciones"][1] == pytest.approx(-0.02)


def test_ratios_handle_missing_and_zero():
    assert compute({}) == {} and ratios_markdown(None) == ""
    broken = {"years": ["2024"], "income_statement": {"Total Revenues": [0.0]}, "balance_sheet": {}, "cash_flow": {}}
    assert ratios_markdown(broken) == ""        # nada calculable: sin tabla
    md = ratios_markdown(STRUCT)
    assert "| Ratio | 2023 | 2024 | LTM |" in md and "N/A" in md


def test_context_tables_in_millions_and_limited_years():
    years = [str(y) for y in range(2010, 2026)] + ["LTM"]
    struct = {"years": years, "income_statement": {"Total Revenues": [1_234_000_000.0] * len(years),
                                                   "Gross Profit Margin": [0.4567] * len(years)},
              "balance_sheet": {}, "cash_flow": {}}
    ctx = analysis_api.financial_tables_context({"full_audit_structure": struct})
    header = next(ln for ln in ctx.splitlines() if ln.startswith("| Métricas"))
    assert "2015" not in header and "2016" in header and "LTM" in header
    assert "1,234.0" in ctx and "0.457" in ctx


# --- Contextos condensados y normas --------------------------------------------------------------------------------

def test_condensed_report_keeps_key_sections_and_summary():
    text = ("# TÍTULO\n\n## 🗺️ 1. MODELO DE NEGOCIO Y MONETIZACIÓN\nbm\n\n## 📊 4. COMPETENCIA\nlargo detalle\n\n"
            "## 🛡️ 3. FUENTES DEL ECONOMIC MOAT\ntabla\n\n## ⭐ PUNTUACIÓN DE FRANQUICIA: 8 / 10\njust\n\n"
            "---\n### Resumen del Agente\n**Score Final:** 8/10")
    out = analysis_api.condensed_report({"franchise": text, "franchise_rejection": "motivo"}, "franchise")
    assert "bm" in out and "tabla" in out and "Resumen del Agente" in out and "motivo" in out
    assert "largo detalle" not in out
    assert analysis_api.condensed_report({}, "franchise") == ""


def test_rules_context_from_pillars():
    data = {"rules_pilar1": "x", "rules_pilar1_score": "2.0/2.0", "rules_pilar1_pros": ["foso"], "rules_pilar1_cons": [],
            "rules_pilar3": "x", "rules_pilar3_score": "0.5/2.0", "rules_pilar3_pros": [], "rules_pilar3_cons": ["roic bajo"],
            "rules_pilar3_rejection": "ROIC < 10%"}
    out = analysis_api.rules_context(data)
    assert "2.5 / 4.0" in out and "+ foso" in out and "− roic bajo" in out and "ROIC < 10%" in out
    assert analysis_api.rules_context({"rules": "informe antiguo"}) == "informe antiguo"
    assert "aún no" in analysis_api.rules_context({})


# --- El veredicto genera antes el abogado del diablo si no existe ------------------------------------------------

@pytest.mark.parametrize("existing, expect_run", [(None, True), ("## 💀 VEREDICTO DEL ABOGADO\nya hecho", False)])
def test_verdict_runs_destroyer_first_when_missing(existing, expect_run):
    db_data = {"franchise": "F", "mgmt_alloc": "M", "audit": "A"}
    if existing:
        db_data["thesis_destroyer"] = existing
    repo = MagicMock()
    crew = MagicMock()
    crew.run_thesis_destroyer.return_value = ("<resumen><score>6</score><pros><item>p</item></pros><cons><item>c</item>"
                                              "</cons></resumen>\n## 💀 VEREDICTO DEL ABOGADO\namenaza X")
    crew.run_investment_director.return_value = "<resumen><score>8</score></resumen>\n# VEREDICTO"
    tracker = MagicMock()
    with patch.object(analysis_api, "SessionLocal"), patch.object(analysis_api, "SqlAlchemyAssetRepository", return_value=repo), \
            patch.object(analysis_api, "crew_logic", crew), patch.object(analysis_api, "update_task_db"):
        analysis_api._run_agent_task("t1", "AAPL", "verdict", "Apple", db_data, "", tracker)
    assert crew.run_thesis_destroyer.called == expect_run
    destroyer_ctx = crew.run_investment_director.call_args.args[7]
    assert ("amenaza X" if expect_run else "ya hecho") in destroyer_ctx
    saved = {c.args[1] for c in repo.save_asset_data.call_args_list}
    assert ("thesis_destroyer" in saved) == expect_run and "verdict" in saved


# --- Normas de Calidad -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rule_index", [0, 1, 2, 3, 4])
def test_pillar_prompt_has_method_metrics_and_user_rule(rule_index):
    from agents.rules_auditor import run_rules_auditor
    seen, fake_task = _capture_tasks()
    rules = [{"title": f"Regla {i}", "content": f"<<REGLA{i}>>"} for i in range(5)]
    with patch.object(utils, "Task", side_effect=fake_task), patch.object(utils, "get_agent", return_value=MagicMock()), \
            patch.object(utils, "execute_crew", return_value="ok"), \
            patch("app.infrastructure.dependencies.get_settings_from_db", return_value={"custom_evaluation_rules": rules}):
        run_rules_auditor("AAPL", "Apple", "", MARK["audit_context"], rule_index=rule_index, pillar_metrics="<<METRICAS>>")
    p = seen[0]
    assert p.count("<<METRICAS>>") == 1 and p.count(MARK["audit_context"]) == 1
    assert f"<<REGLA{rule_index}>>" in p and all(f"<<REGLA{i}>>" not in p for i in range(5) if i != rule_index)
    assert "La NORMA DEL USUARIO (abajo) manda" in p and "RVRC" not in p
    assert not LEFTOVER.findall(p)


def test_default_quality_rules_are_sensible():
    from app.infrastructure.dependencies import DEFAULT_SETTINGS
    rules = DEFAULT_SETTINGS["custom_evaluation_rules"]
    assert len(rules) == 5 and all(r["title"].startswith(f"Pilar {i}") for i, r in enumerate(rules, 1))
    text = "\n".join(r["content"] for r in rules)
    assert "RVRC" not in text
    assert "Tabla resumen final con los 5 pilares" not in rules[4]["content"]   # el pilar 5 solo habla de sí mismo


def test_pillar_statements_per_pillar():
    struct = {**STRUCT}
    ctx_p1 = analysis_api.financial_tables_context({"full_audit_structure": struct}, analysis_api.PILLAR_STATEMENTS[0])
    assert "Income Statement" in ctx_p1 and "Balance Sheet" not in ctx_p1 and "Ratios forenses" in ctx_p1


def test_pillar_score_format():
    ps = analysis_api.pillar_score
    assert ps("1.5/10") == "1.5/2.0" and ps("1.3 / 2.0") == "1.3/2.0" and ps("2") == "2.0/2.0"
    assert ps("N/A") == "N/A" and ps("8.5/10") == "8.5/10"


def test_pillar_metrics():
    from app.application.services.forensic_ratios_service import pillar_metrics_markdown, _cagr
    assert _cagr([100.0, 110.0, 121.0], [0, 1, 2], 2) == pytest.approx(0.10)
    assert _cagr([-5.0, 10.0], [0, 1], 1) is None
    md = pillar_metrics_markdown(STRUCT)
    assert "| Ventas | N/A | N/A | 20.0% |" in md        # 2 ejercicios completos: solo 1 año de recorrido
    assert "ROIIC" in md and "Quick ratio" in md
    # salto de acciones (salida a bolsa): los CAGR por acción que lo cruzan, N/A*
    ipo = {"years": ["2020", "2021", "2022", "2023"],
           "income_statement": {"Total Revenues": [100.0, 120.0, 140.0, 160.0], "Diluted EPS": [50.0, 1.0, 1.2, 1.4],
                                "Diluted Weighted Average Shares Outstanding": [1.0, 100.0, 100.0, 100.0]},
           "balance_sheet": {}, "cash_flow": {}}
    md = pillar_metrics_markdown(ipo)
    assert "| BPA diluido | N/A* |" in md and "Salto anómalo de acciones en 2021" in md
    assert pillar_metrics_markdown(None) == ""


def test_quality_rules_without_double_penalties():
    from app.infrastructure.dependencies import DEFAULT_SETTINGS
    p1, p2, p3, p4, p5 = (r["content"] for r in DEFAULT_SETTINGS["custom_evaluation_rules"])
    assert "a un dígito sin razón cíclica" not in p1 and "El crecimiento NO se juzga aquí" in p1
    assert all("1.6 a 1.9" in p for p in (p2, p3, p5))      # el Pilar 4 tiene sus escalones propios (1.5, 1.0…)
    assert "Altman Z'' < 1,1: fragilidad" not in p4 and "nunca una nota automática" in p4
    assert "ROE" in p3 and "CET1" in p4


def test_roiic_needs_relevant_reinvestment_and_avg3_cagr():
    from app.application.services.forensic_ratios_service import _cagr_avg3, pillar_metrics_markdown
    assert _cagr_avg3([10.0, 10.0, 10.0, 20.0, 20.0, 20.0], list(range(6))) == pytest.approx(2 ** (1 / 3) - 1)
    assert _cagr_avg3([1.0] * 5, list(range(5))) is None
    flat = {"years": ["2020", "2021", "2022", "2023"],
            "income_statement": {"Total Revenues": [100.0] * 4, "Operating Profit": [10.0, 12.0, 14.0, 16.0],
                                 "Effective Tax Rate": [0.2] * 4},
            "balance_sheet": {"Total Shareholders' Equity": [100.0, 101.0, 102.0, 103.0]}, "cash_flow": {}}
    assert "| N/A / N/A / N/A |" in pillar_metrics_markdown(flat)   # capital +3%: el ROIIC sería ruido
