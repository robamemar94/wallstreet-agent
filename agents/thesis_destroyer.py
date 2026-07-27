from agents.utils import get_current_date, create_agent_and_task

def run_thesis_destroyer(ticker: str, company_name: str = "", verdict_context: str = "", thesis_context: str = "", franchise_context: str = "", mgmt_alloc_context: str = "", feedback: str = "", audit_context: str = "", rules_context: str = ""):
    target_name = f"{company_name} ({ticker})" if company_name and company_name != ticker else ticker
    format_args = {
        'current_date': get_current_date(),
        'target_name': target_name,
        'ticker': ticker,
        'audit_context': audit_context,
        'verdict_context': verdict_context,
        'thesis_context': thesis_context,
        'franchise_context': franchise_context,
        'mgmt_alloc_context': mgmt_alloc_context,
        'rules_context': rules_context,
    }
    return create_agent_and_task('thesis_destroyer', 'thesis_destroyer_task', format_args, feedback)
