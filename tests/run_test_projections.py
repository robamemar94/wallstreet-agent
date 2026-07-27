import sys
import os
from unittest.mock import patch
from fastapi.testclient import TestClient

# Ensure root directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import app

client = TestClient(app)

def test_save_projection_scenarios():
    print("Running test_save_projection_scenarios...")
    ticker = "AAPL"
    payload = {
        "initial_eps": 6.50,
        "normalized_eps_explanation": "Custom normalized baseline EPS.",
        "dividend_yield": 0.015,
        "bear": {"eps_growth": 0.04, "exit_pe": 16.0, "div_growth": 0.01},
        "base": {"eps_growth": 0.10, "exit_pe": 22.0, "div_growth": 0.05},
        "bull": {"eps_growth": 0.15, "exit_pe": 26.0, "div_growth": 0.10},
        "rationale": "Test custom rationale."
    }
    response = client.post(f"/api/projections/save/{ticker}", json=payload)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert response.json() == {"status": "success"}, f"Expected success status, got {response.json()}"
    print("test_save_projection_scenarios: PASSED")


@patch("app.interfaces.api.analysis_api.generate_projection_scenarios_json")
def test_generate_projection_scenarios(mock_generate):
    print("Running test_generate_projection_scenarios...")
    ticker = "AAPL"
    mock_generate.return_value = {
        "initial_eps": 6.25,
        "normalized_eps_explanation": "Mocked explanation of normalized EPS.",
        "bear": {"eps_growth": 0.05, "exit_pe": 15.0, "div_growth": 0.02},
        "base": {"eps_growth": 0.11, "exit_pe": 20.0, "div_growth": 0.06},
        "bull": {"eps_growth": 0.16, "exit_pe": 25.0, "div_growth": 0.11},
        "rationale": "Mocked rationale."
    }
    
    response = client.post(f"/api/projections/generate/{ticker}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    res_json = response.json()
    assert res_json["status"] == "success", f"Expected success status, got {res_json}"
    assert res_json["data"]["initial_eps"] == 6.25, f"Expected 6.25, got {res_json['data']['initial_eps']}"
    assert res_json["data"]["bear"]["eps_growth"] == 0.05, f"Expected 0.05, got {res_json['data']['bear']['eps_growth']}"
    assert res_json["data"]["base"]["exit_pe"] == 20.0, f"Expected 20.0, got {res_json['data']['base']['exit_pe']}"
    assert res_json["data"]["bear"]["div_growth"] == 0.02, f"Expected 0.02, got {res_json['data']['bear']['div_growth']}"
    print("test_generate_projection_scenarios: PASSED")

if __name__ == "__main__":
    try:
        test_save_projection_scenarios()
        test_generate_projection_scenarios()
        print("\nALL TESTS PASSED SUCCESSFULLY! 🎉")
    except AssertionError as e:
        print(f"\nTEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nUNEXPECTED ERROR: {e}")
        sys.exit(1)
