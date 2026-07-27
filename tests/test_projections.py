import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_save_projection_scenarios():
    # Test saving scenario projections
    ticker = "AAPL"
    payload = {
        "initial_eps": 6.50,
        "bear": {"eps_growth": 0.04, "exit_pe": 16.0},
        "base": {"eps_growth": 0.10, "exit_pe": 22.0},
        "bull": {"eps_growth": 0.15, "exit_pe": 26.0},
        "rationale": "Test custom rationale."
    }
    
    # Send post to save projections
    response = client.post(f"/api/projections/save/{ticker}", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "success"}


@patch("app.interfaces.api.analysis_api.generate_projection_scenarios_json")
def test_generate_projection_scenarios(mock_generate):
    # Mocking Gemini response to avoid API calls during tests
    ticker = "AAPL"
    mock_generate.return_value = {
        "initial_eps": 6.25,
        "bear": {"eps_growth": 0.05, "exit_pe": 15.0},
        "base": {"eps_growth": 0.11, "exit_pe": 20.0},
        "bull": {"eps_growth": 0.16, "exit_pe": 25.0},
        "rationale": "Mocked rationale."
    }
    
    response = client.post(f"/api/projections/generate/{ticker}")
    assert response.status_code == 200
    res_json = response.json()
    assert res_json["status"] == "success"
    assert res_json["data"]["initial_eps"] == 6.25
    assert res_json["data"]["bear"]["eps_growth"] == 0.05
    assert res_json["data"]["base"]["exit_pe"] == 20.0
