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
    # desglose agrupado por secciones (PER trailing y liquidez corriente se quitaron del desglose a propósito)
    table = response.text.split('id="fundamentalsTable"')[1].split("</thead>")[0]
    for section in ("Calidad", "Crecimiento", "Valoración", "Balance", "Accionista", "Riesgo"):
        assert section in table, f"Section {section} missing!"
    for col in ("ROIC", "M. bruto", "M. oper.", "Crec. NTM", "PER fwd", "PEG", "Yield FCF", "Deuda/EBITDA", "Yield div.", "Beta"):
        assert col in table, f"Column {col} missing!"
    
    print("Portfolio institutional fundamental & risk metrics verification successfully passed!")

if __name__ == "__main__":
    test_portfolio_fundamental_metrics()
