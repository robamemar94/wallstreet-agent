# Alpha-Flow — plataforma personal de inversión

FastAPI + Jinja2. Cartera (compras, ventas, dividendos, P&L multidivisa), watchlist con zonas de valoración,
tesis de inversión con radar semanal de noticias, Centro de mando (avisos, agenda, resumen) y agentes de IA (CrewAI + Gemini).

## Reglas de trabajo
- **Responder en español.** Toda la interfaz está en español.
- **Números en formato español** (1.234,56): `num()` en plantillas, `fmtNum()` en JS (definido en `base.html`),
  `utils/number_format.es()` en Python. Los `value=`, `data-*` y los scripts siguen usando punto decimal.
- **Nada de llamadas automáticas a la IA (Gemini):** solo cuando el usuario pulsa un botón. Nada que gaste tokens por sí solo.
- **Git:** dejar en stage (`git add`) lo que se crea o se cambia. **No hacer commit** salvo que se pida.
- Cambios pequeños: hacerlos sin pedir confirmación y avisar. Preguntar solo las decisiones importantes.
- **Mantener este archivo al día:** al terminar algo importante (pantalla o pestaña nueva, servicio nuevo, archivos que se
  mueven o se parten, una regla o convención nueva, un cambio en cómo se prueba o se arranca), actualizar el mapa
  o la sección que toque, dejarlo en stage y decirlo en el resumen final. No hace falta por arreglos pequeños.

## Arrancar y probar
```bash
uv run uvicorn main:app --reload        # http://127.0.0.1:8000 (normalmente ya está arrancada)
alembic upgrade head                    # migraciones (SQLite)
PYTHONPATH=. uv run --with pytest python -m pytest -q tests/test_notices.py   # pytest NO está en el venv
```
- La suite completa tarda unos 5 min: mejor pasar solo los tests del área que se ha tocado.
- Para ver una página sin navegador: `curl -s http://127.0.0.1:8000/mercado/centro | grep ...`

## Mapa: pantalla → archivos
| Pantalla | Ruta | Vista | Plantillas | Servicios |
|---|---|---|---|---|
| Mercado (portada) | `/mercado` | `market_views.py` | `index.html` | `market_cache`, `price_monitor_service` |
| Centro de mando (pestaña de Mercado; se carga aparte) | `/mercado/centro`, `/centro` | `thesis_views.py` (`mercado_centro_fragment`) | `_mercado_centro.html`, `_agenda.html` (Agenda + Resumen), `_notices.html` (avisos, monitor, leído/no leído), `_centro_tabs.html` | `agenda_service`, `notice_service`, `notification_service` |
| Cartera | `/portfolio`, `/portfolio/seguimiento` | `portfolio_views.py`, `thesis_views.py` | `portfolio.html`, `_positions_tracking.html` | `portfolio_service`, `performance_service`, `distribution_service`, `dividend_forecast` |
| Ficha de una posición | `/portfolio/ticker/{t}` | `portfolio_views.py` | `portfolio_ticker.html` | |
| Ficha de una empresa | `/ticker/{t}`, `/api/history/{t}` | `ticker_views.py` | `ticker.html` | `base_valuation_service`, `market_cap_service` |
| Watchlist | `/watchlist` | `watchlist_views.py` | `watchlist.html`, `_watchlist.html`, `_watchlist_table.html` | `alerts_service` (zonas) |
| Tesis | `/tesis`, `/tesis/{id}` | `thesis_views.py` | `theses.html`, `thesis_detail.html`, `_thesis_macros.html`, `_thesis_review.html` | `thesis_service`, `thesis_review_service`, `thesis_ai_service` |
| Noticias (radar) | `/noticias` | `thesis_views.py` | `news.html` | `thesis_news_runner`, `news_summary_service` |
| Calendario (ventana sobre Mercado) | `/api/calendario` | `calendar_views.py` | (en `base.html`) | `results_calendar` |
| Comparar | `/comparar` | `comparar_views.py` | `comparar.html` | |
| Base de datos de empresas | `/database`, `/search`, `/api/database/variacion` | `database_views.py` | `database.html` | |
| Listas | `/lists` | `lists_views.py` | `lists.html` | |
| Ajustes / uso de IA | `/settings`, `/settings/ia`, `/ia` | `settings_views.py` | `settings.html`, `settings_ai.html` | `ai_usage_service` |

- `market_views.py` guarda lo común de precios: `INDEX_CACHE`, `ensure_prices_cached`, `get_currency_symbol`, refresco de precios
  (lo importan las demás vistas y `price_scheduler`). `/oportunidades` redirige a `/watchlist`.
- Las vistas HTML están en `app/interfaces/views/`; la API JSON en `app/interfaces/api/` (prefijo `/api`).
- Los servicios están en `app/application/services/`.
- `base.html` lleva el menú, la campana de notificaciones, los globos de no leídos y los helpers JS comunes (`fmtNum`…).
- Tareas en segundo plano: `cache_warmer` (precarga cada 6 h) y `price_scheduler`.

## Conceptos que se repiten
- **Avisos y señales:** `notice_service.track()` les da una clave estable y guarda leído/descartado en BBDD
  (`/api/notices/seen`, `/api/notices/dismiss`). El recuento de no leídos **solo cuenta la cartera** (`data-held`).
  Ese recuento aparece en varios sitios (lista, pestaña del Centro, Resumen, globo del menú, `/api/centro/pulso`):
  si se cambia uno, hay que revisar los demás.
- **Tesis:** `config/theses/<ticker>.yaml` (datos y reglas) + `.md` (texto). Los documentos fuente están en `data/theses/`.
- **Valoración:** un único caso base con selector BPA o FCF. Zonas en `config/settings.json` → `valuation`.
- **Multidivisa:** cambio de divisa automático con yfinance. Los importes de la cartera se muestran en €.

## Stack
FastAPI, Jinja2, SQLAlchemy 2 + Alembic (SQLite en `data/alpha_flow.db`), yfinance, CrewAI + Gemini 2.5 Pro, `uv`, Python 3.13.
Agentes en `agents/` (prompts en `config/agents.yaml`, `tasks.yaml`, `prompts.yaml`).
Variables en `.env`: `GOOGLE_API_KEY`/`GEMINI_API_KEY`, `SERPER_API_KEY`, `CREWAI_TELEMETRY_OPT_OUT=true`.

## No leer (ruido)
`scripts/legacy/` (scripts sueltos antiguos), `data/*.json` (cachés), `logos_cache/`, `migrations/versions/` (salvo que se toquen migraciones).
