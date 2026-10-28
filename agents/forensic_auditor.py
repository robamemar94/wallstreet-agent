import json
import contextvars
import concurrent.futures
from agents.utils import get_current_date, get_agent, create_task, execute_crew
from file_manager import load_prompts_config

STATEMENTS = (("income", "CUENTA DE RESULTADOS"), ("balance", "BALANCE"), ("cashflow", "FLUJO DE CAJA"))


def _others(texts: dict, own: str) -> str:
    """Los otros dos estados, para que cada analista pueda cruzar el suyo con ellos."""
    parts = [f"{label}:\n{texts[key]}" for key, label in STATEMENTS if key != own and (texts.get(key) or "").strip()]
    return "\n\n".join(parts) or "(no disponibles)"


def run_forensic_auditor(ticker: str, company_name: str = "", raw_data: str = "", feedback: str = "", ratios_context: str = ""):
    target_name = f"{company_name} ({ticker})" if company_name and company_name != ticker else ticker

    # Parsear inputs
    try:
        data = json.loads(raw_data)
        texts = {key: data.get(key, '') or '' for key, _ in STATEMENTS}
        raw_data_input = "\n\n".join(f"{label}:\n{texts[key]}" for key, label in STATEMENTS)
        has_audit_data = any(t.strip() for t in texts.values())
    except Exception:
        raw_data_input = raw_data
        texts = dict.fromkeys((key for key, _ in STATEMENTS), raw_data)
        has_audit_data = bool(raw_data and raw_data.strip())

    prompts = load_prompts_config()
    shared = {
        'current_date': get_current_date(),
        'target_name': target_name,
        'ticker': ticker,
        'ratios_context': ratios_context or "(Sin ratios calculados: no hay cuentas estructuradas.)",
        'forensic_data_rules': prompts['forensic_data_rules'],
        'forensic_red_flag_rule': prompts['forensic_red_flag_rule'],
        'forensic_score_scale': prompts['forensic_score_scale'],
    }

    # 1. Crear Agentes Especializados
    results_agent = get_agent('results_analyst', has_audit_data)
    balance_agent = get_agent('balance_analyst', has_audit_data)
    cashflow_agent = get_agent('cashflow_analyst', has_audit_data)
    director_agent = get_agent('forensic_auditor', has_audit_data)

    # 2. Tareas: cada analista audita su estado y ve los otros dos como contexto (para cruzarlos)
    def analyst_task(task_name, agent, key):
        args = {**shared, 'raw_data_input': texts[key], 'other_statements': _others(texts, key)}
        return create_task(task_name, agent, args, "", feedback)

    task_income = analyst_task('analyze_income_task', results_agent, 'income')
    task_balance = analyst_task('analyze_balance_task', balance_agent, 'balance')
    task_cashflow = analyst_task('analyze_cashflow_task', cashflow_agent, 'cashflow')

    # 3. Ejecutar los 3 analistas en paralelo. Cada hilo lleva una copia del contexto (contextvars) para que el
    # contador de tokens (utils.llm_usage) apunte sus llamadas a este informe.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(contextvars.copy_context().run, execute_crew, agent, task)
                   for agent, task in ((results_agent, task_income), (balance_agent, task_balance),
                                       (cashflow_agent, task_cashflow))]
        res_income, res_balance, res_cashflow = (f.result() for f in futures)

    # 4. Síntesis: los tres informes + las cuentas + los ratios (para cruzar estados y corregir a los analistas)
    synthesis_args = {
        **shared,
        'raw_data_input': raw_data_input,
        'audit_context': f"--- RESULTADOS INCOME ---\n{res_income}\n\n--- RESULTADOS BALANCE ---\n{res_balance}\n\n--- RESULTADOS CASHFLOW ---\n{res_cashflow}",
    }
    task_synthesis = create_task('forensic_audit_task', director_agent, synthesis_args, "", feedback)
    res_synthesis = execute_crew(director_agent, task_synthesis)

    # Empaquetamos con delimitadores para que el parser lo separe
    return f"""
---AUDIT_INCOME---
{res_income}
---AUDIT_BALANCE---
{res_balance}
---AUDIT_CASHFLOW---
{res_cashflow}
---AUDIT_SYNTHESIS---
{res_synthesis}
"""
