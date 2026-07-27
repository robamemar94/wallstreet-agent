import pytest
from unittest.mock import patch, MagicMock
from agents.investment_director import run_investment_director

@patch("agents.investment_director.create_agent_and_task")
def test_run_investment_director_passes_rules_context(mock_create_agent_and_task):
    mock_create_agent_and_task.return_value = "Mocked Verdict Output"

    res = run_investment_director(
        ticker="AAPL",
        company_name="Apple Inc",
        franchise_context="Franchise and Moat details",
        mgmt_alloc_context="Management and Capital allocation details",
        feedback="Include more details",
        audit_context="Clean audit",
        rules_context="Rules audit: Complies with 10/11 rules"
    )

    assert res == "Mocked Verdict Output"
    mock_create_agent_and_task.assert_called_once()
    
    # Verify that mock_create_agent_and_task was called with expected arguments
    args, kwargs = mock_create_agent_and_task.call_args
    assert args[0] == "investment_director"
    assert args[1] == "investment_director_task"
    
    format_args = args[2]
    assert format_args["ticker"] == "AAPL"
    assert format_args["target_name"] == "Apple Inc (AAPL)"
    assert format_args["franchise_context"] == "Franchise and Moat details"
    assert format_args["mgmt_alloc_context"] == "Management and Capital allocation details"
    assert format_args["audit_context"] == "Clean audit"
    assert format_args["rules_context"] == "Rules audit: Complies with 10/11 rules"
    
    assert args[3] == "Include more details"
