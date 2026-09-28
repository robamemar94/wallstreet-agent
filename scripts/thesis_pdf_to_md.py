"""
Convierte una tesis en PDF a Markdown por apartados (borrador para revisar a mano).

Uso:
    python scripts/thesis_pdf_to_md.py data/theses/X.pdf config/theses/x.md

- Los apartados se detectan por tamaño de letra: líneas en negrita claramente mayores que el cuerpo
  y que empiezan por "N." pasan a ser '## N. Título'.
- Las tablas se extraen con pdfplumber. Si el PDF ya trae celdas recortadas o texto solapado,
  la extracción lo hereda: revisa el resultado antes de importarlo.
"""
import re
import sys
from collections import Counter

import pdfplumber

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


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write(convert(sys.argv[1]))
    print(f"Escrito {sys.argv[2]}")
