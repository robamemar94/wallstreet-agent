"""
Normaliza una tesis escrita en Markdown estilo Pandoc para el visor de la app (Python-Markdown):

- Tablas «simple» y «multiline» de Pandoc (columnas alineadas con líneas de guiones) -> tablas GFM con '|'.
- Escapes de Pandoc (\\~ \\$ \\> \\<) -> caracteres normales.
- Niveles de encabezado: si los apartados principales vienen en '#', se bajan un nivel para que '##' sean los
  apartados del índice (como en las tesis convertidas desde PDF).
"""
import re
from typing import List, Optional, Tuple

SEP_RE = re.compile(r"^\s*-{2,}(?:\s+-{2,})+\s*$")      # línea de columnas: grupos de guiones
RULE_RE = re.compile(r"^\s*-{10,}\s*$")                 # línea completa de guiones (borde de tabla multilínea)


def _spans(sep_line: str) -> List[Tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(r"-+", sep_line)]


def _split_cells(line: str, spans: List[Tuple[int, int]]) -> List[str]:
    """Reparte los fragmentos de texto de una línea entre columnas según su posición.
    Un fragmento que cruza el límite entre dos columnas se corta por el espacio más cercano al límite."""
    cells = [""] * len(spans)
    bounds = [s for s, _ in spans[1:]]                  # inicio de cada columna a partir de la segunda
    for m in re.finditer(r"\S+(?: \S+)*", line):
        start, text = m.start(), m.group(0)
        pieces = [(start, text)]
        for b in bounds:
            new = []
            for ps, pt in pieces:
                if ps < b < ps + len(pt):
                    # cortar por el espacio más cercano al límite de columna
                    spaces = [i for i, ch in enumerate(pt) if ch == " "]
                    if spaces:
                        cut = min(spaces, key=lambda i: abs(ps + i - b))
                        new += [(ps, pt[:cut]), (ps + cut + 1, pt[cut + 1:])]
                        continue
                new.append((ps, pt))
            pieces = new
        for ps, pt in pieces:
            center = ps + len(pt) / 2
            col = min(range(len(spans)), key=lambda i: 0 if spans[i][0] <= center <= spans[i][1] + 1
                      else min(abs(center - spans[i][0]), abs(center - spans[i][1])))
            cells[col] = (cells[col] + " " + pt).strip()
    return cells


def _gfm(header: List[str], rows: List[List[str]]) -> List[str]:
    esc = lambda c: c.replace("|", "\\|")
    out = ["| " + " | ".join(esc(c) for c in header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows if any(r)]
    return out


def convert_tables(text: str) -> str:
    lines = text.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not SEP_RE.match(line):
            out.append(line)
            i += 1
            continue
        spans = _spans(line)
        # ¿tabla multilínea? (borde de guiones completo encima de la cabecera)
        top: Optional[int] = None
        for j in range(len(out) - 1, max(len(out) - 5, -1), -1):
            if RULE_RE.match(out[j]):
                top = j
                break
            if not out[j].strip():
                break
        if top is not None:
            header_lines = [l for l in out[top + 1:] if l.strip()]
            del out[top:]
            header = [""] * len(spans)
            for hl in header_lines:
                header = [(a + " " + b).strip() for a, b in zip(header, _split_cells(hl, spans))]
            rows, cur = [], None
            i += 1
            while i < len(lines) and not RULE_RE.match(lines[i]):
                if not lines[i].strip():
                    if cur:
                        rows.append(cur)
                    cur = None
                else:
                    cells = _split_cells(lines[i], spans)
                    cur = cells if cur is None else [(a + " " + b).strip() for a, b in zip(cur, cells)]
                i += 1
            if cur:
                rows.append(cur)
            i += 1   # borde inferior
        else:
            header_line = out.pop() if out and out[-1].strip() else ""
            header = _split_cells(header_line, spans)
            rows = []
            i += 1
            while i < len(lines) and lines[i].strip() and not RULE_RE.match(lines[i]):
                rows.append(_split_cells(lines[i], spans))
                i += 1
        out += [""] + _gfm(header, rows) + [""]
    return "\n".join(out)


def normalize_pandoc_markdown(text: str) -> str:
    text = convert_tables(text)
    for a, b in (("\\~", "~"), ("\\$", "$"), ("\\>", ">"), ("\\<", "&lt;"), ("\\*", "*")):
        text = text.replace(a, b)
    # si los apartados principales («# 0. …») están en nivel 1, bajarlos para que '##' sea el índice
    if len(re.findall(r"^# \d+\.", text, re.M)) >= 3:
        lines, first_title = [], True
        for l in text.split("\n"):
            m = re.match(r"^(#{1,5}) (.*)$", l)
            if m and not (first_title and len(m.group(1)) == 1 and not re.match(r"\d", m.group(2))):
                l = "#" + l
            elif m:
                first_title = False
            lines.append(l)
        text = "\n".join(lines)
    # subapartados escritos como apartado («# 1.3 …» o «## 1.3 …»): van al nivel 3 para que el índice muestre solo 0., 1., 2…
    text = re.sub(r"^#{1,2} (\d+\.\d+\b)", r"### \1", text, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text)
