"""«Mi precio» de la Watchlist: precio de compra puesto a mano, aviso al alcanzarlo y recordatorio de revisarlo."""
from datetime import date

from app.interfaces.views import watchlist_views as wv


def _no_splits(monkeypatch):
    monkeypatch.setattr(wv.split_service, "fetch_splits", lambda t: [])


def test_my_price_distance_and_review(monkeypatch):
    _no_splits(monkeypatch)
    d = {wv.MY_PRICE_KEY: {"price": 90.0, "set_at": "2026-06-01"}}
    m = wv.my_price("AAA", d, 100.0, today=date(2026, 10, 16))
    assert m["drop"] == 10.0 and not m["hit"]
    assert m["age_days"] == 137 and m["review_due"]
    m = wv.my_price("AAA", d, 85.0, today=date(2026, 6, 20))
    assert m["hit"] and not m["review_due"]
    assert wv.my_price("AAA", {}, 100.0) is None


def test_my_price_adjusted_by_split(monkeypatch):
    monkeypatch.setattr(wv.split_service, "fetch_splits", lambda t: [("2026-07-01", 2.0)])
    m = wv.my_price("AAA", {wv.MY_PRICE_KEY: {"price": 100.0, "set_at": "2026-06-01"}}, 60.0, today=date(2026, 7, 2))
    assert m["price"] == 50.0 and m["drop"] > 0


def test_attention_my_price(monkeypatch):
    monkeypatch.setattr("app.application.services.insider_service.signal_notices", lambda *a, **k: [])
    monkeypatch.setattr("app.application.services.estimate_revisions_service.signal_notices", lambda *a, **k: [])
    monkeypatch.setattr("app.application.services.estimate_revisions_service.rise_notices", lambda *a, **k: [])
    row = {"ticker": "AAA", "held": False, "silent": False, "val": None, "price": 80.0, "group": "sin", "tier": None,
           "mine": {"price": 90.0, "set_at": "2026-06-01", "drop": -12.5, "hit": True, "age_days": 137, "review_due": True}}
    out = wv.watchlist_attention([row])
    types = [a["type"] for a in out]
    assert "mi_precio" in types and "mi_precio_revisar" in types
    assert out[0]["type"] == "mi_precio"   # prioridad 0, como la zona de compra
    # en cartera o silenciada: no avisa
    assert not [a for a in wv.watchlist_attention([{**row, "held": True}]) if a["type"].startswith("mi_precio")]
