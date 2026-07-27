import sys
import os
from datetime import date, timedelta

# Añadir directorio raíz al path para importar correctamente
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.financial_math import calculate_position_cagr

def test_calculate_position_cagr_active_positive():
    # Compra hace 2 años, rentabilidad 44% -> CAGR debe ser aprox 20%
    today = date.today()
    two_years_ago = (today - timedelta(days=2*365)).strftime('%Y-%m-%d')
    ledger = [
        {"type": "BUY", "date": two_years_ago, "shares": 10, "price": 100}
    ]
    cagr = calculate_position_cagr("TEST", ledger, return_pct=44.0, is_active=True)
    # 1.44 ** 0.5 - 1 = 1.2 - 1 = 0.20 (20.0%)
    assert cagr is not None
    assert abs(cagr - 20.0) < 0.5
    print("test_calculate_position_cagr_active_positive: PASSED")

def test_calculate_position_cagr_active_negative():
    # Compra hace 1 año, rentabilidad -19% -> CAGR debe ser aprox -19%
    today = date.today()
    one_year_ago = (today - timedelta(days=365)).strftime('%Y-%m-%d')
    ledger = [
        {"type": "BUY", "date": one_year_ago, "shares": 10, "price": 100}
    ]
    cagr = calculate_position_cagr("TEST", ledger, return_pct=-19.0, is_active=True)
    assert cagr is not None
    assert abs(cagr - (-19.0)) < 0.5
    print("test_calculate_position_cagr_active_negative: PASSED")

def test_calculate_position_cagr_closed():
    # Compra hace 3 años, venta hace 1 año (holding period 2 años), retorno de 21% -> CAGR debe ser aprox 10%
    today = date.today()
    three_years_ago = (today - timedelta(days=3*365)).strftime('%Y-%m-%d')
    one_year_ago = (today - timedelta(days=365)).strftime('%Y-%m-%d')
    ledger = [
        {"type": "BUY", "date": three_years_ago, "shares": 10, "price": 100},
        {"type": "SELL", "date": one_year_ago, "shares": 10, "price": 121}
    ]
    cagr = calculate_position_cagr("TEST", ledger, return_pct=21.0, is_active=False)
    # 1.21 ** 0.5 - 1 = 1.1 - 1 = 0.10 (10.0%)
    assert cagr is not None
    assert abs(cagr - 10.0) < 0.5
    print("test_calculate_position_cagr_closed: PASSED")

def test_calculate_position_cagr_short_holding():
    # Compra hace 2 días (holding period demasiado corto) -> Debe retornar None
    today = date.today()
    two_days_ago = (today - timedelta(days=2)).strftime('%Y-%m-%d')
    ledger = [
        {"type": "BUY", "date": two_days_ago, "shares": 10, "price": 100}
    ]
    cagr = calculate_position_cagr("TEST", ledger, return_pct=10.0, is_active=True)
    assert cagr is None
    print("test_calculate_position_cagr_short_holding: PASSED")

def test_calculate_position_cagr_empty_ledger():
    cagr = calculate_position_cagr("TEST", [], return_pct=10.0, is_active=True)
    assert cagr is None
    print("test_calculate_position_cagr_empty_ledger: PASSED")

if __name__ == "__main__":
    test_calculate_position_cagr_active_positive()
    test_calculate_position_cagr_active_negative()
    test_calculate_position_cagr_closed()
    test_calculate_position_cagr_short_holding()
    test_calculate_position_cagr_empty_ledger()
    print("All CAGR math unit tests successfully passed!")
