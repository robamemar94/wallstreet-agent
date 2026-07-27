from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_portfolio_fundamental_metrics():
    print("Requesting /portfolio to verify expanded fundamental and valuation metrics...")
    response = client.get("/portfolio")
    assert response.status_code == 200, f"Page load failed with status {response.status_code}"
    
    # Verify that the new cards and sections are rendered in the HTML
    assert "Calidad, Crecimiento y Riesgo (Ponderado)" in response.text, "Quality & Risk card title missing!"
    assert "Valoración, Dividendos y Solvencia (Ponderado)" in response.text, "Valuation & Solvency card title missing!"
    assert "Desglose de Fundamentales por Compañía" in response.text, "Individual companies comparative table missing!"
    
    # Verify key individual metric column headers
    assert "ROIC" in response.text, "ROIC column missing!"
    assert "Crecimiento" in response.text, "Expected growth column missing!"
    assert "M. Bruto" in response.text, "Gross margin column missing!"
    assert "M. Oper." in response.text, "Operating margin column missing!"
    assert "PER Trail." in response.text, "Trailing P/E column missing!"
    assert "PER Fwd." in response.text, "Forward P/E column missing!"
    assert "PEG" in response.text, "PEG Ratio column missing!"
    assert "Yield FCF" in response.text, "FCF Yield column missing!"
    assert "Deuda/EBITDA" in response.text, "Net Debt/EBITDA column missing!"
    assert "Liquidez" in response.text, "Liquidity (Current Ratio) column missing!"
    assert "Yield Div." in response.text, "Dividend yield column missing!"
    
    print("Portfolio institutional fundamental & risk metrics verification successfully passed!")

if __name__ == "__main__":
    test_portfolio_fundamental_metrics()
