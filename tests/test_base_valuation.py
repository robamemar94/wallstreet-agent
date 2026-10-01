from datetime import date, datetime, timezone

import pytest

from app.application.services import base_valuation_service as bv


def _val(**kw):
    return {"metric": "eps", "growth": 0.10, "exit_multiple": 20.0, "dps": 0.0, "div_growth": 0.0, "years": 5, **kw}


def test_without_dividends_cagr_is_price_cagr():
    # BPA 5 → 5·1,1⁵ = 8,05 → ×20 = 161,05 en 5 años; desde 100: CAGR = 1,6105^(1/5) − 1 = 10 %
    out = bv.evaluate(_val(), 100.0, 5.0)
    assert out["price_n"] == pytest.approx(161.05, abs=0.01)
    assert out["cagr"] == pytest.approx(10.0, abs=0.01)
    assert out["buy_price"] == pytest.approx(161.051 / 1.18 ** 5, abs=0.01)
    assert out["zone"] == "justo"


def test_buy_price_gives_exactly_the_target_return():
    v = _val(dps=2.0, div_growth=0.05)
    buy = bv.evaluate(v, 100.0, 5.0)["buy_price"]
    assert bv.evaluate(v, buy, 5.0)["cagr"] == pytest.approx(bv.TARGET_RETURN_PCT, abs=0.01)


def test_dividends_raise_the_return():
    assert bv.evaluate(_val(dps=3.0), 100.0, 5.0)["cagr"] > bv.evaluate(_val(), 100.0, 5.0)["cagr"]


def test_margin_and_zone_below_buy_price():
    out = bv.evaluate(_val(), 65.0, 5.0)   # precio de compra ≈ 70,40
    assert out["margin_pct"] > 0 and out["zone"] == "ganga"


@pytest.mark.parametrize("cagr, zone", [(18, "ganga"), (17.9, "barata"), (13, "barata"), (12, "justo"), (8, "justo"), (7, "cara"), (None, None)])
def test_zones(cagr, zone):
    assert bv.zone(cagr) == zone


def test_invalid_inputs_return_none():
    assert bv.evaluate(_val(), 100.0, -1.0) is None
    assert bv.evaluate(_val(), 0, 5.0) is None
    assert bv.evaluate(_val(exit_multiple=0), 100.0, 5.0) is None


def test_legacy_projection_uses_base_scenario():
    legacy = {"initial_eps": 4.0, "dividend_yield": 0.02, "is_manual": True,
              "bear": {"eps_growth": 0.0, "exit_pe": 10}, "base": {"eps_growth": 0.08, "exit_pe": 25, "div_growth": 0.04}}
    v = bv.stored({"projection_scenarios": legacy})
    assert v["metric"] == "eps" and v["metric_value"] is None   # el BPA fijo antiguo se sustituye por el TTM
    assert v["growth"] == 0.08 and v["exit_multiple"] == 25
    assert v["dps"] is None   # guardaba rentabilidad, no importe: se toma el dividendo actual
    assert v["legacy"] is True
    assert bv.stored({"base_valuation": {"metric": "fcf"}, "projection_scenarios": legacy})["metric"] == "fcf"


def test_live_uses_ttm_metric_when_not_fixed(monkeypatch):
    monkeypatch.setattr(bv, "ttm_metric", lambda t, m: 5.0 if m == "fcf" else None)
    monkeypatch.setattr(bv, "ttm_end_date", lambda t: None)
    monkeypatch.setattr(bv, "analyst_eps_estimates", lambda t: None)
    out = bv.live("X", {"base_valuation": _val(metric="fcf", metric_value=None)}, 100.0)
    assert out["auto_metric"] and out["metric0"] == 5.0
    assert bv.live("X", {"base_valuation": _val(metric="eps", metric_value=None)}, 100.0) is None
    assert bv.live("X", {}, 100.0) is None


def test_live_takes_current_dividend_when_not_stored(monkeypatch):
    monkeypatch.setattr(bv, "_market_info", lambda t: {"trailingEps": 5.0, "dividendRate": 2.0})
    out = bv.live("X", {"base_valuation": _val(metric_value=5.0, dps=None)}, 100.0)
    assert out["inputs"]["dps"] == 2.0 and out["dividends"] == pytest.approx(10.0)


def test_london_per_share_figures_are_converted_to_pence(monkeypatch):
    from app.application.services.thesis_service import quote_unit_factor
    assert quote_unit_factor("GBp") == 100 and quote_unit_factor("USD") == 1 and quote_unit_factor(None) == 1

    class FakeTicker:
        def __init__(self, t): self.info = {"currency": "GBp", "trailingEps": 0.98, "dividendRate": 0.25}
    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", FakeTicker)
    monkeypatch.setattr(bv.market_cache, "cached", lambda kind, t, fetch, ttl_hours=12: fetch())
    info = bv._market_info("HLMA.L")
    assert info["trailingEps"] == pytest.approx(98.0) and info["dividendRate"] == pytest.approx(25.0)
    # con el precio en peniques, el PER sale razonable (~36x) y no 3.600x
    assert bv.evaluate({**_val(), "exit_multiple": 30}, 3544.0, info["trailingEps"])["multiple_now"] == pytest.approx(36.2, abs=0.1)


# --- Horizonte fijo y corrección de un año concreto ---

def test_horizon_is_always_five_years():
    assert bv.evaluate(_val(years=10), 100.0, 5.0, year0=2026)["years"] == 5
    assert bv.normalize({"exit_multiple": 20, "years": 7})["years"] == 5


def test_anchor_replaces_the_path_from_its_year():
    # TTM 5 en 2026; BPA 2027 corregido a 8 → 2031 = 8·1,1⁴
    v = _val(anchor={"year": 2027, "value": 8.0, "metric": "eps"})
    out = bv.evaluate(v, 100.0, 5.0, year0=2026)
    assert out["metric_n"] == pytest.approx(8.0 * 1.1 ** 4, abs=1e-3)
    assert out["year_n"] == 2031
    a = out["anchor"]
    assert a["state"] == "active" and a["auto"] == pytest.approx(5.5) and a["diff_pct"] == pytest.approx(45.5, abs=0.1)
    assert a["cagr_auto"] == bv.evaluate(_val(), 100.0, 5.0, year0=2026)["cagr"]
    assert out["cagr"] > a["cagr_auto"]


def test_anchor_reprojects_with_growth_when_the_year_rolls():
    v = _val(anchor={"year": 2027, "value": 8.0, "metric": "eps"})
    # en 2027 el horizonte llega a 2032: la corrección sigue y se proyecta un año más con g
    assert bv.evaluate(v, 100.0, 5.0, year0=2027)["metric_n"] == pytest.approx(8.0 * 1.1 ** 5, abs=1e-3)


def test_anchor_expires_once_its_year_has_passed():
    v = _val(anchor={"year": 2027, "value": 8.0, "metric": "eps"})
    out = bv.evaluate(v, 100.0, 6.0, year0=2028)
    assert out["metric_n"] == pytest.approx(6.0 * 1.1 ** 5, abs=1e-3)   # vuelve al TTM
    assert out["anchor"]["state"] == "expired" and out["anchor"]["auto"] == 6.0 and "cagr_auto" not in out["anchor"]


def test_anchor_of_another_metric_is_ignored():
    v = _val(metric="fcf", anchor={"year": 2027, "value": 8.0, "metric": "eps"})
    out = bv.evaluate(v, 100.0, 5.0, year0=2026)
    assert out["anchor"] is None and out["metric_n"] == pytest.approx(5.0 * 1.1 ** 5, abs=1e-3)


def test_normalize_keeps_valid_anchor_only():
    a = bv.normalize({"metric": "eps", "anchor": {"year": "2027", "value": "1.2"}})["anchor"]
    assert a["year"] == 2027 and a["value"] == 1.2 and a["metric"] == "eps" and a["set_on"]
    assert bv.normalize({"anchor": {"year": 2027, "value": 0}})["anchor"] is None
    assert bv.normalize({"anchor": None})["anchor"] is None


# --- ¿Toca revisar la valoración? ---

def test_stale_after_two_results_since_review():
    v = {"last_updated": "2026-02-10"}
    st = bv.staleness(v, ["2026-07-29", "2026-04-30", "2026-01-28"], today=date(2026, 9, 30))
    assert st["stale"] and st["results_since"] == ["2026-04-30", "2026-07-29"]


def test_one_result_since_review_is_not_stale_yet():
    st = bv.staleness({"last_updated": "2026-05-15"}, ["2026-07-29", "2026-04-30"], today=date(2026, 9, 30))
    assert not st["stale"] and st["results_since"] == ["2026-07-29"]


def test_reviewed_without_changes_restarts_the_count():
    v = {"last_updated": "2026-02-10", "reviewed_at": "2026-08-01"}
    st = bv.staleness(v, ["2026-07-29", "2026-04-30"], today=date(2026, 9, 30))
    assert st["reviewed"] == "2026-08-01" and not st["stale"] and st["results_since"] == []


def test_results_on_review_day_count_as_covered():
    st = bv.staleness({"last_updated": "2026-07-29"}, ["2026-07-29", "2026-04-30"], today=date(2026, 9, 30))
    assert st["results_since"] == []


def test_without_calendar_falls_back_to_seven_months():
    v = {"last_updated": "2026-02-28"}
    assert bv.staleness(v, [], today=date(2026, 9, 28))["stale"] is False    # 212 días
    assert bv.staleness(v, [], today=date(2026, 9, 30))["stale"] is True     # 214 días


def test_legacy_or_missing_valuation_has_no_staleness():
    assert bv.staleness(None, ["2026-07-29"]) is None
    assert bv.staleness({"last_updated": "2025-01-01", "legacy": True}, ["2026-07-29"]) is None


# --- El TTM va con su fecha: el año en curso suma lo que le falta ---

def test_ttm_offset_is_the_time_left_until_year_end():
    assert bv.ttm_offset(date(2026, 6, 30), 2026) == pytest.approx(0.504, abs=1e-3)
    assert bv.ttm_offset(date(2026, 12, 31), 2026) == 0.0
    assert bv.ttm_offset(None, 2026) == 0.0
    assert bv.ttm_offset(date(2026, 9, 30), 2027) == pytest.approx(1.25, abs=1e-2)   # enero: aún sin el 4T publicado


def test_projection_starts_at_the_ttm_date():
    # TTM 5 a 30/06/2026, g 10 %: 2026 = 5·1,1^0,5 y 2031 = 5·1,1^5,5
    out = bv.evaluate(_val(), 100.0, 5.0, year0=2026, ttm_end=date(2026, 6, 30))
    off = bv.ttm_offset(date(2026, 6, 30), 2026)
    assert out["metric_year0"] == pytest.approx(5.0 * 1.1 ** off, abs=1e-3)
    assert out["metric_n"] == pytest.approx(5.0 * 1.1 ** (5 + off), abs=1e-3)
    assert out["cagr"] > bv.evaluate(_val(), 100.0, 5.0, year0=2026)["cagr"]   # antes se perdía medio año
    assert out["multiple_now"] == 20.0   # el PER actual sigue siendo sobre el TTM


def test_anchor_stays_until_the_ttm_covers_its_year():
    v = _val(anchor={"year": 2026, "value": 6.0, "metric": "eps"})
    # enero de 2027 con el TTM aún a septiembre: la corrección de 2026 sigue valiendo
    out = bv.evaluate(v, 100.0, 5.0, year0=2027, ttm_end=date(2026, 9, 30))
    assert out["anchor"]["state"] == "active" and out["metric_n"] == pytest.approx(6.0 * 1.1 ** 6, abs=1e-3)
    assert out["anchor"]["auto"] == pytest.approx(5.0 * 1.1 ** 0.25, abs=1e-2)
    # publicado el 4T (TTM a 31/12/2026): caduca y vuelve al TTM
    assert bv.evaluate(v, 100.0, 5.0, year0=2027, ttm_end=date(2026, 12, 31))["anchor"]["state"] == "expired"


def test_anchor_on_the_current_year_replaces_the_ttm_path():
    v = _val(anchor={"year": 2026, "value": 6.0, "metric": "eps"})
    out = bv.evaluate(v, 100.0, 5.0, year0=2026, ttm_end=date(2026, 6, 30))
    assert out["metric_year0"] == 6.0 and out["metric_n"] == pytest.approx(6.0 * 1.1 ** 5, abs=1e-3)


def test_live_dates_the_ttm_only_when_automatic(monkeypatch):
    monkeypatch.setattr(bv, "ttm_metric", lambda t, m: 5.0)
    monkeypatch.setattr(bv, "ttm_end_date", lambda t: date(date.today().year, 6, 30))
    monkeypatch.setattr(bv, "analyst_eps_estimates", lambda t: None)
    auto = bv.live("X", {"base_valuation": _val(metric_value=None)}, 100.0)
    fixed = bv.live("X", {"base_valuation": _val(metric_value=5.0)}, 100.0)
    assert auto["ttm_end"] and fixed["ttm_end"] is None
    assert auto["metric_n"] > fixed["metric_n"]


class _Estimates:
    """earnings_estimate de yfinance (avg, yearAgoEps, numberOfAnalysts por periodo)."""
    def __init__(self, rows):
        import pandas as pd
        self.df = pd.DataFrame(rows, index=["0y", "+1y"][:len(rows)])


def _fake_yf(monkeypatch, info, est_rows, gaap_eps=None, fx=None):
    import pandas as pd
    import yfinance

    class FakeTicker:
        def __init__(self, t):
            self.info = info
            self.earnings_estimate = _Estimates(est_rows).df
            self.income_stmt = pd.DataFrame({"2025": [gaap_eps]}, index=["Diluted EPS"])
            self.fast_info = {"last_price": fx}
    monkeypatch.setattr(yfinance, "Ticker", FakeTicker)
    monkeypatch.setattr(bv.market_cache, "cached", lambda kind, t, fetch, ttl_hours=12: fetch())


def test_analyst_estimates_with_fiscal_year_and_basis_gap(monkeypatch):
    jun_2027 = int(datetime(2027, 6, 30, tzinfo=timezone.utc).timestamp())
    _fake_yf(monkeypatch, {"currency": "USD", "financialCurrency": "USD", "nextFiscalYearEnd": jun_2027},
             [{"avg": 19.75, "yearAgoEps": 17.28, "numberOfAnalysts": 34}, {"avg": 23.64, "yearAgoEps": 19.75, "numberOfAnalysts": 33}],
             gaap_eps=13.0)
    out = bv.analyst_eps_estimates("MSFT")
    assert [e["fy_end"] for e in out["estimates"]] == ["2027-06-30", "2028-06-30"]
    assert out["estimates"][0]["value"] == 19.75 and out["estimates"][0]["analysts"] == 34
    assert out["basis_gap_pct"] == pytest.approx(32.9, abs=0.1)   # 17,28 ajustado frente a 13 contable


def test_analyst_estimates_converted_to_quote_unit(monkeypatch):
    dec_2026 = int(datetime(2026, 12, 31, tzinfo=timezone.utc).timestamp())
    # Evolution: estima en euros, cotiza en coronas
    _fake_yf(monkeypatch, {"currency": "SEK", "financialCurrency": "EUR", "nextFiscalYearEnd": dec_2026},
             [{"avg": 5.0, "yearAgoEps": 4.0, "numberOfAnalysts": 18}], gaap_eps=4.0, fx=11.0)
    out = bv.analyst_eps_estimates("EVO.ST")
    assert out["estimates"][0]["value"] == pytest.approx(55.0) and out["basis_gap_pct"] == 0.0
    # Halma: estima en libras, cotiza en peniques
    _fake_yf(monkeypatch, {"currency": "GBp", "financialCurrency": "GBP", "nextFiscalYearEnd": dec_2026},
             [{"avg": 1.33, "yearAgoEps": 1.14, "numberOfAnalysts": 17}])
    assert bv.analyst_eps_estimates("HLMA.L")["estimates"][0]["value"] == pytest.approx(133.0)


# --- El año en curso: analistas (por defecto), media o TTM ---

EST = {"estimates": [{"period": "0y", "fy_end": "2026-12-31", "value": 6.0, "analysts": 10},
                     {"period": "+1y", "fy_end": "2027-12-31", "value": 7.0, "analysts": 9}]}
JUN = date(2026, 6, 30)


def test_current_year_comes_from_analysts_by_default():
    out = bv.evaluate(_val(), 100.0, 5.0, year0=2026, ttm_end=JUN, estimates=EST)
    assert out["metric_year0"] == 6.0 and out["year0"]["source"] == "analysts"
    assert out["metric_n"] == pytest.approx(6.0 * 1.1 ** 5, abs=1e-3)
    assert out["year0"]["ttm"] == pytest.approx(5.0 * 1.1 ** bv.ttm_offset(JUN, 2026), abs=1e-3)


def test_mean_and_ttm_sources():
    ttm0 = 5.0 * 1.1 ** bv.ttm_offset(JUN, 2026)
    mean = bv.evaluate(_val(year0_source="mean"), 100.0, 5.0, year0=2026, ttm_end=JUN, estimates=EST)
    assert mean["metric_year0"] == pytest.approx((6.0 + ttm0) / 2, abs=1e-3)
    ttm = bv.evaluate(_val(year0_source="ttm"), 100.0, 5.0, year0=2026, ttm_end=JUN, estimates=EST)
    assert ttm["metric_year0"] == pytest.approx(ttm0, abs=1e-3) and ttm["year0"]["source"] == "ttm"


def test_falls_back_to_ttm_without_estimates_for_the_year_or_with_fcf():
    assert bv.evaluate(_val(), 100.0, 5.0, year0=2028, ttm_end=date(2027, 12, 31), estimates=EST)["year0"]["source"] == "ttm"
    assert bv.evaluate(_val(metric="fcf"), 100.0, 5.0, year0=2026, ttm_end=JUN, estimates=EST)["year0"]["source"] == "ttm"
    # en enero el 0y aún es el año pasado: vale el +1y para el año en curso
    assert bv.evaluate(_val(), 100.0, 5.0, year0=2027, ttm_end=date(2026, 9, 30), estimates=EST)["metric_year0"] == 7.0


def test_fiscal_year_estimates_are_moved_to_the_calendar_year():
    est = {"estimates": [{"period": "0y", "fy_end": "2027-06-30", "value": 10.0, "analysts": 30}]}
    cal = bv.calendar_estimates(est, 0.1)
    assert list(cal) == [2026] and cal[2026] == pytest.approx(10.0 * 1.1 ** (-181 / 365.25), abs=1e-4)   # medio año antes del cierre fiscal


def test_manual_anchor_still_wins_over_analysts():
    v = _val(anchor={"year": 2026, "value": 8.0, "metric": "eps"})
    out = bv.evaluate(v, 100.0, 5.0, year0=2026, ttm_end=JUN, estimates=EST)
    assert out["metric_year0"] == 8.0 and out["anchor"]["auto"] == 6.0   # «sin corrección» = lo de los analistas


def test_normalize_keeps_the_year0_source():
    assert bv.normalize({"year0_source": "mean"})["year0_source"] == "mean"
    assert bv.normalize({"year0_source": "bogus"})["year0_source"] == "analysts"
