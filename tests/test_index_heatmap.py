from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_index_heatmap_toggle():
    print("Requesting / to verify index page and heatmap toggle HTML...")
    response = client.get("/")
    assert response.status_code == 200, f"Page load failed with status {response.status_code}"
    
    # Check that our heatmap controls are rendered on the homepage
    assert "Distribución: Mercado vs Cartera" in response.text, "Heatmap card header title missing!"
    assert "btnHeatmapMarket" in response.text, "Heatmap market button ID missing!"
    assert "btnHeatmapPortfolio" in response.text, "Heatmap portfolio button ID missing!"
    assert "setHeatmapMode" in response.text, "setHeatmapMode Javascript click handler missing!"
    assert "renderHeatmap" in response.text, "renderHeatmap Javascript renderer function missing!"
    
    print("Homepage and heatmap toggle verification successfully passed!")

if __name__ == "__main__":
    test_index_heatmap_toggle()
