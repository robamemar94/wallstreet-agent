from app.application.services import split_service


def test_missing_split_in_portfolio_is_flagged(monkeypatch):
    monkeypatch.setattr(split_service, "fetch_splits", lambda t: [("2026-04-06", 25.0)])
    held = {"BKNG": "2026-02-12", "OLD": "2026-05-01"}          # OLD compró después del split: no le afecta
    assert split_service.portfolio_notices(held, {"BKNG": [("2026-04-06", 25.0)]}) == []   # ya registrado
    (n,) = split_service.portfolio_notices(held, {})
    assert n["ticker"] == "BKNG" and n["prio"] == 0 and "25:1" in n["text"]


def test_manual_values_before_a_split_are_flagged_with_the_factor(monkeypatch):
    monkeypatch.setattr(split_service, "fetch_splits", lambda t: [("2025-11-17", 10.0), ("2026-06-12", 2.0)])
    (n,) = split_service.manual_value_notices([("X", "precio de referencia de la tesis", "2025-10-01", "/tesis/1")])
    assert "20" in n["text"]                                     # 10 × 2
    assert split_service.manual_value_notices([("X", "precio", "2026-07-01", "/")]) == []


def test_factor_since_adjusts_added_price():
    splits = [("2025-11-17", 10.0), ("2026-06-12", 0.5)]         # split y contrasplit
    assert split_service.factor_since(splits, "2025-01-01") == 5.0
    assert split_service.factor_since(splits, "2026-01-01") == 0.5
    assert split_service.factor_since(splits, None) == 1.0
