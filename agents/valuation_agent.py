from agents.utils import get_current_date, create_agent_and_task

def run_valuation_agent(ticker: str, company_name: str = "", verdict_context: str = "", feedback: str = "", audit_context: str = ""):
    target_name = f"{company_name} ({ticker})" if company_name and company_name != ticker else ticker
    format_args = {
        'current_date': get_current_date(),
        'target_name': target_name,
        'ticker': ticker,
        'audit_context': audit_context,
        'verdict_context': verdict_context
    }
    return create_agent_and_task('valuation_agent', 'valuation_agent_task', format_args, feedback)
