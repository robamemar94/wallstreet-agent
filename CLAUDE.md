# Alpha-Flow — plataforma personal de inversión

FastAPI + Jinja2. Cartera (compras, ventas, dividendos, P&L multidivisa), watchlist con zonas de valoración,
tesis de inversión con radar semanal de noticias, Centro de mando (avisos, agenda, resumen) y agentes de IA (CrewAI + Gemini).

**Dónde está el detalle:**
- `app/CLAUDE.md`: conceptos de dominio. Se carga al tocar `app/`.
- `templates/CLAUDE.md`: convenciones de interfaz. Se carga al tocar `templates/`.
- `docs/pantallas/<pantalla>.md`: qué hace cada pantalla. **Léelo antes de cambiar esa pantalla.**

## Reglas de trabajo
- **Responder en español.** Toda la interfaz está en español.
- **Sin scroll en las páginas (importante):** cada pantalla o pestaña cabe en la ventana.
  - Lo largo va paginado con las filas que quepan (`rowsPerPage`/`pageOverflowRows` en `base.html`) o con scroll interno
    del bloque (`fitScroll`).
  - Lo primero que se ve tiene que entenderse sin leer: la respuesta arriba y el detalle debajo o al lado.
  - Comprobarlo con la skill `captura` (mide el scroll y guarda la captura; en `wallstreet/.claude/skills/`).
- **Filtros que no se pierden:**
  - los filtros de una pantalla van en la dirección (`?nivel=tier1…`), con `urlState.get/set`;
  - las pestañas, con `#…`;
  - las preferencias fijas del usuario, en `localStorage`.
- **Números en formato español** (1.234,56): `num()` en plantillas, `fmtNum()` en JS, `utils/number_format.es()` en
  Python. Los `value=`, `data-*` y los scripts siguen usando punto decimal.
- **Nada de llamadas automáticas a la IA (Gemini):** solo cuando el usuario pulsa un botón. Nada que gaste tokens por sí
  solo. Excepciones decididas por el usuario:
  - el radar semanal de noticias;
  - desde el 13/10/2026, la revisión trimestral y la carta anual de los informes (Gemini Pro, unos céntimos).
- **Git:** dejar en stage (`git add`) lo que se crea o se cambia. **No hacer commit** salvo que se pida.
  - Cuando el usuario dice «stash» quiere decir el stage.
  - Si un archivo tiene cambios suyos sin relación, solo los trozos propios.
  - Nunca `data/alpha_flow.db`, `.env` ni `__pycache__`.
- **Cuándo preguntar:**
  - cambios pequeños: hacerlos sin pedir confirmación y avisar; preguntar solo las decisiones importantes;
  - funciones grandes: comentar el diseño antes de programar.
  - al terminar una función grande, pasarla por el subagente `revisor` antes de darla por terminada.
- **Textos de ayuda breves y profesionales:** globos y leyendas de una línea (≈ 80 caracteres), sin explicar lo
  obvio ni repetir reglas internas («no cuenta en las cifras de hoy», «pulsa para…»).
- **Destacado sutil:** color solo en las cifras clave. Nada de barras, fondos ni leyendas pesadas, salvo que el usuario
  lo pida (p. ej. el fondo suave de la Var. día € de mercado abierto).
- **Mantener la documentación al día:** al terminar algo importante, actualizar lo que toque, dejarlo en stage y decirlo
  en el resumen final:
  - el `docs/pantallas/*.md` de la pantalla;
  - el mapa de abajo, si hay pantalla, archivo o ruta nueva;
  - `app/CLAUDE.md` o `templates/CLAUDE.md`, si hay concepto o convención nueva.
  Aquí, solo reglas y mapa. No hace falta por arreglos pequeños.

## Arrancar y probar
```bash
uv run uvicorn main:app --reload        # http://127.0.0.1:8000 (normalmente ya está arrancada)
alembic upgrade head                    # migraciones (SQLite)
uv run pytest -q                        # suite completa, ~90 s, sin internet
uv run pytest -q tests/test_notices.py  # solo un área
uv run pytest -q -m network             # los que llaman a yfinance de verdad (se saltan por defecto)
```
- **Base de datos de los tests:** usan una **copia temporal** (`tests/conftest.py` + variable `ALPHA_FLOW_DB`), nunca la
  real. Si un test nuevo necesita internet, márcalo con `@pytest.mark.network`.
- **ruff** (solo reglas F): `uv run ruff check --fix .`. El hook `.githooks/pre-commit` lo pasa sobre los `.py` del
  commit (en un clon nuevo: `git config core.hooksPath .githooks`).
- **yfinance:**
  - `yf.download` no aguanta dos llamadas a la vez: `utils/yf_guard.py` (se instala en `main.py`) las pone en fila;
  - para descargas en paralelo, `yf.Ticker(t).history(...)`;
  - el aviso de máximo/mínimo de 52 semanas exige casi un año de histórico (`MIN_52W_SPAN_DAYS`).
- **Ver una página sin navegador:** `curl -s http://127.0.0.1:8000/mercado/centro | grep ...`

## Mapa: pantalla → archivos
Vistas en `app/interfaces/views/`; servicios en `app/application/services/`.

| Pantalla | Ruta | Vista | Plantillas | Servicios | Detalle |
|---|---|---|---|---|---|
| Portada (Mercado + Centro de mando) | `/mercado`, `/`, `/portada/bandeja`, `/portada/agenda`, `/api/centro/pulso`, `/mercado/centro` | `market_views.py`, `portada_views.py` | `index.html`, `_portada_bandeja.html`, `_portada_agenda.html`, `_mercado_centro.html`, `_agenda.html` | `market_cache`, `price_monitor_service`, `agenda_service`, `notification_service` | `portada.md` |
| Cartera | `/portfolio`, `/portfolio/seguimiento`, `/portfolio/decisiones`, `/portfolio/informes` | `portfolio_views.py`, `seguimiento_views.py` | `portfolio.html` (partida en `_cartera/`), `_positions_tracking.html`, `_decisions.html`, `_reports.html` | `portfolio_service`, `performance_service`, `distribution_service`, `dividend_forecast`, `decision_review_service`, `report_service`, `cash_service`, `robo_service` | `cartera.md`, `informes.md` |
| Ficha de una posición | `/portfolio/ticker/{t}` | `portfolio_views.py` | `portfolio_ticker.html` | | |
| Ficha de una empresa | `/ticker/{t}` y subrutas; Diario: `/ticker/{t}/diario`, `/api/ticker/{t}/notas`, `/api/notas/{id}` | `ticker_views.py`, `diary_views.py` | `ticker.html` (partida en `_ficha/`), `_insiders.html`, `_my_position.html`, `_diario.html` | `company_notes_service`, `financials_update_service`, `base_valuation_service`, `key_figures_service`, `insider_service`, `estimate_revisions_service`, `position_review_service`, `financial_trends_service`, `financial_labels`, `market_cap_service` | `ficha.md` |
| Diario (notas de todas las empresas y operaciones sin porqué) | `/diario?ticker=…` | `diary_views.py` | `diario.html` | `company_notes_service` | `ficha.md` |
| Watchlist | `/watchlist`, `/watchlist/tabla` | `watchlist_views.py` | `watchlist.html`, `_watchlist.html`, `_watchlist_table.html` | `alerts_service`, `tier_service` | `watchlist.md` |
| Tesis | `/tesis`, `/tesis/{id}` | `thesis_views.py` | `theses.html`, `thesis_detail.html`, `_thesis_macros.html`, `_thesis_review.html` | `thesis_service`, `thesis_review_service`, `thesis_ai_service` | |
| Noticias (radar) | `/noticias?ticker=…&semana=…` | `thesis_views.py` | `news.html` | `thesis_news_runner`, `news_summary_service` | `noticias.md` |
| Calendario (ventana sobre Mercado) | `/api/calendario` | `calendar_views.py` | (en `base.html`) | `results_calendar` | `noticias.md` |
| Comparar | `/comparar` | `comparar_views.py` | `comparar.html` | | |
| Base de datos de empresas | `/database`, `/search`, `/api/database/variacion` | `database_views.py` | `database.html` | `tier_service` | |
| Listas | `/lists`, `/api/lists/{id}/…` | `lists_views.py` | `lists.html` | `tier_service` | |
| Roboadvisor (MyInvestor) | `/roboadvisor`, `/api/portfolio/roboadvisor…` | `robo_views.py` | `roboadvisor.html`, `_cartera/js/roboadvisor.html` | `robo_service` | `roboadvisor.md` |
| Avisos (`/alerts` lleva aquí) | `/avisos`, `/avisos/lista` | `alerts_views.py` | `avisos.html`, `_avisos_list.html` | `notification_service` | `avisos.md` |
| Ajustes / uso de IA | `/settings`, `/settings/ia`, `/ia` | `settings_views.py` | `settings.html`, `settings_ai.html` | `ai_usage_service` | |

## Stack
- FastAPI, Jinja2, SQLAlchemy 2 + Alembic (SQLite en `data/alpha_flow.db`), yfinance, CrewAI + Gemini (Flash por defecto, Pro en los agentes `model: pro`; `GEMINI_MODEL`/`GEMINI_PRO_MODEL`), `uv`,
  Python 3.13.
- Agentes en `agents/` (prompts en `config/agents.yaml`, `tasks.yaml`, `prompts.yaml`).
- Variables en `.env`:
  - `GOOGLE_API_KEY`/`GEMINI_API_KEY`, `SERPER_API_KEY`, `CREWAI_TELEMETRY_OPT_OUT=true`;
  - `ALPHAVANTAGE_API_KEY` (opcional: plan B del histórico financiero, `utils/alpha_vantage.py`);
  - `BACKUP_EXTRA_DIR` (opcional: carpeta donde duplicar las copias de seguridad; ver `app/CLAUDE.md`).

## No leer (ruido)
`scripts/legacy/`, `data/*.json` (cachés), `logos_cache/`, `migrations/versions/` (salvo que se toquen migraciones).
