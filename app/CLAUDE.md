# Conceptos de dominio (vistas y servicios)

Se carga al trabajar en `app/`. Las reglas generales están en el `CLAUDE.md` de la raíz y el detalle de cada pantalla en
`docs/pantallas/`.

## Dónde está cada cosa
- **Vistas y API:** las vistas HTML están en `interfaces/views/`; la API JSON, en `interfaces/api/` (prefijo `/api`).
- **Servicios:** en `application/services/`.
- **Vistas de la portada y del Seguimiento:**
  - `interfaces/views/portada_views.py`: Mi Cartera (`/api/centro/pulso`), Bandeja, Agenda, noticias de la semana y
    `/mercado/centro`;
  - `interfaces/views/seguimiento_views.py`: `positions_tracking`, `live_day` y `/portfolio/seguimiento`;
  - en `thesis_views.py` queda solo lo de Tesis, Noticias y logos, más los helpers que comparten (`portfolio_exposure`,
    `news_weeks`, `_card`…).
- **`interfaces/views/market_views.py`:** guarda lo común de precios, y lo importan las demás vistas y `price_scheduler`.
  - Caché y carga de precios: `INDEX_CACHE`, `ensure_prices_cached`, `get_currency_symbol`, `price_decimals`.
  - Pre/post-market: `refresh_extended_prices`, `ext_quote`, `ext_day`, `asia_open_after`.
  - Cifra del día de la cartera: `portfolio_day()`.
  - `/oportunidades` redirige a `/watchlist`.
- **`day_change()`** (`portfolio_views.py`): la variación del día de una posición. La usan Contable, la tarjeta del día y
  Seguimiento. Da `closed`, `last_pct` y `last_abs`.
- **Tareas en segundo plano:** `cache_warmer` (precarga cada 6 h), `price_scheduler`, el radar de noticias y los informes.

## Cambios de zona (alertas)
- Hace falta un margen de ±0,5 pp de CAGR para cambiar de zona (`ZONE_MARGIN`).
- El mismo cambio de una empresa no vuelve a avisar en 30 días (`ZONE_COOLDOWN_DAYS`). El historial se limpia de esas
  repeticiones en cada escaneo (`drop_repeated_zone_changes`).
- Entrar en barata o ganga es «zona de compra» y enciende los globos.

## Avisos y señales
- `notice_service.track()` les da una clave estable y guarda leído/descartado en la BBDD (`/api/notices/seen`,
  `/api/notices/dismiss`).
- El recuento de no leídos **solo cuenta la cartera** (`data-held`). Si no hay ninguna de la cartera pero sí otras (de fuera
  de cartera, o cambios de valoración que no van hacia zona de compra), la campana muestra solo un punto naranja, sin número
  (`quiet` en `/api/notifications/count`).
- El globo de «Avisos» del menú enseña lo mismo que la campana. `pendientes` sigue en `/api/notifications/count`, pero ya no
  se usa para el globo.
- Leído / no leído va sincronizado sin recargar. Todo lo que marque leído o no leído tiene que pasar por esa función:
  - `markNotificationsRead` (`base.html`) lanza el evento `notif-read` ({keys, read}) y lo reenvía a las otras pestañas
    (BroadcastChannel);
  - la campana, la Bandeja de hoy y Avisos lo escuchan.
- Ese recuento aparece en varios sitios (lista, pestaña del Centro, Resumen, globo del menú, `/api/centro/pulso`). Si se
  cambia uno, hay que revisar los demás.
- Para crear un tipo de aviso nuevo, ver `docs/pantallas/avisos.md`.

## Copias de seguridad (`backup_service`)
- Una copia = un .zip en `data/backups/` (`alpha-flow-AAAA-MM-DD_HHMM.zip`) con una instantánea coherente de la BBDD (API de
  copia de SQLite, comprobada con `integrity_check` antes de guardarla), `config/`, `data/theses`, los CSV de operaciones, los
  JSON de operaciones/ledger y los ajustes. Lo que se puede volver a descargar (cachés, precios, logos) no entra.
- Sola: al arrancar y cada hora se comprueba, y si la última tiene más de 20 h se hace otra (`start_scheduler`, desde el arranque
  de `thesis_views`; no en los tests). Se guardan las 14 últimas y la última de cada mes del último año.
- `BACKUP_EXTRA_DIR` (en `.env`) duplica cada copia en otra carpeta (sincronizada o disco externo): es lo único que protege de
  perder el disco. Ajustes → «Copias de seguridad» enseña el estado y tiene «Copiar ahora» (`/api/backup/status`, `/api/backup/run`).
- Recuperar: `uv run python scripts/maintenance/backup.py restore <archivo>` extrae en `data/restore-<fecha>/` sin pisar nada.
- La copia de solo la BBDD al arrancar (`database.make_rolling_backup`) se mantiene como red de seguridad antes de las migraciones.

## Operaciones
- Se identifican con `tx_ref` (`fecha|tipo|acciones|precio`, en `decision_review_service`).
- En `assets.data` se guardan:
  - `tx_notes`: el porqué;
  - `tx_snapshots`: la zona de la valoración base y el veredicto de la tesis al operar (se guarda al añadir una operación).
- La pestaña Decisiones cuenta desde `performance.start_date` (la del Rendimiento). Deja fuera las empresas compradas antes
  y vendidas después (limpieza de la cartera vieja).
- Entrada por tramos = las compras de una empresa hasta que pasan 45 días sin comprarla o hay una venta.

## Diario de cada empresa (`company_notes_service`, tabla `company_notes`)
- Notas libres con fecha para cualquier empresa, en un panel lateral de la ficha, junto con las operaciones (su porqué) y
  lo de la tesis. Todas juntas, en la pantalla Diario del menú (`/diario`), con las operaciones sin porqué.
- Un solo porqué por operación: `asset.data['tx_notes']`. Lo que se anota en el Diario de decisiones de la tesis con
  una operación vinculada también se guarda ahí, si no tenía.
- La IA del trimestral y el anual recibe las notas del periodo (`notes_between`, `your_notes`) para juzgar tus razones,
  cuánto se movió cada empresa desde su primera nota y cada «Mi precio» que tuviste (historial `my_buy_price_log`): si
  llegó, si compraste y qué hizo después (`waiting_entries`: lo que costó apurar).

## Cuentas anuales (`financials_update_service`)
- Las cuentas pegadas de fiscal.ai (`full_audit_structure`) ganan el año nuevo solas desde Yahoo, solo con las cifras que
  cuadran en el año que ya tienes, y al volver a pegarlas se conservan los años antiguos (`merge`). Detalle en
  `docs/pantallas/ficha.md` (Finanzas).
- Tarea en segundo plano (arranca con las demás en `thesis_views`) y aviso `fin_update`.

## Noticias y radar
- Las noticias sueltas no avisan (ni globos ni punto de la campana). Se revisan con el resumen semanal, que sí es un aviso
  (`news_weekly`, grupo «Tu cartera»).
- No salen en Avisos ni en la Bandeja. Están en Noticias y en la ventana «N noticias esta semana» de la portada.
- El radar corre cada sábado desde las 8:00 (`RADAR_WEEKDAY`/`RADAR_START_HOUR` en `thesis_ai_service`) y repasa la semana
  hábil que acaba de cerrar.
- Las consultas a la IA van en paralelo (`THESIS_NEWS_WORKERS`, 3).

## Informes (`report_service`)
- Fotos fijas semanales, mensuales, trimestrales y anuales, guardadas en `settings` (`report:<W|M|Q|Y><periodo>`); el
  periodo en curso (`live_report`) se calcula al abrir, sin guardarse ni IA.
- Se generan solos, SIN IA. IA: comentario del mensual con botón; revisión trimestral y carta anual (Pro) solas en los
  nuevos (excepción a la regla).
- Al añadir una sección a los trimestrales/anuales, ponerla en `SECTIONS` (`backfill_sections` rehace los guardados).
- Detalle (cuándo se generan, secciones, `display`, `study_start`…): `docs/pantallas/informes.md`.

## Niveles (`tier_service`)
- **Qué hay:**
  - cartera;
  - Tier 1 «objetivo» = la estrella de favorita (`is_favorite`); lo que usaba las favoritas sigue igual;
  - Tier 2 «vigilancia» (`asset.data['watch_tier']='2'`);
  - Tier 3: estado ACCEPTED sin más (clave interna 'seg').
- **Agrupaciones:**
  - «Seguimiento» = T1 + T2 + T3.
  - La watchlist = Tier 1 + las de las listas que sigues + las añadidas a mano. El Tier 2 no entra solo.
- **Avisos:** todo lo que sigues avisa igual (zona, caídas, movimientos raros, compras de directivos). Además:
  - el Tier 1 avisa también de «cerca de compra»;
  - el Tier 3 avisa de caídas solo desde un 20 % (`TIER3_DROP_PCT`);
  - el Tier 2 y 3 que no está en la watchlist (`followed_outside_watchlist`) no sale en su tabla, pero entra en sus avisos y
    su monitor.
- **Dónde se cambia:** selector en la ficha (`/api/ticker/{t}/nivel`) y «Repasar niveles» en la Watchlist.
- **Por aceptar** (`ready_to_accept`): empresa con veredicto y las cinco normas de calidad, aún pendiente. No es un
  aviso (ni Avisos ni Bandeja): pestaña «Por aceptar» de la Base de datos (aceptar como T1/T2/T3, dudas o descartar) y
  número gris en «BBDD» del menú (`ready_to_accept_count`, global de Jinja), que lleva a esa pestaña.
- Tier 1 recomendado ≤ 12.

## Listas (en el código, «theme»)
- Piezas: `theme_targets` y `/api/temas/anadir`.
- **De serie no meten nada en la watchlist.** Cada lista tiene «Seguir en la watchlist» (`theme_targets[id].watch`).
- **Objetivo** (posiciones y peso, para el progreso): opcional («+ Objetivo»).
- **Mejor candidata:** cada lista propone una, la que está en zona o más cerca y, a igualdad, la de más CAGR.
- **Silencio** (aparte de seguir):
  - una lista silenciada (`theme_targets[id].muted`) no avisa de sus empresas, salvo Tier 1, cartera, añadidas a mano o
    que estén en otra lista con avisos;
  - una empresa silenciada (`asset.data['muted']`, campana en la ficha) no avisa nunca;
  - `tier_service.muted_tickers()` se aplica en alertas, avisos, monitor de la watchlist y campana.
- **En la Base de datos** (abre en «Seguimiento»): pestañas por nivel y selección múltiple para añadir a una lista (nueva o
  existente) o cambiar el nivel.

## Tesis y valoración
- **Tesis:** `config/theses/<ticker>.yaml` (datos y reglas) + `.md` (texto). Los documentos fuente están en `data/theses/`.
- **Revisión de resultados con IA** (`thesis_review_service.run_review`, botón de la tesis):
  - qué resultados busca: `last_published` (lo de Yahoo o, si es más reciente, el calendario propio: ventas trimestrales
    que Yahoo no apunta). Solo cifras del periodo: si no se publica un dato ese trimestre, el KPI se omite;
  - al terminar queda «lista»: todo viene rellenado (KPIs en la ventana «Revisar KPIs y guardar», propuestas de KPIs y
    preguntas maestras, veredicto) y el usuario lo valida. No aplicar nada solo: lo quiere así a propósito.
  - cada revisión deja 3-5 comprobaciones para los próximos resultados (`next_checks`: qué, cifra que lo da por bueno,
    por qué, prioridad), por encima de los KPIs; la siguiente las responde una a una (`checks_result`: cumplido / no
    cumplido / sin datos). Se ven en la revisión y en la columna derecha de la tesis («Para los próximos resultados»);

  - en la pestaña Resultados, todas las revisiones en una tabla y, en cada una, «Frente a <la anterior>»
    (`review_changes`: veredicto, KPIs que se mueven, hipótesis que cambian). En el cuadro de mando, la evolución por
    periodo con las cifras y el valor anterior junto al último (`evaluate_kpi` → `prev`).
- **Valoración:** un único caso base con selector BPA o FCF. Las zonas están en `config/settings.json` → `valuation`.
- **Supuestos frente a lo real** (`valuation_reality_service`):
  - compara con el crecimiento de la métrica por acción a 3/5/10 años y con el múltiplo real (cierre de año / métrica;
    media 5 a, mediana 10 a);
  - los clasifica en conservador / ajustado (hasta +5 pp o +20 % de múltiplo) / optimista;
  - se ve en la pestaña Valoración (bajo cada supuesto y en la casilla «Supuestos»);
  - solo si son optimistas sale también en Señales, con un ⚠ junto al CAGR de la Watchlist, y añadido a los avisos de zona
    o cerca de compra. No hay aviso propio.

## Rendimiento, previsiones y divisa
- **Rendimiento y alfas:**
  - todas las alfas van contra el índice de `performance.benchmark_ticker` (SPY: el S&P 500;
    `performance_service.reference_benchmark`);
  - con menos de un año de historia no se anualiza (`annualized()`) y el CAGR sale «—»;
  - el MSCI World queda solo como línea y columna informativas.
- **Previsiones de los analistas** (`estimate_revisions_service`, yfinance `eps_trend`, caché 24 h, en la precarga):
  - el cambio en 7/30/90 días del BPA esperado del año que viene;
  - aviso `estimate_cut` si cae ≥ 5 % en 90 días (cartera en el Centro, Tier 1 en la Watchlist);
  - aviso `estimate_rise` si sube ≥ 5 % en una Tier 1 en zona o cerca de compra.
- **Efecto divisa:**
  - `portfolio_changes` da `fx_eur` por periodo: lo que movió el cambio; el resto es lo que hicieron las empresas en su
    moneda;
  - sale en las tarjetas 1S–1A de Cartera y en el «Resultado» de los informes (`backfill_fx` rellena los viejos).
- **Multidivisa:** cambio de divisa automático con yfinance. Los importes de la cartera se muestran en €.
- **Cambio de cada operación a mano:** si Yahoo no responde al apuntarla, `rebuild_portfolio` lo reintenta y lo guarda
  (`fill_missing_fx`, `utils/fx.py`).

## Comisiones estimadas (`broker_fees`)
- Una operación a mano con comisión 0 recibe la estimada al recalcular. Una sola cuenta para el recálculo, el P&L de las
  ventas y el formulario de operación (`/api/portfolio/fee_estimate`).
- El usuario tiene IBKR con tarifa **escalonada** (EE. UU.: 0,0035 $/acción, mín. 0,35 $, máx. 1 %, más ≈ 0,0005 $/acción
  de tasas; la conversión de divisa va aparte). Los ajustes guardados (`settings['brokers']`) mandan sobre `DEFAULTS`.
- Brókeres: las de DEGIRO vienen del CSV con la comisión real; EVO, GOOGL y SBET están en DEGIRO, el resto en IBKR.
  `AUTO` = IBKR desde 2026.

## Liquidez del bróker
- Piezas: `cash_service`, `settings['cash']` y las rutas `/api/portfolio/liquidez…`.
- Es una cuenta en euros. Se parte de un saldo apuntado («Ajustar saldo» = ancla), y desde ahí:
  - las compras restan;
  - las ventas y los dividendos (netos de retención en origen) suman;
  - el usuario apunta aportaciones y retiradas.
- Aviso `cash_deposit` el día 1 de cada mes («¿cuánto añades?»), hasta que hay una aportación de ese mes.
- Solo se ve en Cartera, con el patrimonio = cartera + roboadvisor + liquidez. El valor, la rentabilidad y los pesos siguen siendo solo
  de lo invertido.
- Sí entra en:
  - Distribución → «Por divisa» (como euros) y su indicador «% del patrimonio»;
  - Rendimiento, como curva aparte «Con liquidez» (`cash_service.patrimony`, desde el saldo de partida), con la cifra
    «Coste de la liquidez».

## Roboadvisor (`robo_service`, `settings['robo']`, `/api/portfolio/roboadvisor…`)
- MyInvestor como una cesta de 7 fondos indexados (ISIN y símbolo de Yahoo en `FUNDS`; el Pacific ex-Japan cotiza en USD).
  No se sabe cómo reparte cada aportación, así que se lleva el valor total.
- **Foto real** = lo que dice MyInvestor (valor de mercado y capital invertido), a mano o pegando su resumen
  (`parse_summary`: saca también el valor de cada fondo → los pesos, y la rentabilidad de cada año).
- **Entre fotos se estima:** valor de la foto × lo que han hecho sus fondos (liquidativo de Yahoo ponderado; caché 6 h) +
  cada aportación del plan desde su fecha. Sin liquidativos, solo suma aportaciones (`complete` = False).
- **Aportación mensual** con historial (`plan`: desde AAAA-MM, importe, día): cambiarla no reescribe meses anteriores.
  El usuario aporta 900 € el día 3 (25/10/2026).
- Aviso `robo_adjust` desde el día 10 hasta que hay una foto posterior a la aportación del mes. Cada foto guarda lo que se
  estimaba (`est`) para ver el desvío.
- **Historia:** aportaciones registradas (`contributions`: real / estimado; desde la última, las del plan) y la tabla de
  MyInvestor (`monthly`: ganancia y % de cada mes; `annual`: total, % e índice). Valor a fin de mes = invertido + ganancia
  acumulada (`series`); el mes en curso, con la estimación. TIR con todas las aportaciones (`xirr`).
- Pantalla propia `/roboadvisor` (ver `docs/pantallas/roboadvisor.md`); en Cartera solo suma al Patrimonio del Resumen.
  La renta fija del usuario se dejó fuera (25/10/2026).

## Informes de IA de la ficha (`agents/`, `interfaces/api/analysis_api.py`)
- Orden: auditoría → franquicia y dirección → abogado del diablo (opcional) → veredicto → valoración y tesis. Normas: 5 pilares
  sueltos (`rules_pilar1..5`); el contexto de normas para otros agentes sale de ellos (`rules_context`).
- Auditoría forense: 3 analistas en paralelo (resultados, balance, caja), cada uno con su estado como foco y los otros
  dos para cruzar, y una síntesis (Pro) con las cuentas y los cruces entre estados. VC/PE: un solo agente (Pro).
- Las cifras que ven los agentes: `get_enriched_audit_context` = síntesis forense + cuentas (en millones, 10 años + LTM)
  + ratios forenses calculados en Python (`forensic_ratios_service`). Los agentes no deben recalcularlos.
- Cada contexto entra **una sola vez** en cada prompt (también ojo con `` `{x}` `` entre backticks: se expande).
  Lo vigila `tests/test_agent_prompts.py`.
- Aguas abajo del director (tesis, abogado del diablo) franquicia y dirección van condensadas (`condensed_report`).
- El abogado del diablo va **antes** del veredicto (ataca franquicia, dirección y auditoría): así el director, la
  valoración (escenario pesimista) y la tesis («Respuesta al abogado del diablo») lo usan sin regenerar nada.
- Normas de Calidad: las de serie en `config/quality_rules.yaml`; las del usuario, en settings `custom_evaluation_rules`
  (Ajustes). La norma del usuario manda: las tareas `rules_pilarN_task` son un esqueleto común (`rules_pillar_method`
  en `prompts.yaml`). Cada pilar recibe sus métricas ya calculadas (`pillar_metrics_markdown`: CAGR, ROIC medio, ROIIC…)
  y solo sus cuentas (`PILLAR_STATEMENTS`). La nota se guarda siempre como «X.X/2.0» (`pillar_score`).
- Crews de dos agentes (franquicia, dirección): `merge_crew_outputs` une las dos salidas; solo la segunda lleva `<resumen>`.
