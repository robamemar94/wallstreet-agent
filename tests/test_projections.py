from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.infrastructure.db.database import Base
from app.infrastructure.dependencies import get_asset_repository
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from main import app


@pytest.fixture
def client_and_repo():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    repo = SqlAlchemyAssetRepository(sessionmaker(bind=engine)())
    app.dependency_overrides[get_asset_repository] = lambda: repo
    yield TestClient(app), repo
    app.dependency_overrides.clear()


def test_save_base_valuation(client_and_repo):
    client, repo = client_and_repo
    payload = {"metric": "fcf", "metric_value": None, "growth": 0.10, "exit_multiple": 22.0,
               "dps": 1.2, "div_growth": 0.05, "years": 5, "rationale": "Test."}
    response = client.post("/api/valuation/AAPL", json=payload)
    assert response.json()["status"] == "success"
    saved = repo.get_asset_data("AAPL")["base_valuation"]
    assert saved["metric"] == "fcf" and saved["metric_value"] is None and saved["exit_multiple"] == 22.0
    assert saved["is_manual"] is True


@patch("app.interfaces.api.analysis_api.yf.Ticker")
@patch("app.interfaces.api.analysis_api.generate_base_valuation_json")
def test_generate_base_valuation(mock_generate, mock_ticker, client_and_repo):
    client, repo = client_and_repo
    mock_ticker.return_value.info = {"longName": "Apple", "dividendRate": 1.04}
    mock_generate.return_value = {"metric_value": 6.25, "metric_explanation": "TTM.", "growth": 0.11,
                                  "div_growth": 0.06, "exit_multiple": 20.0, "rationale": "Mocked rationale."}
    res = client.post("/api/valuation/AAPL/generate?metric=eps").json()
    assert res["status"] == "success"
    assert res["data"]["metric_value"] is None and res["data"]["metric_suggested"] == 6.25   # parte del TTM
    assert res["data"]["exit_multiple"] == 20.0
    assert res["data"]["dps"] == 1.04 and res["data"]["is_manual"] is False
    assert mock_generate.call_args.kwargs["metric"] == "eps"
    assert repo.get_asset_data("AAPL")["base_valuation"]["growth"] == 0.11
