"""Previsión de dividendos a 12 meses y agenda de Mercado."""
import datetime
from types import SimpleNamespace

import pytest

from app.application.services import agenda_service, dividend_forecast as df

TODAY = datetime.date(2026, 10, 2)
D = datetime.date


def test_quarterly_payer_projects_last_year_calendar_with_latest_amount():
    history = {"2025-09-15": 0.51, "2025-12-01": 0.51, "2026-03-13": 0.53, "2026-06-15": 0.53, "2026-09-15": 0.53}
    rows = df.forecast_ticker(history, TODAY)
    assert [r["ex_date"] for r in rows] == [D(2026, 12, 1), D(2027, 3, 13), D(2027, 6, 15), D(2027, 9, 15)]
    assert all(r["amount"] == 0.53 and r["estimated"] for r in rows)


def test_semiannual_payer_keeps_each_payment_amount():
    history = {"2025-04-23": 7.5, "2025-12-01": 5.5, "2026-04-27": 7.5}
    rows = df.forecast_ticker(history, TODAY)
    assert [(r["ex_date"], r["amount"]) for r in rows] == [(D(2026, 12, 1), 5.5), (D(2027, 4, 27), 7.5)]


def test_announced_replaces_the_estimate_of_the_same_payment():
    history = {"2025-11-20": 0.83, "2026-02-19": 0.91, "2026-05-21": 0.91, "2026-08-20": 0.91}
    announced = {"ex_date": "2026-11-19", "pay_date": "2026-12-10", "amount": 0.91}
    rows = df.forecast_ticker(history, TODAY, announced)
    assert rows[0] == {"ex_date": D(2026, 11, 19), "pay_date": D(2026, 12, 10), "amount": 0.91, "estimated": False}
    assert len(rows) == 4 and all(r["estimated"] for r in rows[1:])


def test_payment_already_made_this_year_is_not_projected_again():
    # el año pasado fue el 10/10; este año ya se pagó el 28/09
    history = {"2025-10-10": 1.0, "2026-04-10": 1.0, "2026-09-28": 1.1}
    rows = df.forecast_ticker(history, TODAY)
    assert [r["ex_date"] for r in rows] == [D(2027, 4, 10), D(2027, 9, 28)]


def test_ignores_missing_amounts_and_companies_without_dividends():
    assert df.forecast_ticker({}, TODAY) == []
    assert df.forecast_ticker({"2026-04-06": float("nan")}, TODAY) == []


def test_net_after_withholding_and_spanish_tax():
    assert df.net_factor("Spain") == pytest.approx(0.81)
    assert df.net_factor("United States") == pytest.approx(0.81)       # 15 % fuera, 4 % aquí
    assert df.net_factor("United Kingdom") == pytest.approx(0.81)      # nada fuera, 19 % aquí
    assert df.net_factor("Switzerland") == pytest.approx(0.61)         # 35 % fuera, solo se deducen 15
    assert df.net_factor("Mexico") == pytest.approx(0.81)


def position(ticker, shares, fx=1.0, currency="USD", country="United States", cost=1000.0, value=2000.0, shares_at=None):
    return {"ticker": ticker, "shares": shares, "fx": fx, "currency": currency, "country": country, "cost_eur": cost,
            "value_eur": value, "shares_at": shares_at or (lambda d: shares)}


def test_portfolio_forecast_in_euros_by_month():
    histories = {"KO": {"2025-12-01": 0.5, "2026-03-13": 0.5, "2026-06-15": 0.5, "2026-09-15": 0.5}}
    out = df.build_forecast([position("KO", 100, fx=0.9)], histories, {}, TODAY)
    assert out["total_gross"] == pytest.approx(4 * 0.5 * 100 * 0.9)
    assert out["total_net"] == pytest.approx(out["total_gross"] * 0.81)
    assert out["yield_on_cost"] == pytest.approx(18.0)
    assert out["by_ticker"][0]["frequency"] == "Trimestral"
    dec = next(m for m in out["months"] if m["month"] == "2026-12")
    assert dec["estimated_eur"] == pytest.approx(45.0) and dec["announced_eur"] == 0
    assert len(out["months"]) == 12 and out["months"][0]["month"] == "2026-10"


def test_pence_quotes_are_converted_to_pounds():
    out = df.build_forecast([position("DGE.L", 100, fx=1.15, currency="GBp", country="United Kingdom")],
                            {"DGE.L": {"2026-04-10": 40.0}}, {}, TODAY)
    assert out["total_gross"] == pytest.approx(0.40 * 100 * 1.15)


def test_pending_payment_counts_only_if_held_on_ex_date():
    announced = {"X": {"ex_date": "2026-09-25", "pay_date": "2026-10-15", "amount": 1.0}}
    bought_after = position("X", 50, shares_at=lambda d: 0.0)
    assert df.build_forecast([bought_after], {}, announced, TODAY)["events"] == []
    held = position("X", 50, shares_at=lambda d: 30.0)
    ev = df.build_forecast([held], {}, announced, TODAY)["events"]
    assert len(ev) == 1 and ev[0]["shares"] == 30.0 and ev[0]["date"] == D(2026, 10, 15)


def test_position_without_fx_is_left_out():
    assert df.build_forecast([position("X", 10, fx=None)], {"X": {"2026-04-10": 1.0}}, {}, TODAY)["total_gross"] == 0


def test_shares_held_at_replays_buys_sells_and_splits():
    e = lambda date, kind, shares: SimpleNamespace(date=date, type=kind, shares=shares)
    ledger = [e("2025-01-10", "BUY", 10), e("2025-06-01", "SPLIT", 2), e("2026-01-05", "SELL", 5), e("2026-01-01", "DIVIDEND", 20)]
    assert df.shares_held_at(ledger, D(2025, 3, 1)) == 10
    assert df.shares_held_at(ledger, D(2025, 12, 31)) == 20
    assert df.shares_held_at(ledger, D(2026, 2, 1)) == 15


# --- Agenda ---

def test_agenda_upcoming_windows_and_order():
    results = [{"ticker": "MSFT", "date": "2026-10-07", "thesis_id": 3, "held": True},
               {"ticker": "KO", "date": "2026-10-25", "thesis_id": None, "held": True}]
    divs = [{"ticker": "ROL", "ex_date": D(2026, 10, 7), "pay_date": D(2026, 10, 20), "amount": 0.18, "currency": "USD",
             "gross_eur": 20.0, "net_eur": 16.2, "estimated": False}]
    a = agenda_service.build_agenda(TODAY, results, divs, {}, {}, [], [], [])
    assert [(i["ticker"], i["kind"]) for i in a["semana"]["upcoming"]] == [("MSFT", "results")]
    assert [(i["ticker"], i["kind"]) for i in a["mes"]["upcoming"]] == [("MSFT", "results"), ("KO", "results")]
    assert [(i["ticker"], i["kind"]) for i in a["semana"]["dividends"]] == [("ROL", "ex")]
    assert [(i["ticker"], i["kind"]) for i in a["mes"]["dividends"]] == [("ROL", "ex"), ("ROL", "pay")]
    assert a["semana"]["upcoming"][0]["days"] == 5


def test_agenda_past_summary_for_last_7_and_30_days():
    periods = {"7d": {"pct": -0.5, "abs_eur": -300.0, "benchmark_pct": -1.0}, "30d": {"pct": 0.5, "abs_eur": 270.0}}
    positions = {"NVDA": {"pnl_eur": {"7d": 220.0, "30d": 110.0}}, "OMAB": {"pnl_eur": {"7d": -275.0, "30d": -60.0}},
                 "KO": {"pnl_eur": {"7d": 0.0, "30d": 5.0}}}
    collected = [{"ticker": "KO", "date": "2026-09-29", "gross_eur": 12.0}, {"ticker": "MSFT", "date": "2026-09-20", "gross_eur": 9.0},
                 {"ticker": "GOOGL", "date": "2026-08-20", "gross_eur": 3.0}]
    txs = [{"ticker": "KO", "date": "2026-10-01", "type": "BUY", "shares": 10}, {"ticker": "META", "date": "2026-09-10", "type": "SELL", "shares": 2}]
    reported = [{"ticker": "NKE", "date": "2026-09-30", "surprise_pct": 12.0}, {"ticker": "FDX", "date": "2026-09-16", "surprise_pct": -3.0}]
    a = agenda_service.build_agenda(TODAY, [], [], periods, positions, collected, txs, reported)
    week, month = a["semana"]["past"], a["mes"]["past"]
    assert week["start"] == D(2026, 9, 25) and month["start"] == D(2026, 9, 2)
    assert week["pct"] == -0.5 and week["benchmark_pct"] == -1.0 and month["pct"] == 0.5
    assert [m["ticker"] for m in week["gainers"]] == ["NVDA"] and [m["ticker"] for m in week["losers"]] == ["OMAB"]
    assert week["dividends_eur"] == 12.0 and month["dividends_eur"] == 21.0
    assert [t["ticker"] for t in week["transactions"]] == ["KO"] and [t["ticker"] for t in month["transactions"]] == ["KO", "META"]
    assert [r["ticker"] for r in week["reported"]] == ["NKE"] and [r["ticker"] for r in month["reported"]] == ["NKE", "FDX"]


def test_repeated_buys_of_a_company_are_summed_with_average_price():
    txs = [{"ticker": "ROL", "date": "2026-09-15", "type": "BUY", "shares": 15, "price": 50.0, "currency": "USD"},
           {"ticker": "ROL", "date": "2026-09-24", "type": "BUY", "shares": 25, "price": 58.0, "currency": "USD"},
           {"ticker": "ROL", "date": "2026-09-20", "type": "SELL", "shares": 5, "price": 55.0, "currency": "USD"},
           {"ticker": "UBER", "date": "2026-09-25", "type": "BUY", "shares": 5, "price": 80.0, "currency": "USD"}]
    g = agenda_service.group_transactions(txs)
    assert [(x["ticker"], x["type"]) for x in g] == [("UBER", "BUY"), ("ROL", "BUY"), ("ROL", "SELL")]
    rol = g[1]
    assert rol["shares"] == 40 and rol["count"] == 2 and rol["avg_price"] == pytest.approx((15 * 50 + 25 * 58) / 40)
    assert (rol["first"], rol["last"]) == ("2026-09-15", "2026-09-24")


def test_rolling_periods_measure_from_the_close_n_days_ago_without_counting_buys():
    import pandas as pd
    from app.application.services import price_monitor_service as pm
    idx = pd.bdate_range("2026-08-03", "2026-10-02")
    df = pd.DataFrame({"X": [100.0 + i for i in range(len(idx))]}, index=idx)
    txs = [{"ticker": "X", "date": "2026-08-03", "type": "BUY", "shares": 10, "price": 100.0, "currency": "EUR", "fx": 1.0},
           {"ticker": "X", "date": "2026-09-30", "type": "BUY", "shares": 10, "price": 141.0, "currency": "EUR", "fx": 1.0}]
    out = pm.portfolio_changes(df, txs, {}, rolling_days=(7, 30))
    p7, p30 = out["periods"]["7d"], out["periods"]["30d"]
    # desde el cierre de hace 7 días naturales (25/09): las 10 primeras ganan desde ese cierre; las 10 compradas el
    # 30/09 a 141, solo desde la compra
    now = df["X"].iloc[-1]
    assert p7["abs_eur"] == pytest.approx(10 * (now - df.loc["2026-09-25", "X"]) + 10 * (now - 141))
    assert p30["abs_eur"] == pytest.approx(10 * (now - df.loc["2026-09-02", "X"]) + 10 * (now - 141))
    assert "1w" in out["periods"] and "1y" in out["periods"]   # los de Cartera siguen ahí


def test_agenda_dividends_are_only_confirmed_ones_inside_the_window():
    divs = [{"ticker": "ROL", "date": D(2026, 10, 10), "ex_date": D(2026, 10, 10), "pay_date": None, "gross_eur": 22.0, "estimated": True},
            {"ticker": "MSFT", "date": D(2026, 12, 10), "ex_date": D(2026, 11, 19), "pay_date": D(2026, 12, 10), "gross_eur": 13.0, "estimated": False},
            {"ticker": "KO", "date": D(2026, 10, 20), "ex_date": D(2026, 10, 6), "pay_date": D(2026, 10, 20), "gross_eur": 5.0, "estimated": False}]
    results = [{"ticker": "OMAB", "date": "2026-10-05", "thesis_id": None, "held": True}]
    a = agenda_service.build_agenda(TODAY, results, divs, {}, {}, [], [], [])
    assert [(i["ticker"], i["kind"]) for i in a["semana"]["dividends"]] == [("KO", "ex")]          # el cobro (20/10) cae fuera
    assert [(i["ticker"], i["kind"]) for i in a["mes"]["dividends"]] == [("KO", "ex"), ("KO", "pay")]   # ROL es estimado; MSFT, en noviembre
    assert [i["ticker"] for i in a["semana"]["upcoming"]] == ["OMAB"]                               # resultados aparte


def test_agenda_today_window_is_today_and_tomorrow_and_since_yesterday():
    results = [{"ticker": "MSFT", "date": "2026-10-03", "thesis_id": 3, "held": True},
               {"ticker": "KO", "date": "2026-10-04", "thesis_id": None, "held": True}]
    periods = {"1d": {"pct": 0.8, "abs_eur": 412.0, "benchmark_pct": 0.5}, "7d": {"pct": 1.0, "abs_eur": 500.0}}
    positions = {"NVDA": {"pnl_eur": {"1d": 300.0, "7d": 10.0}}, "OMAB": {"pnl_eur": {"1d": -40.0, "7d": 5.0}}}
    reported = [{"ticker": "NKE", "date": "2026-10-01", "surprise_pct": 12.0}, {"ticker": "FDX", "date": "2026-09-30", "surprise_pct": -3.0}]
    a = agenda_service.build_agenda(TODAY, results, [], periods, positions, [], [], reported)
    today = a["hoy"]
    assert [i["ticker"] for i in today["upcoming"]] == ["MSFT"]               # mañana sí; pasado mañana, no
    assert today["past"]["start"] == D(2026, 10, 1) and today["past"]["pct"] == 0.8
    assert [m["ticker"] for m in today["past"]["gainers"]] == ["NVDA"] and [m["ticker"] for m in today["past"]["losers"]] == ["OMAB"]
    assert [r["ticker"] for r in today["past"]["reported"]] == ["NKE"]         # publicó anoche (desde ayer)
    assert 1 not in agenda_service.ROLLING_DAYS   # «1d» es la sesión de hoy, no un periodo corrido que la pise


def test_watchlist_today_zone_near_and_strong_moves():
    items = [{"ticker": "ADBE", "drop": -4.0, "day_pct": 1.0, "closed": False},
             {"ticker": "CAT", "drop": 3.2, "day_pct": -3.5, "closed": False},
             {"ticker": "ASML", "drop": 12.0, "day_pct": 5.0, "closed": False},
             {"ticker": "KO", "drop": None, "day_pct": None, "closed": True},
             {"ticker": "V", "drop": -1.0, "day_pct": 0.2, "closed": False}]
    w = agenda_service.watchlist_today(items)
    assert [i["ticker"] for i in w["zone"]] == ["ADBE", "V"]      # la más metida en zona, primero
    assert [i["ticker"] for i in w["near"]] == ["CAT"]            # ASML está a un 12%: no está a punto
    assert [i["ticker"] for i in w["movers"]] == ["ASML", "CAT"]


def test_since_last_visit_marks_what_appeared_after_it():
    notices = [{"key": "a", "first_seen": "2026-10-02T09:00:00"}, {"key": "b", "first_seen": "2026-10-01T20:00:00"}]
    news = [{"id": 7, "found_at": "2026-10-02 08:30:00"}, {"id": 6, "found_at": "2026-10-01T10:00:00"}]
    reported = [{"ticker": "NKE", "date": "2026-10-02"}, {"ticker": "FDX", "date": "2026-09-30"}]
    v = agenda_service.since_last_visit("2026-10-01T21:15:00", notices, news, reported)
    assert v["notices"] == ["a"] and v["news"] == [7] and v["reported"] == ["NKE"]
    assert agenda_service.since_last_visit(None, notices, news, reported) is None


# --- Agenda del resto (empresas que sigues sin tenerlas) ---

def test_other_agenda_lists_upcoming_results_by_window():
    results = [{"ticker": "ADBE", "date": "2026-10-05", "thesis_id": None},
               {"ticker": "ASML.AS", "date": "2026-10-20", "thesis_id": 3},
               {"ticker": "CAT", "date": "2026-11-20", "thesis_id": None}]
    a = agenda_service.build_other_agenda(TODAY, results)
    assert [i["ticker"] for i in a["semana"]["upcoming"]] == ["ADBE"]
    assert [i["ticker"] for i in a["mes"]["upcoming"]] == ["ADBE", "ASML.AS"]
    assert a["mes"]["upcoming"][1]["thesis_id"] == 3 and a["mes"]["upcoming"][0]["days"] == 3


# --- Resumen del Centro en Mercado ---

def test_pulse_summarises_notices_results_dividend_and_30_days():
    notes = [{"read": False, "portfolio": True, "group": "monitor", "ticker": "META", "kind": "monitor", "text": "x", "href": "/mercado#centro", "date": "2026-10-02T09:00"},
             {"read": False, "portfolio": True, "group": "avisos", "ticker": "OMAB", "kind": "resultados", "text": "y", "href": "/tesis/1", "date": "2026-10-01T09:00"},
             {"read": False, "portfolio": False, "group": "avisos", "ticker": "ZZZ", "kind": "aviso", "text": "z", "href": "/", "date": "2026-10-02"},
             {"read": True, "portfolio": True, "group": "avisos", "ticker": "KO", "kind": "aviso", "text": "w", "href": "/", "date": "2026-10-02"}]
    results = [{"ticker": "OMAB", "date": "2026-10-22"}, {"ticker": "RMS.PA", "date": "2026-10-22"},
               {"ticker": "MSFT", "date": "2026-10-28"}, {"ticker": "KO", "date": "2026-12-01"}]
    divs = [{"ticker": "ROL", "date": D(2026, 11, 10), "gross_eur": 22.0, "estimated": True, "pay_date": None},
            {"ticker": "MSFT", "date": D(2026, 12, 10), "gross_eur": 13.0, "estimated": False, "pay_date": D(2026, 12, 10)}]
    p = agenda_service.pulse(TODAY, notes, results, divs, {"pct": 0.59, "abs_eur": 305.0, "benchmark_pct": 0.1})
    assert p["unread"] == 2 and p["top_notice"]["ticker"] == "OMAB"          # los avisos van antes que el monitor
    assert p["results"] == {"first_date": "2026-10-22", "days": 20, "first_tickers": ["OMAB", "RMS.PA"], "count": 3}
    assert p["dividend"]["ticker"] == "MSFT" and not p["dividend"]["estimated"]   # el confirmado antes que el estimado
    assert p["period"]["pct"] == 0.59


def test_pulse_falls_back_to_estimated_dividend_and_handles_nothing():
    divs = [{"ticker": "ROL", "date": D(2026, 11, 10), "gross_eur": 22.0, "estimated": True, "pay_date": None}]
    p = agenda_service.pulse(TODAY, [], [], divs, None)
    assert p["unread"] == 0 and p["top_notice"] is None and p["results"] is None and p["period"] is None
    assert p["dividend"]["estimated"] and p["dividend"]["kind"] == "ex"


# --- Históricos cortados de Yahoo (variación de +587 %) ---

def _hist():
    import pandas as pd
    idx = pd.bdate_range("2026-01-02", "2026-10-02")
    return idx, pd.Series([100.0 + 0.1 * i for i in range(len(idx))], index=idx)


def test_position_without_start_price_is_left_out_instead_of_counting_zero():
    import pandas as pd
    from app.application.services import price_monitor_service as pm
    idx, full = _hist()
    cut = full.copy()
    cut[cut.index < "2026-09-29"] = float("nan")          # Yahoo solo devolvió los últimos días
    df = pd.DataFrame({"A": full, "B": cut})
    txs = [{"ticker": t, "date": "2026-02-02", "type": "BUY", "shares": 10, "price": 100.0, "currency": "EUR", "fx": 1.0} for t in "AB"]
    out = pm.portfolio_changes(df, txs, {}, rolling_days=(30,))
    p30 = out["periods"]["30d"]
    assert p30["incomplete"] == ["B"]
    assert p30["abs_eur"] == pytest.approx(10 * (full.iloc[-1] - full.loc[:"2026-09-02"].iloc[-1]))
    assert abs(p30["pct"]) < 5
    assert "incomplete" not in out["periods"]["1d"]       # el día sí tiene precio de B


def test_truncated_bulk_download_is_completed_with_single_retry(monkeypatch, tmp_path):
    import pandas as pd
    from app.application.services import price_monitor_service as pm
    idx, full = _hist()
    bulk = pd.DataFrame({"A": full, "B": full.where(full.index >= "2026-09-29")})
    bulk.columns = pd.MultiIndex.from_product([["Close"], ["A", "B"]])
    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda *a, **k: bulk)
    monkeypatch.setattr(pm, "_single_history", lambda sym: full if sym == "B" else None)
    monkeypatch.setattr(pm, "BACKUP_FILE", str(tmp_path / "backup.pkl"))
    monkeypatch.setattr(pm, "_cache", {})
    df = pm.download_history(["A", "B"])
    assert df["B"].first_valid_index() == full.index[0]
    assert pm._load_backup()["B"].index[0] == full.index[0]


def test_backup_keeps_older_history_when_a_shorter_series_arrives(monkeypatch, tmp_path):
    from app.application.services import price_monitor_service as pm
    idx, full = _hist()
    monkeypatch.setattr(pm, "BACKUP_FILE", str(tmp_path / "backup.pkl"))
    pm._save_backup({"B": full})
    pm._save_backup({"B": full[full.index >= "2026-09-29"]})
    assert pm._load_backup()["B"].index[0] == full.index[0]


def test_upcoming_results_notice_only_for_held_within_7_days():
    results = [{"ticker": "MSFT", "date": "2026-10-09", "thesis_id": 4},     # 7 días
               {"ticker": "KO", "date": "2026-10-03", "thesis_id": None},    # mañana, sin tesis
               {"ticker": "OMAB", "date": "2026-10-22", "thesis_id": 2},     # demasiado lejos
               {"ticker": "AMZN", "date": "2026-10-05", "thesis_id": 9},     # no está en cartera
               {"ticker": "NKE", "date": "2026-09-30", "thesis_id": 1}]      # ya pasó
    out = agenda_service.upcoming_results_notices(results, {"MSFT", "KO", "OMAB", "NKE"}, TODAY)
    assert [(n["ticker"], n["text"], n["href"]) for n in out] == [
        ("MSFT", "Presenta resultados el 09/10 (en 7 d)", "/tesis/4"),
        ("KO", "Presenta resultados el 03/10 (mañana)", "/ticker/KO")]
    assert out[0]["type"] == "upcoming_results" and out[0]["ref"] == "2026-10-09"
