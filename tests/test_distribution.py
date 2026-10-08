from app.application.services.distribution_service import build

ROLES = [{"key": "CORE", "name": "Core", "color": "#22c55e", "min": 30, "max": 40},
         {"key": "GROWTH", "name": "Growth", "color": "#3b82f6", "min": 25, "max": 35}]


def pos(ticker, value, currency="USD", sector="Tecnología", country="EE. UU.", role=None):
    return {"ticker": ticker, "name": ticker + " Inc", "value": value, "currency": currency, "sector": sector,
            "country": country, "role": role}


def test_small_positions_grouped_in_others():
    d = build([pos("A", 600), pos("B", 391), pos("C", 5), pos("D", 4)], ROLES)
    labels = [a["label"] for a in d["assets"]]
    assert labels == ["A", "B", "Otros"]
    others = d["assets"][-1]
    assert others["members"] == ["C", "D"] and others["ticker"] is None
    assert round(others["pct"], 1) == 0.9


def test_single_small_position_not_grouped():
    d = build([pos("A", 995), pos("B", 5)], ROLES)
    assert [a["label"] for a in d["assets"]] == ["A", "B"]


def test_concentration():
    d = build([pos(t, 100) for t in "ABCDEFGHIJ"], ROLES)        # 10 posiciones iguales
    c = d["concentration"]
    assert round(c["effective_n"], 6) == 10 and round(c["max_pct"]) == 10 and round(c["top5_pct"]) == 50
    d = build([pos("A", 500), pos("B", 300), pos("C", 200)], ROLES)
    assert round(d["concentration"]["effective_n"], 2) == 2.63     # 1 / (0,25 + 0,09 + 0,04)


def test_roles_vs_target_and_without_role():
    d = build([pos("A", 350, role="CORE"), pos("B", 200, role="GROWTH"), pos("C", 450)], ROLES)
    r = {x["label"]: x for x in d["roles"]}
    assert r["Core"]["band"] == "en_rango" and r["Growth"]["band"] == "bajo"
    assert r["Sin rol"]["members"] == ["C"] and round(r["Sin rol"]["pct"]) == 45


def test_currency_and_main_currency():
    d = build([pos("A", 700), pos("B", 300, currency="EUR", country="España")], ROLES)
    assert [c["label"] for c in d["currency"]] == ["USD", "EUR"]
    assert d["concentration"]["main_currency"] == "USD" and round(d["concentration"]["main_currency_pct"]) == 70


def test_empty_portfolio():
    assert build([], ROLES) is None
