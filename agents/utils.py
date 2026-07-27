import sys
import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

# Añadir el directorio raíz al path para que pueda importar módulos como file_manager o tools
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from crewai import Agent, Task, Crew
from file_manager import load_agent_config, load_task_config, load_prompts_config
from tools.search_tool import (
    search_internet,
    search_bull_bear_thesis,
    simulate_bull_bear_debate,
    search_financial_data,
    search_capital_allocation,
    get_yfinance_metrics,
    get_yfinance_financials,
    tool_generate_15y_financials,
    get_analyst_estimates,
    calculate_irr_scenarios,
    buscar_sec_filings,
    buscar_transcripcion_earnings,
    buscar_noticias_directivos,
    get_technical_analysis_data
)

_agents_cache: Dict[Tuple[str, bool], Agent] = {}

def get_tools_for_agent(agent_name: str, has_audit_data: bool = False):
    """Devuelve solo las herramientas específicas que necesita cada agente."""
    if agent_name in ['forensic_auditor', 'results_analyst', 'balance_analyst', 'cashflow_analyst']:
        tools = [get_yfinance_metrics, tool_generate_15y_financials, search_internet]
    elif agent_name == 'moat_analyst':
        tools = [search_bull_bear_thesis, simulate_bull_bear_debate, search_internet]
    elif agent_name == 'capital_allocation_specialist':
        tools = [search_capital_allocation, search_internet]
    elif agent_name == 'management_specialist':
        tools = [buscar_sec_filings, buscar_transcripcion_earnings, buscar_noticias_directivos, search_internet]
    elif agent_name in ['business_model', 'pe_forensic', 'thesis_destroyer']:
        tools = [search_internet]
    elif agent_name == 'investment_director':
        tools = [search_bull_bear_thesis, search_internet]
    elif agent_name == 'valuation_agent':
        tools = [get_yfinance_metrics, get_yfinance_financials, get_analyst_estimates, calculate_irr_scenarios, search_internet]
    elif agent_name == 'technical_analyst':
        tools = [get_technical_analysis_data, search_internet]
    else:
        tools = [search_internet]

    if has_audit_data:
        # Castración/Desarmado Dinámico de Herramientas Financieras redundantes si ya tenemos los datos auditados
        forbidden_tools = [
            search_financial_data,
            get_yfinance_metrics,
            get_yfinance_financials,
            tool_generate_15y_financials,
            search_capital_allocation,
            get_analyst_estimates
        ]
        # Filtrar herramientas excluyendo las prohibidas
        tools = [t for t in tools if t not in forbidden_tools]

    return tools

def get_llm():
    return f"gemini/{os.getenv('GEMINI_MODEL', 'gemini-flash-latest')}"

def get_current_date():
    return datetime.now().strftime("%A, %d de %B de %Y")

def get_agent(agent_name: str, has_audit_data: bool = False) -> Agent:
    """Creates and returns a new agent instance based on name and audit context."""
    agent_config = load_agent_config()[agent_name]

    agent = Agent(
        role=agent_config['role'],
        goal=agent_config['goal'],
        backstory=agent_config['backstory'],
        verbose=True,
        allow_delegation=False,
        tools=get_tools_for_agent(agent_name, has_audit_data),
        llm=get_llm(),
        max_iter=30
    )
    return agent

def create_task(task_name: str, agent: Agent, format_args: dict, methodology: str = "", feedback: str = "") -> Task:
    """Creates a task for a given agent."""
    task_config = load_task_config()[task_name]
    prompts = load_prompts_config()

    # Pass methodology into format_args so it can be rendered in the task description
    args_with_methodology = format_args.copy()
    args_with_methodology['methodology'] = methodology

    description = task_config['description'].format(**args_with_methodology)
    if feedback:
        description += f"\n\n════════════════════════════════════════════\nFEEDBACK DEL USUARIO PARA ESTA REGENERACIÓN:\n{feedback}\n\nDEBES APLICAR ESTAS DIRECTRICES Y CORRECCIONES EN TU ANÁLISIS.\n════════════════════════════════════════════"

    if task_name == 'valuation_agent_task':
        expected_output = task_config['expected_output'].format(**args_with_methodology) + "\n\n" + prompts['valuation_summary_instruction']
    elif task_name == 'technical_analysis_task':
        expected_output = task_config['expected_output'].format(**args_with_methodology) + "\n\n" + prompts['technical_summary_instruction']
    elif task_name in ['forensic_audit_task', 'business_model_task', 'moat_analyst_task', 'investment_director_task', 'evaluate_management_task', 'capital_allocation_task', 'rules_auditor_task', 'pe_forensic_task', 'thesis_task', 'thesis_destroyer_task'] or task_name.startswith('rules_pilar'):
        expected_output = task_config['expected_output'].format(**args_with_methodology) + "\n\n" + prompts['summary_instruction']
    else:
        # Para tareas intermedias especializadas de analistas, no forzamos la salida del XML de resumen
        expected_output = task_config['expected_output'].format(**args_with_methodology)

    task = Task(description=description, expected_output=expected_output, agent=agent)
    return task

def execute_crew(agent: Agent, task: Task) -> str:
    """Executes the crew with error handling."""
    logging.info(f"Iniciando ejecución de agente: {agent.role}")
    crew = Crew(agents=[agent], tasks=[task], verbose=True)
    try:
        result = crew.kickoff()
        logging.info(f"Ejecución completada con éxito.")
        return str(result)
    except Exception as e:
        logging.error(f"Error during crew kickoff: {e}", exc_info=True)
        raise

def create_agent_and_task(agent_name: str, task_name: str, format_args: dict, feedback: str = "") -> str:
    """Helper facade to coordinate agent creation, task creation, and execution."""
    agent_config = load_agent_config()[agent_name]
    methodology = agent_config.get('methodology', '')
    
    # Inyectar fechas relativas para no harcodear años en los prompts
    current_year = datetime.now().year
    format_args['current_year'] = current_year
    format_args['last_year'] = current_year - 1
    format_args['next_year'] = current_year + 1
    format_args['two_years_ago'] = current_year - 2

    # Cargar las normas personalizadas de evaluación desde la base de datos y compilarlas en Markdown
    try:
        from app.infrastructure.db.database import SessionLocal
        from app.infrastructure.dependencies import get_settings_from_db
        db = SessionLocal()
        try:
            settings = get_settings_from_db(db)
            custom_rules_data = settings.get("custom_evaluation_rules", [])
            
            if isinstance(custom_rules_data, list):
                rule_idx = format_args.get('rule_index')
                if rule_idx is not None and 0 <= rule_idx < len(custom_rules_data):
                    selected_rule = custom_rules_data[rule_idx]
                    title = selected_rule.get("title", "Norma")
                    content = selected_rule.get("content", "")
                    custom_rules = f"### {title}\n{content}"
                else:
                    compiled_rules = []
                    for rule in custom_rules_data:
                        title = rule.get("title", "Norma")
                        content = rule.get("content", "")
                        compiled_rules.append(f"### {title}\n{content}")
                    custom_rules = "\n\n".join(compiled_rules)
            else:
                custom_rules = str(custom_rules_data)
        finally:
            db.close()
    except Exception as e:
        logging.warning(f"No se pudieron cargar las normas de evaluación de la base de datos: {e}")
        custom_rules = ""

    format_args['custom_evaluation_rules'] = custom_rules

    # Formateamos la methodology aquí con los argumentos
    try:
        methodology = methodology.format(**format_args)
    except Exception as e:
        logging.warning(f"No se pudo formatear methodology completamente: {e}")
        
    audit_ctx = format_args.get('audit_context', '')
    has_audit_data = bool(audit_ctx and audit_ctx.strip())
    agent = get_agent(agent_name, has_audit_data)
    task = create_task(task_name, agent, format_args, methodology, feedback)
    return execute_crew(agent, task)
