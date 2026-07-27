import yaml
import os

def _load_yaml(file_path):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"El archivo de configuración {file_path} no existe.")
    with open(file_path, 'r', encoding='utf-8') as file:
        return yaml.safe_load(file)

def load_agent_config(file_path="config/agents.yaml"):
    """Carga la configuración de los agentes desde el archivo YAML."""
    return _load_yaml(file_path)

def load_task_config(file_path="config/tasks.yaml"):
    """Carga la configuración de las tareas desde el archivo YAML."""
    return _load_yaml(file_path)

def load_prompts_config(file_path="config/prompts.yaml"):
    """Carga la configuración de prompts adicionales desde el archivo YAML."""
    return _load_yaml(file_path)
