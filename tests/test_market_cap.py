"""Capitalización en USD y su categoría."""
from datetime import date

from app.application.services import market_cap_service as mc

# cruces a EUR como los de INDEX_CACHE["data"]
PRICES = {"USDEUR=X": {"price": 0.9}, "CHFEUR=X": {"price": 1.08}, "GBPEUR=X": {"price": 1.17}}


def test_tiers():
    assert mc.tier(250e9)["key"] == "mega"
    assert mc.tier(10e9)["key"] == "large"
    assert mc.tier(5e9)["key"] == "mid"
    assert mc.tier(1e9)["key"] == "small"
    assert mc.tier(100e6)["key"] == "micro"
    assert mc.tier(10e6)["key"] == "nano"
    assert mc.tier(None) is None and mc.tier(0) is None


def test_to_usd_with_fx_and_pence():
    assert mc.to_usd(100.0, "USD", PRICES) == 100.0
    assert round(mc.to_usd(90.0, "EUR", PRICES), 6) == 100.0
    assert round(mc.to_usd(100.0, "CHF", PRICES), 2) == 120.0          # 100 CHF = 108 € = 120 $
    assert round(mc.to_usd(10000.0, "GBp", PRICES), 2) == 130.0        # 10.000 peniques = 100 £ = 117 € = 130 $
    assert mc.to_usd(100.0, "SEK", PRICES) is None                     # sin cruce: sin dato (no se inventa)


def test_shares_prefer_implied_and_fix_pence():
    assert mc.shares_from_info({"sharesOutstanding": 300, "impliedSharesOutstanding": 390}) == 390
    assert mc.shares_from_info({"sharesOutstanding": 300}) == 300
    # Londres: marketCap en libras y precio en peniques
    assert mc.shares_from_info({"marketCap": 3_417_597_952, "currentPrice": 471.7, "currency": "GBp"}) == 3_417_597_952 * 100 / 471.7


def test_describe_and_label():
    d = mc.describe(94_046_249, 70.35, "CHF", PRICES)
    assert d["tier"]["key"] == "mid" and d["label"] == "7,9 B$"
    assert mc.fmt_usd(2.85e12) == "2,85 T$" and mc.fmt_usd(850e6) == "850 M$" and mc.fmt_usd(150e9) == "150 B$"


def test_needs_refresh():
    today = date(2026, 10, 8)
    assert mc.needs_refresh({}, today)
    assert not mc.needs_refresh({mc.SHARES_KEY: 1, mc.SHARES_DATE_KEY: "2026-09-20"}, today)
    assert mc.needs_refresh({mc.SHARES_KEY: 1, mc.SHARES_DATE_KEY: "2026-08-01"}, today)
