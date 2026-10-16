# Conceptos de dominio (vistas y servicios)

Se carga al trabajar en `app/`. Las reglas generales están en el `CLAUDE.md` de la raíz y el detalle de cada pantalla en
`docs/pantallas/`.

## Dónde está cada cosa
- **Vistas y API:** las vistas HTML están en `interfaces/views/`; la API JSON, en `interfaces/api/` (prefijo `/api`).
- **Servicios:** en `application/services/`.
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

## Operaciones
- Se identifican con `tx_ref` (`fecha|tipo|acciones|precio`, en `decision_review_service`).
- En `assets.data` se guardan:
  - `tx_notes`: el porqué;
  - `tx_snapshots`: la zona de la valoración base y el veredicto de la tesis al operar (se guarda al añadir una operación).
- La pestaña Decisiones cuenta desde `performance.start_date` (la del Rendimiento). Deja fuera las empresas compradas antes
  y vendidas después (limpieza de la cartera vieja).
- Entrada por tramos = las compras de una empresa hasta que pasan 45 días sin comprarla o hay una venta.

## Noticias y radar
- Las noticias sueltas no avisan (ni globos ni punto de la campana). Se revisan con el resumen semanal, que sí es un aviso
  (`news_weekly`, grupo «Tu cartera»).
- No salen en Avisos ni en la Bandeja. Están en Noticias y en la ventana «N noticias esta semana» de la portada.
- El radar corre cada sábado desde las 8:00 (`RADAR_WEEKDAY`/`RADAR_START_HOUR` en `thesis_ai_service`) y repasa la semana
  hábil que acaba de cerrar.
- Las consultas a la IA van en paralelo (`THESIS_NEWS_WORKERS`, 3).

## Informes (`report_service`)
- Fotos fijas semanales (sábado 12:00, tras el radar), mensuales, trimestrales y anuales.
- Se guardan en `settings` con la clave `report:<W|M|Q|Y><periodo>`.
- Se generan solos, SIN IA, al arrancar y cada hora. También hacia atrás desde `performance.start_date`, marcados
  «reconstruido».
- IA:
  - comentario corto en el mensual (Flash, con el botón);
  - revisión trimestral y carta anual (Pro, `GEMINI_PRO_MODEL`): se escriben solas en los informes nuevos (excepción a la
    regla) y con el botón en los reconstruidos.
- La carta anual guarda tus objetivos del año siguiente.

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

## Liquidez del bróker
- Piezas: `cash_service`, `settings['cash']` y las rutas `/api/portfolio/liquidez…`.
- Es una cuenta en euros. Se parte de un saldo apuntado («Ajustar saldo» = ancla), y desde ahí:
  - las compras restan;
  - las ventas y los dividendos (netos de retención en origen) suman;
  - el usuario apunta aportaciones y retiradas.
- Aviso `cash_deposit` el día 1 de cada mes («¿cuánto añades?»), hasta que hay una aportación de ese mes.
- Solo se ve en Cartera, con el patrimonio = cartera + liquidez. El valor, la rentabilidad y los pesos siguen siendo solo
  de lo invertido.
- Sí entra en:
  - Distribución → «Por divisa» (como euros) y su indicador «% del patrimonio»;
  - Rendimiento, como curva aparte «Con liquidez» (`cash_service.patrimony`, desde el saldo de partida), con la cifra
    «Coste de la liquidez».
