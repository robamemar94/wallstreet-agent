"""Pasa un Markdown de pandoc (tablas simples/multilínea, escapes, ---) a Markdown que entiende Python-Markdown."""
import re, sys

def spans(sep):
    return [(m.start(), m.end()) for m in re.finditer(r"-+", sep)]

import unicodedata

def _w(ch):
    if unicodedata.combining(ch) or ch in "️‍":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1

def slice_cols(line, a, b):
    """Trozo de la línea entre las columnas de pantalla a y b (los emoji ocupan dos)."""
    out, col = [], 0
    for ch in line:
        if a <= col < b:
            out.append(ch)
        col += _w(ch)
    return "".join(out)

def cells(line, cols):
    out = []
    for i, (a, b) in enumerate(cols):
        end = cols[i + 1][0] if i + 1 < len(cols) else 10**6
        out.append(slice_cols(line, a if i else 0, end).strip())
    return out

def pipe(rows):
    rows = [[c.replace("|", "\\|") for c in r] for r in rows]
    head, body = rows[0], rows[1:]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return lines

def convert_tables(lines):
    out, i = [], 0
    is_dash = lambda l: re.fullmatch(r"\s*-{3,}(\s+-{3,})*\s*", l or "") is not None
    while i < len(lines):
        l = lines[i]
        full = re.fullmatch(r"  -{10,}\s*", l)
        if full:   # multilínea: ---- / cabecera / -- -- / filas separadas por línea en blanco / ----
            j = i + 1
            header = []
            while j < len(lines) and not is_dash(lines[j]):
                header.append(lines[j]); j += 1
            cols = spans(lines[j]); j += 1
            rows, cur = [], []
            while j < len(lines) and not re.fullmatch(r"  -{10,}\s*", lines[j]):
                if lines[j].strip() == "":
                    if cur: rows.append(cur); cur = []
                else:
                    cur.append(lines[j])
                j += 1
            if cur: rows.append(cur)
            merge = lambda block: [" ".join(x for x in col if x) for col in zip(*[cells(b, cols) for b in block])]
            table = [merge(header)] + [merge(r) for r in rows]
            out += pipe(table); i = j + 1
            continue
        if l.startswith("  ") and l.strip() and i + 1 < len(lines) and is_dash(lines[i + 1]) and len(spans(lines[i + 1])) > 1:
            cols = spans(lines[i + 1])   # simple: cabecera / -- -- / filas hasta línea en blanco
            table = [cells(l, cols)]
            j = i + 2
            while j < len(lines) and lines[j].strip():
                table.append(cells(lines[j], cols)); j += 1
            out += pipe(table); i = j
            continue
        out.append(l); i += 1
    return out

src = open(sys.argv[1], encoding="utf-8").read()
lines = convert_tables(src.split("\n"))
text = "\n".join(lines)
text = re.sub(r"\\([$~<>\"'])", r"\1", text)        # escapes de pandoc
text = re.sub(r"\\\n", "  \n", text)                 # salto de línea con barra final
text = re.sub(r"(?<=\S) --- (?=\S)", " — ", text)    # raya
text = re.sub(r"(?<=\w)--(?=\w)", "–", text)         # rangos 15--20
text = re.sub(r"\n-   *\n", "\n", text)             # viñetas vacías
text = re.sub(r"\n# (🟢|🟡|🔴)([^\n]*)", r"\n**\1\2**", text)          # estados como encabezado
text = re.sub(r"\n# (ESTADO FINAL[^\n]*)", r"\n**\1**", text)
open(sys.argv[2], "w", encoding="utf-8").write(text)
