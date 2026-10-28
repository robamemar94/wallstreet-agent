# Ficha de una empresa

## Archivos
`ticker.html` es solo el esqueleto: las pestañas y los includes. Cada pieza está en su archivo:

| Pieza | Archivo en `templates/_ficha/` |
|---|---|
| Cabecera | `cabecera.html` |
| Estilos | `estilos.html` |
| JavaScript de la cabecera y el Resumen | `js_resumen.html` |
| Resumen | `resumen.html` |
| Finanzas | `finanzas.html` |
| Análisis IA | `analisis.html` |
| Macro de los informes de IA, también usada por Técnico | `informe_ia.html` |
| Valoración | `valoracion.html` |
| Mi cartera | `mi_cartera.html` |
| JavaScript grande, en una sola etiqueta `<script>` | `js/graficos`, `arranque`, `acciones`, `informes_ia`, `metricas`, `niveles_listas`, `rechazos`, `valoracion` |

`/ticker/{t}`. Los hashes `#mi-cartera`, `#finanzas`, `#graficos`, `#cuentas` y `#valoracion` abren esa pestaña.

Rutas que se cargan aparte: `/api/history/{t}`, `/ticker/{t}/directivos`, `/ticker/{t}/mi-posicion`,
`/api/ticker/{t}/precio-valoracion`.

## Cabecera
- Insignias: calidad, zona · CAGR, tesis, en cartera y «Mi precio» (de compra o, si la tienes, «Ampliar»; se edita ahí).
- Nivel y «⋯ Gestionar»: rol, estado, avisos, listas y borrar.

## Diario (panel lateral)
- Botón «Diario» en la cabecera, con el número de notas; `#diario` lo abre al cargar.
- Se carga al abrirlo (`/ticker/{t}/diario`, `_diario.html`, JS en `_ficha/js/diario.html`).
- Arriba, una nota nueva (fecha y texto) y la nota fija de la Watchlist; debajo, una sola línea de tiempo con:
  - tus notas (editar y borrar);
  - tus compras y ventas, con su porqué editable (el mismo de Decisiones, `/api/portfolio/transaction/note`);
  - de la tesis, los cambios de veredicto, sus notas y las presentaciones de resultados.
- Pantalla «Diario» del menú (`/diario`, `diario.html`): a la derecha, todo lo que has escrito de todas las empresas
  (notas y porqués de operaciones, `company_notes_service.written`; filtro `?ticker=`); a la izquierda, una nota nueva
  eligiendo la empresa y las operaciones sin porqué desde el inicio del Rendimiento, para anotarlo ahí mismo.

## Resumen
- **Franja de cifras clave** (`key_figures_service`): ocho números, en color según sean buenos o flojos, con contexto y
  minigráfico de su histórico anual. Son PER (12 meses), PER forward (NTM), FCF yield, ROE, margen, crecimiento, deuda y
  dividendo.
- **Calidad:** nota y perfil (su porqué, plegado tras la ⓘ); debajo, a qué se dedica en dos frases
  (`asset.data['business_summary']` o la línea «A qué se dedica» del informe de franquicia, que pide
  `business_model_task`; `business_summary()` en `ticker_views`). Los factores clave, en una ventana.
- El Resumen llena la ventana (el gráfico se estira).
- **Panel «Señales»:** una línea por tema con semáforo, y el recuento arriba.
  - Temas: valoración, tesis, previsiones, técnico en directo, directivos y acciones a 1/3/5 años (estas dos llegan de
    `_insiders.html`).
  - Debajo, próximos eventos y noticias de la tesis.
- **Resultados cercanos:** si presentan en 7 días o menos, la cabecera lo enseña como primera insignia («Resultados mañana 🌙»: roja con hoy o mañana, ámbar hasta 7 días; con 🌅 / 🌙 / 🕒 si se sabe que es antes de abrir, tras el cierre o en sesión, y la hora de Sevilla en el globo). Mismas fecha y hora que la agenda (`upcoming_events`). Además se mantiene, como siempre, en la franja de eventos de debajo de Señales (con los dividendos y los resultados más lejanos): a propósito se ve duplicado.
- **«Directivos y acciones»** (`_insiders.html`): datos de mercado, aparte del análisis de IA; incluye la previsión de BPA.
  - **Directivos** (`insider_service.analyze`, sin IA): la línea da una conclusión con lo de los **últimos 3 meses** (si no
    hay nada, «nada en 3 meses» y el resumen del año): Compran · Ventas a vigilar · Ventas rutinarias · Venden · Quietos.
    Con el precio medio frente al de hoy (solo si es la misma moneda: no en empresas canadienses), qué parte de la empresa
    es y, en las ventas, si son periódicas (plan: 3+ del mismo tamaño o a intervalos regulares) o lo que cobran en acciones
    (lo vendido ≤ 1,25 × lo recibido como sueldo en el año). «A vigilar»: alguien vende el 25 % o más de lo que ya tenía
    (participación de `insider_roster_holders`), fuera de un plan y sin ser su sueldo.
  - La ventana: conclusión, 3 meses frente a 12, barras por mes (compras arriba, ventas abajo), una fila por directivo
    (cargo, precio medio y % frente a hoy, % de lo suyo, patrón) y, plegadas, todas las operaciones. Precios y acciones
    ajustados por splits.
- La pestaña Técnico va oculta: su informe de IA se abre desde el indicador.
- Junto al precio, siempre el dato completo de pre/post-market («al cierre · 🌙 536,65 $ −2,79 (−0,52 %) · 01:55»).

## Mi cartera
Se carga al abrirla (`_my_position.html`, `position_review_service`). Sin scroll de página; de arriba abajo:
- **Cuatro respuestas:** resultado en € y frente al S&P 500 (el mismo dinero en las mismas fechas), peso y dividendos
  cobrados (total, nº de cobros y último).
- **Gráfico (izquierda, 7/12, a todo el alto)** con dos vistas: «Precio» (tus compras y ventas, precio medio) y «Mi dinero»
  (lo que vale lo que te queda + lo sacado + los dividendos, frente a lo puesto; `money_series`, mismo cálculo que el
  Resultado).
- **Columna derecha (5/12), dos tarjetas apiladas:**
  - **«Tu posición en cifras»** (arriba, altura fija): de dónde viene el resultado (cotización y divisa frente a dividendos),
    tu historia (tiempo, precio medio, mejor y peor compra, cuántas ganaron al índice a 12 meses; `position_stats`) y
    dividendos por año (lista de los últimos 4).
  - **«Operaciones y decisiones»** (debajo, llena el resto con scroll por dentro): une el libro de operaciones (bróker,
    comisión, borrar) con el balance de cada decisión: acciones × precio (el importe en € y el detalle, en el globo), Rent.
    (compra: lo que ha hecho desde ese día; venta: lo ganado sobre su coste), CAGR (solo compras de 3 meses o más), vs S&P y
    el porqué (estrecho, entero en el globo). Los dividendos no van en la tabla.
- El desglose de divisa, en una ventana.

## Finanzas
- **Estadísticas:** seis minigráficos de 10 años con su tendencia en color, y las cifras de hoy en tabla
  (`financial_trends_service`).
- **Gráficos.**
- **Cuentas anuales:** en español, en millones, de la más antigua a la más reciente (`financial_labels`).
- Plan B del histórico: Alpha Vantage, si hay clave en `.env`.
- **Al pegar de nuevo de fiscal.ai:** lo nuevo manda en los años que trae y se conservan los antiguos que ya no vienen
  (`financials_update_service.merge`).
- **Año fiscal nuevo, solo y gratis** (`financials_update_service`): al arrancar y cada día, cada empresa de la cartera,
  Tier 1 y Tier 2 se mira como mucho una vez por semana contra las cuentas anuales de Yahoo.
  - El año se empareja por las ventas (±0,5 %); si no cuadran, no se toca nada y avisa para pegarlas de fiscal.ai.
  - Cada línea se comprueba en el último año que ya tienes (beneficio y BPA ±1,5 %, FCF ±5 %, el resto ±2 %, márgenes
    ±0,5 puntos): solo se añaden las que cuadran; las demás quedan vacías ese año. El LTM viejo se quita.
  - Un aviso «cuentas anuales» por pasada con novedades (dos semanas); el mismo caso que no cuadra, una sola vez.
  - Las líneas que quedaron vacías en un año que vino de Yahoo se rellenan en las pasadas siguientes si pasan a cuadrar
    (`refill`, p. ej. al reconocer otro nombre de Yahoo como el flujo por el método directo de Australia).
  - En Finanzas, junto al origen del histórico: «2026 desde Yahoo» (al pasar el ratón, cuándo y qué líneas faltan).

## Valoración
Ver «Valoración» en `app/CLAUDE.md`. Los supuestos se contrastan con lo real.
