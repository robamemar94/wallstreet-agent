from unittest.mock import patch, MagicMock
from agents.pe_forensic import run_pe_forensic

@patch("agents.utils.execute_crew")
@patch("agents.utils.get_agent")
@patch("agents.utils.create_task")
def test_run_pe_forensic(mock_create_task, mock_get_agent, mock_execute_crew):
    mock_execute_crew.return_value = "Mocked PE Forensic Analysis Output"
    mock_agent = MagicMock()
    mock_get_agent.return_value = mock_agent

    ticker = "AAPL"
    company_name = "Apple Inc."
    feedback = "Focus on the cash balance and inventory."
    audit_context = "Audited Income Statement and Cash Flow for Apple."

    # Run the pe_forensic agent creator facade
    res = run_pe_forensic(ticker, company_name, feedback, audit_context)

    # Asserts
    assert res == "Mocked PE Forensic Analysis Output"
    mock_get_agent.assert_called_once_with("pe_forensic", True)
    mock_create_task.assert_called_once()
    
    # Verify that first parameter of mock_create_task is 'pe_forensic_task'
    args, kwargs = mock_create_task.call_args
    assert args[0] == 'pe_forensic_task'
    assert args[1] == mock_agent
    assert args[2]['ticker'] == "AAPL"
    assert args[2]['target_name'] == "Apple Inc. (AAPL)"
    assert args[2]['audit_context'] == audit_context
    assert "PASO 0" in args[3] # methodology loaded dynamically from agents.yaml
    assert args[4] == feedback
