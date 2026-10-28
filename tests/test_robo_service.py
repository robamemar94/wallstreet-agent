import datetime

import pandas as pd

from app.application.services import robo_service as rs

D = datetime.date


def _data(weights=(0.6, 0.4), plan=None, snap_date="2026-10-01"):
    funds = [{"isin": "A", "name": "A", "yahoo": "AA", "currency": "EUR", "weight": weights[0]},
             {"isin": "B", "name": "B", "yahoo": "BB", "currency": "EUR", "weight": weights[1]}]
    return {"funds": funds, "plan": plan or [{"since": "2024-01", "amount": 900, "day": 3}],
            "snapshots": [{"date": snap_date, "value": 10000.0, "invested": 8000.0, "cash": None, "est": None}]}


def _navs(a_end, b_end):
    idx = pd.to_datetime(["2026-09-30", "2026-10-02", "2026-10-21"])
    return {"AA": pd.Series([10.0, 10.0, 10.0 * a_end], index=idx), "BB": pd.Series([20.0, 20.0, 20.0 * b_end], index=idx)}


def test_plan_history_and_contributions():
    plan = [{"since": "2024-01", "amount": 900, "day": 3}, {"since": "2027-01", "amount": 1200, "day": 5}]
    assert rs.plan_on(plan, "2026-12")["amount"] == 900 and rs.plan_on(plan, "2027-02")["amount"] == 1200
    got = rs.contributions(plan, D(2026, 11, 3), D(2027, 2, 10))
    assert got == [{"date": "2026-12-03", "amount": 900}, {"date": "2027-01-05", "amount": 1200}, {"date": "2027-02-05", "amount": 1200}]


def test_estimate_follows_funds_and_adds_contributions():
    # A +10 %, B −5 %: la cesta 0,6·1,10 + 0,4·0,95 = 1,04; la aportación del 3/10 también crece desde el 2/10
    e = rs.estimate(_data(), D(2026, 10, 22), navs=_navs(1.10, 0.95))
    assert round(e["value"], 2) == round(10000 * 1.04 + 900 * 1.04, 2)
    assert e["invested"] == 8900 and e["estimated"] and e["complete"]


def test_estimate_without_prices_keeps_value_plus_contributions():
    e = rs.estimate(_data(), D(2026, 10, 22), navs={})
    assert e["value"] == 10900 and not e["complete"]


def test_cash_part_does_not_move():
    e = rs.estimate(_data(weights=(0.5, 0.3)), D(2026, 10, 21), navs=_navs(1.10, 1.0))
    # 20 % en efectivo: 0,5·1,10 + 0,3·1 + 0,2 = 1,05; la aportación del 3/10 crece igual
    assert round(e["value"] - 900 * 1.05, 2) == round(10000 * 1.05, 2)


def test_adjust_due_from_day_10_until_new_snapshot():
    data = _data(snap_date="2026-10-25")
    assert not rs.adjust_due(data, D(2026, 11, 9))
    assert rs.adjust_due(data, D(2026, 11, 10))
    data["snapshots"].append({"date": "2026-11-12", "value": 1, "invested": 1})
    assert not rs.adjust_due(data, D(2026, 11, 20))


def test_parse_summary():
    text = ("Valor de mercado: 26.177,56 €   Capital invertido: 21.751,30 €   Efectivo: 160,31 €  Total 2026: +2.966,82 € (+15,59 %) "
            "Ishares Us Equity Index Fund:Valor: 10.405,53 €  Fidelity MSCI Japan Index Fund:Valor: 3.080,56 € "
            "Vanguard US Equity Index Fund:Valor: 1.059,17 €   Fidelity MSCI Pacific Ex-Japan Index Fund:Valor: 717,96 €")
    p = rs.parse_summary(text)
    assert (p["value"], p["invested"], p["cash"]) == (26177.56, 21751.30, 160.31)
    assert p["returns"] == {"2026": 15.59}
    assert p["funds"] == {"IE000N4ZYX28": 10405.53, "IE00BYX5N771": 3080.56, "IE00B1G3DH73": 1059.17, "IE00BDZVHT63": 717.96}


def test_recorded_contributions_then_plan():
    data = _data(snap_date="2026-10-25")
    data["contributions"] = [{"date": "2026-09-03", "amount": 900, "source": "real"}, {"date": "2026-10-03", "amount": 900, "source": "real"}]
    got = rs.all_contributions(data, D(2026, 12, 10))
    assert [(c["date"], c.get("source")) for c in got] == [("2026-09-03", "real"), ("2026-10-03", "real"), ("2026-11-03", "plan"), ("2026-12-03", "plan")]


def test_series_and_year_table():
    data = _data(snap_date="2026-10-25", plan=[{"since": "2026-01", "amount": 0, "day": 3}])
    data["snapshots"][0].update(value=2050.0, invested=2000.0)
    data["contributions"] = [{"date": "2026-08-01", "amount": 1000, "source": "estimado"}, {"date": "2026-09-03", "amount": 1000, "source": "real"}]
    data["monthly"] = {"2026-08": {"gain": 20.0, "pct": 2.0}, "2026-09": {"gain": 40.0, "pct": 2.0}}
    data["annual"] = {"2026": {"gain": 60.0, "pct": 4.04, "index": 5.0}}
    est = rs.estimate(data, D(2026, 10, 25), navs={})
    rows = rs.series(data, est, D(2026, 10, 25))
    assert [r["month"] for r in rows] == ["2026-08", "2026-09", "2026-10"]
    assert rows[1]["value"] == 2060.0 and rows[0]["source"] == "estimado"
    assert round(rows[2]["gain"], 2) == -10.0 and not rows[2]["from_table"]   # octubre: valor de hoy − fin de septiembre
    year = rs.year_table(data, rows)["years"][0]
    assert year["live"] and year["gain"] == 50.0 and year["index"] == 5.0


def test_compare_calibrates_years_and_starts_portfolio_late(monkeypatch):
    rows = [{"month": "2025-11", "pct": 1.0, "from_table": True}, {"month": "2025-12", "pct": 1.0, "from_table": True},
            {"month": "2026-01", "pct": 2.0, "from_table": False}]
    monkeypatch.setattr(rs, "_index_cache", {"at": 9e18, "closes": {"2025-10": 100.0, "2025-12": 102.0}})
    c = rs.compare(rows, {"2025": {"pct": 3.0}}, ["2025-12-15", "2026-01-20"], [10.0, 11.0])
    assert c["months"] == ["2025-10", "2025-11", "2025-12", "2026-01"]
    assert round(c["robo"][2], 2) == 103.0                      # nov+dic calibrados al +3 % del año
    assert round(c["robo"][3], 2) == round(103.0 * 1.02, 2)     # el mes fuera de la tabla, tal cual
    assert c["index"] == [100.0, None, 102.0, None]
    assert c["port"] == [None, None, 10.0, 11.0]


def test_zones_add_up(monkeypatch):
    data = _data()
    data["funds"] = [dict(f, weight=w) for f, w in zip(rs.FUNDS, [0.4, 0.2, 0.13, 0.12, 0.08, 0.04, 0.03])]
    monkeypatch.setattr(rs, "_nav_cache", {"at": 9e18, "since": D(2000, 1, 1), "series": {"x": pd.Series(dtype=float)}})
    z = {a["zone"]: a["pct"] for a in rs.fund_details(data, 1000.0, D(2026, 10, 25))["zones"]}
    assert round(sum(z.values()), 0) == 100 and z["EE. UU."] > z["Europa"]


def test_periods_band(monkeypatch):
    data = _data(weights=(0.5, 0.5))
    idx = pd.to_datetime(["2026-10-21", "2026-10-22"])
    monkeypatch.setattr(rs, "_nav_cache", {"at": 9e18, "since": D(2000, 1, 1),
                                           "series": {"AA": pd.Series([10.0, 10.2], index=idx), "BB": pd.Series([20.0, 20.0], index=idx)}})
    rows = [{"month": f"2026-{m:02d}", "gain": 10.0, "pct": 1.0} for m in range(1, 11)]
    b = {x["key"]: x for x in rs.periods(data, rows, 1010.0, D(2026, 10, 25))}
    assert round(b["1d"]["pct"], 2) == 1.0 and round(b["1d"]["gain"], 2) == 10.0     # 0,5 × 2 % sobre lo de ayer
    assert b["1m"]["gain"] == 10.0 and b["3m"]["gain"] == 30.0 and b["ytd"]["gain"] == 100.0
    assert round(b["ytd"]["pct"], 2) == round(100 * (1.01 ** 10 - 1), 2)


def test_periods_last_day_same_date_and_stale(monkeypatch):
    """El último día usa el día que tienen todos los fondos y avisa si no es la última sesión."""
    data = _data(weights=(0.5, 0.5))
    a = pd.Series([10.0, 10.2, 10.5], index=pd.to_datetime(["2026-10-22", "2026-10-23", "2026-10-26"]))
    b = pd.Series([20.0, 20.4], index=pd.to_datetime(["2026-10-22", "2026-10-23"]))
    monkeypatch.setattr(rs, "_nav_cache", {"at": 9e18, "since": D(2000, 1, 1), "series": {"AA": a, "BB": b}})
    d = {x["key"]: x for x in rs.periods(data, [], 1000.0, D(2026, 10, 27))}["1d"]
    assert round(d["pct"], 2) == 2.0                        # los dos, del 22 al 23 (no el lunes de AA)
    assert d["label"] == "vie 23/10" and d["stale"]         # el martes debería estar el del lunes 26
    assert "26/10" in d["note"] and "por detrás" in d["note"]
    fresh = {x["key"]: x for x in rs.periods(data, [], 1000.0, D(2026, 10, 26))}["1d"]
    assert not fresh["stale"]                               # el lunes, el del viernes es el último
