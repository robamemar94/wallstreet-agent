import pytest

from app.application.services import base_valuation_service as bv


def _val(**kw):
    return {"metric": "eps", "growth": 0.10, "exit_multiple": 20.0, "dps": 0.0, "div_growth": 0.0, "years": 5, **kw}


def test_without_dividends_cagr_is_price_cagr():
    # BPA 5 → 5·1,1⁵ = 8,05 → ×20 = 161,05 en 5 años; desde 100: CAGR = 1,6105^(1/5) − 1 = 10 %
    out = bv.evaluate(_val(), 100.0, 5.0)
    assert out["price_n"] == pytest.approx(161.05, abs=0.01)
    assert out["cagr"] == pytest.approx(10.0, abs=0.01)
    assert out["buy_price"] == pytest.approx(161.051 / 1.15 ** 5, abs=0.01)
    assert out["zone"] == "barata"


def test_buy_price_gives_exactly_the_target_return():
    v = _val(dps=2.0, div_growth=0.05)
    buy = bv.evaluate(v, 100.0, 5.0)["buy_price"]
    assert bv.evaluate(v, buy, 5.0)["cagr"] == pytest.approx(bv.TARGET_RETURN_PCT, abs=0.01)


def test_dividends_raise_the_return():
    assert bv.evaluate(_val(dps=3.0), 100.0, 5.0)["cagr"] > bv.evaluate(_val(), 100.0, 5.0)["cagr"]


def test_margin_and_zone_below_buy_price():
    out = bv.evaluate(_val(), 70.0, 5.0)   # precio de compra ≈ 80,07
    assert out["margin_pct"] > 0 and out["zone"] == "ganga"


@pytest.mark.parametrize("cagr, zone", [(15, "ganga"), (12, "barata"), (7, "justo"), (3, "cara"), (None, None)])
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
    out = bv.live("X", {"base_valuation": _val(metric="fcf", metric_value=None)}, 100.0)
    assert out["auto_metric"] and out["metric0"] == 5.0
    assert bv.live("X", {"base_valuation": _val(metric="eps", metric_value=None)}, 100.0) is None
    assert bv.live("X", {}, 100.0) is None


def test_live_takes_current_dividend_when_not_stored(monkeypatch):
    monkeypatch.setattr(bv, "_market_info", lambda t: {"trailingEps": 5.0, "dividendRate": 2.0})
    out = bv.live("X", {"base_valuation": _val(metric_value=5.0, dps=None)}, 100.0)
    assert out["inputs"]["dps"] == 2.0 and out["dividends"] == pytest.approx(10.0)
