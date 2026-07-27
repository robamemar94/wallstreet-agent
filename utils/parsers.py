import re

def parse_summary(markdown_text):
    score = "N/A"
    rejection = None
    pros = []
    cons = []
    main_text = markdown_text
    signal_match = None

    # 1. Intentar analizar con el nuevo formato XML (<resumen>...</resumen>)
    xml_match = re.search(r'<resumen>(.*?)</resumen>', markdown_text, re.DOTALL | re.IGNORECASE)
    
    if xml_match:
        summary_xml = xml_match.group(1)
        full_match_text = xml_match.group(0)
        
        # Extraer el texto principal borrando las etiquetas XML del principio
        main_text = markdown_text.replace(full_match_text, "").strip()

        # Check si hay aviso de rechazo
        rejection_match = re.search(r'<rejection>(.*?)</rejection>', summary_xml, re.DOTALL | re.IGNORECASE)
        if rejection_match:
            rejection = rejection_match.group(1).strip()
            if not rejection or rejection.lower() in ["none", "n/a", "na", "no"]:
                rejection = None

        # Check si es un resumen de valoración (<signal>)
        signal_match = re.search(r'<signal>(.*?)</signal>', summary_xml, re.IGNORECASE)
        if signal_match:
            score = signal_match.group(1).strip()
            # El target price lo podemos dejar en el markdown si el modelo no lo incluyó o lo ignoramos aquí 
            # ya que el markdown detallado tendrá la tabla final.
            return main_text, score, [], [], rejection

        # Extraer Score normal
        score_match = re.search(r'<score>(.*?)</score>', summary_xml, re.IGNORECASE)
        if score_match:
            score = score_match.group(1).strip()
            # Asegurar formato "/10"
            if not score.endswith("/10") and score.replace(".", "").isdigit():
                score += "/10"

        # Función auxiliar para extraer <item> de una sección XML
        def extract_xml_items(section_tag, xml_text):
            section_match = re.search(f'<{section_tag}>(.*?)</{section_tag}>', xml_text, re.DOTALL | re.IGNORECASE)
            if section_match:
                section_content = section_match.group(1)
                items = re.findall(r'<item>(.*?)</item>', section_content, re.DOTALL | re.IGNORECASE)
                return [i.strip() for i in items if i.strip()]
            return []

        pros = extract_xml_items('pros', summary_xml)
        cons = extract_xml_items('cons', summary_xml)

    else:
        # 2. Fallback para el formato antiguo (---RESUMEN ESTRUCTURADO---)
        match = re.search(r'-{0,3}\s*RESUMEN ESTRUCTURADO\s*-{0,3}(.*?)(?:---FIN RESUMEN ESTRUCTURADO---|$)', markdown_text, re.DOTALL | re.IGNORECASE)
        
        def extract_items(text):
            raw_items = re.split(r'(?:^|\n|\s+)(?:[-*•]|\d+\.)(?:\s+|$)', text)
            return [i.strip().replace('\n', ' ') for i in raw_items if i.strip() and not re.match(r'^(ninguno|none|n/a|na)$', i.strip(), re.IGNORECASE)]

        if match:
            summary_text = match.group(1)
            full_match_text = match.group(0)
            main_text = markdown_text.replace(full_match_text, "").strip()

            score_match = re.search(r"SCORE:\s*([0-9.]+(?:/10)?)", summary_text, re.IGNORECASE)
            if score_match:
                score = score_match.group(1)
                if not score.endswith("/10"):
                    score += "/10"

            pros_section = re.search(r"PROS:(.*?)(?:CONS:|$)", summary_text, re.DOTALL | re.IGNORECASE)
            if pros_section:
                pros = extract_items(pros_section.group(1))

            cons_section = re.search(r"CONS:(.*?)$", summary_text, re.DOTALL | re.IGNORECASE)
            if cons_section:
                cons = extract_items(cons_section.group(1))

    # 3. Soporte inteligente de extracción para el formato cualitativo de Liderazgo/Management si no hay XML
    if score == "N/A" and not pros and not cons:
        # Extraer Nota (Score)
        score_match = re.search(r"Nota:\s*([0-9.]+)(?:/10)?", markdown_text, re.IGNORECASE)
        if score_match:
            score = score_match.group(1).strip()
            if not score.endswith("/10"):
                score += "/10"
                
        # Extraer Banderas Verdes (Pros)
        pros_match = re.search(r"\[Bandera Verde 🟢\]\s*\n(.*?)(?=\n\s*\[Bandera Roja 🔴\]|\n\s*\[Veredicto de Confianza\]|$)", markdown_text, re.DOTALL | re.IGNORECASE)
        if pros_match:
            lines = [line.strip() for line in pros_match.group(1).split('\n') if line.strip()]
            for line in lines:
                cleaned = re.sub(r'^(?:[-*•]|\d+\.)\s*', '', line).strip()
                # Limpiar asteriscos de negrita en títulos si los hay
                cleaned = cleaned.replace('**', '').replace('__', '')
                if cleaned and not cleaned.lower().startswith('otra bandera'):
                    pros.append(cleaned)
                    
        # Extraer Banderas Rojas (Cons)
        cons_match = re.search(r"\[Bandera Roja 🔴\]\s*\n(.*?)(?=\n\s*\[Veredicto de Confianza\]|$)", markdown_text, re.DOTALL | re.IGNORECASE)
        if cons_match:
            lines = [line.strip() for line in cons_match.group(1).split('\n') if line.strip()]
            for line in lines:
                cleaned = re.sub(r'^(?:[-*•]|\d+\.)\s*', '', line).strip()
                cleaned = cleaned.replace('**', '').replace('__', '')
                if cleaned and not cleaned.lower().startswith('otra bandera'):
                    cons.append(cleaned)

    # Construir el resumen Markdown solo si encontramos datos de pros/cons (esquivando valoración)
    if score != "N/A" and not signal_match and (pros or cons):
        summary_md = f"\n\n---\n### Resumen del Agente\n**Score Final:** {score}\n\n**Puntos Fuertes (Pros):**\n"
        for p in pros:
            summary_md += f"- {p}\n"
        summary_md += "\n**Puntos Débiles (Cons):**\n"
        for c in cons:
            summary_md += f"- {c}\n"
            
        main_text = main_text + summary_md

    return main_text, score, pros, cons, rejection


def parse_sub_report(markdown_text):
    # Clean up bold asterisks to make regex matching extremely robust and simple
    clean_text = markdown_text.replace('**', '').replace('__', '')
    
    # Extract score
    score_match = re.search(r'Nota de (?:Resultados|Balance|Flujo de Caja):\s*([0-9.]+)', clean_text, re.IGNORECASE)
    score = score_match.group(1).strip() + "/10" if score_match else "N/A"
    
    # Extract pros
    pros = []
    # Match Puntos Fuertes followed by any characters except newlines/colons, then a colon, spaces, and the bullet list
    pros_match = re.search(r'Puntos Fuertes[^\n:]*:\s*\n((?:\s*-\s*[^\n]+\r?\n*)+)', clean_text, re.IGNORECASE)
    if pros_match:
        items = re.findall(r'\s*-\s*([^\n\r]+)', pros_match.group(1))
        pros = [i.strip() for i in items if i.strip()]
        
    # Extract cons
    cons = []
    # Match Puntos Débiles followed by any characters except newlines/colons, then a colon, spaces, and the bullet list
    cons_match = re.search(r'Puntos Débiles[^\n:]*:\s*\n((?:\s*-\s*[^\n]+\r?\n*)+)', clean_text, re.IGNORECASE)
    if cons_match:
        items = re.findall(r'\s*-\s*([^\n\r]+)', cons_match.group(1))
        cons = [i.strip() for i in items if i.strip()]
        
    return score, pros, cons
