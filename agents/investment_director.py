from agents.utils import get_current_date, create_agent_and_task

def run_investment_director(ticker: str, company_name: str = "", franchise_context: str = "", mgmt_alloc_context: str = "", feedback: str = "", audit_context: str = "", rules_context: str = ""):
    target_name = f"{company_name} ({ticker})" if company_name and company_name != ticker else ticker
    format_args = {
        'current_date': get_current_date(),
        'target_name': target_name,
        'ticker': ticker,
        'audit_context': audit_context,
        'franchise_context': franchise_context,
        'mgmt_alloc_context': mgmt_alloc_context,
        'rules_context': rules_context
    }
    return create_agent_and_task('investment_director', 'investment_director_task', format_args, feedback)
