from agents.utils import get_current_date, create_agent_and_task
from file_manager import load_prompts_config


def run_rules_auditor(ticker: str, company_name: str = "", feedback: str = "", audit_context: str = "", rule_index: int = None,
                      pillar_metrics: str = ""):
    target_name = f"{company_name} ({ticker})" if company_name and company_name != ticker else ticker
    format_args = {
        'current_date': get_current_date(), 
        'target_name': target_name, 
        'ticker': ticker,
        'audit_context': audit_context,
        'rule_index': rule_index,
        'pillar_metrics': pillar_metrics or "(Sin cuentas estructuradas: no hay métricas calculadas.)",
        'rules_pillar_method': load_prompts_config()['rules_pillar_method'],
    }
    task_name = f"rules_pilar{rule_index + 1}_task" if rule_index is not None else "rules_auditor_task"
    return create_agent_and_task('rules_auditor', task_name, format_args, feedback)
