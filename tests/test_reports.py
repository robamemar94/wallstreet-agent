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


def test_weekly_report_waits_for_its_radar(monkeypatch):
    from app.application.services import thesis_news_runner
    from app.infrastructure.db.database import SessionLocal
    w = rs.period("W", datetime.date(2026, 10, 14))                      # semana del 12 al 16
    assert rs.radar_window(w) == ("2026-10-17T08:00:00", "2026-10-24T08:00:00")   # las del radar que la repasa
    monkeypatch.setattr(rs, "start_date", lambda db: datetime.date(2026, 10, 12))
    monday = datetime.datetime(2026, 10, 19, 9)
    db = SessionLocal()
    try:
        db.query(rs.DBSetting).filter(rs.DBSetting.key.like(rs.KEY_PREFIX + "W2026-10-%")).delete(synchronize_session=False)
        monkeypatch.setattr(thesis_news_runner, "radar_pending", lambda: True)
        assert "W2026-10-12" not in [p["key"] for p in rs.due(db, monday)]       # radar sin hacer: espera
        assert "W2026-10-12" in [p["key"] for p in rs.due(db, datetime.datetime(2026, 10, 24, 9))]   # semana pasada: no espera
        monkeypatch.setattr(thesis_news_runner, "radar_pending", lambda: False)
        assert "W2026-10-12" in [p["key"] for p in rs.due(db, monday)]
    finally:
        db.rollback()
        db.close()


def test_display_condenses_long_lists():
    decs = [{"ticker": "AAA" if i % 2 else "BBB", "type": "BUY", "date": f"2026-0{1 + i % 9}-10", "amount_eur": 100.0 * (i + 1),
             "note": "barata" if i == 3 else None} for i in range(10)] + [{"ticker": "CCC", "type": "SELL", "date": "2026-05-05", "amount_eur": 500.0, "note": None}]
    divs = [{"ticker": t, "date": "2026-03-01", "eur": e} for t, e in [("A", 1), ("B", 2), ("C", 3), ("D", 4), ("E", 5), ("F", 6), ("A", 7), ("B", 0.5), ("G", 1)]]
    theses = [{"type": "earnings", "title": "Resultados Q2"}, {"type": "verdict", "title": "Veredicto: INTACTA → VIGILANCIA"}]
    news = [{"title": str(i)} for i in range(8)]
    r = rs.display({"kind": "Q", "decisions": decs, "dividends": {"items": divs}, "theses": theses, "news": news, "watchlist": [{"ticker": "Z"}]})
    assert r["dec_totals"] == {"BUY": {"n": 10, "eur": 5500.0}, "SELL": {"n": 1, "eur": 500.0}}
    assert [(g["ticker"], g["n"]) for g in r["dec_groups"]][:2] == [("AAA", 5), ("BBB", 5)] and r["dec_groups"][0]["amount_eur"] == 3000.0
    assert r["dec_groups"][0]["notes"] == ["10/04: barata"]
    assert [d["ticker"] for d in r["div_top"]] == ["A", "F", "E", "D", "C"] and r["div_rest"] == 2
    assert [e["type"] for e in r["theses"]] == ["verdict"] and len(r["news"]) == 5 and r["watchlist"] == []
    y = rs.display({"kind": "Y", "decisions": decs[:2], "news": news, "insiders": [{"ticker": "Z"}], "theses_close": [{"ticker": "A"}], "theses": theses})
    assert y["news"] == [] and y["insiders"] == [] and y["theses"] == [] and len(y["dec_groups"]) == 2   # en el anual, siempre agrupadas
    short = rs.display({"kind": "M", "decisions": decs[:3], "dividends": {"items": divs[:2]}, "theses": theses, "news": news})
    assert "dec_groups" not in short and "div_top" not in short and len(short["theses"]) == 2 and len(short["news"]) == 8


def test_risk_cash_cost_and_bridge():
    port = [100 * (1.01 if i % 2 else 0.995) ** i for i in range(30)]
    bench = [100 + i * 0.1 for i in range(30)]
    nav = {"dates": [f"2026-03-{i + 1:02d}" for i in range(30)], "port": port, "bench": bench, "from": None,
           "cash_uv": [None] * 5 + port[5:], "cash_pct": [None] * 5 + [10.0] * 25}
    rk = rs.risk(nav)
    assert rk["vol"] > rk["bench_vol"] > 0 and rk["beta"] is not None
    assert rs.risk({**nav, "port": port[:10], "bench": bench[:10]}) is None   # pocas sesiones
    cc = rs.cash_cost(nav)
    assert cc["pp"] == pytest.approx(0) and cc["avg_pct"] == 10.0 and cc["from"] == "2026-03-06"
    assert rs.cash_cost({**nav, "cash_uv": [None] * 25 + port[25:]}) is None
    b = rs.bridge({"performance": {"value_start": 1000, "value_end": 1600, "pnl_eur": 100, "fx_eur": -20}, "flows": {"net": 500}})
    assert [(x["label"], x["eur"]) for x in b["steps"]] == [("Al empezar", 1000), ("Aportaciones", 500), ("Empresas", 120), ("Divisa", -20)]
    assert b["steps"][-1]["lo"] == 1600 and b["steps"][-1]["hi"] == 1620 and b["end_pct"] == pytest.approx(100 * 1600 / 1620)


def test_gains_and_inaction():
    idx = pd.date_range("2026-01-01", "2026-03-31", freq="B")
    df = pd.DataFrame({"AAA": [10.0 if d < pd.Timestamp("2026-02-01") else 20.0 for d in idx]}, index=idx)
    txs = [{"ticker": "AAA", "date": "2026-01-05", "type": "BUY", "shares": 10, "price": 10.0, "currency": "EUR"},
           {"ticker": "AAA", "date": "2026-02-10", "type": "SELL", "shares": 4, "price": 20.0, "currency": "EUR"}]
    g = rs.gains(df, txs, {}, "2026-02-01", "2026-02-28")
    assert g["realized_eur"] == pytest.approx(40) and g["n_sales"] == 1          # 4 × (20 − 10)
    assert g["latent_eur"] == pytest.approx(60) and g["latent_pct"] == pytest.approx(100)
    ina = rs.inaction(df, txs, {}, datetime.date(2026, 2, 1), datetime.date(2026, 2, 28))
    assert ina["pct"] == pytest.approx(100)                                      # sin vender: las 10 acciones doblan
    assert rs.sectors({"AAA": 50.0, "BBB": -10.0}, {"AAA": {"sector": "Technology"}})[0] == {
        "sector": "Tecnología", "pnl_eur": 50.0, "tickers": [{"ticker": "AAA", "pnl_eur": 50.0}]}
