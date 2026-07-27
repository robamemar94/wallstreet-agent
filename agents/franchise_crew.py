from agents.utils import get_current_date, get_agent, create_task, execute_crew, load_agent_config
from crewai import Crew
import logging

def run_franchise_crew(ticker: str, company_name: str = "", feedback: str = "", audit_context: str = ""):
    target_name = f"{company_name} ({ticker})" if company_name and company_name != ticker else ticker
    format_args = {
        'current_date': get_current_date(), 
        'target_name': target_name, 
        'ticker': ticker,
        'audit_context': audit_context
    }
    
    # 1. Cargar agentes y metodologías
    has_audit_data = bool(audit_context and audit_context.strip())
    
    agent_bm = get_agent('business_model', has_audit_data)
    methodology_bm = load_agent_config()['business_model'].get('methodology', '').format(**format_args)
    task_bm = create_task('business_model_task', agent_bm, format_args, methodology_bm, feedback)
    
    agent_moat = get_agent('moat_analyst', has_audit_data)
    methodology_moat = load_agent_config()['moat_analyst'].get('methodology', '').format(**format_args)
    task_moat = create_task('moat_analyst_task', agent_moat, format_args, methodology_moat, feedback)
    
    # 2. Crear Crew secuencial
    logging.info(f"Iniciando Crew de Franquicia (Modelo de Negocio + Moat) para {ticker}")
    crew = Crew(
        agents=[agent_bm, agent_moat],
        tasks=[task_bm, task_moat],
        verbose=True
    )
    
    try:
        result = crew.kickoff()
        logging.info("Crew de Franquicia completada con éxito.")
        return str(result)
    except Exception as e:
        logging.error(f"Error during franchise crew kickoff: {e}", exc_info=True)
        raise
