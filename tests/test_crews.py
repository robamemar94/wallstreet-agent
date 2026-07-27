import pytest
from unittest.mock import patch, MagicMock
from agents.franchise_crew import run_franchise_crew
from agents.management_crew import run_management_crew

@patch("agents.franchise_crew.Crew")
@patch("agents.franchise_crew.get_agent")
@patch("agents.franchise_crew.create_task")
def test_run_franchise_crew(mock_create_task, mock_get_agent, mock_crew):
    mock_crew_instance = MagicMock()
    mock_crew.return_value = mock_crew_instance
    mock_crew_instance.kickoff.return_value = "Mocked Franchise Output"

    res = run_franchise_crew(
        ticker="MSFT",
        company_name="Microsoft Corp",
        feedback="Include AI integration details",
        audit_context="Good financial conversion"
    )

    assert res == "Mocked Franchise Output"
    mock_crew.assert_called_once()
    mock_get_agent.assert_any_call("business_model", True)
    mock_get_agent.assert_any_call("moat_analyst", True)
    assert mock_create_task.call_count == 2


@patch("agents.management_crew.Crew")
@patch("agents.management_crew.get_agent")
@patch("agents.management_crew.create_task")
def test_run_management_crew(mock_create_task, mock_get_agent, mock_crew):
    mock_crew_instance = MagicMock()
    mock_crew.return_value = mock_crew_instance
    mock_crew_instance.kickoff.return_value = "Mocked Management Output"

    res = run_management_crew(
        ticker="MSFT",
        company_name="Microsoft Corp",
        feedback="Verify CEO bonus criteria",
        audit_context="Good buybacks"
    )

    assert res == "Mocked Management Output"
    mock_crew.assert_called_once()
    mock_get_agent.assert_any_call("management_specialist", True)
    mock_get_agent.assert_any_call("capital_allocation_specialist", True)
    assert mock_create_task.call_count == 2
