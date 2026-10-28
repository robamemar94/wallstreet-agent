import datetime

import pytest

from app.application.services import insider_service as ins


def test_only_open_market_trades_count():
    assert ins.classify("Purchase at price 42.10 per share.") == "buy"
    assert ins.classify("Acquisition in the public market at price 80.33 per share.") == "buy"
    assert ins.classify("Sale at price 495.97 - 500.78 per share.") == "sell"
    assert ins.classify("Disposition in the public market at price 80.14 per share.") == "sell"
    for noise in ("Stock Award(Grant) at price 42.14 per share.", "Stock Gift at price 0.00 per share.",
                  "Conversion of Exercise of derivative security", "Acquisition (Non Open Market) at price 0.00 per share."):
        assert ins.classify(noise) is None
    assert ins._price("Sale at price 495.97 - 500.78 per share.") == pytest.approx(498.375)


def _trade(days_ago, kind="buy", who="A", value=10_000):
    d = (datetime.date(2026, 10, 13) - datetime.timedelta(days=days_ago)).isoformat()
    return {"date": d, "insider": who, "position": "", "kind": kind, "shares": 100, "value": value, "price": None}


def test_signal_two_insiders_or_one_big_buy_in_30_days():
    today = datetime.date(2026, 10, 13)
    assert ins.signal([_trade(5, who="A"), _trade(10, who="B")], today)["insiders"] == ["A", "B"]
    assert ins.signal([_trade(5, who="A", value=150_000)], today)
    assert ins.signal([_trade(5, who="A"), _trade(40, who="B")], today) is None        # la otra, fuera de la ventana
    assert ins.signal([_trade(5, "sell", "A", 9e6), _trade(6, "sell", "B", 9e6)], today) is None   # las ventas no avisan


def test_share_change_adjusted_for_splits():
    months = [f"{2023 + (m // 12)}-{m % 12 + 1:02d}-28" for m in range(36)]
    # 1.000 acciones hasta un split 10×1 a mitad, y luego 9.700: en acciones de hoy, una recompra del 3 % en 3 años
    raw = [[d, 1000.0 if i < 18 else 9700.0] for i, d in enumerate(months)]
    assert ins.share_change(raw) is None                                         # sin ajustar: «dilución» imposible
    adjusted = ins.split_adjusted(raw, [(months[18], 10.0)])
    ch = ins.share_change(adjusted)
    assert ch["label"] == "recompra" and ch["pct"] == pytest.approx(100 * (0.97 ** (1 / ch["years"]) - 1), abs=0.05)
    assert ins.share_change([[d, 500.0] for d in months])["label"] == "estable"


def test_notices_only_from_cache(monkeypatch):
    cache = {"AAA": [_trade(2, who="Ann"), _trade(3, who="Bob")], "BBB": None}
    monkeypatch.setattr(ins.market_cache, "peek", lambda kind, t: cache.get(t))
    out = ins.signal_notices(["BBB", "AAA", "CCC"], datetime.date(2026, 10, 13))
    assert [n["ticker"] for n in out] == ["AAA"] and out[0]["type"] == "insider_buy"
    assert "Compran 2 directivos" in out[0]["text"] and out[0]["href"] == "/ticker/AAA#directivos"


def test_summary_net_buys_minus_sells_in_12_months(monkeypatch):
    cache = {"insider_trades_v2": [_trade(10, "buy", "A", 300_000), _trade(20, "sell", "B", 100_000),
                                   _trade(400, "sell", "C", 9e6)],            # hace más de un año: fuera
             "shares_history_v2": None, "splits_all_v1": []}
    monkeypatch.setattr(ins.market_cache, "peek", lambda kind, t: cache.get(kind))
    s = ins.summary("AAA", datetime.date(2026, 10, 13), fetch=False)
    assert s["net"] == 200_000 and s["buys"]["insiders"] == 1 and s["sells"]["count"] == 1
    assert s["signal"] and len(s["trades"]) == 2 and s["shares"] is None


def test_share_windows_1_3_5_years():
    # 5 años y medio de datos mensuales: 1.000 acciones al principio que bajan un 2 % al año (recompra)
    start = datetime.date(2021, 4, 30)
    series = []
    for m in range(67):
        d = datetime.date(start.year + (start.month - 1 + m) // 12, (start.month - 1 + m) % 12 + 1, 28)
        series.append([d.isoformat(), 1000 * 0.98 ** (m / 12)])
    w = ins.share_windows(series, datetime.date.fromisoformat(series[-1][0]))
    for n in (1, 3, 5):
        assert w[n]["annual"] == pytest.approx(-2, abs=0.35)   # la mediana de 3 meses retrasa un mes el final and w[n]["total"] == pytest.approx(100 * (0.98 ** n - 1), abs=0.4)
    assert ins.share_windows(series[-30:])[5] is None and ins.share_windows(series[-30:])[1] is not None   # no llega a 5 años


def test_shares_from_annual_accounts():
    fin = {"years": ["2019", "2020", "2021", "2022", "2023", "2024", "2025", "LTM"],
           "income_statement": {"Diluted Weighted Average Shares Outstanding": [1248.4, 1753.0, 1895.5, 1974.9, 2091.8, 2150.5, 2119.7, 2106.9]}}
    out = ins.shares_from_financials(fin)
    assert [y for y, _ in out["series"]][-1] == "2025"                       # sin el LTM
    assert out["windows"][3]["total"] == pytest.approx(100 * (2119.7 / 1974.9 - 1))
    assert out["windows"][5]["total"] == pytest.approx(100 * (2119.7 / 1753.0 - 1)) and out["shares"]["label"] == "diluye"
    assert ins.shares_from_financials({"years": ["2025"], "income_statement": {}}) is None


# --- Lectura (analyze): 3 meses de titular, parte de lo suyo, ventas periódicas o de sueldo, precio frente a hoy ---

TODAY = datetime.date(2026, 10, 13)


def _op(days_ago, kind, who, shares, price, position="Director"):
    d = (TODAY - datetime.timedelta(days=days_ago)).isoformat()
    return {"date": d, "insider": who, "position": position, "kind": kind, "shares": shares,
            "value": shares * price if kind != "award" else None, "price": price if kind != "award" else None}


def test_periodic_sales_are_a_plan():
    weekly = [_op(7 * i, "sell", "A", 1575, 700) for i in range(6)]
    assert ins.is_plan(weekly)
    assert not ins.is_plan(weekly[:2])                                        # 2 no son patrón
    assert not ins.is_plan([_op(5, "sell", "A", 100, 1), _op(50, "sell", "A", 9000, 1), _op(200, "sell", "A", 2500, 1)])


def test_big_sale_of_own_stake_is_flagged_but_selling_what_you_are_paid_is_routine():
    roster = {"Big Seller": {"position": "Chief Financial Officer", "direct": 1000.0, "indirect": None},
              "Paid In Stock": {"position": "Chief Executive Officer", "direct": 50_000.0, "indirect": None}}
    items = [_op(10, "sell", "Big Seller", 4000, 120, "Chief Financial Officer"),                   # vende 4.000 de 5.000
             _op(20, "award", "Paid In Stock", 10_000, 0), _op(15, "sell", "Paid In Stock", 9000, 120, "Chief Executive Officer")]
    r = ins.analyze(items, roster, shares_out=1_000_000, price_now=96.0, today=TODAY)
    big = next(p for p in r["people"] if p["name"] == "Big Seller")
    paid = next(p for p in r["people"] if p["name"] == "Paid In Stock")
    assert big["stake_sold_pct"] == pytest.approx(80) and big["role"] == "CFO" and not big["comp"]
    assert paid["comp"] and paid["received"] == 10_000                       # vende lo que cobra en acciones
    assert r["verdict"] == "vigilar" and [p["name"] for p in r["watch"]] == ["Big Seller"]
    assert r["sells"]["avg"] == pytest.approx(120) and r["sells"]["vs_now"] == pytest.approx(-20)    # hoy un 20 % por debajo
    assert r["sells"]["pct_company"] == pytest.approx(1.3)


def test_buys_lead_and_compare_with_today():
    items = [_op(5, "buy", "A", 1000, 95, "Chief Financial Officer"), _op(8, "buy", "B", 500, 95), _op(9, "sell", "C", 100, 100)]
    r = ins.analyze(items, None, price_now=104.0, today=TODAY)
    assert r["verdict"] == "compran" and r["buys"]["insiders"] == ["A", "B"]
    assert r["buys"]["vs_now"] == pytest.approx(100 * (104 / 95 - 1)) and r["senior_buyers"] == ["CFO"]
    assert ins.analyze(items, None, price_now=None, today=TODAY)["buys"]["vs_now"] is None   # otra moneda: no se compara


def test_nothing_recent_falls_back_to_the_year_and_prices_are_split_adjusted():
    old = [_op(200, "sell", "A", 100, 2000)]
    r = ins.analyze(old, None, price_now=None, splits=[((TODAY - datetime.timedelta(days=100)).isoformat(), 25.0)], today=TODAY)
    assert r["verdict"] == "quietos_reciente" and r["year_sells"]["count"] == 1
    assert r["year_sells"]["avg"] == pytest.approx(80) and r["year_sells"]["shares"] == pytest.approx(2500)   # 2.000 / 25
    assert sum(m["sell"] for m in r["months"]) == pytest.approx(200_000) and len(r["months"]) == 12
