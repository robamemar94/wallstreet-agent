from unittest.mock import patch, MagicMock
from agents.thesis_agent import run_thesis_agent

@patch("agents.utils.execute_crew")
@patch("agents.utils.get_agent")
@patch("agents.utils.create_task")
def test_run_thesis_agent(mock_create_task, mock_get_agent, mock_execute_crew):
    mock_execute_crew.return_value = "Mocked Thesis Output"
    mock_agent = MagicMock()
    mock_get_agent.return_value = mock_agent

    ticker = "AAPL"
    company_name = "Apple Inc."
    feedback = "Include extra details on margin monitoring."
    audit_context = "Audited financials."
    verdict_context = "Positive verdict."
    valuation_context = "Valuation scenarios."
    franchise_context = "Franchise and economic moat analysis."
    mgmt_alloc_context = "Management and capital allocation details."
    rules_context = "Rules compliance report."

    # Run the thesis_agent creator facade
    res = run_thesis_agent(
        ticker=ticker,
        company_name=company_name,
        verdict_context=verdict_context,
        valuation_context=valuation_context,
        franchise_context=franchise_context,
        mgmt_alloc_context=mgmt_alloc_context,
        feedback=feedback,
        audit_context=audit_context,
        rules_context=rules_context
    )

    # Asserts
    assert res == "Mocked Thesis Output"
    mock_get_agent.assert_called_once_with("thesis_agent", True)
    mock_create_task.assert_called_once()
    
    # Verify that first parameter of mock_create_task is 'thesis_task'
    args, kwargs = mock_create_task.call_args
    assert args[0] == 'thesis_task'
    assert args[1] == mock_agent
    assert args[2]['ticker'] == "AAPL"
    assert args[2]['target_name'] == "Apple Inc. (AAPL)"
    assert args[2]['audit_context'] == audit_context
    assert args[2]['verdict_context'] == verdict_context
    assert args[2]['valuation_context'] == valuation_context
    assert args[2]['franchise_context'] == franchise_context
    assert args[2]['mgmt_alloc_context'] == mgmt_alloc_context
    assert args[2]['rules_context'] == rules_context
    assert args[2]['fin_context'] == franchise_context
    assert args[2]['moat_context'] == franchise_context
    assert args[2]['mgmt_context'] == mgmt_alloc_context
    assert args[2]['cap_context'] == mgmt_alloc_context
    assert "PASO 1" in args[3] # methodology loaded dynamically from agents.yaml
    assert args[4] == feedback
