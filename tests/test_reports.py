import datetime

import pandas as pd
import pytest

from app.application.services import report_service as rs


def test_periods_close_at_the_right_time():
    w = rs.period("W", datetime.date(2026, 10, 8))                       # jueves → semana del lunes 5 al viernes 9
    assert (w["key"], w["start"], w["end"]) == ("W2026-10-05", datetime.date(2026, 10, 5), datetime.date(2026, 10, 9))
    assert w["ready"] == datetime.datetime(2026, 10, 10, 12)              # el sábado a mediodía (tras el radar)
    m, q, y = rs.period("M", datetime.date(2026, 2, 12)), rs.period("Q", datetime.date(2026, 8, 3)), rs.period("Y", datetime.date(2026, 5, 1))
    assert (m["key"], m["end"], m["label"]) == ("M2026-02", datetime.date(2026, 2, 28), "Febrero 2026")
    assert (q["key"], q["start"], q["end"]) == ("Q2026-3", datetime.date(2026, 7, 1), datetime.date(2026, 9, 30))
    assert (y["key"], y["ready"]) == ("Y2026", datetime.datetime(2027, 1, 1))
    now = datetime.datetime(2026, 10, 13, 9)
    assert [p["key"] for p in rs.closed_periods("Q", datetime.date(2026, 2, 12), now)] == ["Q2026-1", "Q2026-2", "Q2026-3"]
    assert rs.closed_periods("Y", datetime.date(2026, 2, 12), now) == []
    assert rs.closed_periods("W", datetime.date(2026, 2, 12), now)[-1]["key"] == "W2026-10-05"


def _df():
    idx = pd.bdate_range("2026-01-01", "2026-03-31")
    df = pd.DataFrame({"AAA": 100.0, "SPY": 500.0, "USDEUR=X": 1.0}, index=idx)
    df.loc["2026-02-16":, "AAA"] = 110.0      # sube un 10 % la tercera semana de febrero
    df.loc["2026-02-16":, "SPY"] = 505.0
    return df


def test_performance_counts_buys_as_contributions_not_gains():
    df = _df()
    txs = [{"ticker": "AAA", "date": "2026-01-05", "type": "BUY", "shares": 10, "price": 100.0, "currency": "USD", "fx": 1.0},
           {"ticker": "AAA", "date": "2026-02-18", "type": "BUY", "shares": 10, "price": 110.0, "currency": "USD", "fx": 1.0}]
    p = rs.performance(df, txs, {}, datetime.date(2026, 2, 1), datetime.date(2026, 2, 28))
    assert p["value_start"] == pytest.approx(1000) and p["value_end"] == pytest.approx(2200)
    assert p["pnl_eur"] == pytest.approx(100)                       # 2.200 − 1.000 − 1.100 metidos: ganó 100 €
    assert p["bench_pct"] == pytest.approx(1.0) and p["best"][0]["ticker"] == "AAA"
    f = rs.flows(df, txs, "2026-02-01", "2026-02-28")
    assert f == {"buys": pytest.approx(1100), "sells": 0.0, "net": pytest.approx(1100)}


def test_report_notices_only_for_new_reports(monkeypatch):
    now = datetime.datetime(2026, 10, 13, 9)
    reports = [{"key": "W2026-10-05", "kind": "W", "label": "Semana", "created_at": "2026-10-10T13:00:00", "reconstructed": False, "pct": 2.34},
               {"key": "M2026-09", "kind": "M", "label": "Septiembre 2026", "created_at": "2026-10-13T08:00:00", "reconstructed": True, "pct": 0.4},
               {"key": "M2026-08", "kind": "M", "label": "Agosto 2026", "created_at": "2026-09-01T01:00:00", "reconstructed": False, "pct": 1.0}]
    monkeypatch.setattr(rs, "list_reports", lambda db: reports)
    out = rs.notices(None, now)
    assert [n["key"] for n in out] == ["informes|att|report|W2026-10-05|"]
    assert "+2,3 %" in out[0]["text"] and out[0]["href"] == "/portfolio#informes-W2026-10-05"


def test_ai_prompts_by_kind():
    base = {"label": "3.º trimestre 2026", "benchmark": "S&P 500", "performance": {"pct": 11.85}}
    q = rs.ai_prompt({**base, "kind": "Q"}, {"roles": [{"role": "Growth", "weight_pct": 40.8, "target_min": 25, "target_max": 35}]})
    assert '"attribution"' in q and '"review"' in q and "40.8" in q and "No des órdenes" in q
    y = rs.ai_prompt({**base, "kind": "Y", "label": "Año 2027"}, {}, "no pasar del 10 % en ninguna empresa")
    assert '"lessons"' in y and '"next_year"' in y and "no pasar del 10 %" in y
    m = rs.ai_prompt({**base, "kind": "M", "label": "Septiembre 2026"})
    assert '"points"' in m and '"attribution"' not in m


def test_auto_ai_only_for_new_quarterly_and_annual(monkeypatch):
    called = []
    monkeypatch.setattr(rs, "ai_comment", lambda db, key: called.append(key))
    for r in ({"key": "Q2026-4", "kind": "Q", "reconstructed": False}, {"key": "Y2026", "kind": "Y", "reconstructed": False},
              {"key": "Q2026-3", "kind": "Q", "reconstructed": True}, {"key": "M2026-12", "kind": "M", "reconstructed": False}):
        rs.auto_ai(None, r)
    assert called == ["Q2026-4", "Y2026"]
