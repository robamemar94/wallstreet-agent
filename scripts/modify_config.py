import yaml

# Modify agents.yaml
with open('config/agents.yaml', 'r', encoding='utf-8') as f:
    agents_data = yaml.safe_load(f)

moat_methodology_addon = """

TESIS DEL MERCADO (BULL VS BEAR):
- Es OBLIGATORIO usar la herramienta `search_bull_bear_thesis` para investigar qué dice el mercado (foros, analistas, inversores) sobre las ventajas y amenazas de la empresa.
- Debes exponer claramente los motivos de la tesis alcista y bajista.
- Si existe subjetividad importante o escenarios polarizados (ej. la tecnología puede hacerla obsoleta, pero también puede dominar el nuevo mercado), NO descartes automáticamente la empresa.
- En su lugar, da tu opinión y razonamiento experto, pero delega explícitamente la decisión final al usuario indicando: "RIESGO SUBJETIVO: Expongo los argumentos, pero el usuario debe decidir si asume este riesgo".
"""

if 'moat_analyst' in agents_data:
    agents_data['moat_analyst']['methodology'] += moat_methodology_addon

with open('config/agents.yaml', 'w', encoding='utf-8') as f:
    yaml.dump(agents_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

# Modify tasks.yaml
with open('config/tasks.yaml', 'r', encoding='utf-8') as f:
    tasks_data = yaml.safe_load(f)

if 'moat_analyst_task' in tasks_data:
    tasks_data['moat_analyst_task']['description'] = """Hoy es {current_date}. Realiza un análisis crítico de Moat para {target_name} siguiendo tu metodología de Brand, Network Effects, Cost Advantage, etc., detallada a continuación.

{methodology}

Usa la herramienta `search_bull_bear_thesis` para encontrar tesis bajistas y alcistas sobre el moat. Expón los motivos de ambas tesis y, en caso de subjetividad o escenarios polarizados, emite tu razonamiento experto pero deja claro que la decisión final de descarte la debe tomar el usuario. Evalúa la durabilidad y el riesgo de obsolescencia tecnológica. Responde las 4 PREGUNTAS FINALES antes de dar tu PUNTUACIÓN FINAL (0-10). ES OBLIGATORIO usar las herramientas de búsqueda para el contexto y no inventar datos."""

with open('config/tasks.yaml', 'w', encoding='utf-8') as f:
    yaml.dump(tasks_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

