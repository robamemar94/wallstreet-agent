# Alpha-Flow — plataforma personal de inversión

FastAPI + Jinja2. Cartera (compras, ventas, dividendos, P&L multidivisa), watchlist con zonas de valoración,
tesis de inversión con radar semanal de noticias, Centro de mando (avisos, agenda, resumen) y agentes de IA (CrewAI + Gemini).

## Reglas de trabajo
- **Responder en español.** Toda la interfaz está en español.
- **Sin scroll en las páginas (importante):** cada pantalla o pestaña cabe en la ventana. Lo largo va paginado con las filas
  que quepan (`rowsPerPage`/`pageOverflowRows` en `base.html`, recalculado al cambiar el tamaño) o con scroll interno del
  bloque (`fitScroll`). Lo primero que se ve tiene que entenderse sin leer: la respuesta arriba y el detalle debajo o al lado.
  Comprobarlo con `google-chrome --headless=new --window-size=1440,900 --virtual-time-budget=25000 --screenshot=... URL`.
- **Filtros que no se pierden:** los filtros de una pantalla van en la dirección (`?nivel=tier1&region=usa…`) con
  `urlState.get/set` (en `base.html`), para que sigan al recargar, al volver atrás o al guardar el enlace; las pestañas, con
  `#…`. Las preferencias fijas de cada usuario (p. ej. una vista elegida), en `localStorage`. Hecho en Watchlist, en la
  «Actividad del día» de Mercado y en la Base de datos (pestaña, búsqueda, filtros, nota, orden y periodo). Ojo: leer la
  dirección al cargar ANTES del primer filtrado (el arranque guarda su estado y la pisaría).
- **Números en formato español** (1.234,56): `num()` en plantillas, `fmtNum()` en JS (definido en `base.html`),
  `utils/number_format.es()` en Python. Los `value=`, `data-*` y los scripts siguen usando punto decimal.
- **Nada de llamadas automáticas a la IA (Gemini):** solo cuando el usuario pulsa un botón. Nada que gaste tokens por sí solo.
  Excepciones decididas por el usuario: el radar semanal de noticias y, desde el 13/10/2026, la revisión trimestral y la
  carta anual de los informes (se escriben solas al cerrarse el periodo; Gemini Pro, unos céntimos).
- **Git:** dejar en stage (`git add`) lo que se crea o se cambia. **No hacer commit** salvo que se pida.
- Cambios pequeños: hacerlos sin pedir confirmación y avisar. Preguntar solo las decisiones importantes.
- **Mantener este archivo al día:** al terminar algo importante (pantalla o pestaña nueva, servicio nuevo, archivos que se
  mueven o se parten, una regla o convención nueva, un cambio en cómo se prueba o se arranca), actualizar el mapa
  o la sección que toque, dejarlo en stage y decirlo en el resumen final. No hace falta por arreglos pequeños.

## Arrancar y probar
```bash
uv run uvicorn main:app --reload        # http://127.0.0.1:8000 (normalmente ya está arrancada)
alembic upgrade head                    # migraciones (SQLite)
uv run pytest -q                        # suite completa, ~40 s, sin internet
uv run pytest -q tests/test_notices.py  # solo un área
uv run pytest -q -m network             # los que llaman a yfinance de verdad (se saltan por defecto)
```
- Los tests usan una **copia temporal** de la base de datos (`tests/conftest.py` + variable `ALPHA_FLOW_DB`); nunca la real.
  Si un test nuevo necesita internet, márcalo con `@pytest.mark.network`.
- **ruff** (solo reglas F: nombres sin definir, imports y variables que no se usan): `uv run ruff check --fix .`
  El hook `.githooks/pre-commit` lo pasa sobre los `.py` del commit (en un clon nuevo: `git config core.hooksPath .githooks`).
- Para ver una página sin navegador: `curl -s http://127.0.0.1:8000/mercado/centro | grep ...`

## Mapa: pantalla → archivos
| Pantalla | Ruta | Vista | Plantillas | Servicios |
|---|---|---|---|---|
| Mercado (portada) | `/mercado` | `market_views.py` | `index.html` | `market_cache`, `price_monitor_service` |
| Centro de mando (pestaña de Mercado, oculta: se elige con los accesos «Mercado | Centro» de arriba a la izquierda; se carga aparte). Arriba el Resumen en franja (Hoy / 7 / 30 días, un bloque por tema en horizontal) y la Agenda compacta (una línea por día) a su derecha, con el mismo alto; debajo, casi todo el alto para la **Bandeja**: Pendiente agrupado (`grouped_inbox` en `_notices.html`: los avisos por tipo y, debajo, «Movimientos de precio»: caídas/subidas fuertes, se mueve solo, mínimos y máximos de 52 s; una línea por grupo con las empresas como etiquetas, una línea por empresa con su texto breve, leído y descartar en cada línea y, al pulsarla, su detalle completo) y Noticias al lado | `/mercado/centro`, `/centro` | `thesis_views.py` (`mercado_centro_fragment`) | `_mercado_centro.html`, `_agenda.html` (`agenda_compact`, Resumen), `_notices.html` (avisos, monitor, leído/no leído), `_centro_tabs.html` | `agenda_service`, `notice_service`, `notification_service` |
| Cartera (pestañas: Resumen (la primera, pero se entra en Posiciones); Posiciones = Contable · Seguimiento · Fundamentales; Rendimiento = Gráfica · Informes; Distribución; Dividendos; Operaciones = Historial · Decisiones) | `/portfolio` (`#fundamentales`, `#rendimiento`, `#informes`, `#historial`, `#decisiones`, `#resumen`…), `/portfolio/seguimiento`, `/portfolio/decisiones`, `/portfolio/informes` | `portfolio_views.py` (`portfolio_overview` para el Resumen), `thesis_views.py` | `portfolio.html` (menú agrupado `data-pf-group`; las pestañas de Bootstrap de siempre siguen ocultas en `#portfolioTabs`), `_positions_tracking.html`, `_decisions.html`, `_reports.html` | `portfolio_service`, `performance_service`, `distribution_service`, `dividend_forecast`, `decision_review_service`, `report_service` |
| Ficha de una posición | `/portfolio/ticker/{t}` | `portfolio_views.py` | `portfolio_ticker.html` | |
| Ficha de una empresa (cabecera con insignias: calidad, zona · CAGR, tesis, en cartera; nivel y «⋯ Gestionar» con rol, estado, avisos, listas y borrar. Resumen: Calidad con los factores clave en una ventana, el Resumen llena la ventana (el gráfico se estira), cuatro indicadores —a compra, tesis, previsiones, técnico en directo (media 200, RSI, desde máximo)— y el gráfico. La pestaña Técnico va oculta: su informe de IA se abre desde el indicador) | `/ticker/{t}`, `/api/history/{t}`, `/ticker/{t}/directivos`, `/api/ticker/{t}/precio-valoracion` | `ticker_views.py` | `ticker.html`, `_insiders.html` (recuadro «Directivos y acciones» del Resumen: datos de mercado, aparte del análisis de IA; incluye la previsión de BPA) | `base_valuation_service`, `market_cap_service`, `insider_service`, `estimate_revisions_service` |
| Watchlist (tabla a todo lo ancho, agrupada por cercanía a compra con barra de distancia; avisos y monitor en paneles laterales; filtros en un desplegable) | `/watchlist` | `watchlist_views.py` | `watchlist.html`, `_watchlist.html`, `_watchlist_table.html` | `alerts_service` (zonas) |
| Tesis | `/tesis`, `/tesis/{id}` | `thesis_views.py` | `theses.html`, `thesis_detail.html`, `_thesis_macros.html`, `_thesis_review.html` | `thesis_service`, `thesis_review_service`, `thesis_ai_service` |
| Noticias (radar) | `/noticias` | `thesis_views.py` | `news.html` | `thesis_news_runner`, `news_summary_service` |
| Calendario (ventana sobre Mercado) | `/api/calendario` | `calendar_views.py` | (en `base.html`) | `results_calendar` |
| Comparar | `/comparar` | `comparar_views.py` | `comparar.html` | |
| Base de datos de empresas | `/database`, `/search`, `/api/database/variacion` | `database_views.py` | `database.html` | `tier_service` (niveles) |
| Listas | `/lists`, `/api/lists/{id}/watchlist`, `/api/lists/{id}/silencio`, `/api/lists/{id}/objetivo` | `lists_views.py` | `lists.html` | `tier_service` (seguir en la watchlist, objetivos, mejor candidata) |
| Ajustes / uso de IA | `/settings`, `/settings/ia`, `/ia` | `settings_views.py` | `settings.html`, `settings_ai.html` | `ai_usage_service` |

- «Actividad del día» (Mercado): un valor cuyo mercado aún no ha abierto sale con su última sesión, atenuado
  (`tr[data-not-open]`), con la etiqueta «ayer»/«vie» (`session_label`) y siempre debajo de lo de hoy. El mapa de calor y
  las cifras del día de la cartera siguen siendo solo de hoy.
- `market_views.py` guarda lo común de precios: `INDEX_CACHE`, `ensure_prices_cached`, `get_currency_symbol`, refresco de precios
  (lo importan las demás vistas y `price_scheduler`). `/oportunidades` redirige a `/watchlist`.
- Las vistas HTML están en `app/interfaces/views/`; la API JSON en `app/interfaces/api/` (prefijo `/api`).
- Los servicios están en `app/application/services/`.
- `base.html` lleva el menú, los accesos directos «Mercado | Centro» (arriba a la izquierda, con los no leídos de la
  cartera; `syncHomeNav` marca el activo), la campana de notificaciones, la cifra del día de la cartera (`/api/portfolio/day`, con
  `portfolio_day()` de `market_views.py`, la misma cuenta que la tarjeta «Mi Cartera»), los globos de no leídos y los
  helpers JS comunes (`fmtNum`…).
- Tareas en segundo plano: `cache_warmer` (precarga cada 6 h), `price_scheduler`, el radar de noticias y los informes.

## Conceptos que se repiten
- **Avisos y señales:** `notice_service.track()` les da una clave estable y guarda leído/descartado en BBDD
  (`/api/notices/seen`, `/api/notices/dismiss`). El recuento de no leídos **solo cuenta la cartera** (`data-held`); si no hay ninguna de la cartera pero sí otras (de fuera de cartera, o cambios de valoración que no van hacia zona de compra), la campana muestra solo un punto naranja, sin número (`quiet` en `/api/notifications/count`).
  Ese recuento aparece en varios sitios (lista, pestaña del Centro, Resumen, globo del menú, `/api/centro/pulso`):
  si se cambia uno, hay que revisar los demás.
- **Operaciones:** se identifican con `tx_ref` (`fecha|tipo|acciones|precio`, en `decision_review_service`). En `assets.data`:
  `tx_notes` (el porqué) y `tx_snapshots` (zona de la valoración base y veredicto de la tesis al operar; se guarda al añadir una operación).
  La pestaña Decisiones cuenta desde `performance.start_date` (la del Rendimiento) y deja fuera las empresas compradas antes
  y vendidas después (limpieza de la cartera vieja). Entrada por tramos = compras de una empresa hasta 45 días sin comprarla o una venta.
- **Radar de noticias:** cada sábado desde las 8:00 (`RADAR_WEEKDAY`/`RADAR_START_HOUR` en `thesis_ai_service`); repasa la semana
  hábil que acaba de cerrar. Las consultas a la IA van en paralelo (`THESIS_NEWS_WORKERS`, 3).
- **Informes** (`report_service`): fotos fijas semanales (sábado 12:00, tras el radar), mensuales, trimestrales y anuales,
  guardadas en `settings` con la clave `report:<W|M|Q|Y><periodo>`. Se generan solos al arrancar y cada hora (y hacia atrás
  desde `performance.start_date`, marcados «reconstruido»), SIN IA. IA: comentario corto en el mensual (Flash, con el botón); la
  revisión trimestral y la carta anual (Pro, `GEMINI_PRO_MODEL`) se escriben solas en los informes nuevos (excepción a la regla)
  y con el botón en los reconstruidos. La carta anual guarda tus objetivos del año siguiente.
- **Niveles** (`tier_service`): cartera · Tier 1 «objetivo» (= la estrella de favorita, `is_favorite`: lo que usaba las
  favoritas sigue igual) · Tier 2 «vigilancia» (`asset.data['watch_tier']='2'`) · Tier 3 (estado ACCEPTED sin más; clave interna 'seg'). «Seguimiento» = el grupo T1 + T2 + T3. La watchlist =
  Tier 1 + las de las listas que sigues + añadidas a mano (el Tier 2 no entra solo); filtro por nivel y por lista. Avisos: todo lo que sigues avisa igual (zona, caídas, movimientos raros, compras de directivos); el Tier 1, además, «cerca de compra»; el Tier 3, las caídas solo desde un 20 % (`TIER3_DROP_PCT`). El Tier 2 y 3 que no está en la watchlist (`tier_service.followed_outside_watchlist`) no sale en su tabla pero entra en sus avisos y su monitor (`followed_monitor`). Selector en la
  ficha (`/api/ticker/{t}/nivel`) y «Repasar niveles» en la Watchlist. Tier 1 recomendado ≤ 12.
- **Listas** (en el código, «theme»: `theme_targets`, `/api/temas/anadir`): grupos de empresas. **De serie no meten nada en la
  watchlist**: cada lista tiene «Seguir en la watchlist» (`theme_targets[id].watch`). El objetivo (posiciones y peso, para el
  progreso) es opcional («+ Objetivo»); cada lista propone la mejor candidata (en zona o más cerca y, a igualdad, más CAGR).
  Silencio (aparte de seguir): una lista silenciada (`theme_targets[id].muted`) no avisa de sus empresas (salvo Tier 1,
  cartera, añadidas a mano u otra lista con avisos); una empresa silenciada (`asset.data['muted']`, campana en la ficha) no
  avisa nunca. `tier_service.muted_tickers()` se aplica en alertas, avisos y monitor de la watchlist y en la campana.
  En la base de datos (abre en «Seguimiento»): pestañas por nivel y selección múltiple (añadir a una lista, nueva o
  existente, o cambiar el nivel: `/api/temas/anadir`). En Mercado, el filtro de «Actividad del día» va por niveles (T1, T2,
  seguimiento, dudas…: el `status` de cada fila es 'T1'/'T2' o el estado); la tarjeta de la derecha sigue con Favoritos (= Tier 1) · Compra.
- **Tesis:** `config/theses/<ticker>.yaml` (datos y reglas) + `.md` (texto). Los documentos fuente están en `data/theses/`.
- **Valoración:** un único caso base con selector BPA o FCF. Zonas en `config/settings.json` → `valuation`.
- **Rendimiento y alfas:** todas las alfas van contra el índice de `performance.benchmark_ticker` (SPY: el S&P 500;
  `performance_service.reference_benchmark`). Con menos de un año de historia no se anualiza (`annualized()`): el CAGR sale «—».
  El MSCI World queda solo como línea y columna informativas.
- **Previsiones de los analistas** (`estimate_revisions_service`, yfinance `eps_trend`, caché 24 h, en la precarga): cambio en 7/30/90 días
  del BPA esperado del año que viene. Columna «Prev. 90 d» en Seguimiento, línea en «Directivos y acciones» y aviso `estimate_cut`
  si cae ≥ 5 % en 90 días (cartera en el Centro, Tier 1 en la Watchlist).
- **Efecto divisa:** `portfolio_changes` da `fx_eur` por periodo (lo que movió el cambio; el resto es lo que hicieron las empresas
  en su moneda). Sale en las tarjetas 1S–1A de Cartera y en el «Resultado» de los informes (`backfill_fx` rellena los viejos).
- **Liquidez del bróker** (`cash_service`, `settings['cash']`, rutas `/api/portfolio/liquidez…`): una cuenta en euros. Se parte de un
  saldo apuntado («Ajustar saldo» = ancla); desde ahí las compras restan, las ventas y los dividendos (netos de retención en
  origen) suman, y el usuario apunta aportaciones y retiradas. Aviso `cash_deposit` el día 1 de cada mes («¿cuánto añades?»)
  hasta que hay una aportación de ese mes. Solo se ve en Cartera (recuadro en Posiciones, ventana `#liquidez`, línea del
  Resumen, con el patrimonio = cartera + liquidez): el valor, la rentabilidad y los pesos siguen siendo solo de lo invertido.
  La liquidez sí entra en Distribución → «Por divisa» (como euros) y en su indicador «% del patrimonio», y en Rendimiento
  como curva aparte «Con liquidez» (`cash_service.patrimony`, desde el saldo de partida) con la cifra «Coste de la liquidez».
- **Multidivisa:** cambio de divisa automático con yfinance. Los importes de la cartera se muestran en €.

## Stack
FastAPI, Jinja2, SQLAlchemy 2 + Alembic (SQLite en `data/alpha_flow.db`), yfinance, CrewAI + Gemini 2.5 Pro, `uv`, Python 3.13.
Agentes en `agents/` (prompts en `config/agents.yaml`, `tasks.yaml`, `prompts.yaml`).
Variables en `.env`: `GOOGLE_API_KEY`/`GEMINI_API_KEY`, `SERPER_API_KEY`, `CREWAI_TELEMETRY_OPT_OUT=true`,
`ALPHAVANTAGE_API_KEY` (opcional: plan B del histórico financiero en la ficha, `utils/alpha_vantage.py`; sin ella no se usa).

## No leer (ruido)
`scripts/legacy/` (scripts sueltos antiguos), `data/*.json` (cachés), `logos_cache/`, `migrations/versions/` (salvo que se toquen migraciones).
