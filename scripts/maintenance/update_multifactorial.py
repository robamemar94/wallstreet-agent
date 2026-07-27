import re

with open('config/agents.yaml', 'r') as f:
    content = f.read()

old_paso2 = """    ════════════════════════════════════════════
    PASO 2 — SÍNTESIS DE CALIDAD (Media Ponderada)
    ════════════════════════════════════════════
    La calidad general del negocio se calcula combinando matemáticamente los 3 pilares:
    - 40% Salud Financiera
    - 30% Economic Moat
    - 30% Capital Allocation
    
    Calcula el Score Ponderado exacto multiplicando cada nota por su peso. Este será tu Score Final."""

new_paso2 = """    ════════════════════════════════════════════
    PASO 2 — MATRIZ MULTIFACTORIAL (Arquetipo de Inversión)
    ════════════════════════════════════════════
    Calcula primero la media ponderada (40% Finanzas, 30% Moat, 30% Capital) para obtener el Score Final.
    Luego, cruza las tres puntuaciones obtenidas para clasificar a la empresa en uno de estos arquetipos:

    | Arquetipo | Condición (Puntuaciones) | Descripción |
    |-----------|--------------------------|-------------|
    | 💎 COMPOUNDER (Máquina de Componer) | Fin ≥ 7, Moat ≥ 7, Cap ≥ 7 | Negocio excepcional con gestión brillante. El Santo Grial. |
    | 👑 FRANQUICIA MALGESTIONADA | Moat ≥ 7, Cap ≤ 5 | Foso enorme pero el management destruye o retiene valor sin sentido. |
    | 🐄 VACA LECHERA (Cash Cow) | Fin ≥ 7, Moat ≤ 5, Cap ≥ 6 | Sin gran foso, pero imprime caja y recompensa al accionista. |
    | 🏗️ TURNAROUND (Reestructuración) | Fin ≤ 5, Moat ≥ 6 | Foso real pero balance o ciclo financiero tocado. Requiere paciencia. |
    | 🪤 TRAMPA DE VALOR (Value Trap) | Fin ≤ 5, Moat ≤ 5 | Negocio mediocre y finanzas débiles. Parece barata pero es un espejismo. |
    | 🧨 DESTRUCTOR DE CAPITAL | Cap ≤ 3 | El management activamente quema la caja en M&A ruinosa o dilución. |"""

content = content.replace(old_paso2, new_paso2)

with open('config/agents.yaml', 'w') as f:
    f.write(content)

with open('config/tasks.yaml', 'r') as f:
    task_content = f.read()

old_matriz = """    ## 3. MATRIZ DE CALIDAD GLOBAL
    - **Score Ponderado:** X.X / 10 (40% Fin, 30% Moat, 30% Cap)
    - **Calidad del Negocio:** [EXCEPCIONAL (9-10) / SÓLIDO (7-8) / PROMEDIO (5-6) / DEFICIENTE (3-4) / IN-INVERTIBLE (<3)]"""

new_matriz = """    ## 3. MATRIZ MULTIFACTORIAL Y CALIDAD GLOBAL
    - **Score Ponderado Final:** X.X / 10 (40% Fin, 30% Moat, 30% Cap)
    - **Arquetipo de Inversión:** [Ej: 💎 COMPOUNDER / 👑 FRANQUICIA MALGESTIONADA / etc.]
    - **Justificación del Arquetipo:** [Explica brevemente por qué encaja en este perfil según el cruce específico de sus 3 notas]."""

task_content = task_content.replace(old_matriz, new_matriz)

with open('config/tasks.yaml', 'w') as f:
    f.write(task_content)

print("Updated multifactorial matrix")
