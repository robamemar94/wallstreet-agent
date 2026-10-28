"""Editar una operación añadida a mano y comprobarla antes de guardar (contra la copia temporal de la BBDD, sin internet)."""
import pytest


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from main import app
    from app.application.services.portfolio_service import PortfolioService
    from app.interfaces.views import market_views
    monkeypatch.setattr(PortfolioService, "rebuild_portfolio", lambda self, **kw: None)   # descargaría splits y dividendos
    monkeypatch.setattr(market_views, "ensure_prices_cached", lambda *a, **k: None)
    monkeypatch.setitem(market_views.INDEX_CACHE["data"], "ZZTEST", {"price": 35.0, "prev": 34.0})
    return TestClient(app)


def _seed(**kw):
    from app.domain.models import Transaction, TransactionType
    from app.infrastructure.db.database import SessionLocal
    from app.infrastructure.repositories.sqlalchemy_portfolio_repository import SqlAlchemyPortfolioRepository
    from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
    from app.application.services.decision_review_service import TX_NOTES_KEY, tx_ref
    db = SessionLocal()
    repo = SqlAlchemyPortfolioRepository(db)
    txs = [t for t in repo.load_manual_transactions() if t.ticker != "ZZTEST"]
    txs.append(Transaction(date="2026-10-22", type=TransactionType.SELL, ticker="ZZTEST", shares=140, price=3.0,
                           currency="USD", fee=3.2, fx_rate_at_purchase=0.89, broker="IBKR"))
    repo.save_manual_transactions(txs)
    SqlAlchemyAssetRepository(db).save_asset_data("ZZTEST", TX_NOTES_KEY, {tx_ref("2026-10-22", "SELL", 140, 3.0): "reducir peso"})
    return db


def test_get_and_update_moves_the_note(client):
    from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
    from app.application.services.decision_review_service import TX_NOTES_KEY, tx_ref
    db = _seed()
    q = {"date": "2026-10-22", "type": "SELL", "shares": 140, "price": 3.0}
    tx = client.get("/api/portfolio/transaction/ZZTEST", params=q).json()
    assert (tx["price"], tx["fee"], tx["broker"], tx["comment"]) == (3.0, 3.2, "IBKR", "reducir peso")

    new = {**tx, "price": 35.0, "fee": 11.88, "comment": None}   # sin tocar el porqué: se conserva
    r = client.post("/api/portfolio/transaction/update/ZZTEST", json={"old": q, "new": new})
    assert r.json()["status"] == "success"
    notes = SqlAlchemyAssetRepository(db).get_asset_data("ZZTEST").get(TX_NOTES_KEY)
    assert notes == {tx_ref("2026-10-22", "SELL", 140, 35.0): "reducir peso"}
    assert client.get("/api/portfolio/transaction/ZZTEST", params={**q, "price": 35.0}).json()["fee"] == 11.88
    assert client.get("/api/portfolio/transaction/ZZTEST", params=q).status_code == 404   # la de 3 $ ya no está
    assert client.post("/api/portfolio/transaction/update/ZZTEST", json={"old": q, "new": new}).status_code == 404


def test_check_gives_price_and_shares_held(client):
    _seed()
    chk = client.get("/api/portfolio/check/ZZTEST").json()
    assert chk["price"] == 35.0 and chk["known"] is True
    assert client.get("/api/portfolio/check/NOEXISTE123").json()["known"] is False
