import json
import os
import tempfile

DATA_DIR = "data"
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")

def _get_filepath(ticker):
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    return os.path.join(DATA_DIR, f"{ticker.upper()}.json")

SETTINGS_FILE = os.path.join("config", "settings.json")

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    
    # Default settings
    return {
        "ui": {
            "items_per_page": 10,
            "dark_mode": False
        },
        "valuation": {
            "ganga": 0.60,
            "barata": 0.85,
            "justo_max": 1.15,
            "cara_max": 1.40
        },
        "performance": {
            "benchmark_ticker": "URTH",
            "cache_duration_min": 60
        }
    }

def save_settings(settings):
    config_dir = os.path.dirname(SETTINGS_FILE)
    if not os.path.exists(config_dir):
        os.makedirs(config_dir)
    
    fd, temp_path = tempfile.mkstemp(dir=config_dir, text=True)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)
    os.replace(temp_path, SETTINGS_FILE)

def load_data(ticker):
    filepath = _get_filepath(ticker)
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_data(ticker, key, value):
    data = load_data(ticker)
    data[key] = value
    filepath = _get_filepath(ticker)
    
    # Escritura Atómica para evitar que la web se rompa si el agente escribe
    fd, temp_path = tempfile.mkstemp(dir=DATA_DIR, text=True)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(temp_path, filepath)

def delete_data(ticker):
    filepath = _get_filepath(ticker)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            return True
        except Exception:
            return False
    return False

def get_all_analyzed_tickers():
    if not os.path.exists(DATA_DIR):
        return []
    tickers = []
    # Lista de archivos de sistema que NO son tickers
    system_files = ["portfolio.json", "closed_portfolio.json", "ledger.json", 
                    "manual_transactions.json", "historical_dividends.json", 
                    "historical_splits.json", "performance_cache.json", "alerts.json"]
    
    for filename in os.listdir(DATA_DIR):
        if filename.endswith(".json") and filename not in system_files:
            tickers.append(filename.replace(".json", ""))
    return sorted(tickers)

def get_all_analyzed_companies():
    """Retorna una lista de diccionarios con ticker y nombre de empresa."""
    tickers = get_all_analyzed_tickers()
    companies = []
    for t in tickers:
        data = load_data(t)
        companies.append({
            "ticker": t,
            "name": data.get("company_name", t)
        })
    return companies

def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def load_closed_portfolio():
    closed_file = os.path.join(DATA_DIR, "closed_portfolio.json")
    if os.path.exists(closed_file):
        try:
            with open(closed_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_closed_portfolio(portfolio_data):
    closed_file = os.path.join(DATA_DIR, "closed_portfolio.json")
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    
    fd, temp_path = tempfile.mkstemp(dir=DATA_DIR, text=True)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(portfolio_data, f, indent=4, ensure_ascii=False)
    os.replace(temp_path, closed_file)

def save_portfolio(portfolio_data):
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

    fd, temp_path = tempfile.mkstemp(dir=DATA_DIR, text=True)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(portfolio_data, f, indent=4, ensure_ascii=False)
    os.replace(temp_path, PORTFOLIO_FILE)

ALERTS_FILE = os.path.join(DATA_DIR, "alerts.json")

def load_alerts():
    if os.path.exists(ALERTS_FILE):
        try:
            with open(ALERTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"last_statuses": {}, "history": []}
    return {"last_statuses": {}, "history": []}

def save_alerts(alerts_data):
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    
    fd, temp_path = tempfile.mkstemp(dir=DATA_DIR, text=True)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(alerts_data, f, indent=4, ensure_ascii=False)
    os.replace(temp_path, ALERTS_FILE)

