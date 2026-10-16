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
- Insignias: calidad, zona · CAGR, tesis, en cartera.
- Nivel y «⋯ Gestionar»: rol, estado, avisos, listas y borrar.

## Resumen
- **Franja de cifras clave** (`key_figures_service`): ocho números, en color según sean buenos o flojos, con contexto y
  minigráfico de su histórico anual. Son PER (12 meses), PER forward (NTM), FCF yield, ROE, margen, crecimiento, deuda y
  dividendo.
- **Calidad:** los factores clave, en una ventana.
- El Resumen llena la ventana (el gráfico se estira).
- **Panel «Señales»:** una línea por tema con semáforo, y el recuento arriba.
  - Temas: valoración, tesis, previsiones, técnico en directo, directivos y acciones a 1/3/5 años (estas dos llegan de
    `_insiders.html`).
  - Debajo, próximos eventos y noticias de la tesis.
- **«Directivos y acciones»** (`_insiders.html`): datos de mercado, aparte del análisis de IA; incluye la previsión de BPA.
- La pestaña Técnico va oculta: su informe de IA se abre desde el indicador.
- Junto al precio, siempre el dato completo de pre/post-market («al cierre · 🌙 536,65 $ −2,79 (−0,52 %) · 01:55»).

## Mi cartera
Se carga al abrirla (`_my_position.html`, `position_review_service`):
- Resultado en € y frente al S&P 500, con el mismo dinero en las mismas fechas.
- Peso y dividendos.
- Compras y ventas sobre el gráfico.
- Cada operación con su porqué y su balance, en una tabla que une el libro de operaciones (bróker, comisión, borrar) con el
  balance de cada decisión.
- El desglose de divisa, en una ventana.

## Finanzas
- **Estadísticas:** seis minigráficos de 10 años con su tendencia en color, y las cifras de hoy en tabla
  (`financial_trends_service`).
- **Gráficos.**
- **Cuentas anuales:** en español, en millones, de la más antigua a la más reciente (`financial_labels`).
- Plan B del histórico: Alpha Vantage, si hay clave en `.env`.

## Valoración
Ver «Valoración» en `app/CLAUDE.md`. Los supuestos se contrastan con lo real.
