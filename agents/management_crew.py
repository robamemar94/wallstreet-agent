from agents.utils import get_current_date, get_agent, create_task, execute_crew, load_agent_config
from crewai import Crew
import logging

def run_management_crew(ticker: str, company_name: str = "", feedback: str = "", audit_context: str = ""):
    target_name = f"{company_name} ({ticker})" if company_name and company_name != ticker else ticker
    format_args = {
        'current_date': get_current_date(), 
        'target_name': target_name, 
        'ticker': ticker,
        'audit_context': audit_context
    }
    
    # 1. Cargar agentes y metodologías
    has_audit_data = bool(audit_context and audit_context.strip())
    
    agent_mgmt = get_agent('management_specialist', has_audit_data)
    methodology_mgmt = load_agent_config()['management_specialist'].get('methodology', '').format(**format_args)
    task_mgmt = create_task('evaluate_management_task', agent_mgmt, format_args, methodology_mgmt, feedback)
    
    agent_cap = get_agent('capital_allocation_specialist', has_audit_data)
    methodology_cap = load_agent_config()['capital_allocation_specialist'].get('methodology', '').format(**format_args)
    task_cap = create_task('capital_allocation_task', agent_cap, format_args, methodology_cap, feedback)
    
    # 2. Crear Crew secuencial
    logging.info(f"Iniciando Crew de Dirección y Asignación de Capital para {ticker}")
    crew = Crew(
        agents=[agent_mgmt, agent_cap],
        tasks=[task_mgmt, task_cap],
        verbose=True
    )
    
    try:
        result = crew.kickoff()
        logging.info("Crew de Dirección completada con éxito.")
        return str(result)
    except Exception as e:
        logging.error(f"Error during management crew kickoff: {e}", exc_info=True)
        raise
