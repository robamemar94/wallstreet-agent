from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_closed_positions_ui_elements():
    print("Requesting /portfolio to verify closed positions section is rendered correctly...")
    response = client.get("/portfolio")
    assert response.status_code == 200, f"Page load failed with status {response.status_code}"
    
    # Verify that our newly added closed positions elements are present in the HTML response
    assert "Resumen de Posiciones Cerradas" in response.text, "Closed positions table section header is missing!"
    assert "Rendimiento histórico agrupado por activo" in response.text, "Closed positions table description is missing!"
    assert "closedPositionsTable" in response.text, "Closed positions table element ID is missing!"
    
    # Verify table columns
    assert "Pr. Compra" in response.text, "Purchase price column header is missing!"
    assert "Pr. Venta" in response.text, "Sale price column header is missing!"
    assert "Coste Total" in response.text, "Total cost column header is missing!"
    assert "Total Cobrado" in response.text, "Total collected column header is missing!"
    assert "CAGR %" in response.text, "CAGR column header is missing!"
    assert "Divs Cobrados" in response.text, "Dividends collected column header is missing!"
    assert "Efecto Salida Actual" in response.text, "Efecto Salida Actual tooltip is missing!"
    assert "P&amp;L Realizado" in response.text or "P&L Realizado" in response.text, "Realized P&L column header is missing!"
    
    # Verify that the labels for detailed operations section are present
    assert "Detalle de Operaciones Individuales" in response.text, "Individual operations section header is missing!"

if __name__ == "__main__":
    test_closed_positions_ui_elements()
