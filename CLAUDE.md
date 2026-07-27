# Alpha-Flow — Investment Analysis Platform

## What it does
FastAPI web app that combines portfolio tracking with AI-driven investment analysis (CrewAI + Gemini). Tracks buy/sell/dividend transactions, calculates P&L, and runs 5 specialized agents to produce financial, moat, capital allocation, and valuation analyses.

## How to run
```bash
python main.py          # starts at http://127.0.0.1:8000
alembic upgrade head    # apply DB migrations (SQLite)
```

## Tech stack
- **Backend**: FastAPI + Jinja2 templates, Uvicorn
- **AI**: CrewAI v0.100+, Google Gemini 2.5 Pro
- **DB**: SQLite via SQLAlchemy 2.0 + Alembic migrations
- **Data**: yfinance, DuckDuckGo search
- **Package manager**: `uv` (lockfile: `uv.lock`)
- **Python**: 3.11+

## Architecture (Clean Architecture)
```
app/
  application/services/   # business logic (portfolio, performance, alerts)
  domain/                 # Pydantic models + repository interfaces
  infrastructure/         # SQLAlchemy DB, repository implementations, config
  interfaces/
    api/                  # REST endpoints
    views/                # HTML template routes
agents/                   # 5 CrewAI agents
tools/                    # data-fetching tools (yfinance, Gemini, DDG)
config/                   # YAML: agents.yaml, tasks.yaml, prompts.yaml, settings.json
templates/                # Jinja2 HTML
data/                     # SQLite DB (alpha_flow.db) + JSON files
migrations/               # Alembic versions
```

## Key files
| File | Purpose |
|---|---|
| `main.py` | FastAPI app init + route registration |
| `app/interfaces/views/market_views.py` | Main dashboard route (582 lines) |
| `app/application/services/portfolio_service.py` | Portfolio calculations (313 lines) |
| `app/application/services/performance_service.py` | Performance metrics (261 lines) |
| `app/application/services/alerts_service.py` | Alert generation (133 lines) |
| `agents/utils.py` | Agent orchestration |
| `tools/search_tool.py` | Data fetching tools |
| `config/agents.yaml` | Agent roles, goals, methodology |
| `config/tasks.yaml` | Task definitions + expected outputs |
| `storage.py` | JSON file I/O |
| `utils/financial_math.py` | CAGR, FCF Yield |

## Environment variables (`.env`)
```
GOOGLE_API_KEY / GEMINI_API_KEY   # Gemini API
SERPER_API_KEY                     # web search
CREWAI_TELEMETRY_OPT_OUT=true
PYTHONPATH=.
```

## Database
- **Location**: `./data/alpha_flow.db` (SQLite)
- **Key tables**: `assets`, `transactions`, `ledger_entries`, `portfolio_items`, `settings`, `alerts`, `analysis_tasks`
- Legacy JSON files in `data/*.json` (migrated via `scripts/migrate_json_to_sqlite.py`)

## The 5 AI agents
1. **Financial Analyst** — cash flow, earnings quality, balance sheet audit
2. **Capital Allocation Specialist** — buybacks, dividends, M&A, SBC evaluation
3. **Moat Analyst** — competitive advantage and durability assessment
4. **Investment Director** — synthesizes all analyses into buy/hold/sell decision
5. **Valuation Agent** — IRR scenarios, target entry price for 20% return

## Settings (`config/settings.json`)
```json
{
  "ui": { "items_per_page": 10, "dark_mode": true },
  "valuation": { "ganga": 0.7, "barata": 0.85, "justo_max": 1.15, "cara_max": 1.3 },
  "performance": { "benchmark_ticker": "URTH", "cache_duration_min": 60 }
}
```

## Multi-currency
Supports EUR, GBP, JPY, etc. with automatic FX conversion via yfinance.
