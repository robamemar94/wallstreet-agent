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
    cache = {"insider_trades_v1": [_trade(10, "buy", "A", 300_000), _trade(20, "sell", "B", 100_000),
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
