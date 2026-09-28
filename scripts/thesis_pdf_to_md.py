"""
Convierte una tesis en PDF a Markdown por apartados (borrador para revisar a mano).

Uso:
    python scripts/thesis_pdf_to_md.py data/theses/X.pdf config/theses/x.md

La lógica está en app/application/services/thesis_pdf.py (la usa también «Nueva tesis desde PDF» en la web).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.application.services.thesis_pdf import convert

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write(convert(sys.argv[1]))
    print(f"Escrito {sys.argv[2]}")
