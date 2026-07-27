from unittest.mock import patch, MagicMock
from agents.thesis_destroyer import run_thesis_destroyer

@patch("agents.utils.execute_crew")
@patch("agents.utils.get_agent")
@patch("agents.utils.create_task")
def test_run_thesis_destroyer(mock_create_task, mock_get_agent, mock_execute_crew):
    mock_execute_crew.return_value = "Mocked Destroyer Output"
    mock_agent = MagicMock()
    mock_get_agent.return_value = mock_agent

    ticker = "AAPL"
    company_name = "Apple Inc."
    feedback = "Focus on software/AI risks."
    audit_context = "Audited financials."
    verdict_context = "Positive verdict."
    thesis_context = "Bull thesis."
    franchise_context = "Franchise and economic moat analysis."
    mgmt_alloc_context = "Management and capital allocation details."
    rules_context = "Rules compliance report."

    # Run the thesis_destroyer creator facade
    res = run_thesis_destroyer(
        ticker=ticker,
        company_name=company_name,
        verdict_context=verdict_context,
        thesis_context=thesis_context,
        franchise_context=franchise_context,
        mgmt_alloc_context=mgmt_alloc_context,
        feedback=feedback,
        audit_context=audit_context,
        rules_context=rules_context
    )

    # Asserts
    assert res == "Mocked Destroyer Output"
    mock_get_agent.assert_called_once_with("thesis_destroyer", True)
    mock_create_task.assert_called_once()
    
    # Verify that first parameter of mock_create_task is 'thesis_destroyer_task'
    args, kwargs = mock_create_task.call_args
    assert args[0] == 'thesis_destroyer_task'
    assert args[1] == mock_agent
    assert args[2]['ticker'] == "AAPL"
    assert args[2]['target_name'] == "Apple Inc. (AAPL)"
    assert args[2]['audit_context'] == audit_context
    assert args[2]['verdict_context'] == verdict_context
    assert args[2]['thesis_context'] == thesis_context
    assert args[2]['franchise_context'] == franchise_context
    assert args[2]['mgmt_alloc_context'] == mgmt_alloc_context
    assert args[2]['rules_context'] == rules_context
    assert "PASO 1" in args[3] # methodology loaded dynamically from agents.yaml
    assert args[4] == feedback
