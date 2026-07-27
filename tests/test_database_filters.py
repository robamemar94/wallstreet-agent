from fastapi.testclient import TestClient
from main import app

def test_database_page_renders_with_multiselect_filters():
    client = TestClient(app)
    print("Requesting /database...")
    response = client.get("/database")
    
    print("Status code:", response.status_code)
    assert response.status_code == 200, f"Failed with status {response.status_code}"
    
    html = response.text
    
    # Verify dropdown structures exist in HTML
    assert "id=\"dropdown-status\"" in html, "Multi-select status dropdown missing!"
    assert "id=\"dropdown-valuation\"" in html, "Multi-select valuation dropdown missing!"
    assert "id=\"dropdown-region\"" in html, "Multi-select region dropdown missing!"
    assert "id=\"dropdown-sector\"" in html, "Multi-select sector dropdown missing!"
    assert "id=\"dropdown-subsector\"" in html, "Multi-select subsector dropdown missing!"
    assert "id=\"dropdown-country\"" in html, "Multi-select country dropdown missing!"
    assert "id=\"portfolio-tab\"" in html, "Mi Cartera tab is missing!"
    assert "onclick=\"sortRows(9)\">Media</th>" in html, "Media column header is missing!"
    
    # Verify Javascript functions are included
    assert "function filterDropdownItems" in html, "filterDropdownItems JS function missing!"
    assert "function handleSelectAll" in html, "handleSelectAll JS function missing!"
    assert "function handleItemSelect" in html, "handleItemSelect JS function missing!"
    assert "function updateDropdownLabel" in html, "updateDropdownLabel JS function missing!"
    assert "function getSelectedValues" in html, "getSelectedValues JS function missing!"
    
    # Verify dark/light theme options styles are defined
    assert "[data-bs-theme=\"dark\"] .form-select-sm" in html, "Dark theme form-select styles missing!"
    assert "[data-bs-theme=\"light\"] .form-select-sm" in html, "Light theme form-select styles missing!"
    
    print("All multi-select filters and styling tests successfully validated!")

if __name__ == "__main__":
    test_database_page_renders_with_multiselect_filters()
