import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.infrastructure.db.database import Base
from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository
from app.application.services.thesis_service import (
    classify, compute_auto_metrics, evaluate_thesis, implied_cagr, load_thesis_yaml,
)

HIGHER = {"kind": "numeric", "direction": "higher", "green_threshold": 25, "red_threshold": 10}
LOWER = {"kind": "numeric", "direction": "lower", "green_threshold": 30, "red_threshold": 40}


@pytest.mark.parametrize("value,expected", [(30, "green"), (25, "green"), (15, "amber"), (10, "amber"), (9.9, "red")])
def test_classify_higher_is_better(value, expected):
    assert classify(HIGHER, value) == expected


@pytest.mark.parametrize("value,expected", [(27.8, "green"), (35, "amber"), (40, "amber"), (41, "red")])
def test_classify_lower_is_better(value, expected):
    assert classify(LOWER, value) == expected


def test_classify_without_red_threshold_never_auto_red():
    kpi = {"kind": "numeric", "direction": "higher", "green_threshold": 90, "red_threshold": None}
    assert classify(kpi, 94) == "green"
    assert classify(kpi, 50) == "amber"


def test_classify_qualitative_is_manual():
    assert classify({"kind": "qualitative", "green_threshold": None}, 10) is None


def _obs(*statuses):
    return [{"period": f"P{i}", "date": f"2026-0{i + 1}-01", "value": None, "status": s} for i, s in enumerate(statuses)]


def _thesis(pillars):
    return {"pillars": [{"id": i, "name": f"p{i}", "weight": w, "kpis": kpis} for i, (w, kpis) in enumerate(pillars)]}


def test_health_score_weights_pillars_and_skips_missing_data():
    thesis = _thesis([
        (3, [{"id": 1, "name": "a", "observations": _obs("green")},
             {"id": 2, "name": "b", "observations": []}]),                  # 100, un KPI sin dato
        (1, [{"id": 3, "name": "c", "observations": _obs("red")}]),        # 0
        (0, [{"id": 4, "name": "ia", "observations": _obs("red")}]),       # peso 0: no puntúa
    ])
    ev = evaluate_thesis(thesis)
    assert ev["health_score"] == 75
    assert ev["coverage"] == 75
    assert ev["counts"] == {"green": 1, "amber": 0, "red": 2, "none": 1}


def test_kill_switch_needs_two_consecutive_reds():
    one_red = _thesis([(1, [{"id": 1, "name": "gm", "is_kill_switch": True, "observations": _obs("green", "red")}])])
    two_red = _thesis([(1, [{"id": 1, "name": "gm", "is_kill_switch": True, "observations": _obs("red", "red")}])])
    recovered = _thesis([(1, [{"id": 1, "name": "gm", "is_kill_switch": True, "observations": _obs("red", "green")}])])

    assert evaluate_thesis(one_red)["kill_switches"][0]["state"] == "watch"
    assert not evaluate_thesis(one_red)["review_recommended"]
    assert evaluate_thesis(two_red)["review_recommended"]
    assert evaluate_thesis(recovered)["kill_switches"][0]["state"] == "ok"


def test_trend_compares_last_two_periods():
    ev = evaluate_thesis(_thesis([(1, [{"id": 1, "name": "x", "observations": _obs("green", "amber")}])]))
    assert ev["pillars"][0]["kpis"][0]["eval"]["trend"] == "down"


def test_implied_cagr():
    cagr = implied_cagr(100, 200, 2036, today=datetime.datetime(2026, 1, 1))
    assert cagr == pytest.approx(2 ** 0.1 - 1, rel=1e-3)
    assert implied_cagr(None, 200, 2036) is None


def test_compute_auto_metrics_matches_nvda_q2_fy27():
    cols = pd.to_datetime(["2026-07-31", "2026-04-30", "2026-01-31", "2025-10-31", "2025-07-31"])
    income = pd.DataFrame({c: v for c, v in zip(cols, [
        [96221e6, 72142e6, 63734e6, 24285e6], [81615e6, 61157e6, 53536e6, 24391e6], [68127e6, 51093e6, 44299e6, 24432e6],
        [57006e6, 41849e6, 36010e6, 24483e6], [46743e6, 33853e6, 28440e6, 24532e6]])},
        index=["Total Revenue", "Gross Profit", "Operating Income", "Diluted Average Shares"])
    cashflow = pd.DataFrame({c: v for c, v in zip(cols, [
        [21400e6, -2677e6, 24077e6], [48587e6, -1757e6, 50344e6], [34904e6, -1284e6, 36188e6],
        [22115e6, -1636e6, 23751e6], [13470e6, -1895e6, 15365e6]])},
        index=["Free Cash Flow", "Capital Expenditure", "Operating Cash Flow"])
    out = compute_auto_metrics(income, cashflow)
    m = out["metrics"]
    assert out["period_end"] == "2026-07-31"
    assert m["revenue_yoy"] == pytest.approx(105.85, abs=0.01)
    assert m["gross_margin"] == pytest.approx(74.98, abs=0.01)
    assert m["operating_margin"] == pytest.approx(66.24, abs=0.01)
    assert m["diluted_shares_yoy"] == pytest.approx(-1.01, abs=0.01)


@pytest.fixture
def repo():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    yield SqlAlchemyThesisRepository(db)
    db.close()


@pytest.mark.parametrize("path", ["config/theses/nvda.yaml", "config/theses/dsgx.yaml"])
def test_yaml_theses_import_and_reimport_is_idempotent(repo, path):
    spec = load_thesis_yaml(path)
    first = repo.import_spec(spec)
    kpi = first["pillars"][0]["kpis"][0]
    repo.upsert_observation(kpi["id"], "Q3 FY27", "2026-10-31", None, "amber", "registrado desde la web", "manual")

    second = repo.import_spec(load_thesis_yaml(path))
    kpis_first = sum(len(p["kpis"]) for p in first["pillars"])
    kpis_second = sum(len(p["kpis"]) for p in second["pillars"])
    assert kpis_first == kpis_second
    assert len(second["events"]) == len(first["events"])
    kpi_after = second["pillars"][0]["kpis"][0]
    assert kpi_after["id"] == kpi["id"]
    assert [o["period"] for o in kpi_after["observations"]][-1] == "Q3 FY27"


def test_upsert_observation_overwrites_same_period(repo):
    thesis = repo.import_spec(load_thesis_yaml("config/theses/dsgx.yaml"))
    kpi = next(k for p in thesis["pillars"] for k in p["kpis"] if k["key"] == "adj_ebitda_margin")
    repo.upsert_observation(kpi["id"], "Q2 FY27", "2026-07-31", 42.0, "red", None, "manual")
    obs = repo.get_kpi(kpi["id"])["observations"]
    assert [(o["period"], o["value"]) for o in obs] == [("FY26", 45.0), ("Q2 FY27", 42.0)]


def test_reimport_moves_kpi_to_new_pillar_and_keeps_observations(repo):
    spec = {"ticker": "TST", "title": "t", "pillars": [
        {"key": "old", "name": "Old", "kpis": [
            {"key": "gm", "name": "GM", "green_threshold": 70, "red_threshold": 65,
             "observations": [{"period": "Q1", "date": "2026-01-31", "value": 72}]},
            {"key": "dropped", "name": "Dropped", "kind": "qualitative"}]}]}
    repo.import_spec(spec)
    spec["pillars"] = [{"key": "new", "name": "New", "kpis": [spec["pillars"][0]["kpis"][0]]}]
    thesis = repo.import_spec(spec)
    assert [p["key"] for p in thesis["pillars"]] == ["new"]
    assert [k["key"] for k in thesis["pillars"][0]["kpis"]] == ["gm"]
    assert thesis["pillars"][0]["kpis"][0]["observations"][0]["value"] == 72


def test_thesis_document_renders_sections():
    from app.application.services.thesis_service import render_document
    spec = load_thesis_yaml("config/theses/dsgx.yaml")
    doc = render_document(spec["document_md"])
    assert len(doc["sections"]) == 21
    assert doc["sections"][15]["name"] == "16. Los cuatro kill switches"
    assert '<table>' in doc["html"]
    assert render_document(None) == {"html": None, "sections": []}


# --- IA: parseo de respuestas y radar semanal ---

from app.application.services import thesis_ai_service as ai


def test_extract_json_tolerates_markdown_fences():
    assert ai.extract_json('Aquí tienes:\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        ai.extract_json("sin json")


def test_parse_kpis_drops_unknown_kpis_and_bad_values():
    from app.application.services.thesis_review_service import parse_kpis
    data = {"kpis": [
        {"kpi_id": 1, "value": "12.5", "status": "green", "evidence": "revenue +12.5%", "confidence": "high"},
        {"kpi_id": 2, "value": "n/d", "status": "purple"},
        {"kpi_id": 99, "value": 3},
        {"kpi_id": "x"}]}
    items = parse_kpis(data, {1, 2})
    assert [(i["kpi_id"], i["value"], i["status"]) for i in items] == [(1, 12.5, "green"), (2, None, None)]
    assert items[0]["confidence"] == "high"


def test_normalize_judgement_filters_bad_fields():
    from app.application.services.thesis_review_service import normalize_judgement
    j = normalize_judgement({"verdict": "GENIAL", "hypotheses": [{"id": "H1", "status": "refuerza"}, {"id": "H2", "status": "meh"}],
                             "proposals": [{"type": "nuevo_kpi", "title": "Networking separado"}, {"type": "x"}],
                             "results_summary": "no es lista"})
    assert j["verdict"] is None and [h["id"] for h in j["hypotheses"]] == ["H1"]
    assert [p["title"] for p in j["proposals"]] == ["Networking separado"] and j["results_summary"] == []


def test_credibility_score_across_reviews():
    from app.application.services.thesis_review_service import credibility_score
    reports = [{"call": {"promises_check": [{"result": "cumplida"}, {"result": "parcial"}, {"result": "pendiente"}]}},
               {"call": {"promises_check": [{"result": "incumplida"}]}}, {}]
    assert credibility_score(reports) == {"score": 50, "evaluated": 3}
    assert credibility_score([{}]) is None


def test_classify_and_pick_documents():
    from app.application.services.earnings_docs import classify_document, pick_documents
    transcript = "NVIDIA call participants Operator. " + "word " * 3500 + " Question-and-Answer analyst"
    release = "NVIDIA Announces Financial Results for Second Quarter Fiscal 2027. Revenue net income quarter " + "x " * 500
    assert classify_document(transcript, ["NVIDIA"]) == "transcript"
    assert classify_document(release, ["NVIDIA"]) == "press_release"
    assert classify_document(release, ["Descartes"]) is None
    docs = pick_documents([{"url": "https://blog/x", "text": release}, {"url": "https://www.globenewswire.com/n", "text": release},
                           {"url": "https://www.fool.com/t", "text": transcript}], ["NVIDIA"])
    assert docs["transcript"]["url"] == "https://www.fool.com/t"
    assert "globenewswire" in docs["press_release"]["url"]


def test_notices_new_results_and_ready_review(repo, monkeypatch):
    from app.application.services import thesis_review_service as rs
    thesis = repo.import_spec(load_thesis_yaml("config/theses/dsgx.yaml"))   # reference_date 2026-09-24
    monkeypatch.setattr(rs, "fetch_last_earnings", lambda t: {"date": "2026-12-02"})
    assert [n["type"] for n in rs.compute_notices(repo)] == ["new_results"]
    rid = repo.create_review(thesis["id"])
    assert rs.compute_notices(repo) == []                          # en curso: sin avisos
    repo.finish_review(rid, "ready", report={"period": "Q3 FY27", "report_date": "2026-12-02"})
    notices = rs.compute_notices(repo)
    assert [n["type"] for n in notices] == ["review_ready"]        # ya cubierta por la revisión
    repo.set_review_status(rid, "applied")
    assert rs.compute_notices(repo) == []


def test_parse_news_keeps_only_very_relevant_and_orders():
    data = {"items": [
        {"title": "ruido", "materiality": 2, "step": 1},
        {"title": "rutina", "materiality": 3, "step": 1},
        {"title": "b", "materiality": 4, "step": 1, "date": "2026-09-20"},
        {"title": "a", "materiality": 5, "step": 9, "date": "2026-09-18", "impact": "raro"},
        {"title": "c", "materiality": 4, "step": 2, "date": "2026-09-25"}]}
    out = ai.parse_news(data, n_steps=3)
    assert [i["title"] for i in out["items"]] == ["a", "c", "b"]
    assert out["items"][0]["step"] is None and out["items"][0]["impact"] == "neutral"


def test_news_due_once_a_week():
    now = datetime.datetime(2026, 9, 28, 9, 0)
    assert ai.news_due(None, now)
    assert not ai.news_due({"created_at": "2026-09-25T09:00:00"}, now)
    assert ai.news_due({"created_at": "2026-09-21T09:00:00"}, now)


def test_reimport_keeps_ai_data(repo):
    thesis = repo.import_spec(load_thesis_yaml("config/theses/dsgx.yaml"))
    repo.set_extra(thesis["id"], "news_digest", {"items": [], "created_at": "2026-09-28T09:00:00"})
    again = repo.import_spec(load_thesis_yaml("config/theses/dsgx.yaml"))
    assert again["extra"]["news_digest"]["created_at"] == "2026-09-28T09:00:00"
    assert again["extra"]["document_md"]


def test_news_window_covers_gap_since_last_radar():
    now = datetime.datetime(2026, 10, 10, 9, 0)
    assert ai.news_window_days(None, now) == 7
    assert ai.news_window_days({"created_at": "2026-10-05T09:00:00"}, now) == 7      # forzado antes de 7 días
    assert ai.news_window_days({"created_at": "2026-09-28T09:00:00"}, now) == 13     # 12 días de hueco + 1
    assert ai.news_window_days({"created_at": "2026-06-01T09:00:00"}, now) == 31     # tope


def test_news_dedupe_across_radars_and_unread_count(repo):
    thesis = repo.import_spec(load_thesis_yaml("config/theses/dsgx.yaml"))
    steps = [p["name"] for p in thesis["pillars"]]
    item = {"title": "Descartes compra X", "url": "https://x/1", "date": "2026-10-01", "step": 1, "impact": "positive", "materiality": 5}
    assert repo.add_news(thesis["id"], [item], steps) == 1
    assert repo.add_news(thesis["id"], [dict(item, url="https://otra/2", title="  descartes COMPRA x ")], steps) == 0
    assert repo.unread_news_count() == {"unread": 1, "critical": 1}
    news = repo.list_news(thesis_id=thesis["id"])
    assert news[0]["step_name"] == steps[0]
    repo.mark_news_seen([news[0]["id"]])
    assert repo.unread_news_count()["unread"] == 0


def test_sbc_to_fcf_and_fcf_per_share_cagr_3y():
    q = pd.to_datetime(["2026-07-31", "2026-04-30", "2026-01-31", "2025-10-31", "2025-07-31"])
    income = pd.DataFrame({c: [100.0, 60.0, 40.0, 10.0] for c in q}, index=["Total Revenue", "Gross Profit", "Operating Income", "Diluted Average Shares"])
    cashflow = pd.DataFrame({c: [20.0, -2.0, 25.0, 2.0] for c in q}, index=["Free Cash Flow", "Capital Expenditure", "Operating Cash Flow", "Stock Based Compensation"])
    y = pd.to_datetime(["2026-01-31", "2025-01-31", "2024-01-31", "2023-01-31"])
    a_income = pd.DataFrame({c: [s] for c, s in zip(y, [87.6, 87.3, 86.8, 86.5])}, index=["Diluted Average Shares"])
    a_cash = pd.DataFrame({c: [f] for c, f in zip(y, [261.0, 213.0, 202.0, 186.0])}, index=["Free Cash Flow"])
    m = compute_auto_metrics(income, cashflow, a_income, a_cash)["metrics"]
    assert m["sbc_to_fcf"] == 10.0
    assert m["fcf_per_share_cagr_3y"] == pytest.approx(11.48, abs=0.01)   # (261/87,6)/(186/86,5) a 3 años
    assert compute_auto_metrics(income, cashflow)["metrics"]["fcf_per_share_cagr_3y"] is None


def test_aggregate_capex_yoy_requires_all_hyperscalers():
    from app.application.services.thesis_service import aggregate_capex_yoy
    idx = pd.to_datetime(["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"])
    s = lambda cur, prev: pd.Series([-cur, -1, -1, -1, -prev], index=idx)
    out = aggregate_capex_yoy({"MSFT": s(35.8e9, 17.08e9), "META": s(30.12e9, 16.54e9)})
    assert out["value"] == pytest.approx(100 * (65.92 / 33.62 - 1), abs=0.01)
    assert "MSFT 2026-06-30 35.8B" in out["detail"]
    assert aggregate_capex_yoy({"MSFT": s(35.8e9, 17.08e9), "AMZN": pd.Series([-1.0], index=idx[:1])}) is None


def test_stale_running_reviews_fail_on_startup(repo):
    thesis = repo.import_spec(load_thesis_yaml("config/theses/dsgx.yaml"))
    rid = repo.create_review(thesis["id"])
    assert repo.fail_stale_reviews() == 1
    assert repo.get_review(rid)["status"] == "error"
    assert repo.fail_stale_reviews() == 0


def test_expectations_include_consensus_guidance_promises_and_last_values():
    from app.application.services.thesis_review_service import build_expectations
    ev = evaluate_thesis(_thesis([(1, [{"id": 1, "name": "Gross margin", "unit": "%", "observations": _obs("green")}])]))
    ev["pillars"][0]["kpis"][0]["observations"][0]["value"] = 75.0
    ev = evaluate_thesis({"pillars": [{**ev["pillars"][0], "kpis": [dict(ev["pillars"][0]["kpis"][0])]}]})
    prev = [{"period": "Q2 FY27", "call": {"guidance": [{"metric": "Revenue", "period": "Q3 FY27", "value": "$108B ±2%"}],
                                             "promises": [{"promise": "Rubin 20% del DC", "deadline": "Q3 FY27"}]}}]
    txt = build_expectations(ev, prev, {"eps_estimate": 2.47, "eps_reported": 2.6})
    assert "Consenso de BPA para el trimestre: 2.47" in txt
    assert "Revenue (Q3 FY27): $108B ±2%" in txt and "Rubin 20% del DC" in txt
    assert "Gross margin: 75.0%" in txt
    assert "primera revisión" in build_expectations({"pillars": []}, [], None)


def test_live_valuation_scenarios_and_reverse_dcf():
    from app.application.services.thesis_service import live_valuation
    val = {"scenarios": [{"name": "Base", "fcf_ps_cagr": 10, "multiple": "20x"}]}
    v = live_valuation(val, fcf_ps=5.0, price=100.0, today=datetime.datetime(2026, 1, 1))
    sc = v["scenarios"][0]
    assert v["years"] == 10 and v["target_year"] == 2036
    assert sc["price_target"] == pytest.approx(5 * 1.1 ** 10 * 20, rel=1e-4)            # ≈259,37
    assert sc["cagr"] == pytest.approx(100 * ((sc["price_target"] / 100) ** 0.1 - 1), abs=0.01)
    assert sc["entry_prices"][10] == pytest.approx(sc["price_target"] / 1.1 ** 10, rel=1e-4)
    # DCF inverso: crecimiento que iguala precio*(1+r)^n = fcf*(1+g)^n*m
    g = v["implied_growth"][10] / 100
    assert 5 * (1 + g) ** 10 * 20 == pytest.approx(100 * 1.1 ** 10, rel=1e-3)
    assert live_valuation(val, None, 100) is None



# --- Cambios en la tesis desde la app ---

def test_add_kpi_and_change_threshold_in_yaml(tmp_path):
    from app.application.services.thesis_changes_service import add_kpi_to_yaml, change_threshold_in_yaml
    text = open("config/theses/nvda.yaml", encoding="utf-8").read()
    new_text = add_kpi_to_yaml(text, {"step_name": "Vendor financing", "step_question": "¿Crece la exposición?",
                                      "kpi": {"name": "Garantías / revenue", "unit": "%", "direction": "lower",
                                              "green_threshold": 5, "red_threshold": 15, "green_text": "<5%"}}, {"dc_yoy"})
    new_text = change_threshold_in_yaml(new_text, "gross_margin", {"green_threshold": 70, "green_text": "≥70%, «nuevo»"})
    path = tmp_path / "t.yaml"
    path.write_text(new_text, encoding="utf-8")
    spec = load_thesis_yaml(str(path).replace("t.yaml", "t.yaml"))
    kpis = {k["key"]: k for p in spec["pillars"] for k in p["kpis"]}
    assert kpis["garantias_revenue"]["direction"] == "lower" and kpis["garantias_revenue"]["red_threshold"] == 15
    assert spec["pillars"][-1]["name"] == "Vendor financing"
    assert kpis["gross_margin"]["green_threshold"] == 70 and kpis["gross_margin"]["green_text"] == "≥70%, «nuevo»"
    assert kpis["gross_margin"]["red_threshold"] == 65                      # lo no tocado se conserva
    with pytest.raises(ValueError):
        change_threshold_in_yaml(text, "no_existe", {"green_threshold": 1})


def test_compute_exposure_and_risk_notice():
    from app.application.services.thesis_service import compute_exposure, exposure_risk
    exp = compute_exposure([{"ticker": "NVDA", "shares": 30, "currency": "USD", "cost_eur": 5000},
                            {"ticker": "BARC.L", "shares": 1000, "currency": "GBp", "cost_eur": 2000},
                            {"ticker": "XX", "shares": 5, "currency": "JPY"}],
                           {"NVDA": 225.0, "BARC.L": 300.0, "XX": 10.0}, {"USD": 0.9, "GBP": 1.2})
    nv, barc = exp["positions"]["NVDA"], exp["positions"]["BARC.L"]
    assert nv["value_eur"] == pytest.approx(30 * 225 * 0.9) and barc["value_eur"] == pytest.approx(10 * 300 * 1.2)
    assert "XX" not in exp["positions"]                                   # sin FX: no se inventa
    assert nv["weight"] + barc["weight"] == pytest.approx(100)
    ok = {"verdict": "INTACTA"}
    assert exposure_risk(ok, {"kill_triggered": [], "health_status": "green"}, 60) is None
    assert exposure_risk({"verdict": "DEBILITADA"}, {"kill_triggered": []}, 3) is None      # posición pequeña
    assert "tesis DEBILITADA" in exposure_risk({"verdict": "DEBILITADA"}, {"kill_triggered": []}, 12)


def test_decision_outcome_and_review_schedule():
    from app.application.services.thesis_service import decision_outcome
    buy = {"date": "2026-01-01", "action": "comprar", "price": 100.0}
    o = decision_outcome(buy, 120.0, today=datetime.date(2026, 7, 5))
    assert o["days"] == 185 and o["price_change"] == 20.0 and o["good"] is True and o["review_due"] == 180
    sell = {"date": "2026-01-01", "action": "vender", "price": 100.0}
    assert decision_outcome(sell, 120.0, today=datetime.date(2026, 2, 1))["good"] is False   # vendió y siguió subiendo
    reviewed = {**buy, "reviewed_at": "2026-07-06T10:00:00"}
    assert decision_outcome(reviewed, None, today=datetime.date(2026, 9, 1))["review_due"] is None
    assert decision_outcome(reviewed, None, today=datetime.date(2027, 1, 2))["review_due"] == 365


def test_unlogged_transactions_since_thesis(repo):
    from app.application.services.thesis_service import unlogged_transactions
    txs = [{"date": "2026-10-02", "type": "BUY", "shares": 3, "price": 190, "ref": "2026-10-02|BUY|3|190"},
           {"date": "2026-10-05", "type": "DIVIDEND", "shares": 0, "price": 1, "ref": "d"},
           {"date": "2026-07-01", "type": "BUY", "shares": 1, "price": 180, "ref": "old"}]
    assert [t["ref"] for t in unlogged_transactions(txs, [], "2026-09-28")] == ["2026-10-02|BUY|3|190"]
    assert unlogged_transactions(txs, [{"transaction_ref": "2026-10-02|BUY|3|190"}], "2026-09-28") == []


def test_validate_spec_and_strip_fences():
    from app.application.services.thesis_service import validate_spec
    from app.application.services.thesis_pdf import _strip_fences
    assert _strip_fences("Aquí va:\n```yaml\nticker: X\n```\nfin") == "ticker: X\n"
    ok = {"ticker": "X", "title": "t", "pillars": [{"key": "a", "name": "A", "kpis": [{"key": "k", "name": "K", "green_threshold": 1}]}]}
    assert validate_spec(ok)["ticker"] == "X"
    with pytest.raises(ValueError):
        validate_spec({**ok, "pillars": [{"key": "a", "name": "A", "kpis": [{"key": "k", "name": "K"}]}]})
    with pytest.raises(ValueError):
        validate_spec("no es un dict")
