import json
import concurrent.futures
from agents.utils import get_current_date, get_agent, create_task, execute_crew

def run_forensic_auditor(ticker: str, company_name: str = "", raw_data: str = "", feedback: str = ""):
    target_name = f"{company_name} ({ticker})" if company_name and company_name != ticker else ticker

    # Parsear inputs
    try:
        data = json.loads(raw_data)
        income_text = data.get('income', '')
        balance_text = data.get('balance', '')
        cashflow_text = data.get('cashflow', '')
        raw_data_input = f"INCOME STATEMENT:\n{income_text}\n\nBALANCE SHEET:\n{balance_text}\n\nCASH FLOW:\n{cashflow_text}"
        
        has_audit_data = bool((income_text and income_text.strip()) or 
                              (balance_text and balance_text.strip()) or 
                              (cashflow_text and cashflow_text.strip()))
    except Exception:
        raw_data_input = raw_data
        income_text = balance_text = cashflow_text = raw_data
        has_audit_data = bool(raw_data and raw_data.strip())

    format_args = {
        'current_date': get_current_date(), 
        'target_name': target_name, 
        'ticker': ticker,
        'raw_data_input': raw_data_input
    }

    # 1. Crear Agentes Especializados
    results_agent = get_agent('results_analyst', has_audit_data)
    balance_agent = get_agent('balance_analyst', has_audit_data)
    cashflow_agent = get_agent('cashflow_analyst', has_audit_data)
    director_agent = get_agent('forensic_auditor', has_audit_data)

    # 2. Crear Tareas Individuales
    task_income = create_task('analyze_income_task', results_agent, {'target_name': target_name, 'raw_data_input': income_text}, "", feedback)
    task_balance = create_task('analyze_balance_task', balance_agent, {'target_name': target_name, 'raw_data_input': balance_text}, "", feedback)
    task_cashflow = create_task('analyze_cashflow_task', cashflow_agent, {'target_name': target_name, 'raw_data_input': cashflow_text}, "", feedback)

    try:
        # 3. Ejecutar los 3 analistas en paralelo
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_income = executor.submit(execute_crew, results_agent, task_income)
            future_balance = executor.submit(execute_crew, balance_agent, task_balance)
            future_cashflow = executor.submit(execute_crew, cashflow_agent, task_cashflow)
            
            res_income = future_income.result()
            res_balance = future_balance.result()
            res_cashflow = future_cashflow.result()

        # Inyectar los resultados en el contexto para la síntesis
        format_args['audit_context'] = f"--- RESULTADOS INCOME ---\n{res_income}\n\n--- RESULTADOS BALANCE ---\n{res_balance}\n\n--- RESULTADOS CASHFLOW ---\n{res_cashflow}"

        # 4. Tarea de síntesis final
        task_synthesis = create_task('forensic_audit_task', director_agent, format_args, "", feedback)
        res_synthesis = execute_crew(director_agent, task_synthesis)

        # Empaquetamos con delimitadores para que el parser lo separe
        final_combined = f"""
---AUDIT_INCOME---
{res_income}
---AUDIT_BALANCE---
{res_balance}
---AUDIT_CASHFLOW---
{res_cashflow}
---AUDIT_SYNTHESIS---
{res_synthesis}
"""
        return final_combined
    except Exception:
        raise
