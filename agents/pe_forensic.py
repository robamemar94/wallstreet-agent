from agents.utils import get_current_date, create_agent_and_task

def run_pe_forensic(ticker: str, company_name: str = "", feedback: str = "", audit_context: str = ""):
    target_name = f"{company_name} ({ticker})" if company_name and company_name != ticker else ticker
    format_args = {
        'current_date': get_current_date(), 
        'target_name': target_name, 
        'ticker': ticker,
        'audit_context': audit_context
    }
    return create_agent_and_task('pe_forensic', 'pe_forensic_task', format_args, feedback)
