import os
import json
import yfinance as yf
from datetime import datetime
from crewai.tools import tool
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

def parse_manual_audit_to_financials(ticker: str, raw_text: str, images: dict = None) -> dict:
    """Convierte texto e imágenes en una estructura COMPLETA y fiel de los estados financieros."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key: return {"error": "API Key missing"}
        client = genai.Client(api_key=api_key)
        
        contents = []
        prompt = f"""
Eres un experto en contabilidad y extracción de datos. Tu misión es transcribir fielmente TODOS los datos financieros proporcionados para el ticker {ticker}.

REQUISITOS DE EXTRACCIÓN (CRÍTICOS):
1. UNIDADES: Todos los valores deben ser números ABSOLUTOS (ej: si la tabla dice 1,200 y está en millones, debes devolver 1200000000). Es decir, multiplica por 1M o 1B según corresponda.
2. ORDEN: El array "years" debe estar en orden CRONOLÓGICO ASCENDENTE (ej: ["2015", "2016", ..., "LTM"]).
3. INTEGRIDAD: Extrae CADA FILA (métrica) que encuentres en los 3 estados financieros.
4. NOMBRES: Mantén los nombres originales de las métricas.

ESTRUCTURA DE RESPUESTA REQUERIDA (JSON):
{{
  "years": ["2020", "2021", "2022", ...],
  "income_statement": {{
     "Revenue": [val1, val2, ...],
     "Cost of Revenue": [val1, val2, ...],
     ... (todas las filas encontradas)
  }},
  "balance_sheet": {{
     "Total Assets": [val1, val2, ...],
     ... (todas las filas encontradas)
  }},
  "cash_flow": {{
     "Net Income": [val1, val2, ...],
     "SBC": [val1, val2, ...],
     ... (todas las filas encontradas)
  }},
  "chart_metrics": {{
     "revenue": [val1, val2, ...],
     "net_income": [val1, val2, ...],
     "eps": [val1, val2, ...],
     "fcf": [val1, val2, ...]
  }}
}}

REGLAS CRÍTICAS:
- El objeto "chart_metrics" es OBLIGATORIO para las gráficas del sistema.
- Todos los arrays de una sección deben tener la MISMA longitud que el array de "years".
- DEVUELVE ÚNICAMENTE EL JSON.
"""
        contents.append(prompt)
        
        # Añadir textos de las 3 áreas si están disponibles de forma más estructurada
        # (raw_text ya viene combinado del endpoint)
        contents.append(f"DATOS PROPORCIONADOS:\n{raw_text}")

        if images:
            for img_type, b64_data in images.items():
                if b64_data:
                    contents.append(types.Part.from_bytes(data=b64_data, mime_type='image/png'))

        response = client.models.generate_content(
            model='gemini-flash-latest',
            contents=contents,
            config=types.GenerateContentConfig(temperature=0.1)
        )
        text = response.text.strip()
        
        if "```json" in text: text = text.split("```json")[1].split("```")[0]
        elif "```" in text: text = text.split("```")[1].split("```")[0]
        
        data = json.loads(text.strip())
        return data
    except Exception as e:
        logger.error(f"Error parseando estructura completa: {e}")
        return {"error": str(e)}

def generate_15y_financials_json(ticker: str) -> dict:
    """Busca y genera un JSON estricto con 15 años de datos financieros (Revenue, Net Income, EPS, FCF)."""
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return {"error": "GEMINI_API_KEY no configurada."}

        client = genai.Client(api_key=api_key)
        
        current_dt = datetime.now()
        current_date = current_dt.strftime("%A, %d de %B de %Y")
        
        # Calculate exactly 15 years ending in the previous year
        last_year = current_dt.year - 1
        start_year = last_year - 14
        years_list = [str(y) for y in range(start_year, last_year + 1)]
        years_json_str = json.dumps(years_list)
        
        prompt = f"""
Actúa como una base de datos financiera. Necesito el historial financiero de los últimos 15 años (hasta {last_year}) para la empresa con ticker {ticker}.

Solo necesito estos 4 valores por año:
- Ingresos Totales (Revenue)
- Beneficio Neto (Net Income)
- Beneficio Por Acción (EPS)
- Flujo de Caja Libre (Free Cash Flow - FCF)

DEVUELVE ÚNICAMENTE UN JSON VÁLIDO. SIN MARKDOWN. SIN TEXTO EXTRA.
ESTRUCTURA DEL JSON:
{{
  "years": ["2010", "2011", ... hasta "{last_year}"],
  "revenue": [valor1, valor2, ...],
  "net_income": [valor1, valor2, ...],
  "eps": [valor1, valor2, ...],
  "fcf": [valor1, valor2, ...]
}}

Asegúrate de que los arrays tengan la misma longitud. Usa números (sin símbolos ni letras). Si falta un año, pon 0.
"""
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1
            )
        )
        
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        
        return json.loads(text)
    except Exception as e:
        return {"error": str(e)}

@tool("generate_15y_financials_json")
def tool_generate_15y_financials(ticker: str) -> str:
    """Busca y genera un JSON estricto con 15 años de datos financieros (Revenue, Net Income, EPS, FCF)."""
    return json.dumps(generate_15y_financials_json(ticker))

def generate_fair_pe_json(ticker: str, company_name: str = "") -> dict:
    """Calcula el Fair Forward P/E basado en medias históricas y crecimiento futuro, devolviendo un único valor y status."""
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return {"error": "GEMINI_API_KEY no configurada."}

        client = genai.Client(api_key=api_key)
        current_date = datetime.now().strftime("%A, %d de %B de %Y")
        
        target = f"{company_name} ({ticker})" if company_name else ticker

        prompt = f"""
Hoy es {current_date}. Eres un analista de valoración fundamental muy estricto pero que entiende de ventajas competitivas estructurales.
Tu objetivo es calcular el "Fair Forward P/E" (PER Forward Justo de un único valor) para la empresa {target} y determinar si la acción está cara o barata.

INSTRUCCIONES DE ANÁLISIS Y SÍNTESIS ANALÍTICA:
1. HISTÓRICO: Busca la media histórica del Forward P/E de los últimos 10 años para {target}. Concéntrate principalmente en un horizonte largo de 10 años (en lugar de solo 5 años) para obtener una referencia más robusta, prudente y representativa de todo el ciclo económico. Excluye picos irracionales de burbuja clara.
2. CRECIMIENTO ESPERADO (Base Matemática): Evalúa el crecimiento de beneficios (EPS growth) esperado para los próximos 3-5 años. 
   - El crecimiento dicta la gravedad del múltiplo. Una desaceleración estructural respecto a la década pasada requiere un ajuste a la baja del múltiplo histórico.
3. PREMIUM POR CALIDAD (MOAT EXTREMO): Evalúa el foso económico, ROIC, márgenes y poder de fijación de precios de {company_name if company_name else ticker}.
   - Las empresas con ventajas competitivas duraderas, marcas inelásticas y retornos sobre el capital muy elevados merecen cotizar con una prima estructural permanente sobre su tasa de crecimiento.
4. EL PUNTO MEDIO JUSTO: 
   - No te limites a la media histórica si el crecimiento ha bajado.
   - No te limites al crecimiento (PEG) si la calidad es extrema.
   - Debes encontrar un equilibrio analítico: Si una empresa de altísima calidad cotizaba históricamente a múltiplos muy elevados (ej. 45x), pero su crecimiento se ha moderado a niveles del 10-14%, el valor justo debe reflejar esa nueva realidad de crecimiento pero reteniendo una prima de calidad importante. El múltiplo debe situarse en un punto que sea prudente pero reconozca que el mercado nunca la valorará a niveles de una empresa normal.
5. VALOR JUSTO (fair_forward_pe): Define tu ÚNICO valor exacto basándote en esta síntesis. No hay techos arbitrarios, pero cada punto de múltiplo por encima del crecimiento debe estar justificado por la durabilidad del moat.
6. VALORACIÓN ACTUAL Y STATUS: Busca el Forward P/E real actual del mercado (A) para {ticker} y compáralo con tu valor justo (F):
   - Si A es menor que F por más de un 40% -> "Ganga"
   - Si A es menor que F entre un 15% y 40% -> "Barata"
   - Si A está a +/- 15% de F -> "Precio Justo"
   - Si A es mayor que F entre un 15% y 40% -> "Cara"
   - Si A es mayor que F por más de un 40% -> "Muy Cara / Burbuja"

IMPORTANTE: DEVUELVE ÚNICA Y EXCLUSIVAMENTE UN JSON VÁLIDO. NADA DE MARKDOWN.

ESTRUCTURA EXACTA DEL JSON:
{{
  "historical_pe": [float],
  "sector_pe": [float],
  "fair_forward_pe": [float, tu veredicto final exacto],
  "current_forward_pe": [float, el actual de mercado],
  "valuation_status": "[String exacto: Ganga | Barata | Precio Justo | Cara | Muy Cara / Burbuja]",
  "rationale": "[Explicación concisa justificando cómo has equilibrado la desaceleración o aceleración del crecimiento con la prima por calidad del moat.]"
}}
"""
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                temperature=0.1
            )
        )
        
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        
        return json.loads(text)
    except Exception as e:
        return {"error": str(e)}
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                temperature=0.1
            )
        )
        
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        
        return json.loads(text)
    except Exception as e:
        return {"error": str(e)}

def generate_projection_scenarios_json(ticker: str, company_name: str = "", suggested_pe: float = None) -> dict:
    """Calcula las hipótesis estimadas para proyecciones de retorno a 5 años (crecimiento BPA, múltiplos PER y crecimiento de dividendos)
    para 3 escenarios (Pesimista, Base, Optimista) basándose en fundamentales históricos, sectoriales y un BPA Normalizado.
    """
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return {"error": "GEMINI_API_KEY no configurada."}

        client = genai.Client(api_key=api_key)
        current_date = datetime.now().strftime("%A, %d de %B de %Y")
        
        target = f"{company_name} ({ticker})" if company_name else ticker

        pe_instruction = ""
        if suggested_pe:
            pe_instruction = f"\n- MÚLTIPLO PER SUGERIDO: El sistema de valoración o el usuario ha establecido un PER justo de {suggested_pe}. DEBES utilizar este valor exactamente como el 'exit_pe' de tu Escenario Base, ajustando el escenario Bear sustancialmente por debajo y el Bull por encima."

        prompt = f"""
Hoy es {current_date}. Eres un analista de valoración fundamental de primer nivel (estilo Warren Buffett / Terry Smith).
Tu objetivo es sugerir hipótesis realistas, prudentes e inteligentes de crecimiento anual del BPA (EPS), crecimiento anual de dividendos y múltiplos PER de salida para los próximos 5 años para la empresa {target}.
Debes formular estas hipótesis para 3 escenarios bien diferenciados:
1. Escenario Pesimista (Bear): Un escenario de desaceleración económica, aumento de la competencia, pérdida de márgenes o problemas operativos. Crecimiento de BPA muy bajo o nulo, contracción del múltiplo PER, y dividendos con crecimiento estancado.
2. Escenario Base (Base): El escenario más probable. Crecimiento de BPA y dividendos alineado con el consenso de analistas y la tendencia histórica ajustada por ley de grandes números, con un múltiplo PER razonable y acorde con la calidad de su Moat.
3. Escenario Optimista (Bull): Un escenario macroeconómico favorable, éxito en nuevos productos, expansión de márgenes o fuerte recompra de acciones. Crecimiento de BPA y dividendos elevado y múltiplo PER premium.

INSTRUCCIONES ANALÍTICAS CRÍTICAS:
- EVITAR ANORMALIDADES EN EL BPA INICIAL (BPA NORMALIZADO DEL AÑO EN CURSO):
  * Debes buscar específicamente el BPA (EPS) estimado de consenso para el año fiscal actual (o el BPA normalizado/sostenible). Este será tu punto de partida (Año 0) sobre el cual aplicarás las tasas de crecimiento para los próximos 5 años.
  * Analiza con extremo rigor si este BPA estimado es "anormal" o distorsionado debido a extraordinarios, ciclos extremos, pérdidas de arranque, amortizaciones o costes de reestructuración.
  * Si es anormal o negativo, calcula un BPA Sostenible Normalizado (por ejemplo, aplicando el margen neto histórico promedio de la empresa sobre las ventas proyectadas, o haciendo una media de los BPA recurrentes de los últimos años).
  * Asigna este valor del año fiscal actual (real o normalizado sostenible) al campo "initial_eps" en el JSON. Explica detalladamente en "normalized_eps_explanation" cómo has calculado o verificado este BPA de partida y por qué representa una base de simulación sana.
- CRECIMIENTO DE DIVIDENDOS (div_growth):
  * Sugiere una tasa anual de crecimiento de dividendos realista para cada escenario. Debe estar alineada con el crecimiento del BPA y el nivel de payout de la empresa.{pe_instruction}
- CALIDAD DEL MOAT: Si la empresa es de altísima calidad, los múltiplos PER de salida del escenario base y optimista deben reflejar esta prima de calidad (no bajar de manera absurda), mientras que si es cíclica los múltiplos deben ser mucho más conservadores.

IMPORTANTE: DEVUELVE ÚNICA Y EXCLUSIVAMENTE UN JSON VÁLIDO. NADA DE MARKDOWN.

ESTRUCTURA EXACTA DEL JSON:
{{
  "initial_eps": [float, el BPA de partida sostenible/normalizado que sugieres, ej. 6.25],
  "normalized_eps_explanation": "[Explicación detallada de cómo has calculado o verificado el BPA inicial, indicando si el BPA actual de mercado era anormal/negativo y por qué este nuevo valor es el punto de partida sano]",
  "bear": {{
    "eps_growth": [float, tasa de crecimiento anual del BPA expresada en decimal, ej: 0.05 para 5%],
    "exit_pe": [float, múltiplo PER de salida al año 5, ej: 15.0],
    "div_growth": [float, crecimiento anual del dividendo en decimal, ej: 0.02 para 2%]
  }},
  "base": {{
    "eps_growth": [float, tasa de crecimiento anual del BPA expresada en decimal, ej: 0.11 para 11%],
    "exit_pe": [float, múltiplo PER de salida al año 5, ej: 20.0],
    "div_growth": [float, crecimiento anual del dividendo en decimal, ej: 0.08 para 8%]
  }},
  "bull": {{
    "eps_growth": [float, tasa de crecimiento anual del BPA expresada en decimal, ej: 0.16 para 16%],
    "exit_pe": [float, múltiplo PER de salida al año 5, ej: 25.0],
    "div_growth": [float, crecimiento anual del dividendo en decimal, ej: 0.12 para 12%]
  }},
  "rationale": "[Explicación concisa y profesional justificando las hipótesis elegidas para cada escenario basándose en la calidad del negocio, múltiplos históricos y estimaciones de analistas.]"
}}
"""
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                temperature=0.1
            )
        )
        
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        
        return json.loads(text)
    except Exception as e:
        return {"error": str(e)}

@tool("search")
def search_internet(query: str) -> str:
    """Buscador para encontrar información financiera actualizada, reportes 10-K, ratios, y noticias sobre cualquier empresa. Úsalo SIEMPRE para buscar datos de 2025 y 2026."""
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return "Error: GEMINI_API_KEY no configurada."
            
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"Busca en internet la respuesta más detallada y precisa para esta consulta: {query}",
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                temperature=0.1
            )
        )
        return response.text
    except Exception as e:
        return f"Error al realizar la búsqueda web: {e}"

@tool("search_bull_bear_thesis")
def search_bull_bear_thesis(ticker: str) -> str:
    """
    Busca tesis bajistas y alcistas en fuentes especializadas: Seeking Alpha,
    Motley Fool, Morningstar, Reddit (r/investing, r/SecurityAnalysis),
    Value Investors Club, y análisis de fondos value conocidos.
    Retorna un resumen estructurado BULL / BEAR con fuentes citadas.
    """
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return "Error: GEMINI_API_KEY no configurada."

        client = genai.Client(api_key=api_key)

        prompt = f"""
Eres un analista de inversiones. Busca en internet información RECIENTE y ESPECÍFICA 
sobre {ticker} en estas fuentes prioritarias:

FUENTES PRIORITARIAS (busca explícitamente en ellas):
- seekingalpha.com → artículos de tesis bull/bear
- morningstar.com → moat rating y análisis competitivo  
- fool.com → análisis de ventajas competitivas
- reddit.com/r/SecurityAnalysis y r/investing → debates de inversores
- valueinvestorsclub.com → tesis detalladas de value investors
- dataroma.com o whalewisdom.com → qué fondos value tienen posición y por qué
- Cartas de fondos conocidos (Fundsmith, Baillie Gifford, Pershing Square, etc.)

ESTRUCTURA TU RESPUESTA ASÍ:

## TESIS ALCISTA (BULL CASE) — {ticker}
[3-5 argumentos concretos con fuente y fecha]
- Argumento 1: ... (Fuente: X, Fecha: Y)
...

## TESIS BAJISTA (BEAR CASE) — {ticker}  
[3-5 argumentos concretos con fuente y fecha]
- Argumento 1: ... (Fuente: X, Fecha: Y)
...

## CONSENSO DEL MERCADO
[¿Hay posición dominante? ¿Debate polarizado? ¿Cambio reciente de narrativa?]

## SEÑALES DE ALERTA ESPECÍFICAS AL MOAT
[Menciona cualquier amenaza competitiva, disrupción tecnológica, o cambio regulatorio 
que aparezca en las discusiones recientes]

## FONDOS/INVERSORES NOTABLES CON POSICIÓN
[Si encuentras fondos value o inversores conocidos con posición larga o corta, cítalos]

Sé específico, cita fechas, y NO inventes datos. Si no encuentras algo en una fuente, 
dilo explícitamente.
"""
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                temperature=0.1  # más bajo = más factual
            )
        )
        return response.text

    except Exception as e:
        return f"Error al buscar tesis de mercado: {e}"

@tool("simulate_bull_bear_debate")
def simulate_bull_bear_debate(ticker: str) -> str:
    """
    Simula un debate riguroso entre dos analistas expertos (uno extremadamente ALCISTA/BULL y otro extremadamente BAJISTA/BEAR)
    sobre las ventajas competitivas, riesgos y futuro de la empresa.
    Retorna la transcripción del debate para ayudar a descubrir puntos ciegos o sesgos.
    """
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return "Error: GEMINI_API_KEY no configurada."

        client = genai.Client(api_key=api_key)

        prompt = f"""
Actúa como dos analistas de fondos de cobertura veteranos debatiendo sobre la empresa {ticker}.
Analista BULL: Extremadamente optimista. Cree que el foso económico es inexpugnable, el crecimiento está subestimado y los riesgos son exagerados.
Analista BEAR: Extremadamente pesimista. Cree que las ventajas competitivas se están erosionando, la disrupción es inminente y la contabilidad oculta problemas.

REGLAS DEL DEBATE:
1. El debate debe centrarse en el MOAT (ventajas competitivas) y RIESGOS A LARGO PLAZO, no en el precio a corto plazo.
2. Cada analista debe dar 2 argumentos fuertes y rebatir directamente los argumentos del otro.
3. Sé específico con {ticker} (menciona sus productos, competidores o industria).

ESTRUCTURA LA RESPUESTA ASÍ:
**Debate de Analistas: {ticker}**

**[BULL Analyst]**: (Argumento inicial optimista detallado)
**[BEAR Analyst]**: (Refutación agresiva y argumento pesimista detallado)
**[BULL Analyst]**: (Contraataque desmintiendo al Bear)
**[BEAR Analyst]**: (Golpe final sobre los riesgos existenciales)
**[CONCLUSIÓN DEL DEBATE]**: (Un resumen imparcial de cuál es el verdadero campo de batalla o métrica clave que decidirá el futuro de la empresa).
"""
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7 # Un poco más de creatividad para el debate
            )
        )
        return response.text

    except Exception as e:
        return f"Error al simular el debate: {e}"

@tool("search_financial_data")
def search_financial_data(ticker: str) -> str:
    """
    Busca datos financieros reales y actualizados de una empresa:
    resultados anuales, márgenes históricos, FCF, deuda, guidance
    y estimaciones de analistas. Retorna datos estructurados con fuentes citadas.
    """
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return "Error: GEMINI_API_KEY no configurada."

        client = genai.Client(api_key=api_key)
        current_dt = datetime.now()
        current_date = current_dt.strftime("%A, %d de %B de %Y")
        current_year = current_dt.year
        last_year = current_year - 1
        next_year = current_year + 1

        prompt = f"""
Actúa como un terminal financiero para la empresa con ticker {ticker}. Hoy es {current_date}.

Necesito una tabla/resumen claro de los últimos 10 años (hasta {last_year}) con la siguiente información clave, y además los resultados y estimaciones para {current_year} y {next_year}:

1. INGRESOS Y CRECIMIENTO: Revenue histórico (10Y), CAGR de 10 años, y % de crecimiento YoY reciente.
2. MÁRGENES (10Y): Margen Bruto, Operativo y Neto (o FCF margin).
3. DEUDA Y LIQUIDEZ: Deuda neta, Deuda/EBITDA, y Current Ratio reciente.
4. ACTUALIDAD Y ESTIMACIONES: Resultados clave de {last_year}, guidance oficial de la empresa para {current_year}, y consenso de analistas para {current_year}/{next_year}.

No inventes datos. Si falta algo, pon 'No disponible'. Devuelve un resumen estructurado y directo en Markdown, fácil de leer para un analista. No me expliques cómo lo encontraste, solo dame los datos duros.
"""
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                temperature=0.1
            )
        )
        return response.text

    except Exception as e:
        return f"Error al buscar datos financieros: {e}"


@tool("search_capital_allocation")
def search_capital_allocation(ticker: str) -> str:
    """
    Busca datos reales de capital allocation: recompras, dividendos, M&A,
    share count evolution, SBC y devolución total al accionista.
    Retorna datos estructurados con fuentes citadas.
    """
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return "Error: GEMINI_API_KEY no configurada."

        client = genai.Client(api_key=api_key)
        current_dt = datetime.now()
        current_date = current_dt.strftime("%A, %d de %B de %Y")
        current_year = current_dt.year
        last_year = current_year - 1

        prompt = f"""
Actúa como un analista de Capital Allocation para la empresa con ticker {ticker}. Hoy es {current_date}.
Basándote en la filosofía "The Outsiders" (William Thorndike), enfócate en cómo el management ha optimizado el valor por acción en la última década (hasta {last_year}) y la actualidad ({current_year}).

Necesito un resumen histórico duro y directo:

1. DILUCIÓN Y STOCK-BASED COMPENSATION (SBC): Evolución real de las acciones en circulación (shares outstanding) en 10 años. ¿Diluyen al accionista mediante SBC excesiva o el share count neto realmente baja?
2. RECOMPRAS OPORTUNISTAS: Dinero gastado en recompras. ¿Han sido agresivos cuando la acción caía o solo compran de forma mecánica/en máximos para encubrir la dilución?
3. ROIC VS DIVIDENDOS: ¿Generan un ROIC suficientemente alto como para justificar retener capital? Si pagan dividendos, ¿están cubiertos por FCF real o se endeudan para pagarlos?
4. M&A Y DEUDA: Historial de grandes adquisiciones o desinversiones (spin-offs). ¿Crean valor o destruyen valor? ¿Se ha utilizado la deuda de forma prudente o imprudente para financiar este crecimiento?

No inventes datos. Devuelve un formato estructurado en Markdown con los datos empíricos que el agente de Capital Allocation necesitará para su evaluación, sin disclaimers ni texto de relleno.
"""
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                temperature=0.1
            )
        )
        return response.text

    except Exception as e:
        return f"Error al buscar datos de capital allocation: {e}"

@tool("get_yfinance_metrics")
def get_yfinance_metrics(ticker: str) -> str:
    """
    Obtiene métricas financieras clave en tiempo real utilizando la API de Yahoo Finance (yfinance).
    Proporciona datos como P/E, capitalización de mercado, márgenes, ROE, y deuda.
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        metrics = {
            "Market Cap": info.get("marketCap", "N/A"),
            "Forward P/E": info.get("forwardPE", "N/A"),
            "Trailing P/E": info.get("trailingPE", "N/A"),
            "PEG Ratio": info.get("pegRatio", "N/A"),
            "Price to Book": info.get("priceToBook", "N/A"),
            "Dividend Yield": (info.get("dividendYield") / 100 if info.get("dividendYield") is not None and info.get("dividendYield") > 0.20 else info.get("dividendYield")) if info.get("dividendYield") is not None else "N/A",
            "Profit Margin": info.get("profitMargins", "N/A"),
            "Operating Margin": info.get("operatingMargins", "N/A"),
            "Return on Assets (ROA)": info.get("returnOnAssets", "N/A"),
            "Return on Equity (ROE)": info.get("returnOnEquity", "N/A"),
            "Revenue Growth": info.get("revenueGrowth", "N/A"),
            "Earnings Growth": info.get("earningsGrowth", "N/A"),
            "Debt to Equity": info.get("debtToEquity", "N/A"),
            "Current Ratio": info.get("currentRatio", "N/A"),
            "Free Cash Flow": info.get("freeCashflow", "N/A"),
            "52 Week High": info.get("fiftyTwoWeekHigh", "N/A"),
            "52 Week Low": info.get("fiftyTwoWeekLow", "N/A"),
        }
        
        result = f"Métricas financieras clave para {ticker} (fuente: yfinance):\\n"
        for key, value in metrics.items():
            if isinstance(value, float):
                if "Margin" in key or "Growth" in key or "Yield" in key or "Return" in key:
                    result += f"- {key}: {value:.2%}\\n"
                else:
                    result += f"- {key}: {value:.2f}\\n"
            else:
                result += f"- {key}: {value}\\n"
                
        return result
    except Exception as e:
        return f"Error al obtener datos de yfinance para {ticker}: {e}"

@tool("get_analyst_estimates")
def get_analyst_estimates(ticker: str) -> str:
    """
    Obtiene las estimaciones de consenso de los analistas para los próximos años (crecimiento de ingresos y BPA/EPS).
    Ideal para construir la base de los modelos de valoración.
    """
    try:
        stock = yf.Ticker(ticker)
        
        # Intentar obtener estimaciones de crecimiento
        growth_estimates = stock.growth_estimates if hasattr(stock, 'growth_estimates') else None
        earnings_estimate = stock.earnings_estimate if hasattr(stock, 'earnings_estimate') else None
        revenue_estimate = stock.revenue_estimate if hasattr(stock, 'revenue_estimate') else None
        
        result = f"Estimaciones de Analistas para {ticker}:\n"
        
        if earnings_estimate is not None and not earnings_estimate.empty:
            result += "\n[Estimaciones de EPS (BPA)]\n"
            result += earnings_estimate.to_string() + "\n"
            
        if revenue_estimate is not None and not revenue_estimate.empty:
            result += "\n[Estimaciones de Ingresos]\n"
            result += revenue_estimate.to_string() + "\n"
            
        if growth_estimates is not None and not growth_estimates.empty:
            result += "\n[Estimaciones de Crecimiento a Largo Plazo]\n"
            result += growth_estimates.to_string() + "\n"
            
        if "Estimaciones de" == result.strip():
            return f"No se encontraron estimaciones estructuradas en yfinance para {ticker}. Considera buscar en internet."
            
        return result
    except Exception as e:
        return f"Error al obtener estimaciones: {e}"

@tool("calculate_irr_scenarios")
def calculate_irr_scenarios(
    ticker: str,
    base_metric_value: float, 
    metric_type: str,
    growth_rate_1_5: float, 
    exit_multiple: float, 
    dividend_yield_pct: float = 0.0,
    shares_outstanding: float = 1.0,
    target_irr_pct: float = 20.0
) -> str:
    """
    Calcula la TIR (Tasa Interna de Retorno) a 5 años y el Precio de Entrada óptimo.
    El precio actual se obtiene automáticamente desde Yahoo Finance.
    
    Inputs obligatorios:
    - ticker: Símbolo de la empresa (ej. AAPL).
    - base_metric_value: El valor inicial sobre el que se aplicará el crecimiento. 
                         Si metric_type es 'EPS', este es el BPA actual (ej. 5.2). 
                         Si metric_type es 'FCF', este es el FCF total de la empresa (ej. 10000000000).
    - metric_type: 'EPS' (ganancias por acción) o 'FCF' (flujo de caja libre total).
    - growth_rate_1_5: Tasa de crecimiento anual para los años 1 al 5 como decimal (ej. 0.10 para 10%).
    - exit_multiple: El múltiplo de salida estimado en el año 5 (PER si es EPS, o EV/FCF si es FCF).
    - dividend_yield_pct: Rentabilidad por dividendo actual como porcentaje (ej. 1.5 para 1.5%).
    - shares_outstanding: Número de acciones en circulación (OBLIGATORIO si metric_type='FCF', ej. 1000000. Usa 1.0 si es 'EPS').
    - target_irr_pct: La TIR objetivo como porcentaje (por defecto 20.0).
    """
    try:
        # Validación de inputs
        if metric_type.upper() not in ['EPS', 'FCF']:
            return "Error: metric_type debe ser 'EPS' o 'FCF'."
        
        metric = metric_type.upper()
        
        # Obtener precio real de mercado para evitar alucinaciones
        stock = yf.Ticker(ticker)
        info = stock.info
        current_price = info.get('currentPrice') or info.get('previousClose')
        
        if not current_price or current_price <= 0:
            return f"Error: No se pudo obtener el precio actual válido para el ticker {ticker}."
        
        # Tabla año a año
        table_str = "\nProyección Año a Año:\n"
        table_str += f"| Año | Crecimiento | Valor Total ({metric}) | Valor por Acción | Dividendo Acum. |\n"
        table_str += "|-----|-------------|----------------------|------------------|-----------------|\n"
        
        accumulated_dividends = 0
        annual_dividend = current_price * (dividend_yield_pct / 100)
        
        for year in range(0, 6):
            if year == 0:
                val_total = base_metric_value
                val_per_share = base_metric_value / shares_outstanding if metric == 'FCF' else base_metric_value
                table_str += f"| 0   | N/A         | {val_total:,.2f} | ${val_per_share:.2f} | $0.00 |\n"
            else:
                val_total = base_metric_value * ((1 + growth_rate_1_5) ** year)
                val_per_share = val_total / shares_outstanding if metric == 'FCF' else val_total
                accumulated_dividends += annual_dividend
                table_str += f"| {year}   | {growth_rate_1_5*100:.1f}%       | {val_total:,.2f} | ${val_per_share:.2f} | ${accumulated_dividends:.2f} |\n"

        # Valores finales (Año 5)
        projected_metric = base_metric_value * ((1 + growth_rate_1_5) ** 5)
        
        if metric == 'FCF':
            if shares_outstanding <= 1.0:
                 return "Error: Para calcular por FCF debes proveer el número de shares_outstanding reales."
            metric_per_share_yr5 = projected_metric / shares_outstanding
        else: # EPS
            metric_per_share_yr5 = projected_metric

        # Calcular precio de la acción en el año 5
        future_stock_price = metric_per_share_yr5 * exit_multiple
        total_future_value = future_stock_price + accumulated_dividends
        
        # Calcular TIR actual (con bisección para exactitud de flujos temporales)
        if current_price > 0:
            low, high = -0.99, 10.0
            for _ in range(100):
                mid = (low + high) / 2
                npv = -current_price + (future_stock_price / ((1 + mid) ** 5))
                for y in range(1, 6):
                    npv += annual_dividend / ((1 + mid) ** y)
                
                if npv > 0:
                    low = mid
                else:
                    high = mid
            actual_irr = (low + high) / 2
        else:
            actual_irr = 0
            
        # Calcular Precio de Entrada (Strike Price) para la TIR objetivo
        target_discount_rate = target_irr_pct / 100.0
        pv_future_price = future_stock_price / ((1 + target_discount_rate) ** 5)
        
        pv_dividends = 0
        for year in range(1, 6):
            pv_dividends += annual_dividend / ((1 + target_discount_rate) ** year)
            
        strike_price = pv_future_price + pv_dividends
        
        # Formatear el resultado
        resultado = f"""
=== RESULTADOS CALCULADORA TIR (A 5 AÑOS) ===
Método usado: {metric}
Precio Real Utilizado (API): ${current_price:.2f}
Métrica Año 0: {base_metric_value}
Métrica Año 5 proyectada: {projected_metric} (Crecimiento: {growth_rate_1_5*100}%)
Múltiplo de salida asignado: {exit_multiple}x

{table_str}

Precio futuro estimado de la acción (Año 5): ${future_stock_price:.2f}
Dividendos acumulados estimados: ${accumulated_dividends:.2f}
Valor Total Futuro: ${total_future_value:.2f}

📈 TIR a precios actuales (${current_price:.2f}): {actual_irr*100:.2f}%
🎯 Precio de Entrada Máximo para obtener {target_irr_pct}% TIR: ${strike_price:.2f}
=============================================
"""
        return resultado
    except Exception as e:
        return f"Error en el cálculo de TIR: {e}"


def _find_swing_lows(series, window: int):
    """Devuelve los valores de la serie que son mínimos locales en una ventana centrada de +/- window."""
    roll_min = series.rolling(window * 2 + 1, center=True, min_periods=window + 1).min()
    mask = series == roll_min
    positions = [i for i, v in enumerate(mask.values) if v]
    # Deduplicar posiciones consecutivas dentro de la misma ventana (quedarnos con una por clúster)
    deduped = []
    for p in positions:
        if not deduped or p - deduped[-1] > window:
            deduped.append(p)
    return deduped


@tool("get_technical_analysis_data")
def get_technical_analysis_data(ticker: str) -> str:
    """
    Obtiene datos técnicos detallados en tiempo real utilizando Yahoo Finance.
    Proporciona precio actual, medias móviles clave (SMA 50, SMA 200), RSI de 14 días,
    máximo de 52 semanas, mínimo de 52 semanas, All-Time High (ATH) histórico, porcentajes de caída desde máximos,
    volumen reciente frente a su media trimestral, MACD (12,26,9), Bandas de Bollinger (20,2),
    detección mecánica de divergencia alcista de RSI, niveles de soporte históricos reales (mínimos oscilantes
    de los últimos 2 años) y tendencia semanal (precio vs. media semanal de 10 semanas), además de un patrón de
    vela japonesa (Martillo, Envolvente Alcista o Doji) detectado en la última sesión diaria.
    Ideal para evaluar si una empresa de calidad está sobrevendida, ha caído a soportes clave o está en un punto de entrada óptimo,
    con TODOS los datos anclados a precios/volúmenes reales (no interpretaciones visuales de gráficos).
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        # 1. Obtener precio actual y datos básicos de info
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not current_price:
            # Intentar obtener el último cierre histórico de 5 días
            hist_5d = stock.history(period="5d")
            if not hist_5d.empty:
                current_price = float(hist_5d['Close'].iloc[-1])
            else:
                return f"No se pudo obtener el precio actual para {ticker}."

        high_52w = info.get("fiftyTwoWeekHigh", "N/A")
        low_52w = info.get("fiftyTwoWeekLow", "N/A")
        sma_50 = info.get("fiftyDayAverage", "N/A")
        sma_200 = info.get("twoHundredDayAverage", "N/A")

        # 2. Descargar histórico de precios para cálculos adicionales
        # Descargamos los últimos 2 años para calcular el ATH dentro de este periodo y medias/RSI precisos
        hist = stock.history(period="2y")
        if hist.empty:
            return f"No se pudo descargar el histórico de precios para {ticker} para calcular indicadores técnicos."

        close_series = hist['Close']

        # ATH de los últimos 2 años
        ath = float(close_series.max())
        ath_date = close_series.idxmax().strftime("%d/%m/%Y")

        # Calcular la serie completa de RSI de 14 días (Wilder) para poder detectar divergencias, no solo el valor final
        rsi_value = "N/A"
        rsi_series = None
        if len(close_series) >= 15:
            delta = close_series.diff()
            gain = delta.clip(lower=0)
            loss = -1 * delta.clip(upper=0)
            avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
            rs = avg_gain / avg_loss
            rsi_series = 100 - (100 / (1 + rs))
            rsi_series = rsi_series.where(avg_loss != 0, 100.0)
            rsi_value = float(rsi_series.iloc[-1])

        # --- Volumen: reciente (20 sesiones) vs. media trimestral (63 sesiones) ---
        volume_series = hist['Volume']
        volume_summary = "N/A"
        if len(volume_series) >= 20:
            last_volume = float(volume_series.iloc[-1])
            avg_vol_20 = float(volume_series.tail(20).mean())
            avg_vol_63 = float(volume_series.tail(min(63, len(volume_series))).mean())
            vol_trend_pct = ((avg_vol_20 / avg_vol_63) - 1) * 100 if avg_vol_63 else 0
            last_vs_avg_pct = ((last_volume / avg_vol_20) - 1) * 100 if avg_vol_20 else 0
            price_up_today = close_series.iloc[-1] >= hist['Open'].iloc[-1]
            volume_summary = (
                f"Volumen últimas 20 sesiones {vol_trend_pct:+.1f}% vs. media de 63 sesiones (~3 meses). "
            )
            if last_vs_avg_pct > 50:
                clima = "posible CLÍMAX DE COMPRA/ACUMULACIÓN" if price_up_today else "posible CAPITULACIÓN/PÁNICO VENDEDOR"
                volume_summary += f"Volumen de la última sesión {last_vs_avg_pct:+.1f}% sobre su media de 20 sesiones ({clima})."
            elif vol_trend_pct < -15:
                volume_summary += "Volumen decreciente al acercarse al precio actual (posible agotamiento de la presión vendedora)."
            else:
                volume_summary += "Sin señales de volumen anómalo (ni clímax ni agotamiento claro)."

        # --- MACD (12, 26, 9) ---
        macd_summary = "N/A"
        if len(close_series) >= 35:
            ema12 = close_series.ewm(span=12, adjust=False).mean()
            ema26 = close_series.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            histogram = macd_line - signal_line
            macd_now, signal_now, hist_now = macd_line.iloc[-1], signal_line.iloc[-1], histogram.iloc[-1]
            estado = "ALCISTA (MACD > Señal)" if macd_now > signal_now else "BAJISTA (MACD < Señal)"
            cruce = ""
            recent_hist = histogram.tail(4)
            if (recent_hist.iloc[:-1] < 0).any() and hist_now > 0:
                cruce = " — Cruce alcista reciente detectado."
            elif (recent_hist.iloc[:-1] > 0).any() and hist_now < 0:
                cruce = " — Cruce bajista reciente detectado."
            macd_summary = f"MACD: {macd_now:.2f} | Señal: {signal_now:.2f} | Histograma: {hist_now:.2f} → {estado}.{cruce}"

        # --- Bandas de Bollinger (20, 2) ---
        bollinger_summary = "N/A"
        if len(close_series) >= 20:
            sma20 = close_series.rolling(20).mean()
            std20 = close_series.rolling(20).std()
            bb_upper = float((sma20 + 2 * std20).iloc[-1])
            bb_lower = float((sma20 - 2 * std20).iloc[-1])
            if bb_upper > bb_lower:
                percent_b = (current_price - bb_lower) / (bb_upper - bb_lower)
                if percent_b < 0:
                    posicion = "POR DEBAJO de la banda inferior (sobreventa extrema estadística)"
                elif percent_b < 0.2:
                    posicion = "cerca de la banda inferior (zona de sobreventa)"
                elif percent_b > 1:
                    posicion = "POR ENCIMA de la banda superior (sobrecompra extrema estadística)"
                elif percent_b > 0.8:
                    posicion = "cerca de la banda superior (zona de sobrecompra)"
                else:
                    posicion = "en la zona media del rango"
                bollinger_summary = f"Banda superior: ${bb_upper:.2f} | Banda inferior: ${bb_lower:.2f} | Precio {posicion} (%B={percent_b:.2f})."

        # --- Divergencia de RSI (mecánica, sobre mínimos oscilantes de las últimas ~60 sesiones) ---
        divergence_summary = "No hay suficientes datos para evaluar divergencias."
        if rsi_series is not None and len(close_series) >= 30:
            lookback = min(60, len(close_series))
            recent_close = close_series.tail(lookback)
            recent_rsi = rsi_series.tail(lookback)
            swing_positions = _find_swing_lows(recent_close, window=4)
            if len(swing_positions) >= 2:
                p1, p2 = swing_positions[-2], swing_positions[-1]
                price1, price2 = recent_close.iloc[p1], recent_close.iloc[p2]
                rsi1, rsi2 = recent_rsi.iloc[p1], recent_rsi.iloc[p2]
                date1, date2 = recent_close.index[p1].strftime("%d/%m/%Y"), recent_close.index[p2].strftime("%d/%m/%Y")
                if price2 < price1 and rsi2 > rsi1:
                    divergence_summary = (
                        f"DIVERGENCIA ALCISTA DETECTADA: el precio marcó un mínimo más bajo el {date2} (${price2:.2f}) "
                        f"que el del {date1} (${price1:.2f}), pero el RSI fue más alto ({rsi2:.1f} vs {rsi1:.1f}). "
                        "Señal de agotamiento vendedor."
                    )
                else:
                    divergence_summary = (
                        f"Sin divergencia alcista entre los dos últimos mínimos relevantes ({date1} y {date2}): "
                        "precio y momentum se mueven de forma coherente."
                    )
            else:
                divergence_summary = "No se detectaron suficientes mínimos oscilantes recientes para evaluar divergencia."

        # --- Soportes históricos reales (mínimos oscilantes en los últimos 2 años) ---
        support_summary = "N/A"
        swing_low_positions_2y = _find_swing_lows(close_series, window=10)
        swing_low_prices = sorted(
            {round(float(close_series.iloc[p]), 2) for p in swing_low_positions_2y if close_series.iloc[p] < current_price},
            reverse=True,
        )
        support_levels = []
        for price in swing_low_prices:
            if all(abs(price - lvl) / lvl > 0.02 for lvl in support_levels):
                support_levels.append(price)
            if len(support_levels) >= 2:
                break
        if support_levels:
            support_summary = ", ".join(f"${lvl:.2f}" for lvl in support_levels)
        elif isinstance(low_52w, (int, float)):
            support_summary = f"${low_52w:.2f} (mínimo de 52 semanas, sin mínimos oscilantes claros más cercanos)"

        # --- Tendencia semanal (precio vs. media de 10 semanas) ---
        weekly_summary = "N/A"
        weekly_close = hist['Close'].resample('W-FRI').last().dropna()
        if len(weekly_close) >= 11:
            weekly_sma10 = weekly_close.rolling(10).mean()
            w_last, w_sma = weekly_close.iloc[-1], weekly_sma10.iloc[-1]
            w_dist = ((w_last / w_sma) - 1) * 100
            w_trend = "ALCISTA" if w_last > w_sma else "BAJISTA"
            weekly_summary = f"Cierre semanal actual vs. media de 10 semanas: {w_dist:+.2f}% → Tendencia semanal {w_trend}."

        # --- Patrón de vela japonesa en la última sesión diaria ---
        candle_summary = "Sin patrón de vela relevante en la última sesión."
        if len(hist) >= 2:
            o, h, l, c = hist['Open'].iloc[-1], hist['High'].iloc[-1], hist['Low'].iloc[-1], hist['Close'].iloc[-1]
            prev_o, prev_c = hist['Open'].iloc[-2], hist['Close'].iloc[-2]
            body = abs(c - o)
            rango = h - l
            upper_wick = h - max(o, c)
            lower_wick = min(o, c) - l
            if rango > 0:
                if body <= 0.1 * rango:
                    candle_summary = "DOJI (indecisión del mercado, posible punto de giro)."
                elif lower_wick >= 2 * body and upper_wick <= 0.3 * rango and c >= o:
                    candle_summary = "MARTILLO (Hammer) alcista — mecha inferior larga, posible agotamiento vendedor intradía."
                elif prev_c < prev_o and c > o and o <= prev_c and c >= prev_o:
                    candle_summary = "ENVOLVENTE ALCISTA (Bullish Engulfing) — la vela actual absorbe por completo la bajista previa."

        # Calcular caídas desde picos en porcentaje
        drop_from_52w_high = "N/A"
        if isinstance(high_52w, (int, float)) and high_52w > 0:
            drop_from_52w_high = ((current_price - high_52w) / high_52w) * 100
            
        drop_from_ath = "N/A"
        if ath > 0:
            drop_from_ath = ((current_price - ath) / ath) * 100
            
        # Calcular distancia a medias móviles
        dist_sma_50 = "N/A"
        if isinstance(sma_50, (int, float)) and sma_50 > 0:
            dist_sma_50 = ((current_price - sma_50) / sma_50) * 100
            
        dist_sma_200 = "N/A"
        if isinstance(sma_200, (int, float)) and sma_200 > 0:
            dist_sma_200 = ((current_price - sma_200) / sma_200) * 100

        result = f"### DATOS DE ANÁLISIS TÉCNICO Y TIMING PARA {ticker.upper()}\\n"
        result += f"- **Precio Actual:** ${current_price:.2f}\\n"
        result += f"- **Máximo 52 Semanas:** ${high_52w:.2f}" if isinstance(high_52w, (int, float)) else f"- **Máximo 52 Semanas:** {high_52w}\\n"
        if isinstance(drop_from_52w_high, float):
            result += f" (Caída de {drop_from_52w_high:.2f}% desde máximos)\\n"
        else:
            result += "\\n"
            
        result += f"- **Mínimo 52 Semanas:** ${low_52w:.2f}\\n" if isinstance(low_52w, (int, float)) else f"- **Mínimo 52 Semanas:** {low_52w}\\n"
        result += f"- **Máximo Histórico (ATH de los últimos 2 años):** ${ath:.2f} el {ath_date}"
        if isinstance(drop_from_ath, float):
            result += f" (Caída de {drop_from_ath:.2f}% desde ATH)\\n"
        else:
            result += "\\n"
            
        result += f"- **Media Móvil de 50 días (SMA 50):** ${sma_50:.2f}" if isinstance(sma_50, (int, float)) else f"- **Media Móvil de 50 días (SMA 50):** {sma_50}\\n"
        if isinstance(dist_sma_50, float):
            result += f" (Distancia: {dist_sma_50:+.2f}%)\\n"
        else:
            result += "\\n"
            
        result += f"- **Media Móvil de 200 días (SMA 200):** ${sma_200:.2f}" if isinstance(sma_200, (int, float)) else f"- **Media Móvil de 200 días (SMA 200):** {sma_200}\\n"
        if isinstance(dist_sma_200, float):
            result += f" (Distancia: {dist_sma_200:+.2f}%)\\n"
        else:
            result += "\\n"
            
        if isinstance(rsi_value, float):
            result += f"- **RSI de 14 Días:** {rsi_value:.2f}\\n"
            # Añadir comentario sobre el estado del RSI
            if rsi_value <= 30:
                result += "  ⚠️ **ESTADO: EXTREMADAMENTE SOBREVENDIDO (<30)**. Punto de entrada de alta probabilidad estadística de rebote técnico.\\n"
            elif rsi_value <= 40:
                result += "  📉 **ESTADO: SOBREVENDIDO / CORRECCIÓN EN CURSO (30-40)**. Muy atractivo para acumular a largo plazo.\\n"
            elif rsi_value <= 60:
                result += "  ⚖️ **ESTADO: NEUTRO (40-60)**. El precio consolida en rangos medios.\\n"
            elif rsi_value <= 70:
                result += "  📈 **ESTADO: COMPRADO / FUERTE (60-70)**. Tendencia alcista sólida, pero menor margen de seguridad de corto plazo.\\n"
            else:
                result += "  🔥 **ESTADO: EXTREMADAMENTE SOBRECOMPRADO (>70)**. Evitar compras masivas aquí; esperar corrección para mejor punto de entrada.\\n"
        else:
            result += f"- **RSI de 14 Días:** {rsi_value}\\n"

        result += f"- **Divergencia RSI:** {divergence_summary}\\n"
        result += f"- **Volumen:** {volume_summary}\\n"
        result += f"- **MACD (12,26,9):** {macd_summary}\\n"
        result += f"- **Bandas de Bollinger (20,2):** {bollinger_summary}\\n"
        result += f"- **Soportes Históricos Reales (mínimos oscilantes 2Y):** {support_summary}\\n"
        result += f"- **Tendencia Semanal:** {weekly_summary}\\n"
        result += f"- **Patrón de Vela (última sesión diaria):** {candle_summary}\\n"

        return result
    except Exception as e:
        return f"Error al calcular indicadores técnicos para {ticker}: {e}"


@tool("get_yfinance_financials")
def get_yfinance_financials(ticker: str) -> str:
    """
    Obtiene los estados financieros históricos de los últimos 4 años (Pérdidas y Ganancias, Balance y Flujo de Caja)
    utilizando la API de Yahoo Finance (yfinance). Útil para analizar tendencias de ingresos, márgenes, deuda y FCF.
    """
    try:
        stock = yf.Ticker(ticker)
        
        # Obtener los estados financieros
        income_stmt = stock.financials
        balance_sheet = stock.balance_sheet
        cash_flow = stock.cashflow
        
        result = f"Estados financieros históricos para {ticker} (Últimos 4 años - Fuente: yfinance):\\n\\n"
        
        if not income_stmt.empty:
            result += "### Estado de Resultados (Income Statement):\\n"
            result += income_stmt.to_string() + "\\n\\n"
            
        if not balance_sheet.empty:
            result += "### Balance de Situación (Balance Sheet):\\n"
            result += balance_sheet.to_string() + "\\n\\n"
            
        if not cash_flow.empty:
            result += "### Flujo de Caja (Cash Flow):\\n"
            result += cash_flow.to_string() + "\\n"
            
        if income_stmt.empty and balance_sheet.empty and cash_flow.empty:
            return f"No se encontraron estados financieros para {ticker} en yfinance."
            
        return result
    except Exception as e:
        return f"Error al obtener estados financieros de yfinance para {ticker}: {e}"

@tool("buscar_sec_filings")
def buscar_sec_filings(ticker: str) -> str:
    """Busca en la SEC (EDGAR) el Proxy Statement (DEF 14A) de {ticker} y extrae datos sobre alineación, 'Skin in the game' del equipo directivo (acciones del CEO, bonus, salario, etc.)."""
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return "Error: GEMINI_API_KEY no configurada."

        client = genai.Client(api_key=api_key)
        prompt = f"""
Busca en internet el Proxy Statement (DEF 14A) más reciente presentado ante la SEC para la empresa {ticker}.
Localiza las secciones de:
1. Executive Compensation (Compensación de Ejecutivos): Busca cómo está compuesto el salario y el bonus anual (qué métricas operativas o de rentabilidad exigen para cobrar el bonus, ej: crecimiento de ingresos, margen operativo, ROIC, etc.).
2. Stock Ownership of Directors and Executive Officers (Acciones en propiedad de directivos): Busca qué porcentaje o cuántas acciones de la compañía posee el CEO y el equipo directivo ("Skin in the game").

Resume la información encontrada de forma muy detallada, indicando los porcentajes exactos, cifras, métricas del bonus y políticas de remuneración.
"""
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                temperature=0.1
            )
        )
        return response.text
    except Exception as e:
        return f"Error al buscar Proxy Statement: {e}"

@tool("buscar_transcripcion_earnings")
def buscar_transcripcion_earnings(ticker: str) -> str:
    """Busca la última transcripción disponible de la llamada de resultados de {ticker} (con foco en la sesión de preguntas y respuestas Q&A) para evaluar la honestidad, transparencia y asignación de capital del equipo directivo."""
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return "Error: GEMINI_API_KEY no configurada."

        client = genai.Client(api_key=api_key)
        prompt = f"""
Busca en internet la transcripción completa de la llamada de resultados trimestrales (earnings call transcript) más reciente de {ticker}, con especial foco en la sesión de preguntas y respuestas (Q&A Session) con analistas.
Localiza las preguntas y respuestas clave sobre:
1. Decisiones o visión de asignación de capital (comentarios sobre Capex de expansión, fusiones/adquisiciones, recompra de acciones o dividendos).
2. Tono y honestidad del CEO y CFO al responder preguntas difíciles de analistas (ej. si admiten errores operativos o si son evasivos).

Resume detalladamente el tono general del Q&A, lo que dijeron los ejecutivos sobre la asignación de capital y tu evaluación de su franqueza.
"""
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                temperature=0.1
            )
        )
        return response.text
    except Exception as e:
        return f"Error al buscar transcripción: {e}"

@tool("buscar_noticias_directivos")
def buscar_noticias_directivos(nombre_ceo: str) -> str:
    """Busca noticias, reportes o escándalos sobre el CEO ({nombre_ceo}) o miembros de la directiva de la empresa, incluyendo demandas, controversias de integridad, ventas masivas de acciones (insider selling) o antecedentes de gobierno corporativo deficiente."""
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return "Error: GEMINI_API_KEY no configurada."

        client = genai.Client(api_key=api_key)
        prompt = f"""
Busca en internet noticias, escándalos, demandas, ventas masivas de acciones no planificadas o cualquier controversia relacionada con el CEO {nombre_ceo} o el equipo directivo.
Busca si hay historial de gobierno corporativo problemático o conductas éticas dudosas en empresas anteriores.

Resume detalladamente cualquier bandera roja o aspecto positivo de integridad/reputación que encuentres sobre el directivo.
"""
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                temperature=0.1
            )
        )
        return response.text
    except Exception as e:
        return f"Error al buscar noticias de directivos: {e}"


@tool("get_company_events")
def get_company_events(ticker: str) -> str:
    """
    Obtiene los próximos eventos corporativos de una empresa, como la fecha de resultados (earnings) y dividendos,
    utilizando la API de Yahoo Finance (yfinance).
    """
    try:
        stock = yf.Ticker(ticker)
        
        # 1. Obtener calendario de earnings
        calendar_str = "No disponible."
        try:
            calendar = stock.calendar
            if calendar is not None and 'Earnings Date' in calendar.columns and not calendar.empty:
                earnings_date = calendar['Earnings Date'][0]
                if isinstance(earnings_date, str):
                    calendar_str = earnings_date
                else:
                    calendar_str = earnings_date.strftime('%d de %B de %Y')
        except Exception:
            # Si .calendar falla o está vacío, lo dejamos como no disponible
            pass

        # 2. Obtener información de dividendos
        dividend_str = "No paga dividendos o no hay datos recientes."
        try:
            dividends = stock.dividends
            if not dividends.empty:
                last_dividend = dividends.tail(1)
                if not last_dividend.empty:
                    last_date = last_dividend.index[0].strftime('%d de %B de %Y')
                    last_amount = last_dividend.values[0]
                    dividend_str = f"Último dividendo: ${last_amount:.2f} el {last_date}."
        except Exception:
             # Si .dividends falla, lo dejamos como no disponible
            pass

        result = f"### PRÓXIMOS EVENTOS PARA {ticker.upper()}\\n"
        result += f"- **Próxima Fecha de Resultados (Earnings):** {calendar_str}\\n"
        result += f"- **Pago de Dividendos:** {dividend_str}\\n"
        
        return result
    except Exception as e:
        return f"Error al obtener eventos para {ticker}: {e}"