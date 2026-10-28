import datetime

import pandas as pd

from app.application.services import financials_update_service as fu


def _yahoo(cols, rev, ni, eps, fcf, extra_cash=None):
    c = [pd.Timestamp(x) for x in cols]   # Yahoo: del más reciente al más antiguo
    inc = pd.DataFrame({k: v for k, v in zip(c, zip(rev, ni, eps))}, index=["Total Revenue", "Net Income", "Diluted EPS"])
    cash = pd.DataFrame({k: [v] for k, v in zip(c, fcf)}, index=["Free Cash Flow"])
    return {"income": inc, "balance": pd.DataFrame(), "cash": cash}


def _struct(years, rev, ni, eps, fcf):
    return {"years": years,
            "income_statement": {"Total Revenues": rev, "Net Income Attributable to Common Shareholders": ni, "Diluted EPS": eps,
                                 "Something Else": [1] * len(years)},
            "balance_sheet": {}, "cash_flow": {"Free Cash Flow": fcf},
            "chart_metrics": {"revenue": rev, "net_income": ni, "eps": eps, "fcf": fcf}}


def test_adds_the_new_year_only_with_lines_that_match():
    st = _struct(["2023", "2024", "LTM"], [100.0, 110.0, 115.0], [10.0, 12.0, 12.5], [1.0, 1.2, 1.25], [9.0, 11.0, 11.5])
    yh = _yahoo(["2025-12-31", "2024-12-31", "2023-12-31"], [130, 110.2, 100], [15, 12.1, 10], [1.5, 1.21, 1.0], [14, 13.0, 9])
    p = fu.plan(st, yh)
    assert (p["status"], p["new"], p["checked_on"]) == ("add", ["2025"], "2024")
    assert "Free Cash Flow" in p["skipped"] and "fcf" in p["skipped"]   # 13 frente a 11: no cuadra, no se añade
    new = fu.apply(st, p)
    assert new["years"] == ["2023", "2024", "2025"]                       # sin el LTM
    assert new["income_statement"]["Total Revenues"] == [100.0, 110.0, 130]
    assert new["income_statement"]["Diluted EPS"][-1] == 1.5
    assert new["cash_flow"]["Free Cash Flow"] == [9.0, 11.0, None] and new["chart_metrics"]["fcf"][-1] is None
    assert new["income_statement"]["Something Else"] == [1, 1, None]


def test_nothing_new_or_nothing_matches():
    st = _struct(["2024", "2025"], [110.0, 130.0], [12.0, 15.0], [1.2, 1.5], [11.0, 14.0])
    assert fu.plan(st, _yahoo(["2025-12-31", "2024-12-31"], [130, 110], [15, 12], [1.5, 1.2], [14, 11]))["status"] == "up_to_date"
    other = _yahoo(["2026-12-31", "2025-12-31"], [500, 400], [15, 12], [1.5, 1.2], [14, 11])   # otras ventas: no se toca
    assert fu.plan(st, other)["status"] == "mismatch"
    old = _yahoo(["2025-12-31", "2024-12-31"], [500, 400], [15, 12], [1.5, 1.2], [14, 11])     # nada más reciente: sin aviso
    assert fu.plan(st, old)["status"] == "up_to_date"


def test_month_labels_keep_their_format():
    st = _struct(["Jan '25", "Jan '26"], [100.0, 120.0], [10.0, 12.0], [1.0, 1.2], [9.0, 11.0])
    yh = _yahoo(["2027-01-31", "2026-01-31"], [140, 120], [14, 12], [1.4, 1.2], [13, 11])
    assert fu.plan(st, yh)["new"] == ["Jan '27"]


def test_repasting_keeps_the_old_years():
    old = _struct(["2014", "2015", "2016", "LTM"], [80.0, 90.0, 100.0, 105.0], [8.0, 9.0, 10.0, 11.0], [0.8, 0.9, 1.0, 1.1], [7.0, 8.0, 9.0, 9.5])
    old["income_statement"]["Old Only"] = [5, 6, 7, 8]
    new = _struct(["2015", "2016", "2017"], [90.5, 100.0, 120.0], [9.0, 10.0, 13.0], [0.9, 1.0, 1.3], [8.0, 9.0, 12.0])
    m = fu.merge(old, new)
    assert m["years"] == ["2014", "2015", "2016", "2017"]                 # el LTM viejo fuera; 2014 se conserva
    assert m["income_statement"]["Total Revenues"] == [80.0, 90.5, 100.0, 120.0]   # en los años que trae, manda lo nuevo
    assert m["income_statement"]["Old Only"] == [5, None, None, None]
    assert m["chart_metrics"]["revenue"][0] == 80.0
    assert fu.merge(None, new) is new


def test_run_notifies_once(monkeypatch):
    from app.infrastructure.db.database import SessionLocal
    from app.infrastructure.db.models import DBAsset, DBSetting
    db = SessionLocal()
    db.query(DBAsset).filter(DBAsset.ticker.in_(("ZZFIN", "ZZBAD"))).delete()
    db.query(DBSetting).filter(DBSetting.key == fu.SETTINGS_KEY).delete()
    db.add(DBAsset(ticker="ZZFIN", is_favorite=True, data={fu.STRUCT_KEY: _struct(["2024"], [110.0], [12.0], [1.2], [11.0])}))
    db.add(DBAsset(ticker="ZZBAD", is_favorite=True, data={fu.STRUCT_KEY: _struct(["2024"], [110.0], [12.0], [1.2], [11.0])}))
    db.commit()
    monkeypatch.setattr(fu, "followed", lambda db: ["ZZBAD", "ZZFIN"])
    good = _yahoo(["2025-12-31", "2024-12-31"], [130, 110], [15, 12], [1.5, 1.2], [14, 11])
    bad = _yahoo(["2025-12-31", "2024-12-31"], [999, 888], [15, 12], [1.5, 1.2], [14, 11])
    fetch = lambda t: good if t == "ZZFIN" else bad
    now = datetime.datetime(2026, 3, 2, 9)
    r = fu.run(db, now, fetch, pause_s=0)
    assert [e["ticker"] for e in r["added"]] == ["ZZFIN"] and [e["ticker"] for e in r["mismatched"]] == ["ZZBAD"]
    db.expire_all()
    assert db.get(DBAsset, "ZZFIN").data[fu.STRUCT_KEY]["years"] == ["2024", "2025"]
    assert db.get(DBAsset, "ZZFIN").data[fu.CHART_KEY]["years"] == ["2024", "2025"]
    n = fu.notices(db, now)
    assert len(n) == 1 and "ZZFIN (2025)" in n[0]["text"] and "ZZBAD" in n[0]["text"]
    assert fu.run(db, now + datetime.timedelta(days=1), fetch, pause_s=0) == {"added": [], "mismatched": []}   # ya mirado esta semana
    assert fu.run(db, now + datetime.timedelta(days=8), fetch, pause_s=0)["mismatched"] == []                 # el mismo caso, una vez
    db.query(DBAsset).filter(DBAsset.ticker.in_(("ZZFIN", "ZZBAD"))).delete()
    db.query(DBSetting).filter(DBSetting.key == fu.SETTINGS_KEY).delete()
    db.commit()


def test_refill_lines_that_now_match():
    st = _struct(["2024", "2025"], [110.0, 130.0], [12.0, 15.0], [1.2, 1.5], [11.0, None])
    st["updated_from_yahoo"] = {"years": ["2025"], "skipped": ["Free Cash Flow", "fcf"], "at": "2026-03-02"}
    st["cash_flow"]["Free Cash Flow"] = [11.0, None]
    yh = _yahoo(["2025-12-31", "2024-12-31"], [130, 110], [15, 12], [1.5, 1.2], [14, 11.1])   # ahora el FCF de 2024 cuadra
    new = fu.refill(st, yh)
    assert new["cash_flow"]["Free Cash Flow"] == [11.0, 14]
    assert new["updated_from_yahoo"]["skipped"] == ["fcf"]
    assert fu.refill(new, _yahoo(["2025-12-31", "2024-12-31"], [130, 110], [15, 12], [1.5, 1.2], [14, 99])) is None
