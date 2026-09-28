"""
Convierte una tesis en PDF a Markdown por apartados (## N. Título) y propone su YAML de seguimiento con IA.

- Los apartados se detectan por tamaño de letra: líneas en negrita claramente mayores que el cuerpo y que empiezan
  por "N." pasan a ser '## N. Título'. Las tablas se extraen con pdfplumber.
- Si el PDF trae celdas recortadas o texto solapado, la extracción lo hereda: el resultado se revisa antes de importar.
"""
import re
import sys
from collections import Counter

FOOTER = re.compile(r"(Página \d+$)|(^Investment Research [·-] .* \d+$)")


# Glifos de la fuente Symbol que pdfplumber extrae como letras latinas en PDFs generados con Helvetica
SYMBOL_FIXES = [
    (re.compile(r"\(cid:127\)\s*"), "• "),
    (re.compile(r"‡"), "≥"), (re.compile(r"£"), "≤"), (re.compile(r"»"), "≈"), (re.compile(r"›"), "↑"),
    # 'fl'/'fi' como palabra suelta son flechas; dentro de palabras (definitiva, flota) son letras normales
    (re.compile(r"(?<!\w)fl(?!\w)"), "↓"), (re.compile(r"(?<!\w)fi(?!\w)"), "→"),
    (re.compile(r"^n (Verde|Amarillo|Rojo)$"), r"■ \1"),
]


def _clean(text: str) -> str:
    text = (text or "").replace("\x00", "").replace("\n", " ").replace("|", "/").strip()
    for pattern, repl in SYMBOL_FIXES:
        text = pattern.sub(repl, text)
    return text


def _table_md(rows) -> str:
    rows = [[_clean(c) for c in r] for r in rows if r and any(c for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def convert(pdf_path: str) -> str:
    import pdfplumber

    blocks = []  # (page, top, kind, content)
    with pdfplumber.open(pdf_path) as pdf:
        sizes = Counter(round(ch["size"], 1) for p in pdf.pages for ch in p.chars)
        body = sizes.most_common(1)[0][0]
        for pno, page in enumerate(pdf.pages):
            tables = page.find_tables()
            boxes = [t.bbox for t in tables]
            for t in tables:
                blocks.append((pno, t.bbox[1], "table", _table_md(t.extract())))
            for line in page.extract_text_lines():
                x0, top = line["x0"], line["top"]
                if any(b[0] - 2 <= x0 <= b[2] and b[1] - 2 <= top <= b[3] for b in boxes):
                    continue
                text = _clean(line["text"])
                ch = line["chars"][0]
                size, bold = round(ch["size"], 1), "Bold" in ch["fontname"]
                if not text or FOOTER.search(text):
                    continue
                if bold and size >= max(body * 1.4, 12) and re.match(r"^\d+\.\s", text):
                    kind = "h2"
                elif size >= max(body * 1.8, 18):
                    kind = "title"
                else:
                    kind = "bold" if bold and size >= body else "text"
                blocks.append((pno, top, kind, text))

    blocks.sort(key=lambda b: (b[0], b[1]))
    md, para = [], []

    def flush():
        if para:
            md.append(" ".join(para))
            para.clear()

    for _, _, kind, content in blocks:
        if kind in ("h2", "title", "table"):
            flush()
            md.append({"h2": "## " + content, "title": "# " + content}.get(kind, content))
        elif kind == "bold" and not para:
            para.append(f"**{content}**")
        else:
            para.append(content)
            if content.endswith((".", ":", "?")):
                flush()
    flush()
    return "\n\n".join(md) + "\n"




# --- Propuesta del YAML de seguimiento con IA ---

def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:yaml)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def draft_thesis_yaml(md_text: str, ticker: str, pdf_rel_path: str, md_rel_path: str) -> str:
    """Gemini (modelo de juicio) propone el YAML de la tesis siguiendo el formato de las tesis existentes."""
    from app.application.services.thesis_ai_service import _client, _generate
    from app.application.services.thesis_review_service import JUDGE_MODEL
    from app.application.services.thesis_service import AUTO_METRICS
    from utils import llm_usage

    example = open("config/theses/nvda.yaml", encoding="utf-8").read()
    metrics = "\n".join(f"- {k}: {v}" for k, v in AUTO_METRICS.items())
    prompt = f"""Eres un analista que convierte una tesis de inversión en su fichero de seguimiento (YAML) para un dashboard.

REGLAS (muy importantes):
- Monitoriza SOLO los KPIs CUANTIFICABLES del cuadro de mando / resumen de KPIs de la propia tesis (el apartado donde la
  tesis dice qué medir y con qué umbrales). No añadas KPIs que la tesis no pida ni inventes umbrales: si la tesis solo da
  el objetivo, pon green_threshold y deja red_threshold vacío. Si un KPI no tiene número, no lo metas en el cuadro.
- Cada KPI es un paso («pillar») con una pregunta o hipótesis en "description" (usa las preguntas/hipótesis de la tesis).
- Copia literalmente los textos de las zonas verde/amarillo/rojo de la tesis.
- Kill switches: marca is_kill_switch en los KPIs que la tesis señala como thesis breakers / kill switches.
- "observations": solo cifras que la tesis dé explícitamente como dato actual, con su periodo y fecha.
- Si un KPI se puede calcular con yfinance, pon "auto_metric" con una de estas claves:
{metrics}
  Para objetivos de largo plazo (CAGR del FCF por acción) usa fcf_per_share_cagr_3y, no el YoY trimestral.
- Valoración: escenarios de la tesis con fcf_ps_cagr (número) y multiple ("25x"); target_year si la tesis da año.
- ticker: {ticker}; source_document: {pdf_rel_path}; document: {md_rel_path}.
- Entrecomilla SIEMPRE los textos con comillas dobles (muchos empiezan por >, <, ≥ o contienen «:», y sin comillas el YAML no es válido).

FORMATO: sigue exactamente la estructura de este ejemplo (otra tesis):
```yaml
{example}
```

TESIS A CONVERTIR:
{md_text[:150_000]}

DEVUELVE ÚNICAMENTE EL YAML, sin explicaciones."""
    import yaml
    from app.application.services.thesis_ai_service import GEMINI_MODEL
    from app.application.services.thesis_service import validate_spec

    client = _client()
    with llm_usage.track_usage(f"{ticker}/thesis-from-pdf"):
        text = _strip_fences(_generate(client, JUDGE_MODEL, prompt)["text"])
        try:
            validate_spec(yaml.safe_load(text), "Propuesta")
        except Exception as e:   # una reparación: el modelo corrige su propio YAML con el error concreto
            fix = (f"Este YAML no es válido. Error: {e}\n\nCorrígelo sin cambiar su contenido (entrecomilla los textos) "
                   f"y devuelve ÚNICAMENTE el YAML corregido:\n\n{text}")
            text = _strip_fences(_generate(client, GEMINI_MODEL, fix)["text"])
        return text
