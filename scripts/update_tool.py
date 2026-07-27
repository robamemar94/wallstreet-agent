import re

with open('tools/search_tool.py', 'r', encoding='utf-8') as f:
    content = f.read()

new_func = """def generate_fair_pe_json(ticker: str) -> dict:
    \"\"\"Calcula el Fair Forward P/E basado en medias históricas y crecimiento futuro, devolviendo un único valor y status.\"\"\"
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return {"error": "GEMINI_API_KEY no configurada."}

        client = genai.Client(api_key=api_key)
        current_date = datetime.now().strftime("%A, %d de %B de %Y")
        
        prompt = f\"\"\"
Hoy es {current_date}. Eres un analista de valoración fundamental muy estricto y conservador.
Tu objetivo es calcular el "Fair Forward P/E" (PER Forward Justo de un único valor, NO un rango) para la empresa con ticker {ticker} y determinar si la acción está cara o barata.

INSTRUCCIONES DE ANÁLISIS:
1. HISTÓRICO: Busca la media histórica del Forward P/E de los últimos 10 años para {ticker}. Concéntrate principalmente en un horizonte largo de 10 años (en lugar de solo 5 años) para obtener una referencia más robusta, prudente y representativa de todo el ciclo económico.
   - EXCLUYE años de "burbuja" (P/E > 30-40) si la empresa no crecía al 30% en ese momento.
   - ATENCIÓN: Si es una empresa tecnológica madura (ej. Google, Meta, Apple) cuyo crecimiento se ha desacelerado respecto a hace 10 años, NO uses la media histórica si es alta (ej. 28x-30x). Las empresas que crecen al 10-12% merecen un PER de 18x-22x, NUNCA de 30x o 40x.
2. CRECIMIENTO ESPERADO: Evalúa el crecimiento de beneficios (EPS growth) esperado para los próximos 3 años.
   - Regla PEG (Peter Lynch): Un PER justo suele ser igual o ligeramente superior a la tasa de crecimiento a largo plazo (ej. crecimiento EPS 15% -> PER justo ~15-20x).
   - No asignes un PER Justo de 40x a una empresa que va a crecer al 10%. Usa la lógica matemática y penaliza la desaceleración del crecimiento futuro.
3. VALOR JUSTO (FAIR FORWARD P/E): Define un ÚNICO valor exacto que un inversor prudente pagaría hoy (ej. 21.5).
4. VALORACIÓN ACTUAL: Busca el Forward P/E real al que cotiza la empresa HOY en el mercado.
5. STATUS (MATEMÁTICA ESTRICTA): Compara el Forward P/E actual (A) con el Fair Forward P/E (F) y asigna la etiqueta EXACTA siguiendo esta regla matemática estricta:
   - Si A es menor que F por más de un 20% -> "Ganga"
   - Si A es menor que F entre un 5% y 20% -> "Barata"
   - Si A está a +/- 5% de F -> "Precio Justo"
   - Si A es mayor que F entre un 5% y 20% -> "Cara"
   - Si A es mayor que F por más de un 20% -> "Muy Cara / Burbuja"

IMPORTANTE: DEVUELVE ÚNICA Y EXCLUSIVAMENTE UN JSON VÁLIDO. NADA DE MARKDOWN.

ESTRUCTURA EXACTA DEL JSON:
{{
  "historical_pe": [float, la media histórica después de quitar burbujas],
  "sector_pe": [float, la media del sector],
  "fair_forward_pe": [float, el PER exacto que tú consideras justo, basándote en el crecimiento FUTURO y la calidad],
  "current_forward_pe": [float, el PER al que cotiza HOY],
  "valuation_status": "[String exacto basado en la regla matemática estricta: Ganga | Barata | Precio Justo | Cara | Muy Cara / Burbuja]",
  "rationale": "[Explicación de por qué ese PER es justo. Ejemplo: 'Aunque su media histórica era 30x, su crecimiento esperado ha bajado al 11%, por lo que aplicando la regla de madurez y PEG, su PER justo hoy no debería superar 22x.']"
}}
\"\"\"
        response = client.models.generate_content(
            model='gemini-2.5-pro',
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
        return {"error": str(e)}"""

pattern = re.compile(r'def generate_fair_pe_json\(ticker: str\) -> dict:.*?return \{"error": str\(e\)\}', re.DOTALL)
new_content = pattern.sub(new_func, content)

with open('tools/search_tool.py', 'w', encoding='utf-8') as f:
    f.write(new_content)
