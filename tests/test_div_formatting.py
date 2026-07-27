import pytest
from unittest.mock import MagicMock, patch
from tools.search_tool import get_yfinance_metrics

def test_get_yfinance_metrics_dividend_yield_percentage():
    # Test that a percentage dividend yield (e.g., 2.57) from yfinance is normalized and formatted as "2.57%"
    mock_stock = MagicMock()
    mock_stock.info = {
        "marketCap": 1000000000,
        "dividendYield": 2.57,
        "profitMargins": 0.15
    }
    
    with patch("yfinance.Ticker", return_value=mock_stock):
        result = get_yfinance_metrics.func("KO")
        assert "Dividend Yield: 2.57%" in result
        assert "Profit Margin: 15.00%" in result

def test_get_yfinance_metrics_dividend_yield_fraction():
    # Test that a fractional dividend yield (e.g., 0.0257) from yfinance is preserved and formatted as "2.57%"
    mock_stock = MagicMock()
    mock_stock.info = {
        "marketCap": 1000000000,
        "dividendYield": 0.0257,
        "profitMargins": 0.15
    }
    
    with patch("yfinance.Ticker", return_value=mock_stock):
        result = get_yfinance_metrics.func("KO")
        assert "Dividend Yield: 2.57%" in result
