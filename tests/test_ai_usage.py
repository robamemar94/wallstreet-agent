import json
from datetime import date
from types import SimpleNamespace

from app.application.services import ai_usage_service as svc
from utils import llm_usage


def _log(tmp_path, rows):
    p = tmp_path / "llm_usage.jsonl"
    p.write_text("\n".join(json.dumps({"calls": 1, "total_tokens": 1000, "search_queries": 0, "models": ["gemini-3.8-flash"], **r})
                           for r in rows) + "\n", encoding="utf-8")
    return str(p)


def test_labels_are_classified_by_task():
    assert svc.parse_label("KLAC/rules_pilar2") == {"ticker": "KLAC", "category": "analysis", "task": "Normas · pilar 2"}
    assert svc.parse_label("EVO.ST/thesis-news")["category"] == "radar"
    assert svc.parse_label("news-weekly-summary") == {"ticker": None, "category": "summary", "task": "Resumen semanal"}
    assert svc.parse_label("POST /api/audit/extract/AVGO") == {"ticker": "AVGO", "category": "data", "task": "Extracción de cuentas"}
    assert svc.parse_label("POST /api/algo-nuevo")["category"] == "other"


def test_analysis_parts_in_a_row_are_one_query(tmp_path):
    path = _log(tmp_path, [
        {"label": "KLAC/audit", "date": "2026-10-01 10:00:00", "cost_usd": 0.10},
        {"label": "KLAC/franchise", "date": "2026-10-01 10:20:00", "cost_usd": 0.20},
        {"label": "NVDA/thesis-news", "date": "2026-10-01 10:25:00", "cost_usd": 0.02},
        {"label": "KLAC/verdict", "date": "2026-10-01 10:50:00", "cost_usd": 0.30},
        {"label": "KLAC/franchise", "date": "2026-10-02 18:00:00", "cost_usd": 0.15},    # otro día: repetir el foso
    ])
    runs = svc.group_runs(svc.load_rows(path))
    assert [(r["ticker"], r["task"], round(r["cost"], 2)) for r in runs] == [
        ("KLAC", "Foso", 0.15), ("NVDA", "Radar de noticias", 0.02), ("KLAC", "Análisis de empresa", 0.60)]
    assert runs[2]["parts"] == ["Auditoría forense", "Foso", "Veredicto"]


def test_kpis_forecast_budget_and_free_calls(tmp_path):
    path = _log(tmp_path, [
        {"label": "A/verdict", "date": "2026-09-15 10:00:00", "cost_usd": 4.0},
        {"label": "A/verdict", "date": "2026-10-01 10:00:00", "cost_usd": 3.0},    # jueves
        {"label": "A/verdict", "date": "2026-10-05 09:00:00", "cost_usd": 2.0},    # lunes
        {"label": "news-weekly-summary", "date": "2026-10-05 10:00:00", "cost_usd": 0.5, "free_calls": 1},
    ])
    rows = svc.load_rows(path)
    k = svc.kpis(rows, date(2026, 10, 5))
    assert (k["today"], k["week"], k["month"], k["prev_month"], k["year"]) == (2.0, 2.0, 5.0, 4.0, 9.0)
    assert k["forecast"] == 31.0                                   # 5 $ en 5 días → 31 $ en octubre
    st = svc.budget_status(k["month"], k["forecast"], {"monthly_usd": 6, "alert_pct": 80})
    assert st["level"] == "alert" and st["forecast_over"] and round(st["pct"]) == 83
    assert svc.budget_status(7, 7, {"monthly_usd": 6})["level"] == "over"
    assert svc.budget_status(1, 1, {"monthly_usd": 0}) is None
    by = svc.breakdown(svc.group_runs([r for r in rows if r["when"].month == 10]), "category")
    assert [(c["name"], c["count"], c["free"]) for c in by] == [("Análisis de empresa", 2, 0), ("Resumen semanal", 1, 1)]


def test_budget_notice_one_per_month_and_level(tmp_path):
    path = _log(tmp_path, [{"label": "A/verdict", "date": "2026-10-02 10:00:00", "cost_usd": 9.0}])
    svc._cache.clear()
    n = svc.budget_notice({"monthly_usd": 10, "alert_pct": 80}, date(2026, 10, 5), path)
    assert n["key"] == "ia|att|budget|2026-10|alert" and n["href"] == "/ia"
    assert svc.budget_notice({"monthly_usd": 8}, date(2026, 10, 5), path)["key"].endswith("|over")
    assert svc.budget_notice({"monthly_usd": 20}, date(2026, 10, 5), path) is None


def test_chart_by_day_and_by_month(tmp_path):
    path = _log(tmp_path, [{"label": "A/verdict", "date": "2026-10-02 10:00:00", "cost_usd": 1.0},
                           {"label": "B/thesis-news", "date": "2026-08-02 10:00:00", "cost_usd": 0.5}])
    rows = svc.load_rows(path)
    month = svc.chart_series([r for r in rows if r["when"].month == 10], "mes", date(2026, 10, 5))
    assert len(month["labels"]) == 31 and month["series"][0]["values"][1] == 1.0
    year = svc.chart_series(rows, "anio", date(2026, 10, 5))
    assert year["labels"][-1] == "oct 26" and [s["key"] for s in year["series"]] == ["analysis", "radar"]


def test_free_key_calls_cost_nothing(monkeypatch):
    monkeypatch.setenv("GEMINI_FREE_API_KEY", "free-key")
    assert llm_usage._is_free(SimpleNamespace(_api_client=SimpleNamespace(api_key="free-key")))
    assert not llm_usage._is_free(SimpleNamespace(_api_client=SimpleNamespace(api_key="paid-key")))
    usage = SimpleNamespace(prompt_token_count=1_000_000, cached_content_token_count=0, tool_use_prompt_token_count=0,
                            candidates_token_count=0, thoughts_token_count=0)
    resp = SimpleNamespace(usage_metadata=usage, model_version="gemini-3.8-flash", candidates=[])
    t = llm_usage.UsageTracker("x")
    t.record("gemini-3.8-flash", resp, free=True)
    assert t.summary()["cost_usd"] == 0 and t.summary()["free_calls"] == 1
    t.record("gemini-3.8-flash", resp)
    assert t.summary()["cost_usd"] == 0.75


def test_next_results_found_date_is_not_searched_again_until_it_passes():
    from datetime import datetime
    from app.application.services.results_calendar import is_fresh
    now = datetime(2026, 10, 5, 16, 0)
    found = {"date": "2026-10-22", "fetched_at": "2026-09-28T15:41:40"}           # buscada hace 7 días: sigue valiendo
    assert is_fresh(found, now) and not is_fresh(found, datetime(2026, 10, 23, 9, 0))
    missing = {"date": None, "fetched_at": "2026-10-02T21:34:43"}
    assert is_fresh(missing, datetime(2026, 11, 15, 22, 0)) and not is_fresh(missing, datetime(2026, 11, 17, 22, 0))
    assert not is_fresh(None, now)


def test_old_weekly_summaries_are_free_only_if_flash_lite(tmp_path):
    path = _log(tmp_path, [{"label": "news-weekly-summary", "date": "2026-09-29 16:17:05", "cost_usd": 0.006, "models": ["gemini-3.8-flash"]},
                           {"label": "news-weekly-summary", "date": "2026-10-05 10:07:29", "cost_usd": 0.007, "models": ["gemini-3.5-flash-lite"]}])
    assert [r["free"] for r in svc.load_rows(path)] == [False, True]
