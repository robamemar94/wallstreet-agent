"""
Cambios en la tesis a partir de las propuestas de la revisión de resultados.

El YAML de config/theses/ sigue siendo la fuente de verdad: los cambios se escriben en él (guardando antes una copia
de la versión anterior en config/theses/history/), se validan y se reimporta la tesis. Riesgos y catalizadores no
tocan el cuadro de mando: van a la lista de vigilancia de la tesis.
"""
import json
import os
import re
import shutil
from datetime import datetime
from typing import Any, Dict, Optional

from app.application.services.thesis_ai_service import GEMINI_MODEL, _client, _generate, extract_json
from app.application.services.thesis_service import load_thesis_yaml
from utils import llm_usage

HISTORY_DIR = os.path.join("config", "theses", "history")
KPI_FIELDS = ("name", "unit", "kind", "direction", "green_threshold", "red_threshold", "green_text", "amber_text",
              "red_text", "how_to_measure", "baseline", "is_kill_switch", "kill_rule")
THRESHOLD_FIELDS = ("green_threshold", "red_threshold", "green_text", "amber_text", "red_text", "direction")


def _slug(text: str) -> str:
    import unicodedata
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")
    return s[:40] or "kpi"


def draft_change(thesis: Dict[str, Any], proposal: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte una propuesta (título + motivo) en un cambio concreto del cuadro de mando. 1 llamada Flash, sin búsqueda."""
    kpis = "\n".join(f"- key={k['key']} | {k['name']} | unidad {k.get('unit') or '-'} | verde {k.get('green_text')} | "
                     f"rojo {k.get('red_text')} | umbrales {k.get('green_threshold')}/{k.get('red_threshold')} ({k.get('direction')})"
                     for p in thesis["pillars"] for k in p["kpis"])
    prompt = f"""Traduce esta propuesta de cambio en la tesis de {thesis['ticker']} en un cambio CONCRETO de su cuadro de mando.

PROPUESTA: [{proposal.get('type')}] {proposal.get('title')} — {proposal.get('rationale')}

KPIs ACTUALES:
{kpis}

Si es un KPI nuevo: defínelo cuantificable, con zonas verde/amarillo/rojo económicamente justificadas.
Si es un cambio de umbral: indica el kpi_key existente y los nuevos valores.
"direction": "higher" si más es mejor, "lower" si menos es mejor. Umbrales numéricos en la unidad del KPI.

DEVUELVE ÚNICAMENTE UN JSON VÁLIDO:
{{"action": "add_kpi|change_threshold",
  "kpi_key": "solo para change_threshold",
  "step_name": "nombre del paso (solo add_kpi)", "step_question": "pregunta/hipótesis que responde (solo add_kpi)",
  "kpi": {{"name": "…", "unit": "%", "kind": "numeric", "direction": "higher", "green_threshold": 0, "red_threshold": 0,
          "green_text": "…", "amber_text": "…", "red_text": "…", "how_to_measure": "…"}}}}"""
    with llm_usage.track_usage(f"{thesis['ticker']}/draft-change"):
        data = extract_json(_generate(_client(), GEMINI_MODEL, prompt)["text"])
    kpi = {k: v for k, v in (data.get("kpi") or {}).items() if k in KPI_FIELDS}
    return {"action": data.get("action") if data.get("action") in ("add_kpi", "change_threshold") else "add_kpi",
            "kpi_key": data.get("kpi_key"), "step_name": data.get("step_name") or kpi.get("name"),
            "step_question": data.get("step_question") or "", "kpi": kpi}


def _yaml_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)   # JSON es YAML válido (también dentro de un mapping en línea)


def _flow_mapping(d: Dict[str, Any]) -> str:
    return "{" + ", ".join(f"{k}: {_yaml_value(v)}" for k, v in d.items() if v is not None and v != "") + "}"


def add_kpi_to_yaml(text: str, change: Dict[str, Any], existing_keys: set) -> str:
    """Añade un paso nuevo con su KPI al final de `pillars` (que es el último bloque del YAML)."""
    kpi = {k: v for k, v in change["kpi"].items() if k in KPI_FIELDS}
    if not kpi.get("name"):
        raise ValueError("El KPI nuevo necesita un nombre")
    key = _slug(kpi["name"])
    while key in existing_keys:
        key += "_2"
    if kpi.get("kind", "numeric") == "numeric" and kpi.get("green_threshold") in (None, ""):
        raise ValueError("Un KPI numérico necesita al menos el umbral verde")
    block = (f"\n  - key: {key}\n    name: {_yaml_value(change.get('step_name') or kpi['name'])}\n"
             f"    description: {_yaml_value(change.get('step_question') or '')}\n    kpis:\n"
             f"      - {_flow_mapping({'key': key, **kpi})}\n")
    if not re.search(r"^pillars:\s*$", text, re.M):
        raise ValueError("El YAML no tiene bloque 'pillars'")
    return text.rstrip("\n") + "\n" + block


def _kpi_span(text: str, kpi_key: str) -> Optional[tuple]:
    m = re.search(r"\{\s*key:\s*" + re.escape(kpi_key) + r"\s*,", text)
    if not m:
        return None
    depth, i, in_str = 0, m.start(), False
    while i < len(text):
        c = text[i]
        if c == '"' and text[i - 1] != "\\":
            in_str = not in_str
        elif not in_str and c in "{[":
            depth += 1
        elif not in_str and c in "}]":
            depth -= 1
            if depth == 0:
                return m.start(), i + 1
        i += 1
    return None


def change_threshold_in_yaml(text: str, kpi_key: str, fields: Dict[str, Any]) -> str:
    span = _kpi_span(text, kpi_key)
    if not span:
        raise ValueError(f"No se encuentra el KPI '{kpi_key}' en el YAML")
    block = text[span[0]:span[1]]
    for field, value in fields.items():
        if field not in THRESHOLD_FIELDS or value is None:
            continue
        pattern = re.compile(r"(\b" + field + r":\s*)(\"(?:[^\"\\]|\\.)*\"|[^,}\n]*)")
        if pattern.search(block):
            block = pattern.sub(lambda m: m.group(1) + _yaml_value(value), block, count=1)
        else:
            block = block[:-1].rstrip() + f", {field}: {_yaml_value(value)}" + "}"
    return text[:span[0]] + block + text[span[1]:]


def apply_to_yaml(spec_path: str, change: Dict[str, Any], existing_keys: set) -> str:
    """Escribe el cambio en el YAML (validándolo antes) y guarda la versión anterior. Devuelve la ruta de la copia."""
    with open(spec_path, encoding="utf-8") as f:
        original = f.read()
    if change["action"] == "change_threshold":
        updated = change_threshold_in_yaml(original, change["kpi_key"], change["kpi"])
    else:
        updated = add_kpi_to_yaml(original, change, existing_keys)
    tmp = spec_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(updated)
    try:
        load_thesis_yaml(tmp)          # si el resultado no es una tesis válida, no se toca el original
    except Exception:
        os.remove(tmp)
        raise
    os.makedirs(HISTORY_DIR, exist_ok=True)
    backup = os.path.join(HISTORY_DIR, f"{os.path.splitext(os.path.basename(spec_path))[0]}-{datetime.now():%Y%m%d-%H%M%S}.yaml")
    shutil.copy2(spec_path, backup)
    os.replace(tmp, spec_path)
    return backup



# --- Preguntas maestras ---

def add_master_question_to_yaml(text: str, question: str) -> str:
    question = " ".join((question or "").split())
    if not question:
        raise ValueError("La pregunta está vacía")
    line = f"  - {_yaml_value(question)}\n"
    m = re.search(r"^master_questions:\s*\n((?:[ \t]+-.*\n?)*)", text, re.M)
    if m:
        block = m.group(1)
        if question in block:
            raise ValueError("Esa pregunta ya está en la tesis")
        insert_at = m.end(1)
        if block and not block.endswith("\n"):
            line = "\n" + line
        return text[:insert_at] + line + text[insert_at:]
    # la tesis aún no tiene preguntas maestras: se crea el bloque antes de 'pillars'
    p = re.search(r"^pillars:", text, re.M)
    if not p:
        raise ValueError("El YAML no tiene bloque 'pillars'")
    return text[:p.start()] + "master_questions:\n" + line + "\n" + text[p.start():]


def remove_master_question_from_yaml(text: str, question: str) -> str:
    m = re.search(r"^master_questions:\s*\n((?:[ \t]+-.*\n?)*)", text, re.M)
    if not m:
        raise ValueError("La tesis no tiene preguntas maestras")
    import yaml
    kept, removed = [], False
    for raw in m.group(1).splitlines(keepends=True):
        value = yaml.safe_load(raw.strip()[1:].strip() or '""')
        if not removed and str(value).strip() == question.strip():
            removed = True
            continue
        kept.append(raw)
    if not removed:
        raise ValueError("No se encuentra esa pregunta en la tesis")
    new_block = "".join(kept)
    if not new_block.strip():   # sin preguntas: se quita también la clave
        return text[:m.start()] + text[m.end():]
    return text[:m.start(1)] + new_block + text[m.end(1):]


def apply_text_change(spec_path: str, transform) -> str:
    """Aplica una transformación de texto al YAML con la misma seguridad que apply_to_yaml
    (valida el resultado y guarda la versión anterior). Devuelve la ruta de la copia."""
    with open(spec_path, encoding="utf-8") as f:
        original = f.read()
    updated = transform(original)
    tmp = spec_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(updated)
    try:
        load_thesis_yaml(tmp)
    except Exception:
        os.remove(tmp)
        raise
    os.makedirs(HISTORY_DIR, exist_ok=True)
    backup = os.path.join(HISTORY_DIR, f"{os.path.splitext(os.path.basename(spec_path))[0]}-{datetime.now():%Y%m%d-%H%M%S}.yaml")
    shutil.copy2(spec_path, backup)
    os.replace(tmp, spec_path)
    return backup
