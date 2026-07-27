from unittest.mock import patch, MagicMock
import pytest
from agents.technical_analyst import run_technical_analyst
from tools.search_tool import get_technical_analysis_data

@patch("agents.utils.execute_crew")
@patch("agents.utils.get_agent")
@patch("agents.utils.create_task")
def test_run_technical_analyst(mock_create_task, mock_get_agent, mock_execute_crew):
    mock_execute_crew.return_value = "Mocked Technical Output"
    mock_agent = MagicMock()
    mock_get_agent.return_value = mock_agent

    ticker = "AAPL"
    company_name = "Apple Inc."
    feedback = "Focus on the 200-day moving average."
    audit_context = "Audited financials."

    # Run the technical_analyst creator facade
    res = run_technical_analyst(
        ticker=ticker,
        company_name=company_name,
        feedback=feedback,
        audit_context=audit_context
    )

    # Asserts
    assert res == "Mocked Technical Output"
    mock_get_agent.assert_called_once_with("technical_analyst", True)
    mock_create_task.assert_called_once()
    
    # Verify task details
    args, _ = mock_create_task.call_args
    assert args[0] == 'technical_analysis_task'
    assert args[1] == mock_agent
    assert args[2]['ticker'] == "AAPL"
    assert args[2]['target_name'] == "Apple Inc. (AAPL)"
    assert args[2]['audit_context'] == audit_context
    assert "PASO 1" in args[3] # methodology loaded dynamically from agents.yaml
    assert args[4] == feedback


def test_get_technical_analysis_data_integration():
    # Use a real/popular ticker that always exists to verify the calculation doesn't raise exceptions
    res = get_technical_analysis_data.func("MSFT")
    assert "DATOS DE ANÁLISIS TÉCNICO Y TIMING" in res
    assert "Precio Actual" in res
    assert "RSI de 14 Días" in res
    assert "Media Móvil de 200 días" in res
