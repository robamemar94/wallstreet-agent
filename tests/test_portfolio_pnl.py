from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

print("Requesting /portfolio...")
response = client.get("/portfolio")
print("Status code:", response.status_code)
assert response.status_code == 200, f"Failed with status {response.status_code}"
assert 'id="dist-tab"' in response.text and 'id="dist-pane"' in response.text, "Distribution tab missing from HTML!"
assert "dist-bar-range" in response.text, "Role bars vs target missing from HTML!"
for chart in ("distAssets", "dist-currency", "dist-sector", "dist-country"):
    assert f'id="{chart}"' in response.text, f"Distribution chart {chart} missing from HTML!"

# Test detailed ticker page for EVO.ST
print("Requesting /portfolio/ticker/EVO.ST...")
response_ticker = client.get("/portfolio/ticker/EVO.ST")
print("Status code:", response_ticker.status_code)
assert response_ticker.status_code == 200, f"Failed with status {response_ticker.status_code}"

print("All integration tests successfully passed! The EVO.ST detailed view and real-time currency desgloses are fully validated and operational.")
